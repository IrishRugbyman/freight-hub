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

# Retry budget when the collector holds the AIS DB lock (usually < 1s per write)
_LOCK_RETRIES = 200  # 200 * 0.3s = 60s; collector holds write lock between upserts

# Window within max_ts where a vessel is considered "recently active" for gap closure
_GAP_RECHECK_H = 6

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
            conn = duckdb.connect(str(AIS_DB), read_only=True)
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


# ---------------------------------------------------------------------------
# Analytics DB helpers
# ---------------------------------------------------------------------------


_ANALYTICS_NEW = ANALYTICS_DB.with_suffix(".new.duckdb")


def _open_analytics_scratch() -> duckdb.DuckDBPyConnection:
    """Open a scratch DB for this build run.

    Copies the current live DB (so historical data is preserved), then opens
    the scratch file exclusively. The live DB is never locked during the build;
    only at the very end do we atomically rename scratch -> live.
    """
    ANALYTICS_DB.parent.mkdir(parents=True, exist_ok=True)

    # Remove any leftover scratch from a prior crashed run.
    if _ANALYTICS_NEW.exists():
        _ANALYTICS_NEW.unlink()

    # Seed scratch with all existing historical data so INSERT OR REPLACE
    # only needs to add/update incremental rows.
    if ANALYTICS_DB.exists():
        shutil.copy2(ANALYTICS_DB, _ANALYTICS_NEW)

    conn = duckdb.connect(str(_ANALYTICS_NEW))
    conn.execute(_SCHEMA)
    return conn


def _commit_scratch() -> None:
    """Atomically replace the live analytics DB with the completed scratch file."""
    if not _ANALYTICS_NEW.exists():
        log.warning("scratch file missing at commit time; nothing to promote")
        return
    # On Linux, os.replace is POSIX rename(2) - atomic within the same filesystem.
    os.replace(_ANALYTICS_NEW, ANALYTICS_DB)
    log.info("scratch promoted to live: %s", ANALYTICS_DB)


def _get_watermark(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    row = conn.execute(
        "SELECT ts FROM meta_watermark WHERE key = 'snapshots'"
    ).fetchone()
    return row[0] if row else None


def _set_watermark(conn: duckdb.DuckDBPyConnection, ts: datetime) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta_watermark (key, ts) VALUES ('snapshots', ?)", [ts]
    )


def _load_vessel_states(conn: duckdb.DuckDBPyConnection) -> dict:
    df = conn.execute(
        "SELECT mmsi, max_draught_seen, last_draught, laden FROM vessel_state"
    ).df()
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


def run(reset: bool = False) -> None:
    log.info("analytics.build starting (AIS=%s, analytics=%s)", AIS_DB, ANALYTICS_DB)

    conn = _open_analytics_scratch()
    try:
        _run_inner(conn, reset)
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
        [e["event_id"], e["type"], e["mmsi"], e["mmsi2"],
         e["start_ts"], e["end_ts"], e["lat"], e["lon"],
         e["region"], e["kind"], e["segment"], e["details"]]
        for e in events
    ]
    conn.executemany(_AIS_EVENT_SQL, rows)


def _run_inner(conn: duckdb.DuckDBPyConnection, reset: bool) -> None:

    if reset:
        log.info("--reset: clearing watermark")
        conn.execute("DELETE FROM meta_watermark WHERE key = 'snapshots'")

    watermark = _get_watermark(conn)
    if watermark:
        since = watermark - timedelta(hours=_OVERLAP_HOURS)
        log.info("watermark %s, reading since %s", watermark, since)
    else:
        # First run: process all available history
        since = datetime(2026, 1, 1)
        log.info("no watermark found; reading all history since %s", since)

    # ------------------------------------------------------------------
    # Load snapshots from AIS DB
    # ------------------------------------------------------------------
    df = _ais_query(
        "SELECT mmsi, snapshot_ts, lat, lon, sog, nav_status, draught, destination, "
        "       kind, segment, region "
        "FROM ais_snapshots "
        "WHERE snapshot_ts >= ? "
        "ORDER BY mmsi, snapshot_ts",
        [since],
    )

    if df.empty:
        log.info("no new snapshots; nothing to process")
        conn.close()
        _commit_scratch()
        return

    # Coerce timestamps to datetime
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])

    max_ts = df["snapshot_ts"].max()
    log.info("loaded %d snapshot rows (max_ts=%s)", len(df), max_ts)

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
                    t["mmsi"], t["chokepoint"], t["entered_ts"], t["exited_ts"],
                    t["direction"], t["kind"], t["segment"], t["laden"],
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
                vessel_states[mmsi_int] = {"max_draught_seen": max_seen, "last_draught": last_d, "laden": laden}

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
                [r["ts"], r["region"], r["kind"], r["segment"],
                 r["laden_count"], r["ballast_count"], r["unknown_count"]],
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
                df_48h[df_48h["snapshot_ts"] >= max_ts - timedelta(hours=_GAP_RECHECK_H)]["mmsi"].astype(int).unique().tolist()
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

    # ------------------------------------------------------------------
    # 8. Advance watermark and promote scratch to live
    # ------------------------------------------------------------------
    new_watermark = max_ts_dt
    _set_watermark(conn, new_watermark)
    log.info("watermark advanced to %s", new_watermark)

    conn.close()

    # Atomic swap: live DB was never locked during the build.
    _commit_scratch()

    log.info(
        "analytics.build complete: transits=%d anchored=%d density=%d reroutes=%d dark_voyages=%d spoof=%d",
        len(transits), len(anchored), len(density_rows), len(reroutes), len(dark_voyages), len(spoof_events),
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
    parser.add_argument("--reset", action="store_true", help="Clear watermark and reprocess all history")
    args = parser.parse_args()
    run(reset=args.reset)
