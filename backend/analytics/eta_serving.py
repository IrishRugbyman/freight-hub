"""Phase E of the True ETA build: the live serving scorer.

Phases A-C built and validated the model offline. This module brings it to
production: for every *live* underway vessel it computes a true ETA to the
targets it is plausibly heading toward, attaches the calibrated interval, and
writes one snapshot row per (vessel, target) into `eta_predictions`. The API
read layer (`app.runner_eta`) serves those rows straight to the frontend cards.

Fallback chain (roadmap): **ml -> physics -> naive**. The Phase-D LightGBM
quantile model is served per (target_type, physics-predicted-lead) cell where it
was promoted on the leakage-free walk-forward (`ETAModel.champion_map`); every
other underway vessel gets the `physics` model, and the chain degrades to `naive`
only if the physics estimate is unavailable (no valid effective speed). When no
promoted ML artifact is present the serving is physics-only, unchanged. The
method is recorded per row so the UI can show it honestly.

Target resolution is geometric, never destination-string based: a target counts
as "resolvable" for a vessel when it sits ahead of the vessel's course (approach
bearing within `_AHEAD_ANGLE_DEG` of COG/heading) and within `_MAX_PRED_GC_NM`
great-circle. The nearest few such targets are scored. This mirrors the offline
labels (geometric closest-approach) and keeps the dirty `destination` text out of
the ETA path.

Sole writer of `eta_predictions` is the analytics job; the table is rewritten
each run (a live snapshot, not history). The resolved-AIS-destination row
(`target_type='destination'`) is the one exception to "geometric, never
destination-string based": it exists to show an ETA to what the crew actually
reported, gated by a small persistent `eta_destination_state` table so a noisy
destination-string flip doesn't discontinuously jump the served ETA - see
`_committed_target`.

    python -m analytics.eta_serving     # score live vessels into eta_predictions
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

import duckdb
import numpy as np
import pandas as pd

from analytics.destination_resolver import resolve as resolve_destination
from analytics.eta_backtest import _MIN_SOG_KN
from analytics.eta_labels import (
    ANALYTICS_DB,
    _default_ais_query,
    haversine_nm,
    haversine_nm_vec,
)
from analytics.eta_ml import ETAModel, serving_choice
from analytics.eta_physics import IntervalModel, physics_p50, vectorized_physics_p50
from analytics.eta_routing import RouteCache, snap_cell
from quant_lib.freight import effective_speed, physics_eta, queue_wait, service_speed
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

# Drop any served prediction whose P50 exceeds this horizon (14 days). A barely-
# underway vessel (effective speed floored at 2 kn) on a cape/canal sea route can
# produce a multi-thousand-hour ETA from a 1500 nm great-circle origin - physically
# meaningless and not worth serving. The inbound cards already filter by their own
# (shorter) horizon, so this only trims the absurd long tail.
_MAX_PRED_ETA_H = 336.0

# Score at most this many nearest ahead targets per vessel. A vessel realistically
# heads to one destination but may pass a chokepoint en route, so a few is plenty;
# this bounds payload + routing cost.
_MAX_TARGETS_PER_VESSEL = 3

# Skip a resolved destination the vessel is effectively already at (or that resolved
# to its current port) - no useful ETA, and the tiny distance is noise.
_MIN_DEST_GC_NM = 10.0

# A resolved destination must win this many *consecutive* hourly builds before it
# replaces the committed target. AIS destination free-text flaps constantly (median
# observed spell length ~11h, but a large tail under an hour - terminal-suffix
# noise, abbreviation swaps, near-port chatter); resolving to a different real port
# on a single snapshot is not enough evidence of a genuine reroute. Measured on 3
# weeks of history: ~half of tracked vessels have >=1 destination string change
# that resolves to a genuinely different port while still >50nm out, with a median
# ~800nm (52-72h at typical speed) jump in implied ETA target - exactly the
# discontinuity this hysteresis exists to absorb. 3 consecutive hourly builds
# (~2-3h) clears real reroutes quickly while filtering single-snapshot noise.
_DEST_CONFIRM_STREAK = 3

# Drop committed-destination state for a vessel not seen with a resolvable
# destination in this long - it has presumably left AIS coverage or gone dark for
# good; a fresh sighting should adopt its destination immediately rather than
# inherit a stale commitment.
_DEST_STATE_MAX_AGE_DAYS = 30

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

# Persists across builds (unlike `eta_predictions`, a full-rewrite snapshot) so the
# destination-hysteresis streak survives from one hourly run to the next.
ETA_DEST_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_destination_state (
    mmsi              BIGINT PRIMARY KEY,
    committed_locode  VARCHAR,
    committed_name    VARCHAR,
    committed_lat     DOUBLE,
    committed_lon     DOUBLE,
    candidate_locode  VARCHAR,          -- pending challenger, if any
    candidate_streak  INTEGER,
    updated_ts        TIMESTAMP
);
"""

