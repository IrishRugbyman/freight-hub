"""Pytest fixtures: a temporary ais_positions.duckdb seeded with known vessels,
wired into the app via the AIS_POSITIONS_DB env var, exposed as a TestClient.
Also includes fixtures that seed static JSON for the routes/dispersion endpoints.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

_SCHEMA = """
CREATE TABLE live_positions (
    mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
    sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
    ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
    region VARCHAR, updated_ts TIMESTAMP,
    imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
);
CREATE TABLE ais_snapshots (
    snapshot_ts TIMESTAMP, mmsi BIGINT,
    kind VARCHAR, segment VARCHAR, region VARCHAR,
    lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
    sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
    PRIMARY KEY (snapshot_ts, mmsi)
);
"""

_NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
_STALE = _NOW - timedelta(hours=5)

# mmsi, name, lat, lon, sog, cog, heading, dest, type, len, kind, segment, region, ts,
#   imo, draught, nav_status, eta
_SEED = [
    (1001, "CAPE A", 1.2, 103.6, 12.0, 90.0, 91.0, "CNSHA", 74, 300,
     "bulk", "Capesize", "singapore_malacca", _NOW, None, None, 0, None),
    (1002, "CAPE B", 1.3, 103.7, 0.1, None, None, "SGSIN", 74, 290,
     "bulk", "Capesize", "singapore_malacca", _NOW, None, None, 1, None),
    (1003, "VLCC A", 26.0, 56.2, 14.0, 270.0, 271.0, "AEFJR", 80, 330,
     "tanker", "VLCC", "hormuz", _NOW, 9876543, 20.5, 0, "06-20 06:00"),
    (1004, "COASTER", 51.0, 1.5, 8.0, 45.0, None, None, 70, 100,
     "bulk", "Small", None, _NOW, None, None, None, None),
    (1005, "STALE CAPE", 29.0, 33.0, 10.0, 180.0, None, "EGPSD", 74, 280,
     "bulk", "Capesize", "suez", _STALE, None, None, None, None),
]


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    db_file = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(_SCHEMA)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _SEED)
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


_SNAP_SEED = [
    # snapshot_ts, mmsi, kind, segment, region, lat, lon, ship_type, length_m, sog, nav_s, draught, dest
    (_NOW - timedelta(hours=2), 1003, "tanker", "VLCC", "hormuz", 25.9, 56.1, 80, 330, 14.0, 0, 20.0, "AEFJR"),
    (_NOW - timedelta(hours=1), 1003, "tanker", "VLCC", "hormuz", 26.0, 56.2, 80, 330, 14.0, 0, 20.0, "AEFJR"),
    (_NOW, 1003, "tanker", "VLCC", "hormuz", 26.1, 56.3, 80, 330, 14.0, 0, 20.0, "AEFJR"),
    # old snapshot outside 24h window
    (_NOW - timedelta(hours=30), 1003, "tanker", "VLCC", "hormuz", 25.5, 55.8, 80, 330, 12.0, 0, 20.0, "AEFJR"),
]


@pytest.fixture
def client_with_snaps(tmp_path, monkeypatch) -> TestClient:
    db_file = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(_SCHEMA)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _SEED)
    conn.executemany(
        "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", _SNAP_SEED
    )
    conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(db_file))
    from app.main import app

    return TestClient(app)


_ROUTES_FIXTURE = {
    "name": "test_routes",
    "as_of": "2026-01-01",
    "spots": {"WTI": 70.0},
    "routes": [
        {
            "id": "rt1",
            "origin": "Cushing",
            "destination": "Rotterdam",
            "product_class": "crude",
            "vessel_class": "VLCC",
            "voyage_days": 38,
            "description": "Cushing to Rotterdam VLCC",
            "origin_spot": 70.0,
            "origin_price": 70.0,
            "dest_spot": 72.0,
            "dest_fwd": 71.0,
            "fwd_curve_effect": -1.0,
            "freight": 1.5,
            "freight_base": 1.5,
            "freight_bwet_adjusted": False,
            "port_cost": 0.2,
            "finance_cost": 0.1,
            "insurance_cost": 0.05,
            "total_cost": 1.85,
            "gross_margin": 1.0,
            "net_margin": 0.85,
            "net_margin_baseline": 0.5,
            "breakeven_freight": 0.65,
            "status": "open",
            "status_near": "open",
        }
    ],
    "n_open": 1,
    "n_closed": 0,
    "n_near": 0,
    "hist_series": [],
    "bwet": {
        "bwet_close": 16.6,
        "bwet_baseline": 16.6,
        "scale_factor": 1.0,
        "source": "static",
        "bwet_date": None,
    },
    "matrix": [],
    "matrix_origins": [],
    "matrix_destinations": [],
}

_DISPERSION_FIXTURE = {
    "name": "test_disp",
    "strategy": "mean_reversion",
    "stats": {
        "total_return": 40000.0,
        "ann_return": 4000.0,
        "ann_volatility": 5000.0,
        "sharpe": 0.76,
        "max_drawdown": -7000.0,
        "n_trades": 70,
        "hit_rate": 0.56,
        "n_years": 10.0,
    },
    "equity": [{"date": "2016-01-04", "value": 0.0}, {"date": "2026-01-01", "value": 40000.0}],
    "price_5tc": [{"date": "2016-01-04", "value": 5000.0}],
    "avg_dispersion": [{"date": "2016-01-04", "value": 1234.5}],
}

_ANALYTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
CREATE TABLE IF NOT EXISTS transit_events (
    mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
    direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
    PRIMARY KEY (mmsi, chokepoint, entered_ts)
);
CREATE TABLE IF NOT EXISTS anchored_episodes (
    mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
    kind VARCHAR, segment VARCHAR,
    PRIMARY KEY (mmsi, zone, start_ts)
);
CREATE TABLE IF NOT EXISTS fleet_density (
    ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
    laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
    PRIMARY KEY (ts, region, kind, segment)
);
CREATE TABLE IF NOT EXISTS vessel_state (
    mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
    laden VARCHAR, updated_ts TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ais_events (
    event_id VARCHAR PRIMARY KEY, type VARCHAR,
    mmsi BIGINT, mmsi2 BIGINT,
    start_ts TIMESTAMP, end_ts TIMESTAMP,
    lat DOUBLE, lon DOUBLE,
    region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
);
"""

