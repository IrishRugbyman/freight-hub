"""Read-only access to DuckDB files and PostgreSQL used by the freight API.

DuckDB files:
- ais_positions.duckdb    : owned by the AIS collector (market-data service). We open
  read-only and retry past the write lock (collector holds it several seconds per ~150s cycle).
- freight_analytics.duckdb: owned by the analytics batch job (analytics/build.py). Also
  opened read-only here; the batch job is the sole writer.

PostgreSQL (market_data):
- vessels table           : vessel master, written by the AIS collector and the Equasis
  crawler. pg_query() is the single read path for all vessel registry lookups.

DuckDB paths are env-overridable so tests can inject temp files without touching live data.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb

_DEFAULT_AIS_DB = "~/quant/shared/market-data/data/ais_positions.duckdb"
_DEFAULT_ANALYTICS_DB = Path(__file__).resolve().parents[1] / "data" / "freight_analytics.duckdb"
_DEFAULT_REGISTRY_DB = Path(__file__).resolve().parents[1] / "data" / "vessel_registry.duckdb"

_PG_DSN = os.environ.get("DATABASE_URL", "postgresql:///market_data")

# Vessels not refreshed within this many hours are considered gone.
STALE_HOURS = float(os.environ.get("FREIGHT_STALE_HOURS", "3"))


def db_path() -> Path:
    """Path to the collector's AIS DuckDB (overridable via AIS_POSITIONS_DB)."""
    return Path(os.environ.get("AIS_POSITIONS_DB", _DEFAULT_AIS_DB)).expanduser()


def analytics_db_path() -> Path:
    """Path to the analytics DuckDB (overridable via ANALYTICS_DB)."""
    return Path(os.environ.get("ANALYTICS_DB", str(_DEFAULT_ANALYTICS_DB)))


def registry_db_path() -> Path:
    """Path to the vessel registry DuckDB (overridable via REGISTRY_DB, kept for tests)."""
    return Path(os.environ.get("REGISTRY_DB", str(_DEFAULT_REGISTRY_DB)))


def query(sql: str, params: list | None = None, retries: int = 30, db: Path | None = None):
    """Run a read-only query against the specified DuckDB, returning a DataFrame.

    Empty DataFrame if the DB or table is missing. Retries past the brief lock that
    the collector (or analytics job) holds during each write cycle.

    Args:
        sql: SQL with DuckDB-style ? positional parameters.
        params: Positional parameters for the query.
        retries: Number of lock-retry attempts before giving up.
        db: Path to the DuckDB file. Defaults to the AIS positions DB.
    """
    import pandas as pd

    path = db if db is not None else db_path()
    if not path.exists():
        return pd.DataFrame()
    for attempt in range(retries):
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                return conn.execute(sql, params or []).df()
            finally:
                conn.close()
        except duckdb.CatalogException:
            return pd.DataFrame()  # table not created yet
        except duckdb.IOException:
            if attempt == retries - 1:
                return pd.DataFrame()
            time.sleep(0.3)
    return pd.DataFrame()


def pg_query(sql: str, params: list | None = None):
    """Run a read query against PostgreSQL market_data, returning a DataFrame.

    Uses %s positional parameters (psycopg2 style). For IN-list queries use
    = ANY(%s) with a Python list as the single parameter, e.g.:
        pg_query("SELECT ... WHERE imo = ANY(%s)", [[1, 2, 3]])

    Returns an empty DataFrame on any error (connection failure, missing table, etc.).
    """
    import pandas as pd
    import psycopg2

    try:
        conn = psycopg2.connect(_PG_DSN)
        try:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=cols)
        finally:
            conn.close()
    except Exception:
        return pd.DataFrame()
