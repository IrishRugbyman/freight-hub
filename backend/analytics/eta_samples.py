"""Phase B of the True ETA build: the `eta_samples` training table.

Phase A mined ground-truth *arrivals* and scored a naive baseline by replaying
approach tracks in memory. Phase B turns those replays into a persisted,
feature-bearing table - one row per (approach, observation) - and adds the first
real feature: the sea-route distance a vessel must still sail (`route_dist_nm`),
alongside the great-circle distance kept as both baseline and feature.

Pipeline:

  1. `eta_backtest.build_samples` reconstructs the per-observation rows (label
     `remaining_h`, `gc_dist_nm`, `sog`, `voyage_id`, obs lat/lon, ...).
  2. `enrich_routes` adds `route_dist_nm` + `route_method` via the memoized
     `RouteCache` (snap each fix to a 0.25 deg cell, route once per cell/target).
  3. `persist_samples` writes the whole frame to `eta_samples`.
  4. `score_baselines` re-runs the harness for `model='naive'` and the new
     `model='naive+route'`, proving the distance fix on long-haul targets.

Phase C fills the still-NULL feature columns (`sog_trail6h`, `service_speed`,
`draught`, `dest_queue_h`, `approach_bearing`); the table is created with them now
so no schema migration is needed later. Sole writer is the analytics job.

    python -m analytics.eta_samples     # backfill eta_samples + re-score baselines
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_lib.freight import (
    CANAL_STAGING_HOURS,
    DEFAULT_SERVICE_SPEED,
    SEGMENT_SERVICE_SPEED,
)
from quant_lib.freight.eta import (
    CANAL_STAGING_BAND_NM,
    DEFAULT_CANAL_STAGING_HOURS,
    _BALLAST_FACTOR,
    _LADEN_FACTOR,
)

from analytics import eta_backtest as bt
from analytics.eta_labels import ANALYTICS_DB, _default_ais_query, haversine_nm
from analytics.eta_physics import IntervalModel, make_physics_fn
from analytics.eta_routing import ROUTE_CACHE_SCHEMA, RouteCache, snap_cell

log = logging.getLogger(__name__)

# Full Phase-B+C column set. Phase B populates the distance/context columns; the
# feature columns left NULL here are filled in Phase C (created now to avoid a
# later ALTER). voyage_id is the train/test split unit (no voyage crosses it).
ETA_SAMPLES_SCHEMA = (
    ROUTE_CACHE_SCHEMA
    + """
