"""Pytest fixtures: a temporary ais_positions.duckdb seeded with known vessels,
wired into the app via the AIS_POSITIONS_DB env var, exposed as a TestClient.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from fastapi.testclient import TestClient

_SCHEMA = """
CREATE TABLE live_positions (
    mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
    sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
    ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
    region VARCHAR, updated_ts TIMESTAMP
);
"""

_NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
_STALE = _NOW - timedelta(hours=5)

# mmsi, name, lat, lon, sog, cog, heading, dest, type, len, kind, segment, region, ts
_SEED = [
    (
        1001,
        "CAPE A",
        1.2,
        103.6,
        12.0,
        90.0,
        91.0,
        "CNSHA",
        74,
        300,
        "bulk",
        "Capesize",
        "singapore_malacca",
        _NOW,
    ),
    (
        1002,
        "CAPE B",
        1.3,
        103.7,
        0.1,
        None,
        None,
        "SGSIN",
        74,
        290,
        "bulk",
        "Capesize",
        "singapore_malacca",
        _NOW,
    ),
    (
        1003,
        "VLCC A",
        26.0,
        56.2,
        14.0,
        270.0,
        271.0,
        "AEFJR",
        80,
        330,
        "tanker",
        "VLCC",
        "hormuz",
        _NOW,
    ),
    (1004, "COASTER", 51.0, 1.5, 8.0, 45.0, None, None, 70, 100, "bulk", "Small", None, _NOW),
    (
        1005,
        "STALE CAPE",
        29.0,
        33.0,
        10.0,
        180.0,
        None,
        "EGPSD",
        74,
        280,
        "bulk",
        "Capesize",
        "suez",
        _STALE,
    ),
]


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    db_file = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(_SCHEMA)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _SEED)
    conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(db_file))
    from app.main import app

    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path, monkeypatch) -> TestClient:
    """Client pointed at a non-existent DB (collector never ran)."""
    monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing.duckdb"))
    from app.main import app

    return TestClient(app)
