"""Tests for the MyShipTracking scraper + crawler persistence.

Parsing is verified against a saved real vessel page (tests/fixtures/mst_happy_lady.html)
so no network access is needed. The crawler persistence + dedup is verified against a
temp DuckDB using the parsed snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from app import myshiptracking as mst
from registry import crawl_mst as cm

_FIXTURE = Path(__file__).parent / "fixtures" / "mst_happy_lady.html"


@pytest.fixture
def html() -> str:
    return _FIXTURE.read_text()


@pytest.fixture
def snap(html) -> mst.VesselSnapshot:
    return mst.parse(html, mmsi=241281000)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def test_page_detection(html):
    assert mst.looks_like_vessel_page(html)
    assert not mst.is_blocked(html)  # login recaptcha must not read as a bot-wall


def test_particulars(snap):
    assert snap.name == "HAPPY LADY"
    assert snap.imo == 9644225
    assert snap.mmsi == 241281000
    assert snap.flag == "Greece"
    assert snap.call_sign == "SVBV3"
    assert snap.ship_type == "Oil/Chemical Tanker"
    assert snap.length_m == 183.0
    assert snap.beam_m == 32.0
    assert snap.gross_tonnage == 30201
    assert snap.dwt == 51390
    assert snap.year_built == 2013


def test_live_state(snap):
    # nav status from the position table, NOT the "Active" registry status
    assert snap.status == "At anchor"
    assert snap.course == 213.0
    assert snap.area == "Balearic Sea"
    assert snap.station == "T-AIS"
    assert snap.draught_m == 11.1
    assert snap.destination == "BARCELONA"
    assert snap.eta == "2026-06-29 21:30"
    assert snap.position_received_utc == "2026-06-29 22:19"


def test_latlon_leak_from_contributor_map(snap):
    # position table masks coords as '---'; they leak in contributorMap.php?lat=&lng=
    assert snap.lat == pytest.approx(41.31649)
    assert snap.lon == pytest.approx(2.21184)


def test_voyages(snap):
    assert len(snap.voyages) >= 1
    v = snap.voyages[0]
    assert v.origin == "MOHAMMEDIA"
    assert v.destination == "MERSIN"
    assert v.departure == "2026-04-26 23:01"
    assert v.arrival == "2026-05-29 03:02"
    assert v.distance_nm == pytest.approx(8136.8)
    assert v.draught_m == 11.6
    assert v.avg_speed_kn == 13.6
    assert v.stops == 23
    assert v.key() == "MOHAMMEDIA|2026-04-26 23:01|MERSIN"


def test_port_calls(snap):
    assert len(snap.port_calls) >= 1
    p = snap.port_calls[0]
    assert p.port == "MERSIN"
    assert p.arrival and p.departure


def test_parse_without_mmsi_recovers_it_from_table(html):
    s = mst.parse(html)  # no mmsi passed
    assert s.mmsi == 241281000


# --------------------------------------------------------------------------- #
# persistence + dedup
# --------------------------------------------------------------------------- #
@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "mst.duckdb"))
    c.execute(cm._SCHEMA)
    yield c
    c.close()


def test_persist_then_dedup(conn, snap):
    now = datetime.now(UTC).replace(tzinfo=None)
    nv, nc = cm._persist(conn, snap, now)
    assert nv == len(snap.voyages)
    assert nc == len(snap.port_calls)

    # re-persisting the same snapshot adds nothing (immutable history)
    nv2, nc2 = cm._persist(conn, snap, now)
    assert nv2 == 0
    assert nc2 == 0

    assert conn.execute("SELECT count(*) FROM mst_voyages").fetchone()[0] == len(snap.voyages)
    assert conn.execute("SELECT count(*) FROM mst_vessel_state").fetchone()[0] == 1


def test_state_overwritten_on_refresh(conn, snap):
    now = datetime.now(UTC).replace(tzinfo=None)
    cm._persist(conn, snap, now)
    # simulate a later visit where the live destination changed
    snap.destination = "VALENCIA"
    cm._persist(conn, snap, now)
    dest = conn.execute("SELECT destination FROM mst_vessel_state WHERE mmsi = ?", [snap.mmsi]).fetchone()[0]
    assert dest == "VALENCIA"
    # but history is unchanged (still one row per trip)
    assert conn.execute("SELECT count(*) FROM mst_vessel_state").fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# crawler priority
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture
def mst_client(tmp_path, monkeypatch, snap):
    """TestClient with a temp mst.duckdb seeded from the parsed fixture snapshot."""
    from fastapi.testclient import TestClient

    mst_file = tmp_path / "mst.duckdb"
    c = duckdb.connect(str(mst_file))
    c.execute(cm._SCHEMA)
    cm._persist(c, snap, datetime.now(UTC).replace(tzinfo=None))
    c.close()

    monkeypatch.setenv("MST_DB", str(mst_file))
    monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing.duckdb"))
    from app.main import app

    return TestClient(app)


def test_endpoint_serves_persisted(mst_client):
    r = mst_client.get("/api/vessels/241281000/myshiptracking")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "HAPPY LADY"
    assert body["dwt"] == 51390
    assert body["destination"] == "BARCELONA"
    assert len(body["voyages"]) >= 1
    assert len(body["port_calls"]) >= 1
    assert body["voyages"][0]["origin"]


def test_endpoint_404_when_not_crawled(mst_client):
    assert mst_client.get("/api/vessels/999999999/myshiptracking").status_code == 404


def test_priority_never_scraped_first():
    import pandas as pd

    now = datetime.now(UTC).replace(tzinfo=None)
    state = pd.DataFrame({"mmsi": [100], "fetched_ts": [now]})  # 100 already fresh
    order = cm.priority_order([100, 200, 300], state, now, limit=10)
    # 100 is fresh -> excluded; 200, 300 never scraped -> included
    assert 100 not in order
    assert set(order) == {200, 300}
