"""Hourly analytics job for the freight hub.

Reads ais_positions.duckdb (read-only, lock-retry) since the watermark,
detects transit, anchored, and fleet-density events, and writes results to
freight_analytics.duckdb (sole writer for that file).

Usage:
    python -m analytics.build              # normal incremental run
    python -m analytics.build --reset      # wipe watermark and re-process all history

The job is idempotent: all writes use INSERT OR REPLACE, so re-runs are safe.

Concurrency strategy: the job writes to a scratch file (freight_analytics.new.duckdb),
then atomically renames it over the live DB at the very end. This keeps the production
file fully readable by the API throughout the ~5-10 min build window.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from .detect import (
    ais_gap_events,
    anchored_episodes,
    dark_voyage_events,
    destination_change_events,
    fleet_density_rows,
    gps_spoof_events,
    loitering_events,
    sts_candidates,
    transit_episodes,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (env-overridable for tests)
# ---------------------------------------------------------------------------

_DEFAULT_AIS_DB = "~/quant/shared/market-data/data/ais_positions.duckdb"
_DEFAULT_ANALYTICS_DB = Path(__file__).resolve().parents[1] / "data" / "freight_analytics.duckdb"

AIS_DB = Path(os.environ.get("AIS_POSITIONS_DB", _DEFAULT_AIS_DB)).expanduser()
ANALYTICS_DB = Path(os.environ.get("ANALYTICS_DB", str(_DEFAULT_ANALYTICS_DB)))

# How many hours to reprocess behind the last watermark (overlap handles late snapshots)
_OVERLAP_HOURS = 6

# Fallback window start when no watermark exists (first run / --reset)
_HISTORY_START = datetime(2026, 1, 1)

# Retry budget when the collector holds the AIS DB lock (usually < 1s per write)
_LOCK_RETRIES = 200  # 200 * 0.3s = 60s; collector holds write lock between upserts

# Ceiling for DuckDB's own buffer manager. Unset, DuckDB defaults to 80% of system
# RAM (~6.1 GB on this 7.6 GB box), which is ABOVE the unit's MemoryMax=5G - so it
# would keep allocating until the cgroup killed it instead of spilling to disk.
# The remainder of the 5 GB budget is for the pandas frames this job builds, which
# DuckDB does not count against its limit.
_DUCKDB_MEMORY_LIMIT = os.environ.get("FREIGHT_DUCKDB_MEMORY_LIMIT", "2GB")

# Window within max_ts where a vessel is considered "recently active" for gap closure
_GAP_RECHECK_H = 6


def _tune(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Apply the memory ceiling to a freshly opened connection."""
    conn.execute(f"SET memory_limit='{_DUCKDB_MEMORY_LIMIT}'")
    return conn


# ---------------------------------------------------------------------------
# Analytics DB schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_watermark (
    key     VARCHAR PRIMARY KEY,
    ts      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transit_events (
    mmsi        BIGINT,
    chokepoint  VARCHAR,
    entered_ts  TIMESTAMP,
    exited_ts   TIMESTAMP,
    direction   VARCHAR,
    kind        VARCHAR,
    segment     VARCHAR,
    laden       BOOLEAN,
    PRIMARY KEY (mmsi, chokepoint, entered_ts)
);

CREATE TABLE IF NOT EXISTS anchored_episodes (
    mmsi        BIGINT,
    zone        VARCHAR,
    start_ts    TIMESTAMP,
    end_ts      TIMESTAMP,
    kind        VARCHAR,
    segment     VARCHAR,
    PRIMARY KEY (mmsi, zone, start_ts)
);

CREATE TABLE IF NOT EXISTS fleet_density (
    ts              TIMESTAMP,
    region          VARCHAR,
    kind            VARCHAR,
    segment         VARCHAR,
    laden_count     INTEGER,
    ballast_count   INTEGER,
    unknown_count   INTEGER,
    PRIMARY KEY (ts, region, kind, segment)
);

