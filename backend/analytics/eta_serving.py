"""Phase E of the True ETA build: the live serving scorer.

Phases A-C built and validated the model offline. This module brings it to
production: for every *live* underway vessel it computes a true ETA to the
targets it is plausibly heading toward, attaches the calibrated interval, and
writes one snapshot row per (vessel, target) into `eta_predictions`. The API
read layer (`app.runner_eta`) serves those rows straight to the frontend cards.

Fallback chain (roadmap): **ml -> physics -> naive**. ML is gated on history
(Phase D) so it is absent today; every underway vessel therefore gets the
`physics` model, and the chain degrades to `naive` only if the physics estimate
is unavailable (no valid effective speed). The method is recorded per row so the
UI can show it honestly.

Target resolution is geometric, never destination-string based: a target counts
as "resolvable" for a vessel when it sits ahead of the vessel's course (approach
bearing within `_AHEAD_ANGLE_DEG` of COG/heading) and within `_MAX_PRED_GC_NM`
great-circle. The nearest few such targets are scored. This mirrors the offline
labels (geometric closest-approach) and keeps the dirty `destination` text out of
the ETA path.

Sole writer of `eta_predictions` is the analytics job; the table is rewritten
each run (a live snapshot, not history).

    python -m analytics.eta_serving     # score live vessels into eta_predictions
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

import duckdb
import numpy as np
import pandas as pd

from analytics.eta_backtest import _MIN_SOG_KN
from analytics.eta_labels import (
    ANALYTICS_DB,
    _default_ais_query,
    haversine_nm,
    haversine_nm_vec,
)
from analytics.eta_physics import IntervalModel, physics_p50
from analytics.eta_routing import RouteCache, snap_cell
from quant_lib.freight import effective_speed, physics_eta, queue_wait
from quant_lib.freight.eta import initial_bearing

log = logging.getLogger(__name__)

# A target is "ahead" if the heading a vessel must take to reach it is within this
# many degrees of the course it is actually steering. Generous (a vessel rarely
# points exactly at a distant target, and approaches curve), but enough to drop
# targets behind the vessel or off to the side.
_AHEAD_ANGLE_DEG = 75.0

# Do not emit an ETA beyond this great-circle range: past it the physics estimate
# is too long-lead to defend (the calibrated band is already very wide at 48h+,
# and a vessel ~1500 nm out has likely not committed to this target).
_MAX_PRED_GC_NM = 1500.0

# Score at most this many nearest ahead targets per vessel. A vessel realistically
# heads to one destination but may pass a chokepoint en route, so a few is plenty;
# this bounds payload + routing cost.
_MAX_TARGETS_PER_VESSEL = 3

# Only consider live fixes refreshed within this window (mirrors the API's stale
# cutoff). The collector keeps live_positions current; older rows are noise.
_LIVE_FRESH_H = 6.0

# Trailing-speed lookback for the effective-speed fallback (only used when the
# instantaneous SOG is missing/zero, which underway vessels are not - kept for
# parity with the offline feature and robustness on momentary dropouts).
_TRAIL_H = 6.0

ETA_PREDICTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_predictions (
    mmsi            BIGINT,
    target_id       VARCHAR,
    as_of           TIMESTAMP,
    eta_p50_h       DOUBLE,
    eta_low_h       DOUBLE,           -- calibrated P10
    eta_high_h      DOUBLE,           -- calibrated P90
    eta_naive_h     DOUBLE,           -- great-circle / SOG (the honest baseline)
    method          VARCHAR,          -- 'ml' | 'physics' | 'naive'
    eta_arrival_ts  TIMESTAMP,
    route_dist_nm   DOUBLE,
    gc_dist_nm      DOUBLE,
    route_method    VARCHAR,          -- 'searoute' | 'gc'
    sog             DOUBLE,
    segment         VARCHAR,
    laden           BOOLEAN,
    target_type     VARCHAR,
    target_name     VARCHAR,
    target_lat      DOUBLE,
    target_lon      DOUBLE,
    PRIMARY KEY (mmsi, target_id)
);
"""

_PERSIST_COLS = [
    "mmsi",
    "target_id",
    "as_of",
    "eta_p50_h",
    "eta_low_h",
    "eta_high_h",
    "eta_naive_h",
    "method",
    "eta_arrival_ts",
    "route_dist_nm",
    "gc_dist_nm",
    "route_method",
    "sog",
    "segment",
    "laden",
    "target_type",
    "target_name",
    "target_lat",
    "target_lon",
]


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute angular difference (deg) between two bearings."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def _load_targets(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute(
        "SELECT target_id, target_type, name, lat, lon, is_canal FROM eta_targets"
    ).df()


