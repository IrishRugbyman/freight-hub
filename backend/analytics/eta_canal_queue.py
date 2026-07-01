"""Measured canal staging (the "canal queue"), mined from AIS transit tracks.

Replaces the two hardcoded staging constants (`CANAL_STAGING_HOURS`: Suez 6h,
Panama 10h) with a value *measured* from the vessels that actually transited each
canal. For every completed transit we already have its approach track in
`eta_samples`; the observed staging is the time the vessel spent **loitering**
(nearly stopped, SOG < `_LOITER_SOG_KN`) inside the staging band (<= 60 nm from
the gate) before crossing. The per-canal estimate is the **median** of that over
recent transits - robust to the long anchorage-wait tail.

Why measure it this way (and not from the anchorage-dwell detector): that detector
produces a flat ~6.8 h dwell on every zone (a detection-window artifact), so it is
not trustworthy. Timing the loiter directly off each transit's own track is clean,
uses only real AIS, and is leakage-safe - it is computed from *completed* transits,
never from the fix being predicted.

The result is written to `eta_canal_queue` and installed process-wide via
`quant_lib.freight.eta.set_measured_staging`, so every physics call site (scoring,
serving, the `dest_queue_h` feature) reads the measured figure with the nominal
constant as fallback. On a canal with too few transits to trust, we keep the
constant.

    python -m analytics.eta_canal_queue    # (re)measure from the live eta_samples
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import duckdb
import numpy as np
import pandas as pd

from analytics.eta_labels import ANALYTICS_DB
from quant_lib.freight.eta import CANAL_STAGING_BAND_NM

log = logging.getLogger(__name__)

# A fix counts as "waiting" when nearly stopped: anchored, drifting, or crawling in
# a convoy queue. Above this it is transiting, not staging.
_LOITER_SOG_KN = 3.0
# eta_samples is thinned to ~1 fix per hour, so each loiter fix ~= 1 h of waiting.
_CADENCE_H = 1.0
# A voyage needs a couple of in-band fixes to contribute a staging observation.
_MIN_INBAND_FIXES = 2
# A canal needs at least this many transits before we trust the measured median
# over the nominal constant (a thin sample gives a noisy, unstable estimate).
_MIN_TRANSITS = 20

CANAL_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_canal_queue (
    target_id    VARCHAR PRIMARY KEY,
    queue_h      DOUBLE,      -- measured median observed staging (hours)
    n_transits   INTEGER,     -- transits the median was computed over
    computed_ts  TIMESTAMP
);
"""


def measure_canal_queue(samples: pd.DataFrame) -> dict[str, float]:
    """Per-canal median observed staging (hours), mined from `eta_samples`.

    For each canal target, group its samples by voyage, count the in-band fixes
    (``gc_dist_nm <= CANAL_STAGING_BAND_NM``) that are loitering (``sog <
    _LOITER_SOG_KN``), and take that count x cadence as the voyage's staging time.
    Return the median across voyages, keeping only canals with ``>= _MIN_TRANSITS``
    contributing transits. Pure - no DB access.
    """
    if samples.empty or "is_canal" not in samples.columns:
        return {}
    need = {"target_id", "voyage_id", "gc_dist_nm", "sog"}
    if not need.issubset(samples.columns):
        return {}
    canal = samples[samples["is_canal"].fillna(False).astype(bool)]
    inband = canal[canal["gc_dist_nm"] <= CANAL_STAGING_BAND_NM].copy()
    if inband.empty:
        return {}
    inband["_loiter"] = (inband["sog"].fillna(0.0) < _LOITER_SOG_KN).astype(float)
    out: dict[str, float] = {}
    for target_id, g in inband.groupby("target_id"):
        per_voyage = g.groupby("voyage_id").agg(
            loiter_fixes=("_loiter", "sum"), nfix=("_loiter", "size")
        )
        per_voyage = per_voyage[per_voyage["nfix"] >= _MIN_INBAND_FIXES]
        if len(per_voyage) < _MIN_TRANSITS:
            continue
        staging_h = per_voyage["loiter_fixes"].to_numpy(dtype=float) * _CADENCE_H
        out[str(target_id)] = float(np.median(staging_h))
    return out


def persist(conn: duckdb.DuckDBPyConnection, measured: dict[str, float],
            n_transits: dict[str, int] | None = None,
            now: datetime | None = None) -> None:
    """Replace `eta_canal_queue` with the freshly measured per-canal staging."""
    conn.execute(CANAL_QUEUE_SCHEMA)
    conn.execute("DELETE FROM eta_canal_queue")
    if not measured:
        return
    now = now or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    n_transits = n_transits or {}
    conn.executemany(
        "INSERT OR REPLACE INTO eta_canal_queue (target_id, queue_h, n_transits, computed_ts) "
        "VALUES (?, ?, ?, ?)",
        [(tid, h, int(n_transits.get(tid, 0)), now) for tid, h in measured.items()],
    )


def load_canal_staging(conn: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Load the measured per-canal staging map (empty if the table is absent/empty)."""
    try:
        rows = conn.execute("SELECT target_id, queue_h FROM eta_canal_queue").fetchall()
    except duckdb.CatalogException:
        return {}
    return {str(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _transit_counts(samples: pd.DataFrame) -> dict[str, int]:
    """Contributing-transit count per canal, for the persisted diagnostics column."""
    if samples.empty or "is_canal" not in samples.columns:
        return {}
    canal = samples[samples["is_canal"].fillna(False).astype(bool)]
    inband = canal[canal["gc_dist_nm"] <= CANAL_STAGING_BAND_NM]
    out: dict[str, int] = {}
    for target_id, g in inband.groupby("target_id"):
        nfix = g.groupby("voyage_id").size()
        out[str(target_id)] = int((nfix >= _MIN_INBAND_FIXES).sum())
    return out


def run_in_conn(conn: duckdb.DuckDBPyConnection, samples: pd.DataFrame) -> dict[str, float]:
    """Measure + persist canal staging from a samples frame; return the measured map.

    Called by the hourly build right after `eta_samples` is (re)built, so the
    staging reflects the freshest completed transits. The caller installs the map
    via `set_measured_staging` before physics scoring + serving.
    """
    conn.execute(CANAL_QUEUE_SCHEMA)
    measured = measure_canal_queue(samples)
    persist(conn, measured, _transit_counts(samples))
    if measured:
        log.info("canal queue measured: %s", {k: round(v, 1) for k, v in measured.items()})
    else:
        log.info("canal queue: too few transits to measure; keeping nominal constants")
    return measured


def run() -> dict[str, float]:
    """Standalone entry: (re)measure canal staging from the live `eta_samples`."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        samples = conn.execute(
            "SELECT target_id, voyage_id, is_canal, gc_dist_nm, sog FROM eta_samples"
        ).df()
        measured = run_in_conn(conn, samples)
    finally:
        conn.close()
    print("measured canal staging (hours):", {k: round(v, 2) for k, v in measured.items()})
    return measured


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Measure canal staging from eta_samples").parse_args()
    run()
