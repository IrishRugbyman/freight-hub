"""MyShipTracking crawler.

Single writer of backend/data/mst.duckdb. Reads ais_positions.duckdb read-only to
discover candidate MMSIs from the live fleet, then scrapes myshiptracking at a polite
rate and persists the durable, immutable data so we never re-scrape it:

  mst_voyages      append-only completed trips, PK (mmsi, vkey) -> INSERT OR IGNORE
  mst_port_calls   append-only port calls,      PK (mmsi, pkey) -> INSERT OR IGNORE
  mst_vessel_state latest live snapshot + particulars, upsert by mmsi

A completed voyage / port call never changes, so once stored it is permanent: the only
reason to re-scrape a vessel is to pick up *new* trips since the last visit. State
(destination/ETA/draught/position) is volatile and simply overwritten each visit.

Run:
    cd backend
    python -m registry.crawl_mst [--limit N] [--dry-run]

Priority order per run:
  1. MMSIs currently live that have never been scraped
  2. Rows whose state is older than --refresh-days (default 7)
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd
from app.db import query as _db_query
from app.myshiptracking import MyShipTrackingBlocked, VesselSnapshot, get_vessel

logger = logging.getLogger(__name__)

_MST_DB = Path(__file__).resolve().parents[1] / "data" / "mst.duckdb"
_DEFAULT_AIS_DB = Path(
    os.environ.get("AIS_POSITIONS_DB", "~/quant/shared/market-data/data/ais_positions.duckdb")
).expanduser()

# Polite, IP-rate-limited scraping. Keep the cap modest and the sleep generous - same
# discipline as the Equasis crawler. Never raise these to "improve coverage".
_MAX_PER_RUN = 80
_SLEEP_MIN = 4.0
_SLEEP_MAX = 8.0
_REFRESH_DAYS = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mst_voyages (
    mmsi BIGINT, vkey VARCHAR,
    origin VARCHAR, departure VARCHAR, destination VARCHAR, arrival VARCHAR,
    distance_nm DOUBLE, duration VARCHAR, draught_m DOUBLE,
    avg_speed_kn DOUBLE, max_speed_kn DOUBLE, stops INTEGER,
    first_seen_ts TIMESTAMP,
    PRIMARY KEY (mmsi, vkey)
);
CREATE TABLE IF NOT EXISTS mst_port_calls (
    mmsi BIGINT, pkey VARCHAR,
    port VARCHAR, arrival VARCHAR, departure VARCHAR,
    first_seen_ts TIMESTAMP,
    PRIMARY KEY (mmsi, pkey)
);
CREATE TABLE IF NOT EXISTS mst_vessel_state (
    mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR,
    flag VARCHAR, call_sign VARCHAR, ship_type VARCHAR,
    length_m DOUBLE, beam_m DOUBLE, gross_tonnage INTEGER, dwt INTEGER, year_built INTEGER,
    lat DOUBLE, lon DOUBLE, status VARCHAR, course DOUBLE, area VARCHAR, station VARCHAR,
    draught_m DOUBLE, destination VARCHAR, eta VARCHAR, position_received_utc VARCHAR,
    fetched_ts TIMESTAMP, fetch_ok BOOLEAN
);
"""


def priority_order(
    live_mmsis: list[int], state_df: pd.DataFrame, now: datetime, limit: int
) -> list[int]:
    """Up to `limit` MMSIs: never-scraped first, then state older than refresh window."""
    if state_df.empty:
        return live_mmsis[:limit]

    seen: dict[int, datetime] = {}
    for row in state_df.itertuples(index=False):
        ts = pd.Timestamp(row.fetched_ts).to_pydatetime()
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        seen[int(row.mmsi)] = ts

    never = [m for m in live_mmsis if m not in seen]
    stale = [
        m for m in live_mmsis
        if m in seen and (now - seen[m]).total_seconds() > _REFRESH_DAYS * 86400
    ]
    return (never + stale)[:limit]


