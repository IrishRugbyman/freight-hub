"""Phase A of the True ETA build: ground-truth arrival mining.

This module owns the *labels* the whole ETA effort is validated against. It:

  1. Seeds `eta_targets` - the only legal ETA destinations (the 9 chokepoints
     plus a de-duplicated set of curated ports / anchorage zones). One source of
     truth for each target's centroid + arrival radius.
  2. Mines `eta_arrivals` from `ais_snapshots` - per (mmsi, target) it finds the
     closest-approach fix to the target centroid, generalising the throwaway
     6-chokepoint backtest to every target. Repeat calls are split into distinct
     arrivals by a min-gap rule.

Both tables live in `freight_analytics.duckdb` (sole writer = the analytics job);
this module never writes the collector's AIS DB. It is runnable standalone
(`python -m analytics.eta_labels`) and is also registered in `build.py`'s run
order so the hourly job keeps labels fresh.

The AIS `destination` free-text is never trusted: targets are geometric
(chokepoint / port / anchorage centroids), so an arrival is a real, observed
closest approach, not a parsed string.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from ais.regions import REGIONS

from analytics.zones import ANCHORAGE_ZONES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (env-overridable for tests; mirror build.py)
# ---------------------------------------------------------------------------

_DEFAULT_AIS_DB = "~/quant/shared/market-data/data/ais_positions.duckdb"
_DEFAULT_ANALYTICS_DB = Path(__file__).resolve().parents[1] / "data" / "freight_analytics.duckdb"

AIS_DB = Path(os.environ.get("AIS_POSITIONS_DB", _DEFAULT_AIS_DB)).expanduser()
ANALYTICS_DB = Path(os.environ.get("ANALYTICS_DB", str(_DEFAULT_ANALYTICS_DB)))

# Earliest snapshot to consider (collector started 2026-06; cheap lower bound).
_HISTORY_FLOOR = datetime(2026, 1, 1)

# The 9 transit chokepoints, keyed to their region bbox in regions.py.
_CHOKEPOINTS = [
    "singapore_malacca",
    "suez",
    "hormuz",
    "panama",
    "gibraltar",
    "bosphorus_dardanelles",
    "dover_channel",
    "cape_good_hope",
    "bab_el_mandeb",
]
# Canals add transit + queue dwell (used from Phase C); only true canals here.
_CANALS = {"suez", "panama"}

# Point-port arrival radii (nm). Anchorage zones derive reach from their bbox.
_PORT_REACH_NM = 15.0
_LNG_REACH_NM = 25.0

# Two targets closer than this (nm) are treated as the same place; the earlier
# one in seeding order wins (chokepoints, then bbox anchorages, then points).
_TARGET_DEDUPE_NM = 20.0

# Arrival-miner tuning.
_REACH_MARGIN_NM = 10.0  # qualify a fix if within reach_nm + this of the centroid
_CALL_GAP_H = 24.0  # > this gap between qualifying fixes => a new arrival
_MIN_APPROACH_FIXES = 2  # need at least this many fixes to call it an approach

_R_NM = 3440.065  # Earth radius in nautical miles

# ---------------------------------------------------------------------------
# Schema (Phase A tables). All in freight_analytics.duckdb.
# ---------------------------------------------------------------------------

ETA_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_targets (
    target_id    VARCHAR PRIMARY KEY,
    target_type  VARCHAR,              -- 'chokepoint' | 'port'
    name         VARCHAR,
    lat          DOUBLE,
    lon          DOUBLE,
    reach_nm     DOUBLE,
    is_canal     BOOLEAN
);

CREATE TABLE IF NOT EXISTS eta_arrivals (
    mmsi              BIGINT,
    target_id         VARCHAR,
    arrival_ts        TIMESTAMP,        -- closest-approach time to the target point
    min_dist_nm       DOUBLE,
    segment           VARCHAR,
    laden             BOOLEAN,
    approach_start_ts TIMESTAMP,        -- first qualifying approach fix
    PRIMARY KEY (mmsi, target_id, arrival_ts)
);

CREATE TABLE IF NOT EXISTS eta_model_metrics (
    run_ts            TIMESTAMP,
    model             VARCHAR,
    lead_bucket       VARCHAR,
    target_type       VARCHAR,
    n                 INTEGER,
    med_abs_err_h     DOUBLE,
    bias_h            DOUBLE,
    mape              DOUBLE,
    p90_abs_err_h     DOUBLE,
    interval_coverage DOUBLE,
    PRIMARY KEY (run_ts, model, lead_bucket, target_type)
);
"""

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (scalar)."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return float(2 * _R_NM * np.arcsin(np.minimum(1.0, np.sqrt(a))))