def _load_live(ais_query, now: datetime) -> pd.DataFrame:
    """Latest live fix per vessel, fresh and underway, with laden/trailing speed."""
    since = now - timedelta(hours=_LIVE_FRESH_H)
    live = ais_query(
        "SELECT mmsi, name, lat, lon, sog, cog, heading, kind, segment, "
        "       region, imo, draught "
        "FROM live_positions WHERE updated_ts > ?",
        [since],
    )
    if live is None or live.empty:
        return pd.DataFrame()
    live = live.copy()
    live["sog"] = pd.to_numeric(live["sog"], errors="coerce")
    return live[live["sog"] >= _MIN_SOG_KN]


def _trailing_speed(ais_query, mmsis: list[int], now: datetime) -> dict[int, float]:
    """Trailing-window median SOG per vessel (effective-speed fallback only)."""
    if not mmsis:
        return {}
    since = now - timedelta(hours=_TRAIL_H)
    df = ais_query(
        "SELECT mmsi, median(sog) AS m FROM ais_snapshots "
        "WHERE snapshot_ts >= ? AND sog IS NOT NULL GROUP BY mmsi",
        [since],
    )
    if df is None or df.empty:
        return {}
    return {int(r.mmsi): float(r.m) for r in df.itertuples() if pd.notna(r.m)}


def _laden_map(conn: duckdb.DuckDBPyConnection) -> dict[int, bool | None]:
    try:
        df = conn.execute("SELECT mmsi, laden FROM vessel_state").df()
    except duckdb.CatalogException:
        return {}
    out: dict[int, bool | None] = {}
    for r in df.itertuples():
        if r.laden == "laden":
            out[int(r.mmsi)] = True
        elif r.laden == "ballast":
            out[int(r.mmsi)] = False
        else:
            out[int(r.mmsi)] = None
    return out


def _fit_interval(conn: duckdb.DuckDBPyConnection) -> IntervalModel:
    """Fit the residual-quantile interval on all accumulated eta_samples.

    For *serving* (not evaluation) fitting on the full sample history is correct -
    there is no held-out test set to leak into; we want the best calibration the
    data supports. Returns an unfitted model (zero-width offsets) if no samples.
    """
    try:
        samples = conn.execute(
            "SELECT remaining_h, sog, sog_trail6h, segment, laden, route_dist_nm, "
            "       gc_dist_nm, is_canal, target_id FROM eta_samples"
        ).df()
    except duckdb.CatalogException:
        return IntervalModel()
    return IntervalModel().fit(samples)