_DEST_STATE_COLS = [
    "mmsi",
    "committed_locode",
    "committed_name",
    "committed_lat",
    "committed_lon",
    "candidate_locode",
    "candidate_streak",
    "updated_ts",
]

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
        "       region, imo, draught, destination "
        "FROM live_positions WHERE updated_ts > ?",
        [since],
    )
    if live is None or live.empty:
        return pd.DataFrame()
    live = live.copy()
    live["sog"] = pd.to_numeric(live["sog"], errors="coerce")
    ocean = live[live["sog"] >= _MIN_SOG_KN]
    return ocean[ocean["segment"] != "Small"] if "segment" in ocean.columns else ocean


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


def _candidate_pairs(
    live: pd.DataFrame,
    targets: pd.DataFrame,
) -> list[tuple[int, float, float, float, int]]:
    """First pass: for each live vessel, find target candidates within range.

    Returns list of (vessel_idx, lat, lon, gc_nm, target_idx) tuples, with
    at most ``_MAX_TARGETS_PER_VESSEL`` targets per vessel, sorted by gc_nm
    ascending (closest first). Applies COG/heading bearing gate.
    """
    t_lat = targets["lat"].to_numpy(dtype=float)
    t_lon = targets["lon"].to_numpy(dtype=float)
    pairs = []
    for vi, v in enumerate(live.itertuples()):
        lat, lon = float(v.lat), float(v.lon)
        gc = haversine_nm_vec(t_lat, t_lon, lat, lon)
        cand_idx = np.nonzero(gc <= _MAX_PRED_GC_NM)[0]
        if cand_idx.size == 0:
            continue
        course = None
        if pd.notna(getattr(v, "cog", None)):
            course = float(v.cog)
        elif pd.notna(getattr(v, "heading", None)):
            course = float(v.heading)
        scored = []
        for i in cand_idx:
            if course is not None:
                bearing = initial_bearing(lat, lon, float(t_lat[i]), float(t_lon[i]))
                if _angle_diff(bearing, course) > _AHEAD_ANGLE_DEG:
                    continue
            scored.append((float(gc[i]), int(i)))
        if not scored:
            continue
        scored.sort(key=lambda x: x[0])
        for gc_fix, ti in scored[:_MAX_TARGETS_PER_VESSEL]:
            pairs.append((vi, lat, lon, gc_fix, ti))
    return pairs


def _load_destination_state(conn: duckdb.DuckDBPyConnection, now: datetime) -> dict[int, dict]:
    """Load the persisted per-vessel commitment state, GC'ing long-dead vessels.

    A vessel not seen with a *resolvable* destination in `_DEST_STATE_MAX_AGE_DAYS`
    is dropped so a later sighting adopts fresh rather than inheriting a stale
    commitment.
    """
    conn.execute(ETA_DEST_STATE_SCHEMA)
    cutoff = now - timedelta(days=_DEST_STATE_MAX_AGE_DAYS)
    conn.execute("DELETE FROM eta_destination_state WHERE updated_ts < ?", [cutoff])
    df = conn.execute("SELECT * FROM eta_destination_state").df()
    return {int(r["mmsi"]): r for r in df.to_dict("records")}


