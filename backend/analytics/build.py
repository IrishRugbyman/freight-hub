"""Hourly analytics job for the freight hub.

Reads ais_positions.duckdb (read-only, lock-retry) since the watermark,
detects transit, anchored, and fleet-density events, and writes results to
freight_analytics.duckdb (sole writer for that file).

Usage:
    python -m analytics.build              # normal incremental run
    python -m analytics.build --reset      # wipe watermark and re-process all history

The job is idempotent: all writes use INSERT OR REPLACE, so re-runs are safe.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from .detect import anchored_episodes, fleet_density_rows, transit_episodes

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
_LOCK_RETRIES = 15

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


def _open_analytics() -> duckdb.DuckDBPyConnection:
    ANALYTICS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(ANALYTICS_DB))
    conn.execute(_SCHEMA)
    return conn


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

    conn = _open_analytics()

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
        return

    # Coerce timestamps to datetime
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])

    max_ts = df["snapshot_ts"].max()
    log.info("loaded %d snapshot rows (max_ts=%s)", len(df), max_ts)

    # ------------------------------------------------------------------
    # 1. Transit detection
    # ------------------------------------------------------------------
    transits = transit_episodes(df)
    log.info("detected %d transit episodes", len(transits))

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
    anchored = anchored_episodes(df)
    log.info("detected %d anchored episodes", len(anchored))

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

    for hour_ts, hour_grp in df.groupby("_hour"):
        rows = fleet_density_rows(hour_grp, hour_ts, vessel_states)
        density_rows.extend(rows)

    log.info("computed %d fleet_density rows", len(density_rows))

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
    # 5. Advance watermark
    # ------------------------------------------------------------------
    new_watermark = max_ts.to_pydatetime() if hasattr(max_ts, "to_pydatetime") else max_ts
    _set_watermark(conn, new_watermark)
    log.info("watermark advanced to %s", new_watermark)

    conn.close()
    log.info("analytics.build complete")


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