def build_predictions(
    conn: duckdb.DuckDBPyConnection,
    ais_query,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Score every live underway vessel to its resolvable targets.

    Returns a frame ready for `persist_predictions` (one row per scored
    (vessel, target) pair). Pure read of the AIS DB via the injected `ais_query`.
    """
    now = now or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    targets = _load_targets(conn)
    live = _load_live(ais_query, now)
    if targets.empty or live.empty:
        return pd.DataFrame(columns=_PERSIST_COLS)

    interval = _fit_interval(conn)
    laden_by_mmsi = _laden_map(conn)
    trail_by_mmsi = _trailing_speed(ais_query, live["mmsi"].astype("int64").unique().tolist(), now)
    cache = RouteCache(conn)

    t_lat = targets["lat"].to_numpy(dtype=float)
    t_lon = targets["lon"].to_numpy(dtype=float)

    rows: list[dict] = []
    for v in live.itertuples():
        lat, lon = float(v.lat), float(v.lon)
        sog = float(v.sog)
        # Course we are steering: COG preferred, heading as fallback. Without
        # either we cannot tell "ahead" from "behind", so the bearing gate is
        # skipped and we rely on the distance cap + nearest-K alone.
        course = None
        if pd.notna(getattr(v, "cog", None)):
            course = float(v.cog)
        elif pd.notna(getattr(v, "heading", None)):
            course = float(v.heading)

        gc = haversine_nm_vec(t_lat, t_lon, lat, lon)
        cand_idx = np.where(gc <= _MAX_PRED_GC_NM)[0]
        if cand_idx.size == 0:
            continue

        scored: list[tuple[float, int]] = []
        for i in cand_idx:
            tgt_lat, tgt_lon = float(t_lat[i]), float(t_lon[i])
            if course is not None:
                bearing = initial_bearing(lat, lon, tgt_lat, tgt_lon)
                if _angle_diff(bearing, course) > _AHEAD_ANGLE_DEG:
                    continue
            scored.append((float(gc[i]), int(i)))
        if not scored:
            continue
        scored.sort(key=lambda x: x[0])

        seg = str(v.segment) if pd.notna(v.segment) else None
        laden = laden_by_mmsi.get(int(v.mmsi))
        trail = trail_by_mmsi.get(int(v.mmsi))

        for gc_fix, i in scored[:_MAX_TARGETS_PER_VESSEL]:
            t = targets.iloc[i]
            target = {"target_id": t["target_id"], "lat": float(t["lat"]), "lon": float(t["lon"])}
            cell_route, method = cache.distance(lat, lon, target)
            clat, clon = snap_cell(lat, lon)
            gc_cell = haversine_nm(clat, clon, target["lat"], target["lon"])
            route_dist = max(cell_route - gc_cell + gc_fix, gc_fix)

            obs = {
                "sog": sog,
                "sog_trail6h": trail,
                "segment": seg,
                "laden": laden,
                "route_dist_nm": route_dist,
                "gc_dist_nm": gc_fix,
                "is_canal": bool(t["is_canal"]),
                "target_id": t["target_id"],
            }
            p50, low, high, model = _predict(obs, interval)
            if not np.isfinite(p50):
                continue
            rows.append(
                {
                    "mmsi": int(v.mmsi),
                    "target_id": t["target_id"],
                    "as_of": now,
                    "eta_p50_h": round(p50, 2),
                    "eta_low_h": round(low, 2),
                    "eta_high_h": round(high, 2),
                    "eta_naive_h": round(gc_fix / sog, 2),
                    "method": model,
                    "eta_arrival_ts": now + timedelta(hours=p50),
                    "route_dist_nm": round(route_dist, 1),
                    "gc_dist_nm": round(gc_fix, 1),
                    "route_method": method,
                    "sog": round(sog, 1),
                    "segment": seg,
                    "laden": laden,
                    "target_type": t["target_type"],
                    "target_name": t["name"],
                    "target_lat": float(t["lat"]),
                    "target_lon": float(t["lon"]),
                }
            )
    cache.flush()
    return pd.DataFrame(rows, columns=_PERSIST_COLS)


def _predict(obs: dict, interval: IntervalModel) -> tuple[float, float, float, str]:
    """Fallback chain ml -> physics -> naive. Returns (p50, low, high, method).

    ML is gated (Phase D) so it is skipped today. Physics is the champion; if it
    yields no estimate (no valid effective speed) we degrade to the naive
    kinematic ETA with a zero-width band, labelled honestly.
    """
    p50 = physics_p50(obs)
    if np.isfinite(p50):
        lo_off, hi_off = interval.offsets(p50) if interval.fitted else (0.0, 0.0)
        return p50, max(0.0, p50 + lo_off), p50 + hi_off, "physics"

    # Physics unavailable: naive floor (great-circle / SOG), no calibrated band.
    eff = effective_speed(obs.get("sog"))
    if np.isfinite(eff) and eff > 0:
        naive = physics_eta(obs["gc_dist_nm"], eff, queue_wait(False, obs["gc_dist_nm"]))
        if np.isfinite(naive):
            return naive, naive, naive, "naive"
    return float("nan"), float("nan"), float("nan"), "naive"


def persist_predictions(conn: duckdb.DuckDBPyConnection, preds: pd.DataFrame) -> int:
    """Rewrite eta_predictions with the fresh live snapshot. Returns row count."""
    conn.execute(ETA_PREDICTIONS_SCHEMA)
    conn.execute("DELETE FROM eta_predictions")
    if preds.empty:
        log.info("no eta_predictions to persist (no live underway vessels?)")
        return 0
    rows = list(preds[_PERSIST_COLS].itertuples(index=False, name=None))
    conn.executemany(
        "INSERT OR REPLACE INTO eta_predictions "
        "(" + ", ".join(_PERSIST_COLS) + ") "
        "VALUES (" + ", ".join("?" for _ in _PERSIST_COLS) + ")",
        rows,
    )
    n = conn.execute("SELECT count(*) FROM eta_predictions").fetchone()[0]
    log.info("persisted %d eta_predictions across %d vessels", n, preds["mmsi"].nunique())
    return int(n)


def run_in_conn(conn: duckdb.DuckDBPyConnection, ais_query, now: datetime | None = None) -> int:
    """Build + persist live ETA predictions into an open analytics connection.

    Called by build.py against the scratch DB (shares the atomic swap) after the
    sample/physics phase, so the interval is fit on the freshest eta_samples.
    """
    conn.execute(ETA_PREDICTIONS_SCHEMA)
    preds = build_predictions(conn, ais_query, now=now)
    return persist_predictions(conn, preds)


def run() -> int:
    """Standalone entry: score live vessels straight into the live analytics DB."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        return run_in_conn(conn, _default_ais_query)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Score live vessels into eta_predictions").parse_args()
    run()
