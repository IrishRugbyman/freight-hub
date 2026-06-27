"""Tests for the registry crawler (pure functions) and the /api/vessels/{imo}/equasis endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Pure-function tests: priority_order and _to_int
# ---------------------------------------------------------------------------

def test_to_int_valid():
    from registry.crawl import _to_int

    assert _to_int("12345") == 12345
    assert _to_int(999) == 999
    assert _to_int("171,542") is None  # comma separator not handled
    assert _to_int(None) is None
    assert _to_int("") is None
    assert _to_int("abc") is None


def test_priority_order_all_new():
    from registry.crawl import priority_order

    live = {1001, 1002, 1003}
    result = priority_order(live, pd.DataFrame(), datetime.now(), 10)
    assert set(result) == live


def test_priority_order_limit():
    from registry.crawl import priority_order

    live = {i for i in range(100)}
    result = priority_order(live, pd.DataFrame(), datetime.now(), 10)
    assert len(result) == 10


def test_priority_order_never_fetched_first():
    from registry.crawl import priority_order

    now = datetime.now()
    reg_df = pd.DataFrame([
        {"imo": 1001, "fetch_ok": True, "fetched_ts": now - timedelta(days=60)},  # stale
    ])
    live = {1001, 1002}  # 1002 never fetched
    result = priority_order(live, reg_df, now, 10)
    # 1002 (never fetched) must come before 1001 (stale)
    assert result.index(1002) < result.index(1001)


def test_priority_order_retry_failed():
    from registry.crawl import priority_order

    now = datetime.now()
    reg_df = pd.DataFrame([
        {"imo": 1001, "fetch_ok": False, "fetched_ts": now - timedelta(days=8)},   # retry eligible
        {"imo": 1002, "fetch_ok": False, "fetched_ts": now - timedelta(days=2)},   # too recent
        {"imo": 1003, "fetch_ok": True,  "fetched_ts": now - timedelta(days=60)},  # stale
    ])
    live = {1001, 1002, 1003}
    result = priority_order(live, reg_df, now, 10)
    # 1001 (retry_failed) must come before 1003 (stale)
    assert 1001 in result
    assert 1002 not in result  # too recent, not eligible
    assert result.index(1001) < result.index(1003)


def test_priority_order_excludes_recent_ok():
    from registry.crawl import priority_order

    now = datetime.now()
    # Recently fetched OK row - should not be re-crawled
    reg_df = pd.DataFrame([
        {"imo": 9876543, "fetch_ok": True, "fetched_ts": now - timedelta(days=5)},
    ])
    result = priority_order({9876543}, reg_df, now, 10)
    assert result == []


def test_upsert_idempotent(tmp_path):
    from registry.crawl import _SCHEMA, _upsert

    db_path = tmp_path / "reg.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(_SCHEMA)

    now = datetime.now(UTC).replace(tzinfo=None)
    data = {
        "ship_name": "TEST VESSEL", "flag": "Panama", "flag_code": "PAN",
        "gross_tonnage": "50000", "dwt": "90000", "year_built": "2005",
        "owner": "TEST OWNER",
    }
    _upsert(conn, 1234567, data, now)
    _upsert(conn, 1234567, data, now)  # second call must not error

    count = conn.execute("SELECT COUNT(*) FROM vessel_registry WHERE imo = 1234567").fetchone()[0]
    assert count == 1
    conn.close()


def test_int_cast_stored_correctly(tmp_path):
    from registry.crawl import _SCHEMA, _upsert

    conn = duckdb.connect(str(tmp_path / "reg.duckdb"))
    conn.execute(_SCHEMA)
    now = datetime.now(UTC).replace(tzinfo=None)
    _upsert(conn, 9999999, {"gross_tonnage": "171542", "dwt": "174239", "year_built": "2006"}, now)
    row = conn.execute(
        "SELECT gross_tonnage, dwt, year_built FROM vessel_registry WHERE imo = 9999999"
    ).fetchone()
    assert row == (171542, 174239, 2006)
    conn.close()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

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

_NOW = datetime.now(UTC).replace(tzinfo=None)


def _make_registry_client(tmp_path, monkeypatch, reg_rows: list[tuple]) -> TestClient:
    """Build a TestClient with the given registry rows seeded, AIS DB from conftest."""
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute("""
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
    """)
    ais_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(_REG_SCHEMA)
    for row in reg_rows:
        reg_conn.execute(_REG_INSERT, list(row))
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    return TestClient(app)


def test_equasis_registry_hit(tmp_path, monkeypatch):
    """When the registry has a row with fetch_ok=true, return it without hitting Equasis."""
    row = (
        9321483, "EMMA MAERSK", "Singapore", "SGP", "9VCY3",
        171542, 174239, "Container Ship", 2006, "In Service/Commission",
        "MOLLER SINGAPORE AP PTE LTD", "MAERSK A/S", "MAERSK A/S",
        "American Bureau of Shipping (IACS)", "Britannia",
        10.0, "White", "White", "not targeted",
        _NOW, True,
    )
    client = _make_registry_client(tmp_path, monkeypatch, [row])

    # Patch get_ship_info to fail - should never be called on a registry hit
    monkeypatch.setattr("app.equasis.get_ship_info", lambda imo: None)

    r = client.get("/api/vessels/9321483/equasis")
    assert r.status_code == 200
    body = r.json()
    assert body["ship_name"] == "EMMA MAERSK"
    assert body["gross_tonnage"] == "171542"   # INT stored, returned as str
    assert body["year_built"] == "2006"
    assert body["paris_mou"] == "White"


def test_equasis_registry_miss_fallback(tmp_path, monkeypatch):
    """On registry miss, fall back to live scrape."""
    client = _make_registry_client(tmp_path, monkeypatch, [])  # empty registry

    called = []
    def fake_scrape(imo):
        called.append(imo)
        return {"imo": imo, "ship_name": "FAKE VESSEL"}

    monkeypatch.setattr("app.equasis.get_ship_info", fake_scrape)

    r = client.get("/api/vessels/1234567/equasis")
    assert r.status_code == 200
    assert r.json()["ship_name"] == "FAKE VESSEL"
    assert 1234567 in called


def test_equasis_fetch_ok_false_falls_back(tmp_path, monkeypatch):
    """A row with fetch_ok=false is treated as a miss - live scrape is invoked."""
    row = (
        9999999, None, None, None, None,
        None, None, None, None, None,
        None, None, None, None, None,
        None, None, None, None,
        _NOW, False,  # fetch_ok = false
    )
    client = _make_registry_client(tmp_path, monkeypatch, [row])

    called = []
    def fake_scrape(imo):
        called.append(imo)
        return {"imo": imo, "ship_name": "RECOVERED"}

    monkeypatch.setattr("app.equasis.get_ship_info", fake_scrape)

    r = client.get("/api/vessels/9999999/equasis")
    assert r.status_code == 200
    assert 9999999 in called


def test_equasis_unavailable_503(tmp_path, monkeypatch):
    """503 when both registry and live scrape fail."""
    client = _make_registry_client(tmp_path, monkeypatch, [])
    monkeypatch.setattr("app.equasis.get_ship_info", lambda imo: None)

    r = client.get("/api/vessels/9999998/equasis")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Account-lock detection (the cause of the historical 87% failure rate)
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


def test_is_locked_detects_lock_page():
    from app.equasis import _is_locked, _looks_like_ship_page

    html = (_FIXTURES / "equasis_locked.html").read_text()
    assert _is_locked(html) is True
    # The lock page is the logged-out home, not a populated ship page.
    assert _looks_like_ship_page(html) is False


def test_is_locked_false_on_ship_page():
    from app.equasis import _is_locked, _looks_like_ship_page

    ship_html = "<html><body><b>Gross tonnage</b> 50000 <b>Type of ship</b> Bulk</body></html>"
    assert _is_locked(ship_html) is False
    assert _looks_like_ship_page(ship_html) is True


def test_equasis_endpoint_locked_returns_503(tmp_path, monkeypatch):
    """A locked account raises EquasisAccountLocked; the endpoint must 503, not 500."""
    from app.equasis import EquasisAccountLocked

    client = _make_registry_client(tmp_path, monkeypatch, [])

    def locked(imo):
        raise EquasisAccountLocked()

    monkeypatch.setattr("app.equasis.get_ship_info", locked)
    r = client.get("/api/vessels/9555555/equasis")
    assert r.status_code == 503


def test_crawl_aborts_on_lock(tmp_path, monkeypatch):
    """When Equasis is locked, the crawl stops dead and marks nothing as failed."""
    from app.equasis import EquasisAccountLocked
    from registry import crawl

    ais_file = tmp_path / "ais.duckdb"
    ac = duckdb.connect(str(ais_file))
    ac.execute("CREATE TABLE live_positions (mmsi BIGINT, imo BIGINT)")
    ac.executemany("INSERT INTO live_positions VALUES (?,?)",
                   [(111, 9111111), (222, 9222222), (333, 9333333)])
    ac.close()

    reg_file = tmp_path / "registry.duckdb"

    # Stub out network-touching deps: OFAC list + event counts.
    monkeypatch.setattr("registry.ofac.fetch_sanctioned_imos", lambda: set())

    calls = []

    def locked(imo):
        calls.append(imo)
        raise EquasisAccountLocked()

    monkeypatch.setattr("registry.crawl.get_ship_info", locked)

    crawl.run(ais_path=ais_file, reg_path=reg_file, limit=50)

    # Aborted after the very first fetch raised; no rows written (nothing marked failed).
    assert len(calls) == 1
    rc = duckdb.connect(str(reg_file))
    n = rc.execute("SELECT COUNT(*) FROM vessel_registry").fetchone()[0]
    rc.close()
    assert n == 0