_TRANSIT_SEED = [
    # mmsi, chokepoint, entered_ts, exited_ts, direction, kind, segment, laden
    (1003, "hormuz", _NOW - timedelta(hours=10), _NOW - timedelta(hours=8),
     "outbound", "tanker", "VLCC", True),
    (1001, "singapore_malacca", _NOW - timedelta(hours=5), _NOW - timedelta(hours=4),
     "eastbound", "bulk", "Capesize", False),
]

_ANCHOR_SEED = [
    # mmsi, zone, start_ts, end_ts, kind, segment
    (1002, "singapore_east", _NOW - timedelta(hours=6), _NOW - timedelta(hours=2),
     "bulk", "Capesize"),
]

_DENSITY_SEED = [
    # ts, region, kind, segment, laden, ballast, unknown
    (_NOW - timedelta(hours=2), "hormuz", "tanker", "VLCC", 2, 1, 0),
    (_NOW - timedelta(hours=1), "hormuz", "tanker", "VLCC", 2, 0, 1),
]

_VESSEL_STATE_SEED = [
    # mmsi, max_draught_seen, last_draught, laden, updated_ts
    (1003, 22.0, 20.5, "laden", _NOW),
]

_EVENTS_SEED = [
    # event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon, region, kind, segment, details
    ("gap0000001", "gap", 1001, None,
     _NOW - timedelta(hours=20), _NOW - timedelta(hours=20),
     25.2, 56.5, "hormuz", "tanker", "Aframax", '{"silence_hours":20,"last_sog":9.1}'),
    ("loi0000001", "loiter", 1002, None,
     _NOW - timedelta(hours=15), _NOW - timedelta(hours=3),
     1.2, 103.8, "singapore_malacca", "bulk", "Capesize", '{"duration_hours":12,"mean_sog":0.3}'),
    ("sts0000001", "sts", 1003, 1004,
     _NOW - timedelta(hours=3), _NOW - timedelta(hours=1),
     26.1, 56.3, "hormuz", "tanker", "VLCC", '{"duration_hours":2,"co_location_fixes":12}'),
]


@pytest.fixture
def analytics_client(tmp_path, monkeypatch) -> TestClient:
    """Client with both AIS DB and analytics DB seeded."""
    ais_file = tmp_path / "ais_positions.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_SCHEMA)
    ais_conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _SEED)
    ais_conn.executemany("INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", _SNAP_SEED)
    ais_conn.close()

    an_file = tmp_path / "freight_analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(_ANALYTICS_SCHEMA)
    an_conn.executemany(
        "INSERT INTO transit_events VALUES (?,?,?,?,?,?,?,?)", _TRANSIT_SEED
    )
    an_conn.executemany("INSERT INTO anchored_episodes VALUES (?,?,?,?,?,?)", _ANCHOR_SEED)
    an_conn.executemany("INSERT INTO fleet_density VALUES (?,?,?,?,?,?,?)", _DENSITY_SEED)
    an_conn.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", _VESSEL_STATE_SEED)
    an_conn.executemany(
        "INSERT INTO ais_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", _EVENTS_SEED
    )
    an_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    from app.main import app

    return TestClient(app)


_STATIC_DIR = Path(__file__).parent.parent / "app" / "static"


@pytest.fixture
def static_routes_json():
    """Seed the routes static JSON with a minimal fixture, restore afterwards."""
    path = _STATIC_DIR / "routes_default.json"
    original = path.read_bytes() if path.exists() else None
    _STATIC_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(_ROUTES_FIXTURE))
    yield path
    if original is not None:
        path.write_bytes(original)
    else:
        path.unlink(missing_ok=True)


@pytest.fixture
def static_dispersion_json():
    """Seed the dispersion static JSON with a minimal fixture, restore afterwards."""
    path = _STATIC_DIR / "dispersion_default.json"
    original = path.read_bytes() if path.exists() else None
    _STATIC_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(_DISPERSION_FIXTURE))
    yield path
    if original is not None:
        path.write_bytes(original)
    else:
        path.unlink(missing_ok=True)
