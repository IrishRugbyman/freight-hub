"""Read-only access to DuckDB files used by the freight API.

Two DBs in use:
- ais_positions.duckdb  : owned by the AIS collector (market-data service). We open
  read-only and retry past the rare per-write lock (the writer holds it < 1s every ~90s).
- freight_analytics.duckdb : owned by the analytics batch job (analytics/build.py). Also
  opened read-only here; the batch job is the sole writer.

Both paths are env-overridable (AIS_POSITIONS_DB, ANALYTICS_DB), which is how tests
inject temporary DBs without touching the live files.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb

_DEFAULT_AIS_DB = "~/quant/shared/market-data/data/ais_positions.duckdb"
_DEFAULT_ANALYTICS_DB = Path(__file__).resolve().parents[1] / "data" / "freight_analytics.duckdb"

# Vessels not refreshed within this many hours are considered gone.
STALE_HOURS = float(os.environ.get("FREIGHT_STALE_HOURS", "3"))


def db_path() -> Path:
    """Path to the collector's AIS DuckDB (overridable via AIS_POSITIONS_DB)."""
    return Path(os.environ.get("AIS_POSITIONS_DB", _DEFAULT_AIS_DB)).expanduser()


def analytics_db_path() -> Path:
    """Path to the analytics DuckDB (overridable via ANALYTICS_DB)."""
    return Path(os.environ.get("ANALYTICS_DB", str(_DEFAULT_ANALYTICS_DB)))


def query(sql: str, params: list | None = None, retries: int = 10, db: Path | None = None):
    """Run a read-only query against the specified DB, returning a DataFrame.

    Empty DataFrame if the DB or table is missing. Retries past the brief lock that
    the collector (or analytics job) holds during each write cycle.

    Args:
        sql: SQL to execute.
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
