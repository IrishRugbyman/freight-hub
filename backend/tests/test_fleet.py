"""Tests for /api/fleet, /api/fleet/facets, /api/fleet/export endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from fastapi.testclient import TestClient

_NOW = datetime.now(UTC).replace(tzinfo=None)

_REG_SCHEMA = """
CREATE TABLE IF NOT EXISTS vessel_registry (
    imo BIGINT PRIMARY KEY,
    ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR, call_sign VARCHAR,
    gross_tonnage INTEGER, dwt INTEGER,
    ship_type VARCHAR, year_built INTEGER, ship_status VARCHAR,
    owner VARCHAR, ism_manager VARCHAR, ship_manager VARCHAR,
    class_society VARCHAR, pi_club VARCHAR,
    detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR, uscg_targeting VARCHAR,
    fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
    risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
)
"""

_REG_INSERT = (
    "INSERT INTO vessel_registry "
    "(imo, ship_name, flag, flag_code, call_sign, gross_tonnage, dwt, ship_type, year_built,"
    " ship_status, owner, ism_manager, ship_manager, class_society, pi_club,"
    " detention_rate_pct, paris_mou, tokyo_mou, uscg_targeting, fetched_ts, fetch_ok)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_AIS_SCHEMA = """
CREATE TABLE live_positions (
    mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
    sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
    ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
    region VARCHAR, updated_ts TIMESTAMP,
    imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
);
CREATE TABLE ais_snapshots (
    snapshot_ts TIMESTAMP, mmsi BIGINT, kind VARCHAR, segment VARCHAR,
    region VARCHAR, lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
    sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
    PRIMARY KEY (snapshot_ts, mmsi)
);
"""

# imo, ship_name, flag, flag_code, call_sign, gt, dwt, ship_type, year_built, ship_status,
# owner, ism_mgr, ship_mgr, class, pi, detention, paris, tokyo, uscg, fetched_ts, fetch_ok
_REG_ROWS = [
    (9111111, "ALPHA VLCC", "Liberia", "LBR", "A1A1A1", 160000, 310000,
     "Crude Oil Tanker", 2005, "In Service/Commission",
     "OCEAN OWNER LTD", "OCEAN ISM", "OCEAN ISM",
     "Lloyd's Register (IACS)", "West of England",
     2.5, "White", "White", "not targeted", _NOW, True),
    (9222222, "BETA BULK", "Barbados", "BRB", "B2B2B2", 45000, 82000,
     "Bulk Carrier", 2010, "In Service/Commission",
     "BULK OWNER SA", "BULK ISM", "BULK ISM",
     "DNV (IACS)", "Britannia",
     8.0, "Grey", "White", "targeted", _NOW, True),
    (9333333, "GAMMA TANKER", "Marshall Islands", "MHL", "C3C3C3", 28000, 46000,
     "Chemical Tanker", 2015, "In Service/Commission",
     "OCEAN OWNER LTD", "GAMMA ISM", "GAMMA ISM",
     "Bureau Veritas (IACS)", "UK P&I",
     0.0, "White", "Grey", "not targeted", _NOW, True),
    (9444444, "REGISTRY ONLY", "Panama", "PAN", "D4D4D4", 5000, 8000,
     "General Cargo Ship", 2000, "In Service/Commission",
     "PANAMA OWNER", "PANAMA ISM", "PANAMA ISM",
     "American Bureau of Shipping (IACS)", "Standard P&I",
     15.0, "Black", "Black", "targeted", _NOW, True),
    # fetch_ok=false should be excluded from all results
    (9555555, "FAILED VESSEL", "Togo", "TGO", None,
     None, None, None, None, None,
     None, None, None, None, None,
     None, None, None, None, _NOW, False),
]

# mmsi, name, lat, lon, sog, cog, heading, dest, type, len, kind, segment, region, ts,
# imo, draught, nav_status, eta
_LIVE_ROWS = [
    (1001, "ALPHA VLCC", 25.0, 56.0, 14.0, 270.0, 271.0, "AEFJR", 80, 330,
     "tanker", "VLCC", "hormuz", _NOW, 9111111, 20.0, 0, None),
    (1002, "BETA BULK", 1.2, 103.6, 0.1, None, None, "SGSIN", 74, 200,
     "bulk", "Supramax", "singapore_malacca", _NOW, 9222222, None, 1, None),
    # 9333333 and 9444444 not in live (registry-only vessels)
]


def _make_client(tmp_path, monkeypatch) -> TestClient:
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _LIVE_ROWS
    )
    ais_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    reg_conn.executemany(_REG_INSERT, _REG_ROWS)
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/fleet
# ---------------------------------------------------------------------------

def test_fleet_all(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.json()
    # 4 fetch_ok=true rows
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["page_size"] == 100
    assert len(body["rows"]) == 4


def test_fleet_filter_flag(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?flag=Barbados")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["ship_name"] == "BETA BULK"


def test_fleet_filter_owner(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?owner=ocean")  # case-insensitive, matches OCEAN OWNER LTD
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_fleet_filter_paris_mou(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?paris_mou=Black")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["rows"][0]["imo"] == 9444444


def test_fleet_filter_detention_min(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?detention_min=8")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # BETA BULK (8.0) and REGISTRY ONLY (15.0)


def test_fleet_live_only(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?live_only=true")
    assert r.status_code == 200
    body = r.json()
    # Only 2 vessels are in live_positions with valid IMOs
    assert body["total"] == 2


def test_fleet_sort_and_pagination(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?sort=dwt&order=desc")
    assert r.status_code == 200
    rows = r.json()["rows"]
    dwts = [r["dwt"] for r in rows if r["dwt"] is not None]
    assert dwts == sorted(dwts, reverse=True)


def test_fleet_live_fields_populated(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?flag=Liberia")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["mmsi"] == 1001
    assert row["lat"] == pytest.approx(25.0)
    assert row["kind"] == "tanker"


def test_fleet_registry_only_no_live_fields(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?flag=Panama")
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["mmsi"] is None
    assert row["lat"] is None


def test_fleet_summary_strip(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet")
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert summary["total"] == 4
    assert summary["total_dwt"] is not None
    assert len(summary["top_flags"]) > 0


def test_fleet_search_by_name(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?q=alpha")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_fleet_search_by_imo(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet?q=9333333")
    assert r.status_code == 200
    assert r.json()["total"] == 1


# ---------------------------------------------------------------------------
# /api/fleet/facets
# ---------------------------------------------------------------------------

def test_fleet_facets(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/facets")
    assert r.status_code == 200
    body = r.json()
    flags = {f["value"] for f in body["flags"]}
    assert "Liberia" in flags
    assert "Barbados" in flags
    # fetch_ok=false (Togo) must not appear
    assert "Togo" not in flags
    # Paris MOU facets
    paris = {p["value"] for p in body["paris_mou"]}
    assert "White" in paris
    assert "Black" in paris


# ---------------------------------------------------------------------------
# /api/fleet/export
# ---------------------------------------------------------------------------

def test_fleet_export_csv(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    # Header + 4 data rows
    assert len(lines) >= 5
    assert "ship_name" in lines[0] or "imo" in lines[0]


def test_fleet_export_filtered(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/export?flag=Barbados")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert len(lines) == 2  # header + 1 row
    assert "BETA BULK" in r.text


def test_fleet_risk_min(tmp_path, monkeypatch):
    """risk_min filter returns only vessels with risk_score >= threshold."""
    # Seed two vessels with different risk scores
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    reg_conn.execute(
        "INSERT INTO vessel_registry "
        "(imo, ship_name, flag, fetch_ok, fetched_ts, risk_score) "
        "VALUES (1000001, 'LOW RISK', 'Norway', true, ?, 10)",
        [_NOW],
    )
    reg_conn.execute(
        "INSERT INTO vessel_registry "
        "(imo, ship_name, flag, fetch_ok, fetched_ts, risk_score) "
        "VALUES (1000002, 'HIGH RISK', 'Cameroon', true, ?, 65)",
        [_NOW],
    )
    reg_conn.execute(
        "INSERT INTO vessel_registry "
        "(imo, ship_name, flag, fetch_ok, fetched_ts) "
        "VALUES (1000003, 'NO SCORE', 'Panama', true, ?)",
        [_NOW],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    client = TestClient(app)

    r = client.get("/api/fleet?risk_min=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["ship_name"] == "HIGH RISK"
    assert body["rows"][0]["risk_score"] == 65