def _persist_destination_state(conn: duckdb.DuckDBPyConnection, state_rows: list[dict]) -> None:
    conn.execute(ETA_DEST_STATE_SCHEMA)
    if not state_rows:
        return
    frame = pd.DataFrame(state_rows)[_DEST_STATE_COLS]
    conn.register("_dest_state_frame", frame)
    conn.execute(
        "INSERT OR REPLACE INTO eta_destination_state ("
        + ", ".join(_DEST_STATE_COLS)
        + ") SELECT "
        + ", ".join(_DEST_STATE_COLS)
        + " FROM _dest_state_frame"
    )
    conn.unregister("_dest_state_frame")


def _committed_target(
    mmsi: int, rp, prior: dict | None, now: datetime
) -> tuple[str | None, str | None, float | None, float | None, dict | None]:
    """Apply destination hysteresis and return the target to serve this run.

    `rp` is this run's resolved destination candidate (`None` if the raw string
    was missing/unresolvable). Returns `(locode, name, lat, lon, new_state_row)`;
    `new_state_row` is `None` when nothing changed and there is no prior state to
    refresh (a still-unresolvable vessel with no commitment yet). A brand-new
    commitment is adopted immediately (nothing to be inconsistent with yet); a
    *change* to an already-committed target requires `_DEST_CONFIRM_STREAK`
    consecutive runs resolving to the same different port.
    """
    if rp is None:
        if prior is None:
            return None, None, None, None, None
        # Stay on the committed target; don't touch `updated_ts` (no fresh
        # resolvable sighting) so this vessel is still GC-eligible on schedule.
        return (
            prior["committed_locode"],
            prior["committed_name"],
            prior["committed_lat"],
            prior["committed_lon"],
            None,
        )

    if prior is None:
        new_state = {
            "mmsi": mmsi,
            "committed_locode": rp.locode,
            "committed_name": rp.name,
            "committed_lat": rp.lat,
            "committed_lon": rp.lon,
            "candidate_locode": None,
            "candidate_streak": 0,
            "updated_ts": now,
        }
        return rp.locode, rp.name, rp.lat, rp.lon, new_state

    if rp.locode == prior["committed_locode"]:
        new_state = {**prior, "candidate_locode": None, "candidate_streak": 0, "updated_ts": now}
        return (
            prior["committed_locode"],
            prior["committed_name"],
            prior["committed_lat"],
            prior["committed_lon"],
            new_state,
        )

    streak = int(prior["candidate_streak"]) + 1 if prior["candidate_locode"] == rp.locode else 1
    if streak >= _DEST_CONFIRM_STREAK:
        new_state = {
            "mmsi": mmsi,
            "committed_locode": rp.locode,
            "committed_name": rp.name,
            "committed_lat": rp.lat,
            "committed_lon": rp.lon,
            "candidate_locode": None,
            "candidate_streak": 0,
            "updated_ts": now,
        }
        return rp.locode, rp.name, rp.lat, rp.lon, new_state

    # Not yet confirmed: keep serving the still-committed target.
    new_state = {
        **prior,
        "candidate_locode": rp.locode,
        "candidate_streak": streak,
        "updated_ts": now,
    }
    return (
        prior["committed_locode"],
        prior["committed_name"],
        prior["committed_lat"],
        prior["committed_lon"],
        new_state,
    )


