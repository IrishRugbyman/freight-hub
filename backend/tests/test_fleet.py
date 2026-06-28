"""Tests for /api/fleet, /api/fleet/facets, /api/fleet/export endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from fastapi.testclient import TestClient

from conftest import setup_pg_vessels

_NOW = datetime.now(UTC).replace(tzinfo=None)

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

# Vessel registry rows as dicts for PostgreSQL insertion.
# Columns: imo, ship_name, flag, flag_code, call_sign, gross_tonnage, dwt,
#          ship_type, year_built, ship_status, owner, ism_manager, ship_manager,
#          class_society, pi_club, detention_rate_pct, paris_mou, tokyo_mou,
#          uscg_targeting, fetched_ts, fetch_ok
_REG_ROWS = [
    {"imo": 9111111, "ship_name": "ALPHA VLCC", "flag": "Liberia", "flag_code": "LBR",
     "call_sign": "A1A1A1", "gross_tonnage": 160000, "dwt": 310000,
     "ship_type": "Crude Oil Tanker", "year_built": 2005, "ship_status": "In Service/Commission",
     "owner": "OCEAN OWNER LTD", "ism_manager": "OCEAN ISM", "ship_manager": "OCEAN ISM",
     "class_society": "Lloyd's Register (IACS)", "pi_club": "West of England",
     "detention_rate_pct": 2.5, "paris_mou": "White", "tokyo_mou": "White",
     "uscg_targeting": "not targeted", "fetched_ts": _NOW, "fetch_ok": True},
    {"imo": 9222222, "ship_name": "BETA BULK", "flag": "Barbados", "flag_code": "BRB",
     "call_sign": "B2B2B2", "gross_tonnage": 45000, "dwt": 82000,
     "ship_type": "Bulk Carrier", "year_built": 2010, "ship_status": "In Service/Commission",
     "owner": "BULK OWNER SA", "ism_manager": "BULK ISM", "ship_manager": "BULK ISM",
     "class_society": "DNV (IACS)", "pi_club": "Britannia",
     "detention_rate_pct": 8.0, "paris_mou": "Grey", "tokyo_mou": "White",
     "uscg_targeting": "targeted", "fetched_ts": _NOW, "fetch_ok": True},
    {"imo": 9333333, "ship_name": "GAMMA TANKER", "flag": "Marshall Islands", "flag_code": "MHL",
     "call_sign": "C3C3C3", "gross_tonnage": 28000, "dwt": 46000,
     "ship_type": "Chemical Tanker", "year_built": 2015, "ship_status": "In Service/Commission",
     "owner": "OCEAN OWNER LTD", "ism_manager": "GAMMA ISM", "ship_manager": "GAMMA ISM",
     "class_society": "Bureau Veritas (IACS)", "pi_club": "UK P&I",
     "detention_rate_pct": 0.0, "paris_mou": "White", "tokyo_mou": "Grey",
     "uscg_targeting": "not targeted", "fetched_ts": _NOW, "fetch_ok": True},
    {"imo": 9444444, "ship_name": "REGISTRY ONLY", "flag": "Panama", "flag_code": "PAN",
     "call_sign": "D4D4D4", "gross_tonnage": 5000, "dwt": 8000,
     "ship_type": "General Cargo Ship", "year_built": 2000, "ship_status": "In Service/Commission",
     "owner": "PANAMA OWNER", "ism_manager": "PANAMA ISM", "ship_manager": "PANAMA ISM",
     "class_society": "American Bureau of Shipping (IACS)", "pi_club": "Standard P&I",
     "detention_rate_pct": 15.0, "paris_mou": "Black", "tokyo_mou": "Black",
     "uscg_targeting": "targeted", "fetched_ts": _NOW, "fetch_ok": True},
    # fetch_ok=False should be excluded from all results
    {"imo": 9555555, "ship_name": "FAILED VESSEL", "flag": "Togo", "flag_code": "TGO",
     "fetched_ts": _NOW, "fetch_ok": False},
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

    setup_pg_vessels(monkeypatch, _REG_ROWS)
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
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
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.close()

    setup_pg_vessels(monkeypatch, [
        {"imo": 1000001, "ship_name": "LOW RISK", "flag": "Norway",
         "fetch_ok": True, "fetched_ts": _NOW, "risk_score": 10},
        {"imo": 1000002, "ship_name": "HIGH RISK", "flag": "Cameroon",
         "fetch_ok": True, "fetched_ts": _NOW, "risk_score": 65},
        {"imo": 1000003, "ship_name": "NO SCORE", "flag": "Panama",
         "fetch_ok": True, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    client = TestClient(app)

    r = client.get("/api/fleet?risk_min=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["ship_name"] == "HIGH RISK"
    assert body["rows"][0]["risk_score"] == 65


# ---------------------------------------------------------------------------
# /api/fleet/owner-risk
# ---------------------------------------------------------------------------

def _make_owner_risk_client(tmp_path, monkeypatch) -> "TestClient":
    """Registry with risk_scores set so we can assert concentration math."""
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.close()

    setup_pg_vessels(monkeypatch, [
        {"imo": 8000001, "ship_name": "SHIP A1", "flag": "Liberia", "owner": "OWNER_A",
         "risk_score": 60, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 8000002, "ship_name": "SHIP A2", "flag": "Liberia", "owner": "OWNER_A",
         "risk_score": 40, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 8000003, "ship_name": "SHIP A3", "flag": "Panama", "owner": "OWNER_A",
         "risk_score": 80, "fetch_ok": True, "fetched_ts": _NOW},
        # owner B has 2 vessels (scores 20, 30) -> avg=25, max=30, high=0
        {"imo": 8000004, "ship_name": "SHIP B1", "flag": "Malta", "owner": "OWNER_B",
         "risk_score": 20, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 8000005, "ship_name": "SHIP B2", "flag": "Malta", "owner": "OWNER_B",
         "risk_score": 30, "fetch_ok": True, "fetched_ts": _NOW},
        # owner C has 1 vessel (score 90) - excluded by min_vessels=2
        {"imo": 8000006, "ship_name": "SHIP C1", "flag": "Togo", "owner": "OWNER_C",
         "risk_score": 90, "fetch_ok": True, "fetched_ts": _NOW},
        # fetch_ok=false should be excluded
        {"imo": 8000099, "ship_name": "BROKEN", "flag": "None", "owner": "OWNER_D",
         "risk_score": 50, "fetch_ok": False, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    return TestClient(app)


def test_owner_risk_structure(tmp_path, monkeypatch):
    client = _make_owner_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/owner-risk")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "rows" in d
    assert isinstance(d["rows"], list)
    for row in d["rows"]:
        assert "owner" in row
        assert "vessel_count" in row
        assert "avg_risk_score" in row
        assert "max_risk_score" in row
        assert "high_risk_count" in row
        assert "ofac_count" in row
        assert "flags" in row


def test_owner_risk_values(tmp_path, monkeypatch):
    client = _make_owner_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/owner-risk?min_vessels=2")
    assert r.status_code == 200
    rows = {row["owner"]: row for row in r.json()["rows"]}
    assert "OWNER_A" in rows
    assert "OWNER_B" in rows
    # Single-vessel owner excluded
    assert "OWNER_C" not in rows
    # fetch_ok=false excluded
    assert "OWNER_D" not in rows
    a = rows["OWNER_A"]
    assert a["vessel_count"] == 3
    assert a["avg_risk_score"] == pytest.approx(60.0, abs=0.5)
    assert a["max_risk_score"] == 80
    assert a["high_risk_count"] == 2
    b = rows["OWNER_B"]
    assert b["vessel_count"] == 2
    assert b["avg_risk_score"] == pytest.approx(25.0, abs=0.5)
    assert b["high_risk_count"] == 0


def test_owner_risk_sorted_by_avg_desc(tmp_path, monkeypatch):
    client = _make_owner_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/owner-risk?min_vessels=1")
    assert r.status_code == 200
    rows = r.json()["rows"]
    scores = [row["avg_risk_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)


def test_owner_risk_min_vessels_1_includes_single(tmp_path, monkeypatch):
    client = _make_owner_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/owner-risk?min_vessels=1")
    assert r.status_code == 200
    owners = {row["owner"] for row in r.json()["rows"]}
    assert "OWNER_C" in owners


def test_owner_risk_top_n_clamped(tmp_path, monkeypatch):
    client = _make_owner_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/owner-risk?min_vessels=1&top_n=2")
    assert r.status_code == 200
    assert len(r.json()["rows"]) <= 2


# ---------------------------------------------------------------------------
# /api/analytics/high-risk-positions
# ---------------------------------------------------------------------------

def _make_high_risk_client(tmp_path, monkeypatch) -> "TestClient":
    """AIS DB with IMO-linked vessels + registry with risk scores."""
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    # mmsi, name, lat, lon, sog, cog, heading, dest, type, len, kind, segment, region, ts, imo, draught, nav_status, eta
    ais_conn.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # high risk vessel (score=75), IMO matches registry
            (7001, "HIGH RISK TANKER", 26.0, 56.0, 12.0, 270.0, 271.0, "AEFJR", 80, 330,
             "tanker", "VLCC", "hormuz", _NOW, 5000001, 20.0, 0, None),
            # medium risk vessel (score=45), below default threshold
            (7002, "MED TANKER", 1.2, 103.6, 10.0, 90.0, 91.0, "SGSIN", 80, 280,
             "tanker", "Aframax", "singapore_malacca", _NOW, 5000002, 15.0, 0, None),
            # no IMO in live -> never matches
            (7003, "NO IMO BULK", 51.0, 1.5, 8.0, 45.0, None, None, 74, 200,
             "bulk", "Small", "dover_channel", _NOW, None, None, None, None),
        ],
    )
    ais_conn.close()

    setup_pg_vessels(monkeypatch, [
        {"imo": 5000001, "ship_name": "HIGH RISK TANKER", "flag": "Togo",
         "risk_score": 75, "ofac_sanctioned": False, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 5000002, "ship_name": "MED TANKER", "flag": "Panama",
         "risk_score": 45, "ofac_sanctioned": False, "fetch_ok": True, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    return TestClient(app)


def test_high_risk_positions_structure(tmp_path, monkeypatch):
    client = _make_high_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/analytics/high-risk-positions")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "min_risk" in d
    assert isinstance(d["rows"], list)
    for row in d["rows"]:
        assert "mmsi" in row
        assert "imo" in row
        assert "lat" in row
        assert "lon" in row
        assert "risk_score" in row
        assert "ofac_sanctioned" in row


def test_high_risk_positions_filters_by_threshold(tmp_path, monkeypatch):
    client = _make_high_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/analytics/high-risk-positions?min_risk=60")
    assert r.status_code == 200
    rows = r.json()["rows"]
    # only score=75 vessel should appear (45 is below 60)
    assert len(rows) == 1
    assert rows[0]["mmsi"] == 7001
    assert rows[0]["risk_score"] == 75


def test_high_risk_positions_lower_threshold(tmp_path, monkeypatch):
    client = _make_high_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/analytics/high-risk-positions?min_risk=40")
    assert r.status_code == 200
    mmsis = {row["mmsi"] for row in r.json()["rows"]}
    assert 7001 in mmsis
    assert 7002 in mmsis


def test_high_risk_positions_sorted_desc(tmp_path, monkeypatch):
    client = _make_high_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/analytics/high-risk-positions?min_risk=0")
    rows = r.json()["rows"]
    scores = [row["risk_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)


def test_high_risk_no_imo_excluded(tmp_path, monkeypatch):
    client = _make_high_risk_client(tmp_path, monkeypatch)
    r = client.get("/api/analytics/high-risk-positions?min_risk=0")
    mmsis = {row["mmsi"] for row in r.json()["rows"]}
    assert 7003 not in mmsis


# ---------------------------------------------------------------------------
# /api/fleet/flag-risk
# ---------------------------------------------------------------------------

def test_flag_risk_structure(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/flag-risk")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert isinstance(d["rows"], list)
    for row in d["rows"]:
        assert "flag" in row
        assert "vessel_count" in row
        assert "avg_risk_score" in row
        assert "max_risk_score" in row
        assert "high_risk_count" in row
        assert "ofac_count" in row


def test_flag_risk_values(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/flag-risk")
    assert r.status_code == 200
    # _REG_ROWS has Liberia (fetch_ok=true) but risk_score is NULL (not set in fixture)
    # Rows only appear if risk_score IS NOT NULL - so this returns empty unless we set scores
    # fetch_ok=false vessel (Togo) should be excluded
    d = r.json()
    rows_by_flag = {row["flag"]: row for row in d["rows"]}
    # Togo vessel (fetch_ok=false) never appears
    assert "Togo" not in rows_by_flag


def test_flag_risk_excludes_null_risk(tmp_path, monkeypatch):
    """Vessels without risk_score are excluded from flag-risk."""
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/flag-risk")
    assert r.status_code == 200
    # All vessels in _REG_ROWS have risk_score=NULL -> result is empty
    assert r.json()["rows"] == []


def test_flag_risk_sorted_desc(tmp_path, monkeypatch):
    """When scores exist, rows are sorted by avg_risk_score descending."""
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.close()

    setup_pg_vessels(monkeypatch, [
        {"imo": 9900001, "ship_name": "SHIP1", "flag": "Togo", "flag_code": "TGO",
         "risk_score": 80, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9900002, "ship_name": "SHIP2", "flag": "Malta", "flag_code": "MLT",
         "risk_score": 30, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9900003, "ship_name": "SHIP3", "flag": "Togo", "flag_code": "TGO",
         "risk_score": 60, "fetch_ok": True, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    client = TestClient(app)

    r = client.get("/api/fleet/flag-risk")
    assert r.status_code == 200
    rows = r.json()["rows"]
    # Togo: avg=(80+60)/2=70, Malta: avg=30 -> Togo first
    assert rows[0]["flag"] == "Togo"
    assert rows[0]["vessel_count"] == 2
    assert rows[0]["avg_risk_score"] == pytest.approx(70.0, abs=0.5)
    assert rows[1]["flag"] == "Malta"


# ---- /api/fleet/kpis ----


def _make_kpi_client(tmp_path, monkeypatch) -> TestClient:
    """Registry with 4 fetch_ok vessels (3 scored, 1 OFAC, 1 critical) + empty AIS."""
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.close()

    setup_pg_vessels(monkeypatch, [
        {"imo": 9900001, "ship_name": "CRITICAL", "flag": "Iran",
         "risk_score": 80, "ofac_sanctioned": True, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9900002, "ship_name": "HIGH", "flag": "Togo",
         "risk_score": 55, "ofac_sanctioned": False, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9900003, "ship_name": "LOW", "flag": "Malta",
         "risk_score": 15, "ofac_sanctioned": False, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9900004, "ship_name": "UNSCORED", "flag": "Panama",
         "ofac_sanctioned": False, "fetch_ok": True, "fetched_ts": _NOW},
        # fetch_ok=false vessel - should NOT appear
        {"imo": 9900005, "ship_name": "EXCLUDED", "flag": "Cuba",
         "risk_score": 90, "fetch_ok": False, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    return TestClient(app)


def test_fleet_kpis_structure(tmp_path, monkeypatch):
    client = _make_kpi_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/kpis")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "total_registry", "scored", "elevated", "high_risk",
                "critical", "ofac_count", "avg_risk_score", "pct_scored"):
        assert key in d, f"missing key: {key}"


def test_fleet_kpis_counts(tmp_path, monkeypatch):
    """4 fetch_ok vessels, 3 scored; excludes fetch_ok=false vessel."""
    client = _make_kpi_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/kpis")
    d = r.json()
    assert d["total_registry"] == 4
    assert d["scored"] == 3
    assert d["ofac_count"] == 1


def test_fleet_kpis_risk_bands(tmp_path, monkeypatch):
    """Score 80 -> critical+high+elevated; 55 -> high+elevated; 15 -> elevated only."""
    client = _make_kpi_client(tmp_path, monkeypatch)
    d = client.get("/api/fleet/kpis").json()
    assert d["elevated"] == 2   # 80 and 55
    assert d["high_risk"] == 2  # 80 and 55
    assert d["critical"] == 1   # only 80


def test_fleet_kpis_avg_score(tmp_path, monkeypatch):
    """avg_risk_score is mean of scored vessels (80+55+15)/3 = 50."""
    client = _make_kpi_client(tmp_path, monkeypatch)
    d = client.get("/api/fleet/kpis").json()
    assert d["avg_risk_score"] == pytest.approx(50.0, abs=1.0)


def test_fleet_kpis_pct_scored(tmp_path, monkeypatch):
    """3 of 4 fetch_ok vessels are scored -> 75%."""
    client = _make_kpi_client(tmp_path, monkeypatch)
    d = client.get("/api/fleet/kpis").json()
    assert d["pct_scored"] == pytest.approx(75.0, abs=1.0)


def test_fleet_kpis_empty_registry(tmp_path, monkeypatch):
    """Empty registry returns zeros."""
    ais_file = tmp_path / "ais.duckdb"
    duckdb.connect(str(ais_file)).execute(_AIS_SCHEMA)
    setup_pg_vessels(monkeypatch, [])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    r = TestClient(app).get("/api/fleet/kpis")
    assert r.status_code == 200
    d = r.json()
    assert d["total_registry"] == 0
    assert d["scored"] == 0
    assert d["avg_risk_score"] is None


# ---- /api/fleet/age ----


def _make_age_client(tmp_path, monkeypatch) -> TestClient:
    """Registry with year_built and risk_score for age distribution tests."""
    ais_file = tmp_path / "ais.duckdb"
    duckdb.connect(str(ais_file)).execute(_AIS_SCHEMA)

    setup_pg_vessels(monkeypatch, [
        # year 2026 reference: age = 2026 - year_built
        # 2 new (age 2, band "0-4"), 2 mid-aged (age 8, band "5-9"), 1 old (age 30, band "25+")
        {"imo": 9910001, "ship_name": "NEW1", "flag": "Malta", "year_built": 2024,
         "dwt": 300000, "risk_score": 10, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9910002, "ship_name": "NEW2", "flag": "Malta", "year_built": 2024,
         "dwt": 280000, "risk_score": 15, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9910003, "ship_name": "MID1", "flag": "Panama", "year_built": 2018,
         "dwt": 80000, "risk_score": 40, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9910004, "ship_name": "MID2", "flag": "Panama", "year_built": 2018,
         "dwt": 75000, "risk_score": 55, "fetch_ok": True, "fetched_ts": _NOW},
        {"imo": 9910005, "ship_name": "OLD1", "flag": "Iran", "year_built": 1996,
         "dwt": 150000, "risk_score": 80, "fetch_ok": True, "fetched_ts": _NOW},
        # fetch_ok=false vessel should be excluded
        {"imo": 9910006, "ship_name": "EXCLUDED", "flag": "Cuba", "year_built": 2000,
         "fetch_ok": False, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    return TestClient(app)


def test_fleet_age_structure(tmp_path, monkeypatch):
    client = _make_age_client(tmp_path, monkeypatch)
    r = client.get("/api/fleet/age")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "reference_year" in d
    assert "bands" in d
    assert isinstance(d["bands"], list)
    for b in d["bands"]:
        for key in ("age_band", "vessel_count", "avg_risk_score", "high_risk_count", "avg_dwt"):
            assert key in b


def test_fleet_age_band_counts(tmp_path, monkeypatch):
    """2 vessels in 0-4, 2 in 5-9, 1 in 25+ (fetch_ok=false excluded)."""
    client = _make_age_client(tmp_path, monkeypatch)
    d = client.get("/api/fleet/age").json()
    bands = {b["age_band"]: b for b in d["bands"]}
    assert "0-4" in bands
    assert bands["0-4"]["vessel_count"] == 2
    assert "5-9" in bands
    assert bands["5-9"]["vessel_count"] == 2
    assert "25+" in bands
    assert bands["25+"]["vessel_count"] == 1


def test_fleet_age_risk_by_band(tmp_path, monkeypatch):
    """New vessels have lower avg risk, old vessels have higher."""
    client = _make_age_client(tmp_path, monkeypatch)
    d = client.get("/api/fleet/age").json()
    bands = {b["age_band"]: b for b in d["bands"]}
    # 0-4 band avg = (10+15)/2 = 12.5
    assert bands["0-4"]["avg_risk_score"] == pytest.approx(12.5, abs=0.5)
    # 25+ band only OLD1 with score=80 -> high_risk_count=1
    assert bands["25+"]["high_risk_count"] == 1
    assert bands["25+"]["avg_risk_score"] == pytest.approx(80.0, abs=0.5)


def test_fleet_age_excludes_no_year_built(tmp_path, monkeypatch):
    """Vessels without year_built are excluded from bands."""
    ais_file = tmp_path / "ais.duckdb"
    duckdb.connect(str(ais_file)).execute(_AIS_SCHEMA)
    setup_pg_vessels(monkeypatch, [
        {"imo": 9920001, "ship_name": "NOYR", "flag": "Malta",
         "fetch_ok": True, "fetched_ts": _NOW},
    ])
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app
    d = TestClient(app).get("/api/fleet/age").json()
    assert d["bands"] == []
