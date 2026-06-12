"""Equasis registry crawler.

Single writer of backend/data/vessel_registry.duckdb. Reads ais_positions.duckdb
read-only to discover candidate IMOs, then scrapes Equasis at a polite rate.

Run:
    cd backend
    python -m registry.crawl [--limit N] [--dry-run]

Priority order per run:
  1. IMOs currently live that have never been fetched
  2. Rows with fetch_ok=false older than 7 days
  3. Rows older than 30 days (stale refresh)
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import json

import duckdb
import pandas as pd

from app.db import analytics_db_path as _analytics_db_path
from app.db import query as _db_query
from app.equasis import get_ship_info
from registry.risk import risk_score as _risk_score

logger = logging.getLogger(__name__)

_REGISTRY_DB = Path(__file__).resolve().parents[1] / "data" / "vessel_registry.duckdb"
_DEFAULT_AIS_DB = Path(os.environ.get(
    "AIS_POSITIONS_DB",
    "~/quant/shared/market-data/data/ais_positions.duckdb"
)).expanduser()

_MAX_PER_RUN = 200
_SLEEP_MIN = 6.0
_SLEEP_MAX = 10.0
_RETRY_FAILED_DAYS = 7
_REFRESH_STALE_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vessel_registry (
    imo BIGINT PRIMARY KEY,
    ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR, call_sign VARCHAR,
    gross_tonnage INTEGER, dwt INTEGER,
    ship_type VARCHAR, year_built INTEGER, ship_status VARCHAR,
    owner VARCHAR, ism_manager VARCHAR, ship_manager VARCHAR,
    class_society VARCHAR, pi_club VARCHAR,
    detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR, uscg_targeting VARCHAR,
    fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
    risk_score INTEGER, risk_indicators VARCHAR,
    ofac_sanctioned BOOLEAN
)
"""

_MIGRATIONS = [
    "ALTER TABLE vessel_registry ADD COLUMN IF NOT EXISTS risk_score INTEGER",
    "ALTER TABLE vessel_registry ADD COLUMN IF NOT EXISTS risk_indicators VARCHAR",
    "ALTER TABLE vessel_registry ADD COLUMN IF NOT EXISTS ofac_sanctioned BOOLEAN",
]