def _destination_rows(
    live: pd.DataFrame,
    cache: RouteCache,
    interval: IntervalModel,
    laden_by_mmsi: dict,
    trail_by_mmsi: dict,
    now: datetime,
    conn: duckdb.DuckDBPyConnection,
) -> list[dict]:
    """Compute an ETA to each vessel's *committed* AIS destination.

    Unlike the geometric targets, this trusts the (resolved) destination the crew
    reported, so it is not bearing-gated - a vessel is presumed to be heading to
    its stated destination. The free-text is resolved to a real seaport via
    `destination_resolver` (UN/LOCODE + fuzzy); unresolvable/junk strings don't
    displace an existing commitment. Persisted with ``target_type='destination'``
    and ``target_id='dest:<locode>'`` so the card can show it distinctly next to
    the reported ETA. ETA is the physics model (the champion map has no
    'destination' cell, so ML is not applied here - the model was never validated
    on arbitrary destinations).

    Switching targets is gated by `_committed_target`'s hysteresis: on 3 weeks of
    history, ~half of tracked vessels had a destination-string change that
    resolved to a genuinely different port while still >50nm out, with a median
    ~800nm (52-72h) implied ETA jump. Serving the raw resolved destination
    unguarded reproduces that discontinuity on every hourly build; requiring the
    same new port to resolve on several consecutive runs filters single-snapshot
    noise while still adopting real reroutes within a few hours.
    """
    rows: list[dict] = []
    if live.empty or "destination" not in live.columns:
        return rows
    state = _load_destination_state(conn, now)
    state_updates: list[dict] = []
    for v in live.itertuples():
        mmsi = int(v.mmsi)
        lat, lon = float(v.lat), float(v.lon)
        dest_str = getattr(v, "destination", None)
        rp = None
        if isinstance(dest_str, str) and dest_str.strip():
            rp = resolve_destination(dest_str, lat, lon)

        locode, name, tlat, tlon, new_state = _committed_target(mmsi, rp, state.get(mmsi), now)
        if new_state is not None:
            state_updates.append(new_state)
        if locode is None:
            continue

        gc_fix = haversine_nm(lat, lon, tlat, tlon)
        if gc_fix < _MIN_DEST_GC_NM:
            continue  # already there / resolved to current port
        sog = float(v.sog)
        seg = str(v.segment) if pd.notna(v.segment) else None
        laden = laden_by_mmsi.get(mmsi)
        target = {"target_id": f"dest:{locode}", "lat": tlat, "lon": tlon}
        clat, clon = snap_cell(lat, lon)
        cell_route, method = cache.distance(lat, lon, target)
        gc_cell = haversine_nm(clat, clon, tlat, tlon)
        route_dist = max(cell_route - gc_cell + gc_fix, gc_fix)

        obs = {
            "sog": sog,
            "sog_trail6h": trail_by_mmsi.get(mmsi),
            "segment": seg,
            "laden": laden,
            "route_dist_nm": route_dist,
            "gc_dist_nm": gc_fix,
            "is_canal": False,
            "target_id": target["target_id"],
        }
        p50, low, high, model = _predict(obs, interval)
        if not np.isfinite(p50) or p50 > _MAX_PRED_ETA_H:
            continue
        rows.append(
            {
                "mmsi": mmsi,
                "target_id": target["target_id"],
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
                "target_type": "destination",
                "target_name": name,
                "target_lat": tlat,
                "target_lon": tlon,
            }
        )
    _persist_destination_state(conn, state_updates)
    return rows