def _persist(conn: duckdb.DuckDBPyConnection, snap: VesselSnapshot, now: datetime) -> tuple[int, int]:
    """Append new immutable voyages/port-calls and upsert state. Returns (new_voyages, new_calls)."""
    mmsi = snap.mmsi
    nv = nc = 0

    for v in snap.voyages:
        before = conn.execute(
            "SELECT count(*) FROM mst_voyages WHERE mmsi = ? AND vkey = ?", [mmsi, v.key()]
        ).fetchone()[0]
        if before:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO mst_voyages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [mmsi, v.key(), v.origin, v.departure, v.destination, v.arrival,
             v.distance_nm, v.duration, v.draught_m, v.avg_speed_kn, v.max_speed_kn,
             v.stops, now],
        )
        nv += 1

    for p in snap.port_calls:
        pkey = f"{p.port}|{p.arrival}"
        before = conn.execute(
            "SELECT count(*) FROM mst_port_calls WHERE mmsi = ? AND pkey = ?", [mmsi, pkey]
        ).fetchone()[0]
        if before:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO mst_port_calls VALUES (?,?,?,?,?,?)",
            [mmsi, pkey, p.port, p.arrival, p.departure, now],
        )
        nc += 1

    conn.execute(
        "INSERT OR REPLACE INTO mst_vessel_state VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [mmsi, snap.imo, snap.name, snap.flag, snap.call_sign, snap.ship_type,
         snap.length_m, snap.beam_m, snap.gross_tonnage, snap.dwt, snap.year_built,
         snap.lat, snap.lon, snap.status, snap.course, snap.area, snap.station,
         snap.draught_m, snap.destination, snap.eta, snap.position_received_utc,
         now, True],
    )
    return nv, nc


def run(
    ais_path: Path | None = None,
    mst_path: Path | None = None,
    limit: int = _MAX_PER_RUN,
    dry_run: bool = False,
) -> None:
    ais_path = ais_path or _DEFAULT_AIS_DB
    mst_path = mst_path or _MST_DB

    # Ship MMSIs are 9 digits with a leading MID digit 2-7. Anything else (base
    # stations, AtoN, SAR aircraft, coast stations) has no vessel page - filtering
    # here avoids wasting polite-rate requests on guaranteed misses.
    live_df = _db_query(
        "SELECT DISTINCT CAST(mmsi AS BIGINT) AS mmsi FROM live_positions "
        "WHERE mmsi >= 200000000 AND mmsi <= 799999999",
        db=ais_path,
        retries=200,
    )
    if live_df.empty:
        logger.info("No MMSIs in live_positions (DB locked or empty)")
        return
    live_mmsis = sorted(int(m) for m in live_df["mmsi"].tolist())
    logger.info("Found %d distinct live MMSIs", len(live_mmsis))

    mst_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(mst_path))
    conn.execute(_SCHEMA)

    try:
        state_df = conn.execute("SELECT mmsi, fetched_ts FROM mst_vessel_state").df()
    except Exception:
        state_df = pd.DataFrame()

    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = priority_order(live_mmsis, state_df, now, limit)
    logger.info("Crawl plan: %d candidates (limit %d), dry_run=%s", len(candidates), limit, dry_run)

    n_ok = n_skip = tot_v = tot_c = 0
    blocked = False
    for mmsi in candidates:
        if dry_run:
            logger.info("DRY RUN: would scrape mmsi %d", mmsi)
            continue
        try:
            snap = get_vessel(mmsi, use_cache=False)
        except MyShipTrackingBlocked:
            logger.error(
                "myshiptracking BLOCKED - aborting after %d ok / %d skipped", n_ok, n_skip
            )
            blocked = True
            break
        except Exception as exc:
            logger.warning("mmsi=%d scrape failed: %s", mmsi, exc)
            n_skip += 1
            time.sleep(random.uniform(_SLEEP_MIN, _SLEEP_MAX))
            continue

        if snap is None:
            n_skip += 1
        else:
            nv, nc = _persist(conn, snap, now)
            tot_v += nv
            tot_c += nc
            n_ok += 1

        time.sleep(random.uniform(_SLEEP_MIN, _SLEEP_MAX))

    conn.close()
    status = "ABORTED (blocked)" if blocked else "complete"
    logger.info(
        "MST crawl %s: %d ok, %d skipped, +%d voyages, +%d port calls (of %d candidates)",
        status, n_ok, n_skip, tot_v, tot_c, len(candidates),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Scrape myshiptracking voyage history for the live fleet")
    ap.add_argument("--limit", type=int, default=_MAX_PER_RUN, help=f"Max vessels per run (default {_MAX_PER_RUN})")
    ap.add_argument("--dry-run", action="store_true", help="Print candidates without scraping")
    args = ap.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