CREATE TABLE IF NOT EXISTS vessel_state (
    mmsi                BIGINT PRIMARY KEY,
    max_draught_seen    DOUBLE,
    last_draught        DOUBLE,
    laden               VARCHAR,
    updated_ts          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ais_events (
    event_id    VARCHAR PRIMARY KEY,
    type        VARCHAR,
    mmsi        BIGINT,
    mmsi2       BIGINT,
    start_ts    TIMESTAMP,
    end_ts      TIMESTAMP,
    lat         DOUBLE,
    lon         DOUBLE,
    region      VARCHAR,
    kind        VARCHAR,
    segment     VARCHAR,
    details     VARCHAR
);
"""

# ---------------------------------------------------------------------------
# AIS DB helpers (read-only with lock-retry)
# ---------------------------------------------------------------------------


def _ais_query(sql: str, params: list | None = None) -> pd.DataFrame:
    if not AIS_DB.exists():
        return pd.DataFrame()
    for attempt in range(_LOCK_RETRIES):
        try:
            conn = _tune(duckdb.connect(str(AIS_DB), read_only=True))
            try:
                return conn.execute(sql, params or []).df()
            finally:
                conn.close()
        except duckdb.CatalogException:
            return pd.DataFrame()
        except duckdb.IOException:
            if attempt == _LOCK_RETRIES - 1:
                return pd.DataFrame()
            time.sleep(0.3)
    return pd.DataFrame()


def _rows_beyond(ts: datetime) -> int:
    """Count snapshot rows still unprocessed beyond a capped window's upper bound."""
    df = _ais_query("SELECT count(*) AS n FROM ais_snapshots WHERE snapshot_ts >= ?", [ts])
    return 0 if df.empty else int(df["n"].iloc[0])


# ---------------------------------------------------------------------------
# Analytics DB helpers
# ---------------------------------------------------------------------------


_ANALYTICS_NEW = ANALYTICS_DB.with_suffix(".new.duckdb")


def _wal_for(db: Path) -> Path:
    """DuckDB's write-ahead log sits beside the DB as `<name>.wal`."""
    return db.with_name(db.name + ".wal")


def _open_analytics_scratch() -> duckdb.DuckDBPyConnection:
    """Open a scratch DB for this build run.

    Copies the current live DB (so historical data is preserved), then opens
    the scratch file exclusively. The live DB is never locked during the build;
    only at the very end do we atomically rename scratch -> live.
    """
    ANALYTICS_DB.parent.mkdir(parents=True, exist_ok=True)

    # Remove any leftover scratch from a prior crashed run - including its WAL.
    # Dropping the .duckdb but leaving the .wal would let a dead run's writes
    # replay into the fresh copy we are about to make.
    _ANALYTICS_NEW.unlink(missing_ok=True)
    _wal_for(_ANALYTICS_NEW).unlink(missing_ok=True)

    # Seed scratch with all existing historical data so INSERT OR REPLACE
    # only needs to add/update incremental rows.
    if ANALYTICS_DB.exists():
        shutil.copy2(ANALYTICS_DB, _ANALYTICS_NEW)

    conn = _tune(duckdb.connect(str(_ANALYTICS_NEW)))
    conn.execute(_SCHEMA)
    return conn


def _commit_scratch() -> None:
    """Atomically replace the live analytics DB with the completed scratch file."""
    if not _ANALYTICS_NEW.exists():
        log.warning("scratch file missing at commit time; nothing to promote")
        return
    # On Linux, os.replace is POSIX rename(2) - atomic within the same filesystem.
    os.replace(_ANALYTICS_NEW, ANALYTICS_DB)

    # A WAL belongs to a database *path*, not an inode, so the rename above would
    # otherwise strand the scratch's WAL under the old name and leave any WAL
    # sitting beside the live DB pointing at a file that no longer exists. Keep
    # the pair consistent: carry a real scratch WAL across, drop a stale one.
    scratch_wal, live_wal = _wal_for(_ANALYTICS_NEW), _wal_for(ANALYTICS_DB)
    if scratch_wal.exists():
        os.replace(scratch_wal, live_wal)
    else:
        live_wal.unlink(missing_ok=True)

    log.info("scratch promoted to live: %s", ANALYTICS_DB)


def _window_bounds(
    watermark: datetime | None, max_window_hours: float | None
) -> tuple[datetime, datetime | None]:
    """Resolve the [since, until) snapshot window for one build pass.

    `since` backs off _OVERLAP_HOURS behind the watermark so late-arriving snapshots
    are reprocessed; with no watermark it falls back to the start of history.

    `until` is None for an unbounded run (the normal hourly case: read everything new).
    Passing max_window_hours caps the pass, which is what makes a large backlog
    recoverable - the whole window is loaded into one DataFrame, so a multi-day gap
    read in a single pass is what OOMs the job. Walk it forward in bounded passes
    instead; each advances the watermark by (max_window_hours - _OVERLAP_HOURS).

    Args:
        watermark: Last processed snapshot_ts, or None on a first/reset run.
        max_window_hours: Cap on the window width, or None for unbounded.

    Returns:
        (since, until) where until is None when unbounded.
    """
    since = watermark - timedelta(hours=_OVERLAP_HOURS) if watermark else _HISTORY_START
    if max_window_hours is None:
        return since, None
    if max_window_hours <= _OVERLAP_HOURS:
        raise ValueError(
            f"max_window_hours must exceed the {_OVERLAP_HOURS}h overlap, else the "
            f"window never advances (got {max_window_hours})"
        )
    return since, since + timedelta(hours=max_window_hours)


def _get_watermark(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    row = conn.execute("SELECT ts FROM meta_watermark WHERE key = 'snapshots'").fetchone()
    return row[0] if row else None


def _set_watermark(conn: duckdb.DuckDBPyConnection, ts: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO meta_watermark (key, ts) VALUES ('snapshots', ?)", [ts])


def _load_vessel_states(conn: duckdb.DuckDBPyConnection) -> dict:
    df = conn.execute("SELECT mmsi, max_draught_seen, last_draught, laden FROM vessel_state").df()
    if df.empty:
        return {}
    return {
        int(r.mmsi): {
            "max_draught_seen": r.max_draught_seen,
            "last_draught": r.last_draught,
            "laden": r.laden,
        }
        for r in df.itertuples()
    }


# ---------------------------------------------------------------------------
# Main build logic
# ---------------------------------------------------------------------------


def run(
    reset: bool = False,
    max_window_hours: float | None = None,
    skip_derived: bool = False,
) -> None:
    """Run one analytics build pass against a scratch DB, then promote it.

    Args:
        reset: Clear the watermark and reprocess all history.
        max_window_hours: Cap the snapshot window (see _window_bounds). None reads
            everything since the watermark, which is right for the hourly run and
            wrong for a multi-day backlog.
        skip_derived: Skip the full-history ETA/destination stages (7b-7e).
    """
    log.info("analytics.build starting (AIS=%s, analytics=%s)", AIS_DB, ANALYTICS_DB)

    conn = _open_analytics_scratch()
    try:
        _run_inner(conn, reset, max_window_hours, skip_derived)
    except Exception:
        conn.close()
        # Clean up scratch so next run starts fresh.
        if _ANALYTICS_NEW.exists():
            _ANALYTICS_NEW.unlink()
        raise


_AIS_EVENT_SQL = (
    "INSERT OR REPLACE INTO ais_events "
    "(event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon, "
    " region, kind, segment, details) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_events(conn: duckdb.DuckDBPyConnection, events: list[dict]) -> None:
    if not events:
        return
    rows = [
        [
            e["event_id"],
            e["type"],
            e["mmsi"],
            e["mmsi2"],
            e["start_ts"],
            e["end_ts"],
            e["lat"],
            e["lon"],
            e["region"],
            e["kind"],
            e["segment"],
            e["details"],
        ]
        for e in events
    ]
    conn.executemany(_AIS_EVENT_SQL, rows)


def _run_derived_stages(conn: duckdb.DuckDBPyConnection) -> None:
    """Run the ETA + destination stages (7b-7e) that read the FULL AIS history.

    Split out from _run_inner so a bounded catch-up pass can skip them: they do not
    depend on the incremental snapshot window, they are the most memory-hungry part
    of the job, and only their final state matters (each run overwrites the last).

    Every stage is individually guarded - one failing must not cost the run its
    detector output or its watermark advance.
    """
    # ------------------------------------------------------------------
    # 7b. ETA ground truth (True ETA Phase A): seed targets + mine arrivals.
    # Writes only to the scratch analytics DB, so it shares the atomic swap.
    # Mining reads the full AIS history (arrivals need whole approach tracks),
    # so it uses the injected _ais_query rather than the incremental `df`.
    # ------------------------------------------------------------------
    try:
        from .eta_labels import run_in_conn as _eta_run

        _eta_run(conn, _ais_query)
    except Exception as exc:
        log.warning("ETA label mining failed, skipping: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 7b2. Destination predictor labels: the port-call transition graph +
    # per-vessel visit frequency mined from the arrivals 7b just refreshed
    # (destination predictor Phase 0). Pure read of eta_arrivals - no AIS access.
    # ------------------------------------------------------------------
    try:
        from .destination_labels import run_in_conn as _dest_labels_run

        _dest_labels_run(conn)
    except Exception as exc:
        log.warning("destination label mining failed, skipping: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 7c. ETA samples + sea-route distance + physics ETA (True ETA Phase B/C):
    # build the per-observation training table (with kinematic/context features),
    # enrich with cached searoute distances, and score naive vs naive+route vs
    # physics_v1 (effective speed + calibrated intervals). Depends on 7b's arrivals.
    # ------------------------------------------------------------------
    try:
        from .eta_samples import run_in_conn as _eta_samples_run

        _t0 = time.perf_counter()
        _eta_samples_run(conn, _ais_query)
        log.info("7c eta_samples: %.1fs", time.perf_counter() - _t0)
    except Exception as exc:
        log.warning("ETA sample build failed, skipping: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 7d. ETA serving (True ETA Phase E): score every live underway vessel to
    # its resolvable targets (physics ETA + calibrated interval, fallback chain
    # ml->physics->naive) into eta_predictions, the live snapshot the API serves.
    # Fits its interval on the eta_samples just rebuilt in 7c.
    # ------------------------------------------------------------------
    try:
        from .eta_serving import run_in_conn as _eta_serving_run

        _t0 = time.perf_counter()
        _eta_serving_run(conn, _ais_query)
        log.info("7d eta_serving: %.1fs", time.perf_counter() - _t0)
    except Exception as exc:
        log.warning("ETA serving scorer failed, skipping: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 7f. Destination predictor serving: score every live underway vessel's
    # candidate destinations (geometric + resolved reported dest) and persist the
    # ranked top-k into destination_predictions. Depends on 7b2's fresh priors.
    # ------------------------------------------------------------------
    try:
        from .destination_serving import run_in_conn as _dest_serving_run

        _t0 = time.perf_counter()
        _dest_serving_run(conn, _ais_query)
        log.info("7f destination_serving: %.1fs", time.perf_counter() - _t0)
    except Exception as exc:
        log.warning("destination serving scorer failed, skipping: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 7e. ETA drift watch (True ETA Phase G): compare the champion's just-scored
    # accuracy against its trailing history + the calibration band, and persist
    # any degradation to eta_drift_alerts (+ log warning). Pure monitoring; the
    # refresh itself is already covered by this hourly job.
    # ------------------------------------------------------------------
    try:
        from .eta_drift import run_in_conn as _eta_drift_run

        _t0 = time.perf_counter()
        _eta_drift_run(conn)
        log.info("7e eta_drift: %.1fs", time.perf_counter() - _t0)
    except Exception as exc:
        log.warning("ETA drift watch failed, skipping: %s", exc, exc_info=True)


def _run_inner(
    conn: duckdb.DuckDBPyConnection,
    reset: bool,
    max_window_hours: float | None = None,
    skip_derived: bool = False,
) -> None:
    if reset:
        log.info("--reset: clearing watermark")
        conn.execute("DELETE FROM meta_watermark WHERE key = 'snapshots'")

    watermark = _get_watermark(conn)
    since, until = _window_bounds(watermark, max_window_hours)
    if watermark:
        log.info("watermark %s, reading since %s", watermark, since)
    else:
        # First run: process all available history
        log.info("no watermark found; reading all history since %s", since)
    if until is not None:
        log.info("window capped at %s (--max-window-hours=%s)", until, max_window_hours)

    # ------------------------------------------------------------------
    # Load snapshots from AIS DB
    # ------------------------------------------------------------------
    _SNAPSHOT_COLS = (
        "SELECT mmsi, snapshot_ts, lat, lon, sog, nav_status, draught, destination, "
        "       kind, segment, region "
        "FROM ais_snapshots "
    )
    if until is None:
        df = _ais_query(
            _SNAPSHOT_COLS + "WHERE snapshot_ts >= ? ORDER BY mmsi, snapshot_ts",
            [since],
        )
    else:
        df = _ais_query(
            _SNAPSHOT_COLS
            + "WHERE snapshot_ts >= ? AND snapshot_ts < ? ORDER BY mmsi, snapshot_ts",
            [since, until],
        )

    if df.empty:
        # An empty *bounded* window is not the end of the backlog - the collector may
        # simply have no coverage for this stretch. Advance past it so a walk-forward
        # cannot livelock on a gap.
        if until is not None and _rows_beyond(until):
            log.info("no snapshots in window; advancing watermark to %s", until)
            _set_watermark(conn, until)
            conn.close()
            _commit_scratch()
            return
        log.info("no new snapshots; nothing to process")
        conn.close()
        _commit_scratch()
        return

    # Coerce timestamps to datetime
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])

    max_ts = df["snapshot_ts"].max()
    log.info("loaded %d snapshot rows (max_ts=%s)", len(df), max_ts)

    if until is not None:
        remaining = _rows_beyond(until)
        if remaining:
            log.info(
                "%d snapshot rows remain beyond %s - re-run to continue the walk-forward",
                remaining,
                until,
            )
        else:
            log.info("window reached the end of available snapshots; backlog cleared")

    # ------------------------------------------------------------------
    # 1. Transit detection
    # ------------------------------------------------------------------
    try:
        transits = transit_episodes(df)
        log.info("detected %d transit episodes", len(transits))
    except Exception as exc:
        log.warning("transit detection failed, skipping: %s", exc, exc_info=True)
        transits = []

    if transits:
        for t in transits:
            conn.execute(
                "INSERT OR REPLACE INTO transit_events "
                "(mmsi, chokepoint, entered_ts, exited_ts, direction, kind, segment, laden) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    t["mmsi"],
                    t["chokepoint"],
                    t["entered_ts"],
                    t["exited_ts"],
                    t["direction"],
                    t["kind"],
                    t["segment"],
                    t["laden"],
                ],
            )

    # ------------------------------------------------------------------
    # 2. Anchored episode detection
    # ------------------------------------------------------------------
    try:
        anchored = anchored_episodes(df)
        log.info("detected %d anchored episodes", len(anchored))
    except Exception as exc:
        log.warning("anchored detection failed, skipping: %s", exc, exc_info=True)
        anchored = []

    if anchored:
        for a in anchored:
            conn.execute(
                "INSERT OR REPLACE INTO anchored_episodes "
                "(mmsi, zone, start_ts, end_ts, kind, segment) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [a["mmsi"], a["zone"], a["start_ts"], a["end_ts"], a["kind"], a["segment"]],
            )

    # ------------------------------------------------------------------
    # 3. Vessel state update (max_draught_seen, laden/ballast)
    # ------------------------------------------------------------------
    from .detect import laden_status

    vessel_states = _load_vessel_states(conn)

    if "draught" in df.columns:
        draught_df = df[df["draught"].notna() & (df["draught"] > 0)].copy()
        if not draught_df.empty:
            for mmsi, grp in draught_df.groupby("mmsi"):
                mmsi_int = int(mmsi)
                old = vessel_states.get(mmsi_int, {})
                new_max = float(grp["draught"].max())
                old_max = old.get("max_draught_seen")
                max_seen = max(new_max, old_max) if old_max else new_max
                last_d = float(grp.sort_values("snapshot_ts")["draught"].iloc[-1])
                seg = grp["segment"].iloc[-1] if "segment" in grp.columns else None
                laden = laden_status(last_d, max_seen, str(seg) if seg else None)
                now = datetime.now(UTC).replace(tzinfo=None)
                conn.execute(
                    "INSERT OR REPLACE INTO vessel_state "
                    "(mmsi, max_draught_seen, last_draught, laden, updated_ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [mmsi_int, max_seen, last_d, laden, now],
                )
                vessel_states[mmsi_int] = {
                    "max_draught_seen": max_seen,
                    "last_draught": last_d,
                    "laden": laden,
                }

    # ------------------------------------------------------------------
    # 4. Fleet density (one row per unique snapshot_ts bucket x region x kind x segment)
    # ------------------------------------------------------------------
    # Round snapshot_ts to the nearest hour for density aggregation
    df["_hour"] = df["snapshot_ts"].dt.floor("h")
    density_rows: list[dict] = []
    try:
        for hour_ts, hour_grp in df.groupby("_hour"):
            rows = fleet_density_rows(hour_grp, hour_ts, vessel_states)
            density_rows.extend(rows)
        log.info("computed %d fleet_density rows", len(density_rows))
    except Exception as exc:
        log.warning("fleet density computation failed, skipping: %s", exc, exc_info=True)
        density_rows = []

    if density_rows:
        for r in density_rows:
            conn.execute(
                "INSERT OR REPLACE INTO fleet_density "
                "(ts, region, kind, segment, laden_count, ballast_count, unknown_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    r["ts"],
                    r["region"],
                    r["kind"],
                    r["segment"],
                    r["laden_count"],
                    r["ballast_count"],
                    r["unknown_count"],
                ],
            )

    # ------------------------------------------------------------------
    # 5. Destination changes - detect reroutes in the incremental window
    # ------------------------------------------------------------------
    try:
        reroutes = destination_change_events(df)
        log.info("detected %d destination-change (reroute) events", len(reroutes))
    except Exception as exc:
        log.warning("destination change detection failed, skipping: %s", exc, exc_info=True)
        reroutes = []
    _insert_events(conn, reroutes)

    # ------------------------------------------------------------------
    # 6. Intelligence events (gaps, loitering, STS) - 48h lookback
    # ------------------------------------------------------------------
    max_ts_dt = max_ts.to_pydatetime() if hasattr(max_ts, "to_pydatetime") else max_ts
    lookback_since = max_ts_dt - timedelta(hours=48)

    # Clear re-detected types covering the lookback window before reinserting.
    # STS, loitering, and spoof are re-detected from scratch on every run; their
    # event_id is derived from start_ts which shifts as the sliding window advances,
    # so without this clear step each build creates new duplicates for ongoing events.
    # Gaps use a different mechanism (stable on last-seen-fix) and are NOT cleared.
    for _t in ("sts", "loiter", "spoof"):
        conn.execute(
            "DELETE FROM ais_events WHERE type = ? AND start_ts >= ?",
            [_t, lookback_since],
        )

    df_48h = _ais_query(
        "SELECT mmsi, snapshot_ts, lat, lon, sog, nav_status, draught, destination, "
        "       kind, segment, region "
        "FROM ais_snapshots "
        "WHERE snapshot_ts >= ? "
        "ORDER BY mmsi, snapshot_ts",
        [lookback_since],
    )

    spoof_events: list[dict] = []
    if not df_48h.empty:
        df_48h["snapshot_ts"] = pd.to_datetime(df_48h["snapshot_ts"])

        # 5a. AIS gaps
        try:
            gaps = ais_gap_events(df_48h, max_ts)
            log.info("detected %d gap events", len(gaps))
        except Exception as exc:
            log.warning("gap detection failed, skipping: %s", exc, exc_info=True)
            gaps = []
        if gaps:
            # Close any gap events for vessels that have reappeared
            active_recent = set(
                df_48h[df_48h["snapshot_ts"] >= max_ts - timedelta(hours=_GAP_RECHECK_H)]["mmsi"]
                .astype(int)
                .unique()
                .tolist()
            )
            for mmsi_int in active_recent:
                row = conn.execute(
                    "SELECT event_id, details FROM ais_events "
                    "WHERE type = 'gap' AND mmsi = ? AND end_ts = start_ts",
                    [mmsi_int],
                ).fetchone()
                if row:
                    import json as _json

                    event_id_existing, details_str = row
                    details = _json.loads(details_str) if details_str else {}
                    grp_m = df_48h[df_48h["mmsi"] == mmsi_int].sort_values("snapshot_ts")
                    # Find first fix after gap start
                    gap_start = conn.execute(
                        "SELECT start_ts FROM ais_events WHERE event_id = ?", [event_id_existing]
                    ).fetchone()
                    if gap_start:
                        after = grp_m[grp_m["snapshot_ts"] > gap_start[0]]
                        if not after.empty:
                            refix = after.iloc[0]
                            details["reappeared_lat"] = round(float(refix["lat"]), 5)
                            details["reappeared_lon"] = round(float(refix["lon"]), 5)
                            conn.execute(
                                "UPDATE ais_events SET end_ts = ?, details = ? WHERE event_id = ?",
                                [refix["snapshot_ts"], _json.dumps(details), event_id_existing],
                            )
            _insert_events(conn, gaps)

        # 5b. Loitering
        try:
            loiters = loitering_events(df_48h)
            log.info("detected %d loitering events", len(loiters))
        except Exception as exc:
            log.warning("loitering detection failed, skipping: %s", exc, exc_info=True)
            loiters = []
        _insert_events(conn, loiters)

        # 5c. STS candidates
        try:
            sts = sts_candidates(df_48h)
            log.info("detected %d STS candidates", len(sts))
        except Exception as exc:
            log.warning("STS detection failed, skipping: %s", exc, exc_info=True)
            sts = []
        _insert_events(conn, sts)

        # 5d. GPS spoofing / position jump anomalies
        try:
            spoof_events = gps_spoof_events(df_48h)
            log.info("detected %d GPS position-jump events", len(spoof_events))
        except Exception as exc:
            log.warning("GPS spoof detection failed, skipping: %s", exc, exc_info=True)
            spoof_events = []
        _insert_events(conn, spoof_events)

    # ------------------------------------------------------------------
    # 7. Dark voyage composite detection (operates on ais_events, not raw snapshots)
    # ------------------------------------------------------------------
    try:
        all_events_df = conn.execute(
            "SELECT event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon, region, kind, segment, details "
            "FROM ais_events"
        ).df()
        dark_voyages = dark_voyage_events(all_events_df)
        log.info("detected %d dark voyage composites", len(dark_voyages))
    except Exception as exc:
        log.warning("dark voyage detection failed, skipping: %s", exc, exc_info=True)
        dark_voyages = []
    _insert_events(conn, dark_voyages)

    if skip_derived:
        log.info("--skip-derived: skipping stages 7b-7e (ETA + destination)")
    else:
        _run_derived_stages(conn)

    # ------------------------------------------------------------------
    # 8. Advance watermark and promote scratch to live
    # ------------------------------------------------------------------
    new_watermark = max_ts_dt
    _set_watermark(conn, new_watermark)
    log.info("watermark advanced to %s", new_watermark)

    # Log the scratch file size before closing so we can diagnose "missing" cases.
    try:
        sz_mb = _ANALYTICS_NEW.stat().st_size / (1024 * 1024)
        log.info("closing scratch DB (%.1f MB); final checkpoint will write WAL to disk", sz_mb)
    except OSError:
        log.warning("scratch file already missing before conn.close() - this run will not promote")

    conn.close()
    log.info("conn.close() returned; scratch WAL should be flushed")

    # Atomic swap: live DB was never locked during the build.
    _commit_scratch()

    log.info(
        "analytics.build complete: transits=%d anchored=%d density=%d reroutes=%d dark_voyages=%d spoof=%d",
        len(transits),
        len(anchored),
        len(density_rows),
        len(reroutes),
        len(dark_voyages),
        len(spoof_events),
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Freight analytics batch job")
    parser.add_argument(
        "--reset", action="store_true", help="Clear watermark and reprocess all history"
    )
    parser.add_argument(
        "--max-window-hours",
        type=float,
        default=None,
        help=(
            "Cap the snapshot window at N hours instead of reading everything since the "
            f"watermark. Must exceed the {_OVERLAP_HOURS}h overlap. Use to walk a large "
            "backlog forward in bounded passes - an unbounded multi-day catch-up loads "
            "one DataFrame big enough to OOM the job."
        ),
    )
    parser.add_argument(
        "--skip-derived",
        action="store_true",
        help=(
            "Skip stages 7b-7e (ETA + destination). They read the full AIS history and "
            "only their final state matters, so intermediate catch-up passes can skip them."
        ),
    )
    args = parser.parse_args()
    run(
        reset=args.reset,
        max_window_hours=args.max_window_hours,
        skip_derived=args.skip_derived,
    )