def build_predictions(
    conn: duckdb.DuckDBPyConnection,
    ais_query,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Score every live underway vessel to its resolvable targets.

    Returns a frame ready for `persist_predictions` (one row per scored
    (vessel, target) pair). Pure read of the AIS DB via the injected `ais_query`.

    Route cache pre-warm: a first pass over all candidate (vessel, target)
    pairs collects unique (from_cell, target_id) pairs and routes them all
    before the main prediction loop, so the loop pays only in-memory dict
    lookups (no searoute calls in the hot path).
    """
    now = now or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    targets = _load_targets(conn)
    live = _load_live(ais_query, now)
    if targets.empty or live.empty:
        return pd.DataFrame(columns=_PERSIST_COLS)

    interval = _fit_interval(conn)
    ml_model = ETAModel.load()  # None if no promoted artifact -> physics-only serving
    if ml_model is not None:
        log.info(
            "build_predictions: loaded ETA ML model (%d promoted cells)", len(ml_model.champion_map)
        )
    laden_by_mmsi = _laden_map(conn)
    trail_by_mmsi = _trailing_speed(ais_query, live["mmsi"].astype("int64").unique().tolist(), now)
    cache = RouteCache(conn)

    # Pass 1: collect candidates and pre-warm route cache for all unique pairs
    pairs = _candidate_pairs(live, targets)
    log.info(
        "build_predictions: %d candidate (vessel, target) pairs for %d live vessels",
        len(pairs),
        len(live),
    )
    # unique_cells: maps (clat, clon, tid) -> target dict (for cache.distance)
    unique_cell_target: dict[tuple[float, float, str], dict] = {}
    for _vi, lat, lon, _gc, ti in pairs:
        t = targets.iloc[ti]
        clat, clon = snap_cell(lat, lon)
        key = (clat, clon, str(t["target_id"]))
        if key not in unique_cell_target:
            unique_cell_target[key] = {
                "target_id": str(t["target_id"]),
                "lat": float(t["lat"]),
                "lon": float(t["lon"]),
            }
    log.info(
        "build_predictions: pre-warming route cache for %d unique (cell, target) pairs",
        len(unique_cell_target),
    )
    for (clat, clon, _tid), target_dict in unique_cell_target.items():
        cache.distance(clat, clon, target_dict)
    cache.flush()
    log.info(
        "build_predictions: route cache pre-warm done (%d hits, %d misses)",
        cache.hits,
        cache.misses,
    )

    # Pass 2: assemble a per-pair observation frame with the full feature set
    # (route/gc distance, effective-speed inputs, and the Phase-C/-D features the
    # physics and ML models consume), using the warm in-memory route cache.
    obs_records: list[dict] = []
    meta_records: list[dict] = []
    for vi, lat, lon, gc_fix, ti in pairs:
        v = live.iloc[vi]
        sog = float(v["sog"])
        seg = str(v["segment"]) if pd.notna(v["segment"]) else None
        laden = laden_by_mmsi.get(int(v["mmsi"]))
        trail = trail_by_mmsi.get(int(v["mmsi"]))
        t = targets.iloc[ti]
        target = {"target_id": t["target_id"], "lat": float(t["lat"]), "lon": float(t["lon"])}

        clat, clon = snap_cell(lat, lon)
        cell_route, method = cache.distance(lat, lon, target)
        gc_cell = haversine_nm(clat, clon, target["lat"], target["lon"])
        route_dist = max(cell_route - gc_cell + gc_fix, gc_fix)

        is_canal = bool(t["is_canal"])
        draught = float(v["draught"]) if pd.notna(getattr(v, "draught", None)) else None
        obs_records.append(
            {
                "sog": sog,
                "sog_trail6h": trail,
                "segment": seg,
                "laden": laden,
                "route_dist_nm": route_dist,
                "gc_dist_nm": gc_fix,
                "is_canal": is_canal,
                "target_id": str(t["target_id"]),
                "target_type": str(t["target_type"]),
                "draught": draught,
                "service_speed": service_speed(seg, laden),
                "dest_queue_h": queue_wait(is_canal, route_dist, str(t["target_id"])),
                "approach_bearing": initial_bearing(lat, lon, float(t["lat"]), float(t["lon"])),
            }
        )
        meta_records.append(
            {
                "mmsi": int(v["mmsi"]),
                "sog": sog,
                "seg": seg,
                "laden": laden,
                "route_dist": route_dist,
                "gc_fix": gc_fix,
                "method": method,
                "t": t,
            }
        )

    if not obs_records:
        return pd.DataFrame(columns=_PERSIST_COLS)

    obsdf = pd.DataFrame(obs_records)
    phys_p50 = vectorized_physics_p50(obsdf)
    use_ml, ml_p50, ml_low, ml_high = serving_choice(ml_model, obsdf, phys_p50)

    # Pass 3: blend ML (where the champion map assigns it) with the physics/naive
    # fallback chain, and assemble the persisted rows.
    rows: list[dict] = []
    for i, meta in enumerate(meta_records):
        obs = obs_records[i]
        if use_ml[i]:
            p50, low, high, model = float(ml_p50[i]), float(ml_low[i]), float(ml_high[i]), "ml"
        else:
            p50, low, high, model = _predict(obs, interval)
        if not np.isfinite(p50) or p50 > _MAX_PRED_ETA_H:
            continue
        t = meta["t"]
        sog = meta["sog"]
        rows.append(
            {
                "mmsi": meta["mmsi"],
                "target_id": t["target_id"],
                "as_of": now,
                "eta_p50_h": round(p50, 2),
                "eta_low_h": round(low, 2),
                "eta_high_h": round(high, 2),
                "eta_naive_h": round(meta["gc_fix"] / sog, 2),
                "method": model,
                "eta_arrival_ts": now + timedelta(hours=p50),
                "route_dist_nm": round(meta["route_dist"], 1),
                "gc_dist_nm": round(meta["gc_fix"], 1),
                "route_method": meta["method"],
                "sog": round(sog, 1),
                "segment": meta["seg"],
                "laden": meta["laden"],
                "target_type": t["target_type"],
                "target_name": t["name"],
                "target_lat": float(t["lat"]),
                "target_lon": float(t["lon"]),
            }
        )

    # Append an ETA to each vessel's resolved AIS destination (physics, not
    # bearing-gated - we trust the reported destination's direction).
    dest_rows = _destination_rows(live, cache, interval, laden_by_mmsi, trail_by_mmsi, now, conn)
    if dest_rows:
        cache.flush()  # persist any new (cell, dest) route distances
        log.info("build_predictions: %d resolved-destination ETAs", len(dest_rows))
        rows.extend(dest_rows)
    return pd.DataFrame(rows, columns=_PERSIST_COLS)


def _predict(obs: dict, interval: IntervalModel) -> tuple[float, float, float, str]:
    """Physics -> naive fallback for rows the champion map does not route to ML.

    ML selection happens upstream in `serving_choice` (it needs the batch ML
    prediction + champion map); this handles the physics/naive tail. Physics is
    the champion; if it yields no estimate (no valid effective speed) we degrade
    to the naive kinematic ETA with a zero-width band, labelled honestly.
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
    frame = preds[_PERSIST_COLS]
    conn.register("_preds_frame", frame)
    conn.execute(
        "INSERT OR REPLACE INTO eta_predictions ("
        + ", ".join(_PERSIST_COLS)
        + ") SELECT "
        + ", ".join(_PERSIST_COLS)
        + " FROM _preds_frame"
    )
    conn.unregister("_preds_frame")
    n = conn.execute("SELECT count(*) FROM eta_predictions").fetchone()[0]
    log.info("persisted %d eta_predictions across %d vessels", n, preds["mmsi"].nunique())
    return int(n)


def run_in_conn(conn: duckdb.DuckDBPyConnection, ais_query, now: datetime | None = None) -> int:
    """Build + persist live ETA predictions into an open analytics connection.

    Called by build.py against the scratch DB (shares the atomic swap) after the
    sample/physics phase, so the interval is fit on the freshest eta_samples.
    """
    conn.execute(ETA_PREDICTIONS_SCHEMA)
    # Install the data-measured canal staging so the physics queue term + dest_queue_h
    # match what the batch job measured (a no-op re-install when build.py already set
    # it earlier this process; the source of truth for the standalone path).
    from analytics.eta_canal_queue import load_canal_staging
    from quant_lib.freight.eta import set_measured_staging

    set_measured_staging(load_canal_staging(conn))
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
