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


# ---------------------------------------------------------------------------
# /api/fleet/owner-risk
# ---------------------------------------------------------------------------

def _make_owner_risk_client(tmp_path, monkeypatch) -> "TestClient":
    """Registry with risk_scores set so we can assert concentration math."""
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(_AIS_SCHEMA)
    ais_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    # owner A has 3 vessels (scores 60, 40, 80) -> avg=60, max=80, high=2
    reg_conn.executemany(
        "INSERT INTO vessel_registry (imo, ship_name, flag, owner, risk_score, fetch_ok, fetched_ts) VALUES (?,?,?,?,?,true,?)",
        [
            (8000001, "SHIP A1", "Liberia", "OWNER_A", 60, _NOW),
            (8000002, "SHIP A2", "Liberia", "OWNER_A", 40, _NOW),
            (8000003, "SHIP A3", "Panama", "OWNER_A", 80, _NOW),
            # owner B has 2 vessels (scores 20, 30) -> avg=25, max=30, high=0
            (8000004, "SHIP B1", "Malta", "OWNER_B", 20, _NOW),
            (8000005, "SHIP B2", "Malta", "OWNER_B", 30, _NOW),
            # owner C has 1 vessel (score 90) - excluded by min_vessels=2
            (8000006, "SHIP C1", "Togo", "OWNER_C", 90, _NOW),
            # fetch_ok=false should be excluded
        ],
    )
    reg_conn.execute(
        "INSERT INTO vessel_registry (imo, ship_name, flag, owner, risk_score, fetch_ok, fetched_ts) VALUES (?,?,?,?,?,false,?)",
        [8000099, "BROKEN", "None", "OWNER_D", 50, _NOW],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
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

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    reg_conn.executemany(
        "INSERT INTO vessel_registry (imo, ship_name, flag, risk_score, ofac_sanctioned, fetch_ok, fetched_ts) VALUES (?,?,?,?,?,true,?)",
        [
            (5000001, "HIGH RISK TANKER", "Togo", 75, False, _NOW),
            (5000002, "MED TANKER", "Panama", 45, False, _NOW),
        ],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
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

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    reg_conn.executemany(
        "INSERT INTO vessel_registry (imo, ship_name, flag, flag_code, risk_score, fetch_ok, fetched_ts) VALUES (?,?,?,?,?,true,?)",
        [
            (9900001, "SHIP1", "Togo", "TGO", 80, _NOW),
            (9900002, "SHIP2", "Malta", "MLT", 30, _NOW),
            (9900003, "SHIP3", "Togo", "TGO", 60, _NOW),
        ],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
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

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    reg_conn.executemany(
        "INSERT INTO vessel_registry (imo, ship_name, flag, risk_score, ofac_sanctioned, fetch_ok, fetched_ts) "
        "VALUES (?,?,?,?,?,true,?)",
        [
            (9900001, "CRITICAL", "Iran", 80, True, _NOW),   # critical + ofac
            (9900002, "HIGH", "Togo", 55, False, _NOW),      # high risk
            (9900003, "LOW", "Malta", 15, False, _NOW),      # low risk, scored
            (9900004, "UNSCORED", "Panama", None, False, _NOW),  # no risk_score
        ],
    )
    # fetch_ok=false vessel - should NOT appear
    reg_conn.execute(
        "INSERT INTO vessel_registry (imo, ship_name, flag, risk_score, fetch_ok, fetched_ts) "
        "VALUES (9900005, 'EXCLUDED', 'Cuba', 90, false, ?)",
        [_NOW],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
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
    reg_file = tmp_path / "registry.duckdb"
    duckdb.connect(str(reg_file)).execute(_REG_SCHEMA)
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    r = TestClient(app).get("/api/fleet/kpis")
    assert r.status_code == 200
    d = r.json()
    assert d["total_registry"] == 0
    assert d["scored"] == 0
    assert d["avg_risk_score"] is None