def _to_int(val) -> int | None:
    """Safe cast to int, returning None on failure or None input."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def priority_order(
    live_imos: set[int],
    reg_df: pd.DataFrame,
    now: datetime,
    limit: int,
) -> list[int]:
    """Return up to limit IMOs in priority order (pure, no I/O).

    reg_df must have columns: imo (int), fetch_ok (bool), fetched_ts (datetime).
    """
    if reg_df.empty:
        return list(live_imos)[:limit]

    reg_by_imo: dict[int, tuple[bool, datetime]] = {}
    for row in reg_df.itertuples(index=False):
        imo = int(row.imo)
        ts = pd.Timestamp(row.fetched_ts).to_pydatetime()
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        reg_by_imo[imo] = (bool(row.fetch_ok), ts)

    never_fetched = [imo for imo in live_imos if imo not in reg_by_imo]

    retry_failed = [
        imo for imo in live_imos
        if imo in reg_by_imo
        and not reg_by_imo[imo][0]
        and (now - reg_by_imo[imo][1]).total_seconds() > _RETRY_FAILED_DAYS * 86400
    ]

    stale = [
        imo for imo in live_imos
        if imo in reg_by_imo
        and reg_by_imo[imo][0]
        and (now - reg_by_imo[imo][1]).total_seconds() > _REFRESH_STALE_DAYS * 86400
    ]

    candidates = never_fetched + retry_failed + stale
    return candidates[:limit]


def run(
    ais_path: Path | None = None,
    reg_path: Path | None = None,
    limit: int = _MAX_PER_RUN,
    dry_run: bool = False,
) -> None:
    ais_path = ais_path or _DEFAULT_AIS_DB
    reg_path = reg_path or _REGISTRY_DB

    # Discover candidate IMOs from the live fleet (read-only, with lock-retry via db.query)
    # IMO numbers are 7 digits: 1000000-9999999
    live_df = _db_query(
        "SELECT DISTINCT CAST(imo AS BIGINT) AS imo "
        "FROM live_positions WHERE imo >= 1000000 AND imo <= 9999999",
        db=ais_path,
    )

    if live_df.empty:
        logger.info("No IMOs found in live_positions (DB locked or no data)")
        return

    live_imos: set[int] = set(live_df["imo"].astype(int).tolist())
    logger.info("Found %d distinct IMOs in live fleet", len(live_imos))

    # Open (or create) the registry DB - we are the sole writer
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_conn = duckdb.connect(str(reg_path))
    reg_conn.execute(_SCHEMA)
    for mig in _MIGRATIONS:
        try:
            reg_conn.execute(mig)
        except Exception:
            pass  # column already exists

    try:
        reg_df = reg_conn.execute(
            "SELECT imo, fetch_ok, fetched_ts, owner FROM vessel_registry"
        ).df()
    except Exception:
        reg_df = pd.DataFrame()

    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = priority_order(live_imos, reg_df, now, limit)
    logger.info(
        "Crawl plan: %d candidates (limit %d), dry_run=%s",
        len(candidates), limit, dry_run,
    )

    # Build single-vessel owner set from current registry state (approximation:
    # vessels fetched in this run are not yet counted, but score is refreshed next run)
    if not reg_df.empty and "owner" in reg_df.columns:
        owner_vc = reg_df.dropna(subset=["owner"])["owner"].value_counts()
        single_ship_owners: set[str] = set(owner_vc[owner_vc == 1].index.tolist())
    else:
        single_ship_owners = set()

    # Load 90-day AIS event counts per IMO from the analytics DB (graceful fallback)
    # Events are keyed by MMSI; we join via the MMSI->IMO map from live_positions.
    event_counts_by_imo: dict[int, dict[str, int]] = {}
    try:
        mmsi_imo_df = _db_query(
            "SELECT CAST(mmsi AS BIGINT) AS mmsi, CAST(imo AS BIGINT) AS imo "
            "FROM live_positions WHERE imo >= 1000000 AND imo <= 9999999",
            db=ais_path,
        )
        mmsi_to_imo: dict[int, int] = (
            dict(zip(mmsi_imo_df["mmsi"].astype(int), mmsi_imo_df["imo"].astype(int)))
            if not mmsi_imo_df.empty else {}
        )
        cutoff_90 = now - timedelta(days=90)
        events_df = _db_query(
            "SELECT mmsi, type, COUNT(*) AS n FROM ais_events "
            "WHERE start_ts >= ? GROUP BY mmsi, type",
            [cutoff_90],
            db=_analytics_db_path(),
        )
        for ev_row in events_df.itertuples(index=False):
            imo = mmsi_to_imo.get(int(ev_row.mmsi))
            if imo:
                event_counts_by_imo.setdefault(imo, {})[str(ev_row.type)] = int(ev_row.n)
    except Exception as exc:
        logger.debug("Could not load event counts: %s", exc)

    # Fetch OFAC SDN sanctioned vessel IMOs (non-fatal if network is unavailable)
    from .ofac import fetch_sanctioned_imos
    sanctioned_imos: set[int] = fetch_sanctioned_imos()
    logger.info("OFAC: %d sanctioned vessel IMOs loaded", len(sanctioned_imos))

    # Update ofac_sanctioned flag for all vessels already in the registry
    if sanctioned_imos and not reg_df.empty:
        for imo_val in reg_df["imo"].astype(int).tolist():
            flag_val = imo_val in sanctioned_imos
            reg_conn.execute(
                "UPDATE vessel_registry SET ofac_sanctioned = ? WHERE imo = ?",
                [flag_val, imo_val],
            )

    n_new = n_refreshed = n_failed = 0
    existing_imos = set(reg_df["imo"].astype(int).tolist()) if not reg_df.empty else set()

    for imo in candidates:
        if dry_run:
            logger.info("DRY RUN: would fetch IMO %d", imo)
            continue

        data = get_ship_info(imo)
        # Require at least one meaningful field beyond imo/ship_name - Equasis error
        # pages can parse a "We're sorry..." ship_name but have no registry data
        _meaningful = {"flag", "owner", "class_society", "ship_type", "ism_manager"}
        fetch_ok = data is not None and bool(_meaningful & data.keys())

        if fetch_ok:
            is_sanctioned = imo in sanctioned_imos
            score, indicators = _risk_score(
                imo=imo,
                ship_type=data.get("ship_type"),
                year_built=_to_int(data.get("year_built")),
                pi_club=data.get("pi_club"),
                class_society=data.get("class_society"),
                paris_mou=data.get("paris_mou"),
                tokyo_mou=data.get("tokyo_mou"),
                detention_rate_pct=data.get("detention_rate_pct"),
                event_counts=event_counts_by_imo.get(imo, {}),
                owner=data.get("owner"),
                single_ship_owner=bool(data.get("owner") and data["owner"] in single_ship_owners),
                ofac_sanctioned=is_sanctioned,
            )
            _upsert(reg_conn, imo, data, now, score, json.dumps(indicators), is_sanctioned)
            if imo in existing_imos:
                n_refreshed += 1
            else:
                n_new += 1
        else:
            _upsert_failed(reg_conn, imo, now)
            n_failed += 1

        sleep_s = random.uniform(_SLEEP_MIN, _SLEEP_MAX)
        time.sleep(sleep_s)

    if not dry_run:
        logger.info(
            "Crawl complete: %d new, %d refreshed, %d failed (of %d candidates)",
            n_new, n_refreshed, n_failed, len(candidates),
        )

    reg_conn.close()


def _upsert(
    conn: duckdb.DuckDBPyConnection,
    imo: int,
    data: dict,
    now: datetime,
    risk_score_val: int | None = None,
    risk_indicators_json: str | None = None,
    ofac_sanctioned: bool | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO vessel_registry (
            imo, ship_name, flag, flag_code, call_sign,
            gross_tonnage, dwt, ship_type, year_built, ship_status,
            owner, ism_manager, ship_manager, class_society, pi_club,
            detention_rate_pct, paris_mou, tokyo_mou, uscg_targeting,
            fetched_ts, fetch_ok, risk_score, risk_indicators, ofac_sanctioned
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            imo,
            data.get("ship_name"),
            data.get("flag"),
            data.get("flag_code"),
            data.get("call_sign"),
            _to_int(data.get("gross_tonnage")),
            _to_int(data.get("dwt")),
            data.get("ship_type"),
            _to_int(data.get("year_built")),
            data.get("ship_status"),
            data.get("owner"),
            data.get("ism_manager"),
            data.get("ship_manager"),
            data.get("class_society"),
            data.get("pi_club"),
            data.get("detention_rate_pct"),
            data.get("paris_mou"),
            data.get("tokyo_mou"),
            data.get("uscg_targeting"),
            now,
            True,
            risk_score_val,
            risk_indicators_json,
            ofac_sanctioned,
        ],
    )


def _upsert_failed(conn: duckdb.DuckDBPyConnection, imo: int, now: datetime) -> None:
    """Record a failed lookup so we don't retry every run."""
    conn.execute(
        """
        INSERT OR REPLACE INTO vessel_registry (imo, fetched_ts, fetch_ok)
        VALUES (?, ?, false)
        """,
        [imo, now],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch Equasis registry data for live fleet vessels")
    parser.add_argument("--limit", type=int, default=_MAX_PER_RUN,
                        help=f"Max vessels to fetch per run (default {_MAX_PER_RUN})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print candidates without fetching")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