CREATE TABLE IF NOT EXISTS eta_samples (
    voyage_id        BIGINT,
    mmsi             BIGINT,
    target_id        VARCHAR,
    arrival_ts       TIMESTAMP,
    obs_ts           TIMESTAMP,
    obs_lat          DOUBLE,
    obs_lon          DOUBLE,
    remaining_h      DOUBLE,           -- LABEL: hours from obs_ts to arrival_ts
    route_dist_nm    DOUBLE,           -- sea-route distance (Phase B)
    gc_dist_nm       DOUBLE,           -- great-circle (baseline + feature)
    route_method     VARCHAR,          -- 'searoute' | 'gc'
    sog              DOUBLE,           -- instantaneous SOG at obs
    sog_trail6h      DOUBLE,           -- Phase C: trailing median speed
    service_speed    DOUBLE,           -- Phase C: segment prior
    segment          VARCHAR,
    laden            BOOLEAN,
    draught          DOUBLE,           -- Phase C
    target_type      VARCHAR,
    is_canal         BOOLEAN,
    dest_queue_h     DOUBLE,           -- Phase C: expected anchorage wait
    approach_bearing DOUBLE,           -- Phase C
    lead_bucket      VARCHAR,
    PRIMARY KEY (mmsi, target_id, arrival_ts, obs_ts)
);
"""
)

# Columns persisted to eta_samples (the order used by the INSERT below). Phase C
# adds the kinematic/context features (`sog_trail6h`, `service_speed`, `draught`,
# `dest_queue_h`, `approach_bearing`) the physics model and Phase-D ML consume.
_PERSIST_COLS = [
    "voyage_id", "mmsi", "target_id", "arrival_ts", "obs_ts", "obs_lat", "obs_lon",
    "remaining_h", "route_dist_nm", "gc_dist_nm", "route_method", "sog",
    "sog_trail6h", "service_speed", "draught", "dest_queue_h", "approach_bearing",
    "segment", "laden", "target_type", "is_canal", "lead_bucket",
]


def _add_physics_features(out: pd.DataFrame) -> None:
    """Populate the serve-time-safe Phase-C feature columns in place.

    `service_speed` (segment cruise prior, laden adjusted) and `dest_queue_h`
    (proximity-gated canal staging) are deterministic functions of columns already
    on the frame - no label leakage - so they are computed here in bulk rather
    than per-row in the scorer. `sog_trail6h`, `draught`, `approach_bearing` are
    carried from `build_samples`; ensure they exist for an empty/old frame.
    """
    for col in ("sog_trail6h", "draught", "approach_bearing"):
        if col not in out.columns:
            out[col] = np.nan

    seg = out["segment"].map(SEGMENT_SERVICE_SPEED).fillna(DEFAULT_SERVICE_SPEED)
    laden = out["laden"]
    factor = pd.Series(1.0, index=out.index)  # unknown laden state -> no adjustment
    factor = factor.mask(laden == True, _LADEN_FACTOR)  # noqa: E712 - pandas mask needs ==
    factor = factor.mask(laden == False, _BALLAST_FACTOR)  # noqa: E712
    out["service_speed"] = seg * factor

    dist = out["route_dist_nm"].where(np.isfinite(out["route_dist_nm"]), out["gc_dist_nm"])
    canal = out["is_canal"].fillna(False).astype(bool) & (dist <= CANAL_STAGING_BAND_NM)
    staging = out["target_id"].map(CANAL_STAGING_HOURS).fillna(DEFAULT_CANAL_STAGING_HOURS)
    out["dest_queue_h"] = np.where(canal, staging, 0.0)


def enrich_routes(conn: duckdb.DuckDBPyConnection, samples: pd.DataFrame) -> pd.DataFrame:
    """Add `route_dist_nm` + `route_method` to a samples frame (memoized).

    One `RouteCache` for the whole frame: existing (cell, target) distances are
    served from `eta_route_cache`, only never-seen cells hit searoute, and the new
    ones are flushed back so the next run starts warmer.

    The cache stores the sea route from each grid-cell *centre* to the target, but
    a fix sits somewhere inside its cell. We apply a first-order snap correction so
    the per-fix distance tracks the fix, not the cell centre::

        route_dist = cell_route - gc(cell_centre -> target) + gc(fix -> target)

    This replaces the cell-centre's straight-line leg with the fix's own. At short
    range with open water the route is ~its great circle, the two gc terms cancel,
    and `route_dist -> gc(fix->target)` (so routing never adds snapping noise to the
    already-excellent 0-6 h naive estimate). At long range the gc terms are nearly
    equal while `cell_route` carries the cape/canal detour, so the full routing gain
    survives. Because `cell_route >= gc(cell->target)` (clamped at routing time), the
    result is provably never shorter than `gc(fix->target)`.
    """
    if samples.empty:
        samples = samples.copy()
        samples["route_dist_nm"] = pd.Series(dtype=float)
        samples["route_method"] = pd.Series(dtype=str)
        for col in ("sog_trail6h", "service_speed", "draught", "dest_queue_h", "approach_bearing"):
            samples[col] = pd.Series(dtype=float)
        return samples

    targets = {
        t[0]: {"target_id": t[0], "lat": float(t[1]), "lon": float(t[2])}
        for t in conn.execute("SELECT target_id, lat, lon FROM eta_targets").fetchall()
    }
    cache = RouteCache(conn)
    dists: list[float] = []
    methods: list[str] = []
    for r in samples.itertuples():
        gc_fix = float(r.gc_dist_nm)
        # Only route fixes that are actually underway. A drifting/anchored fix
        # (sog < min) has no kinematic ETA - it is never scored by the baselines
        # and carries no routing signal - so routing it just burns the cold-cache
        # budget (~3x of the rows are sub-threshold). Leave route_dist_nm NULL.
        if (r.sog or 0.0) < bt._MIN_SOG_KN:
            dists.append(float("nan"))
            methods.append(None)
            continue
        target = targets.get(r.target_id)
        if target is None:  # target seeded after this arrival; degrade to gc
            dists.append(gc_fix)
            methods.append("gc")
            continue
        lat, lon = float(r.obs_lat), float(r.obs_lon)
        cell_route, method = cache.distance(lat, lon, target)
        clat, clon = snap_cell(lat, lon)
        gc_cell = haversine_nm(clat, clon, target["lat"], target["lon"])
        route_dist = max(cell_route - gc_cell + gc_fix, gc_fix)
        dists.append(route_dist)
        methods.append(method)
    cache.flush()

    out = samples.copy()
    out["route_dist_nm"] = dists
    out["route_method"] = methods
    _add_physics_features(out)
    return out


def persist_samples(conn: duckdb.DuckDBPyConnection, samples: pd.DataFrame) -> int:
    """Replace `eta_samples` with the freshly built frame. Returns row count.

    Full rebuild (TRUNCATE then insert): like the arrival miner, a changed gate or
    re-mined arrival shifts the PK, so a wipe-and-reload avoids stale rows. The
    route cache is independent and is never cleared.
    """
    conn.execute(ETA_SAMPLES_SCHEMA)
    conn.execute("DELETE FROM eta_samples")
    if samples.empty:
        log.warning("no eta_samples to persist (eta_arrivals empty?)")
        return 0
    rows = samples[_PERSIST_COLS].itertuples(index=False, name=None)
    conn.executemany(
        "INSERT OR REPLACE INTO eta_samples "
        "(" + ", ".join(_PERSIST_COLS) + ") "
        "VALUES (" + ", ".join("?" for _ in _PERSIST_COLS) + ")",
        list(rows),
    )
    n = conn.execute("SELECT count(*) FROM eta_samples").fetchone()[0]
    log.info("persisted %d eta_samples", n)
    return int(n)


# Long-haul targets where great-circle most understates the voyage (cape rounding
# + canal transits). The Phase-B win must show up *here* most strongly.
_LONG_HAUL = ("cp:suez", "cp:singapore_malacca", "cp:cape_good_hope", "cp:bab_el_mandeb")


# Fraction of voyages held out for evaluation. physics_v1 needs a train split to
# fit its empirical interval, so all three models are scored on the SAME test half
# for a leakage-free, apples-to-apples comparison (no voyage spans the split).
_TEST_FRAC = 0.5
_SPLIT_SEED = 0


def score_baselines(conn: duckdb.DuckDBPyConnection, samples: pd.DataFrame) -> pd.DataFrame:
    """Score naive, naive+route and physics_v1 on one held-out test split.

    The physics interval is fit on the train half and evaluated on the test half;
    the two kinematic baselines have nothing to fit but are scored on the same test
    half so every model's metrics share an identical sample set. Persists all three
    metric sets and exports their committed baseline CSVs. Returns the combined
    table.
    """
    if samples.empty:
        return pd.DataFrame()
    train, test = bt.voyage_split(samples, test_frac=_TEST_FRAC, seed=_SPLIT_SEED)
    interval = IntervalModel().fit(train)

    # One shared run_ts across all three models so the scoreboard's "latest run"
    # query returns them together (microsecond-truncated now() can land on
    # different seconds across the three score() calls otherwise).
    from datetime import UTC, datetime

    run_ts = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    naive = bt.score(test, bt.naive_eta_fn, model="naive", run_ts=run_ts)
    route = bt.score(test, bt.route_eta_fn, model="naive+route", run_ts=run_ts)
    physics = bt.score(
        test, make_physics_fn(interval), model="physics_v1", run_ts=run_ts, has_interval=True
    )

    for metrics, name in ((naive, "naive"), (route, "naive+route"), (physics, "physics_v1")):
        if not metrics.empty:
            bt.write_metrics(conn, metrics)
            bt.export_baseline(metrics, name)
    return pd.concat([naive, route, physics], ignore_index=True)


def _log_long_haul_improvement(conn: duckdb.DuckDBPyConnection, samples: pd.DataFrame) -> None:
    """Log the long-haul bias before/after routing - the Phase-B definition of done."""
    if samples.empty:
        return
    lh = samples[samples["target_id"].isin(_LONG_HAUL)]
    if lh.empty:
        log.info("no long-haul (Suez/Malacca/Cape/Bab) samples present yet to compare")
        return
    for lead in ["12-24h", "24-48h", "48h+"]:
        g = lh[lh["lead_bucket"] == lead]
        g = g[g["sog"] >= bt._MIN_SOG_KN]
        if g.empty:
            continue
        naive_err = (g["gc_dist_nm"] / g["sog"]) - g["remaining_h"]
        route_dist = g["route_dist_nm"].where(np.isfinite(g["route_dist_nm"]), g["gc_dist_nm"])
        route_err = (route_dist / g["sog"]) - g["remaining_h"]
        log.info(
            "long-haul %s (n=%d): bias naive %+.1fh -> naive+route %+.1fh | "
            "|err| %.1fh -> %.1fh",
            lead, len(g), naive_err.median(), route_err.median(),
            naive_err.abs().median(), route_err.abs().median(),
        )


def run_in_conn(conn: duckdb.DuckDBPyConnection, ais_query) -> pd.DataFrame:
    """Build + enrich + persist eta_samples and re-score baselines in one conn.

    Called by build.py against the scratch DB (shares the atomic swap) right after
    the Phase-A label mining, so samples reflect the freshly mined arrivals.
    """
    conn.execute(ETA_SAMPLES_SCHEMA)
    samples = bt.build_samples(conn, ais_query)
    log.info("built %d approach samples", len(samples))
    samples = enrich_routes(conn, samples)
    persist_samples(conn, samples)
    metrics = score_baselines(conn, samples)
    _log_long_haul_improvement(conn, samples)
    return metrics


def run() -> pd.DataFrame:
    """Standalone entry: backfill eta_samples + baselines into the live DB."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        metrics = run_in_conn(conn, _default_ais_query)
    finally:
        conn.close()
    _print_compare(metrics)
    return metrics


def _print_compare(metrics: pd.DataFrame) -> None:
    if metrics.empty:
        print("(no samples - eta_arrivals empty? run analytics.eta_labels first)")
        return
    cols = ["model", "target_type", "lead_bucket", "n", "med_abs_err_h", "bias_h", "p90_abs_err_h"]
    show = metrics[metrics["target_type"].isin(["chokepoint", "all"])][cols].copy()
    for c in ["med_abs_err_h", "bias_h", "p90_abs_err_h"]:
        show[c] = show[c].round(2)
    print(show.to_string(index=False))


# Committed reference artifact path reused from the harness.
_BASELINE_DIR = Path(bt.__file__).resolve().parent / "baselines"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Build eta_samples + re-score routing baseline").parse_args()
    run()