def haversine_nm_vec(lats, lons, lat0: float, lon0: float) -> np.ndarray:
    """Vectorised great-circle distance (nm) from many points to one centroid."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    phi1, phi2 = np.radians(lats), np.radians(lat0)
    dphi = np.radians(lat0 - lats)
    dlam = np.radians(lon0 - lons)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return 2 * _R_NM * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def _bbox_centroid(bbox: list[list[float]]) -> tuple[float, float]:
    (lat_min, lon_min), (lat_max, lon_max) = bbox[0], bbox[1]
    return (lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0


def _bbox_half_diag_nm(bbox: list[list[float]]) -> float:
    (lat_min, lon_min), (lat_max, lon_max) = bbox[0], bbox[1]
    return haversine_nm(lat_min, lon_min, lat_max, lon_max) / 2.0


def _bbox_cross_half_width_nm(bbox: list[list[float]]) -> float:
    """Half the *shorter* side of the bbox (nm).

    A chokepoint region box is a corridor: a long axis along the transit
    direction and a short axis across the strait. The half-diagonal is dominated
    by the long approach axis, so it would count a vessel hundreds of nm up the
    corridor as 'arrived'. The cross-strait half-width is the geometrically
    meaningful 'I am at the strait' radius, and it falls straight out of each
    chokepoint's own box - no per-strait constant.
    """
    (lat_min, lon_min), (lat_max, lon_max) = bbox[0], bbox[1]
    lat_side = haversine_nm(lat_min, lon_min, lat_max, lon_min)  # N-S extent
    lon_side = haversine_nm(lat_min, lon_min, lat_min, lon_max)  # E-W extent
    return min(lat_side, lon_side) / 2.0


def _zone_to_bbox(z: tuple[tuple[float, float], tuple[float, float]]) -> list[list[float]]:
    (lat_min, lon_min), (lat_max, lon_max) = z
    return [[lat_min, lon_min], [lat_max, lon_max]]


def _slug(name: str) -> str:
    out = []
    for ch in name.lower():
        out.append(ch if ch.isalnum() else "_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


# ---------------------------------------------------------------------------
# Target seeding (the only legal ETA destinations)
# ---------------------------------------------------------------------------


def _curated_port_points() -> list[dict]:
    """Curated point-terminals pulled from the app's terminal dictionaries.

    Imported lazily and tolerantly: if app.main is unavailable (e.g. a minimal
    test env) we fall back to a small vendored core so seeding still works.
    """
    eur: dict[str, dict] = {}
    lng: list[dict] = []
    try:  # pragma: no cover - exercised in production, bypassed in unit tests
        from app.main import _EUR_TERMINALS, _US_LNG_LOADING_TERMINALS

        eur = _EUR_TERMINALS
        lng = _US_LNG_LOADING_TERMINALS
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        log.warning("could not import app terminal dicts (%s); using vendored core", exc)
        eur = {
            "Rotterdam": {"lat": 51.96, "lon": 4.10},
            "Antwerp": {"lat": 51.26, "lon": 4.40},
            "Fos-Marseille": {"lat": 43.40, "lon": 5.10},
        }
        lng = [{"name": "Sabine Pass", "lat": 29.73, "lon": -93.87}]

    points: list[dict] = []
    for name, d in eur.items():
        points.append(
            {
                "target_id": f"port:{_slug(name)}",
                "target_type": "port",
                "name": name,
                "lat": float(d["lat"]),
                "lon": float(d["lon"]),
                "reach_nm": _PORT_REACH_NM,
                "is_canal": False,
            }
        )
    for d in lng:
        points.append(
            {
                "target_id": f"port:{_slug(d['name'])}",
                "target_type": "port",
                "name": d["name"],
                "lat": float(d["lat"]),
                "lon": float(d["lon"]),
                "reach_nm": _LNG_REACH_NM,
                "is_canal": False,
            }
        )
    return points


def build_targets() -> list[dict]:
    """Return the de-duplicated ETA target list (deterministic order).

    Seeding priority (earlier wins on a < _TARGET_DEDUPE_NM clash):
      1. 9 chokepoints  - region-bbox centroid, half-diagonal reach.
      2. anchorage zones - bbox centroid, half-diagonal reach (proper geometry).
      3. curated point ports / LNG terminals - fixed reach.
    """
    candidates: list[dict] = []

    for cp in _CHOKEPOINTS:
        bbox = REGIONS[cp]
        lat, lon = _bbox_centroid(bbox)
        candidates.append(
            {
                "target_id": f"cp:{cp}",
                "target_type": "chokepoint",
                "name": cp,
                "lat": lat,
                "lon": lon,
                "reach_nm": round(_bbox_cross_half_width_nm(bbox), 2),
                "is_canal": cp in _CANALS,
            }
        )

    for zname, z in ANCHORAGE_ZONES.items():
        bbox = _zone_to_bbox(z)
        lat, lon = _bbox_centroid(bbox)
        candidates.append(
            {
                "target_id": f"zone:{zname}",
                "target_type": "port",
                "name": zname,
                "lat": lat,
                "lon": lon,
                "reach_nm": round(_bbox_half_diag_nm(bbox), 2),
                "is_canal": False,
            }
        )

    candidates.extend(_curated_port_points())

    # Greedy de-dupe: drop any candidate within _TARGET_DEDUPE_NM of one already kept.
    kept: list[dict] = []
    for c in candidates:
        clash = any(
            haversine_nm(c["lat"], c["lon"], k["lat"], k["lon"]) < _TARGET_DEDUPE_NM for k in kept
        )
        if not clash:
            kept.append(c)
        else:
            log.debug("target %s de-duped against a nearer existing target", c["target_id"])
    return kept


def seed_targets(conn: duckdb.DuckDBPyConnection) -> int:
    """Create + (re)seed eta_targets. Idempotent (INSERT OR REPLACE)."""
    conn.execute(ETA_SCHEMA)
    targets = build_targets()
    for t in targets:
        conn.execute(
            "INSERT OR REPLACE INTO eta_targets "
            "(target_id, target_type, name, lat, lon, reach_nm, is_canal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                t["target_id"],
                t["target_type"],
                t["name"],
                t["lat"],
                t["lon"],
                t["reach_nm"],
                t["is_canal"],
            ],
        )
    log.info("seeded %d eta_targets", len(targets))
    return len(targets)


# ---------------------------------------------------------------------------
# Arrival miner
# ---------------------------------------------------------------------------

# Minimal laden classifier (avoids importing the full detect module; mirrors its
# 0.8 / 0.65 ratio thresholds against a per-approach max draught proxy).
_LADEN_RATIO = 0.8
_BALLAST_RATIO = 0.65


def _laden_bool(draught: float | None, max_seen: float | None) -> bool | None:
    if draught is None or draught <= 0 or not max_seen or max_seen <= 0:
        return None
    ratio = draught / max_seen
    if ratio >= _LADEN_RATIO:
        return True
    if ratio <= _BALLAST_RATIO:
        return False
    return None


def _mine_target(df: pd.DataFrame, target: dict) -> list[dict]:
    """Mine arrivals for one target from its pre-filtered snapshot frame.

    `df` must already be limited to fixes near the target and carry a `dist_nm`
    column (distance to the centroid). Returns one row per distinct approach.
    """
    reach = float(target["reach_nm"])
    qualify = reach + _REACH_MARGIN_NM
    out: list[dict] = []

    near = df[df["dist_nm"] <= qualify]
    if near.empty:
        return out

    gap = pd.Timedelta(hours=_CALL_GAP_H)
    for mmsi, grp in near.groupby("mmsi", sort=False):
        grp = grp.sort_values("snapshot_ts")
        ts = grp["snapshot_ts"]
        # Split into distinct calls wherever consecutive qualifying fixes are
        # more than _CALL_GAP_H apart.
        breaks = (ts.diff() > gap).to_numpy()
        ep_id = breaks.cumsum()
        for _, ep in grp.groupby(ep_id, sort=False):
            if len(ep) < _MIN_APPROACH_FIXES:
                continue
            # Closest approach must actually reach the target radius.
            i_min = ep["dist_nm"].values.argmin()
            min_dist = float(ep["dist_nm"].values[i_min])
            if min_dist > reach:
                continue
            arr = ep.iloc[i_min]
            seg = arr.get("segment")
            max_seen = None
            if "draught" in ep.columns:
                dvals = ep["draught"].dropna()
                dvals = dvals[dvals > 0]
                if not dvals.empty:
                    max_seen = float(dvals.max())
            draught = arr.get("draught")
            out.append(
                {
                    "mmsi": int(mmsi),
                    "target_id": target["target_id"],
                    "arrival_ts": arr["snapshot_ts"].to_pydatetime(),
                    "min_dist_nm": round(min_dist, 3),
                    "segment": str(seg) if seg is not None and pd.notna(seg) else None,
                    "laden": _laden_bool(
                        float(draught) if draught is not None and pd.notna(draught) else None,
                        max_seen,
                    ),
                    "approach_start_ts": ep["snapshot_ts"].iloc[0].to_pydatetime(),
                }
            )
    return out


def mine_arrivals(
    conn: duckdb.DuckDBPyConnection,
    ais_query,
    targets: list[dict] | None = None,
    history_since: datetime | None = None,
) -> int:
    """Mine eta_arrivals for every target and persist them. Returns row count.

    `ais_query(sql, params) -> DataFrame` is injected so the caller controls the
    read path (read-only + lock-retry in production; a temp DB in tests).
    """
    conn.execute(ETA_SCHEMA)
    if targets is None:
        targets = build_targets()
    since = history_since or _HISTORY_FLOOR

    total = 0
    for t in targets:
        reach = float(t["reach_nm"])
        qualify = reach + _REACH_MARGIN_NM
        # Cheap bbox pre-filter (degrees), then exact haversine in pandas.
        dlat = qualify / 60.0
        dlon = qualify / (60.0 * max(0.1, np.cos(np.radians(t["lat"]))))
        df = ais_query(
            "SELECT mmsi, snapshot_ts, lat, lon, sog, segment, draught "
            "FROM ais_snapshots "
            "WHERE snapshot_ts >= ? "
            "  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
            "ORDER BY mmsi, snapshot_ts",
            [since, t["lat"] - dlat, t["lat"] + dlat, t["lon"] - dlon, t["lon"] + dlon],
        )
        if df is None or df.empty:
            continue
        df = df.copy()
        df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])
        df["dist_nm"] = haversine_nm_vec(df["lat"].values, df["lon"].values, t["lat"], t["lon"])
        rows = _mine_target(df, t)
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO eta_arrivals "
                "(mmsi, target_id, arrival_ts, min_dist_nm, segment, laden, approach_start_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    r["mmsi"],
                    r["target_id"],
                    r["arrival_ts"],
                    r["min_dist_nm"],
                    r["segment"],
                    r["laden"],
                    r["approach_start_ts"],
                ],
            )
        total += len(rows)
        if rows:
            log.info("target %s: %d arrivals", t["target_id"], len(rows))
    log.info("mined %d eta_arrivals across %d targets", total, len(targets))
    return total


# ---------------------------------------------------------------------------
# Orchestration: standalone + build.py hook
# ---------------------------------------------------------------------------


def run_in_conn(conn: duckdb.DuckDBPyConnection, ais_query, history_since=None) -> None:
    """Seed targets + mine arrivals into an already-open analytics connection.

    Called by build.py against the scratch DB so labels share the atomic swap.
    """
    seed_targets(conn)
    mine_arrivals(conn, ais_query, history_since=history_since)


def _default_ais_query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Read-only AIS query with lock-retry (mirrors build.py for standalone use)."""
    import time

    if not AIS_DB.exists():
        return pd.DataFrame()
    for attempt in range(200):
        try:
            c = duckdb.connect(str(AIS_DB), read_only=True)
            try:
                return c.execute(sql, params or []).df()
            finally:
                c.close()
        except duckdb.CatalogException:
            return pd.DataFrame()
        except duckdb.IOException:
            if attempt == 199:
                return pd.DataFrame()
            time.sleep(0.3)
    return pd.DataFrame()


def run() -> None:
    """Standalone entry point: write directly to the live analytics DB."""
    ANALYTICS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        run_in_conn(conn, _default_ais_query)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Mine ETA ground-truth arrivals").parse_args()
    run()
