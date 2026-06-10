"""Read-only access to the AIS collector's live_positions table.

The collector (market-data/ais/collector.py) owns ais_positions.duckdb and writes
to it every ~90s, connecting per-write so the file is unlocked between writes. We
open read-only and retry briefly past the rare mid-write lock, mirroring
market-data/fetchers/ais_dispersion.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb

_DEFAULT_DB = "~/quant/shared/market-data/data/ais_positions.duckdb"
# Vessels not refreshed within this many hours are considered gone.
STALE_HOURS = float(os.environ.get("FREIGHT_STALE_HOURS", "3"))


def db_path() -> Path:
    """Path to the collector's DuckDB (overridable via AIS_POSITIONS_DB, e.g. in tests)."""
    return Path(os.environ.get("AIS_POSITIONS_DB", _DEFAULT_DB)).expanduser()


def query(sql: str, params: list | None = None, retries: int = 10):
    """Run a read-only query, returning a DataFrame. Empty DataFrame if the DB or
    table is missing; retries past the collector's brief per-write lock (the writer
    holds the file <1s every ~90s, so a generous retry budget avoids spurious empties)."""
    import pandas as pd

    path = db_path()
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
            time.sleep(0.3)  # collector mid-write
    return pd.DataFrame()
