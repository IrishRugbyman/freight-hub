"""Endpoint tests for freight-api against a seeded temp DuckDB (see conftest)."""

from __future__ import annotations

import pytest


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tracked"] == 4  # 4 fresh, 1 stale excluded
    assert body["last_update"] is not None


def test_vessels_excludes_stale(client):
    r = client.get("/api/vessels")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 4
    assert "STALE CAPE" not in {v["name"] for v in rows}


def test_vessels_have_rich_fields(client):
    cape_a = next(v for v in client.get("/api/vessels").json() if v["name"] == "CAPE A")
    assert cape_a["segment"] == "Capesize"
    assert cape_a["kind"] == "bulk"
    assert cape_a["sog"] == 12.0
    assert cape_a["destination"] == "CNSHA"


def test_vessels_new_fields_round_trip(client):
    vessels = client.get("/api/vessels").json()
    vlcc = next(v for v in vessels if v["name"] == "VLCC A")
    assert vlcc["imo"] == 9876543
    assert vlcc["draught"] == 20.5
    assert vlcc["nav_status"] == 0
    assert vlcc["eta"] == "06-20 06:00"
    # vessel with no new fields returns None
    coaster = next(v for v in vessels if v["name"] == "COASTER")
    assert coaster["imo"] is None
    assert coaster["draught"] is None
    assert coaster["nav_status"] is None
    assert coaster["eta"] is None


def test_filter_kind(client):
    rows = client.get("/api/vessels", params={"kind": "tanker"}).json()
    assert len(rows) == 1
    assert rows[0]["name"] == "VLCC A"


def test_filter_segment(client):
    rows = client.get("/api/vessels", params={"segment": "Capesize"}).json()
    assert len(rows) == 2  # both fresh capes; stale one excluded


def test_filter_region(client):
    rows = client.get("/api/vessels", params={"region": "singapore_malacca"}).json()
    assert {v["name"] for v in rows} == {"CAPE A", "CAPE B"}


def test_chokepoints(client):
    by_region = {c["region"]: c for c in client.get("/api/chokepoints").json()}
    assert by_region["singapore_malacca"]["total"] == 2
    assert by_region["hormuz"]["total"] == 1
    assert by_region["suez"]["total"] == 0  # only a stale vessel was there
    assert by_region["singapore_malacca"]["by_segment"]["Capesize"] == 2
    assert by_region["singapore_malacca"]["bbox"] == [[-2.0, 100.0], [6.0, 105.5]]


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["total_tracked"] == 4
    assert set(m["segments"]) == {"Capesize", "VLCC", "Small"}
    assert set(m["kinds"]) == {"bulk", "tanker"}
    assert "singapore_malacca" in m["regions"]


def test_empty_db_resilience(empty_client):
    assert empty_client.get("/api/health").json()["tracked"] == 0
    assert empty_client.get("/api/vessels").json() == []
    assert empty_client.get("/api/meta").json()["total_tracked"] == 0
    # chokepoints still returns one row per region, all zero
    cps = empty_client.get("/api/chokepoints").json()
    assert all(c["total"] == 0 for c in cps)


def test_track_returns_ordered_points(client_with_snaps):
    r = client_with_snaps.get("/api/vessels/1003/track")
    assert r.status_code == 200
    pts = r.json()
    # default 24h window: 3 points (the 30h-old one is excluded)
    assert len(pts) == 3
    # ordered by snapshot_ts ascending
    assert pts[0]["lat"] < pts[-1]["lat"]
    assert pts[0]["sog"] == 14.0


def test_track_hours_clamping(client_with_snaps):
    # hours=48 should include the 30h-old snapshot
    pts_48 = client_with_snaps.get("/api/vessels/1003/track?hours=48").json()
    assert len(pts_48) == 4
    # hours beyond 336 clamped to 336 (still 4 points in our seed)
    pts_huge = client_with_snaps.get("/api/vessels/1003/track?hours=9999").json()
    assert len(pts_huge) == 4


def test_track_empty_for_unknown_vessel(client_with_snaps):
    pts = client_with_snaps.get("/api/vessels/99999/track").json()
    assert pts == []


def test_routes_serves_static(client, static_routes_json):
    r = client.get("/api/routes")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "test_routes"
    assert body["n_open"] == 1
    assert len(body["routes"]) == 1
    assert body["routes"][0]["id"] == "rt1"
    assert body["routes"][0]["status"] == "open"


def test_dispersion_serves_static(client, static_dispersion_json):
    r = client.get("/api/dispersion")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "test_disp"
    assert body["stats"]["sharpe"] == 0.76
    assert len(body["equity"]) == 2


def test_dispersion_live(client):
    r = client.get("/api/dispersion/live")
    assert r.status_code == 200
    rows = r.json()
    # Either the live collector has data (non-empty list) or it has not run yet (empty).
    # Either way the response shape must be valid.
    for row in rows:
        assert "date" in row
        assert "segment" in row
        assert "dispersion_nm" in row
        assert "vessel_count" in row


# ---------------------------------------------------------------------------
# Analytics endpoints
# ---------------------------------------------------------------------------


def test_analytics_transits_empty_when_no_analytics_db(client):
    # With no analytics DB injected, the endpoint returns an empty series gracefully.
    r = client.get("/api/analytics/transits", params={"chokepoint": "hormuz"})
    assert r.status_code == 200
    body = r.json()
    assert body["chokepoint"] == "hormuz"
    assert body["series"] == []


def test_analytics_transits_with_data(analytics_client):
    r = analytics_client.get("/api/analytics/transits", params={"chokepoint": "hormuz", "days": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["chokepoint"] == "hormuz"
    assert len(body["series"]) >= 1
    first = body["series"][0]
    assert "date" in first
    assert "direction" in first
    assert "count" in first
    assert first["direction"] == "outbound"
    assert first["count"] == 1


def test_analytics_congestion_with_data(analytics_client):
    r = analytics_client.get("/api/analytics/congestion", params={"zone": "singapore_east", "days": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["zone"] == "singapore_east"
    assert len(body["series"]) >= 1
    row = body["series"][0]
    assert row["vessel_count"] == 1
    assert row["median_dwell_hours"] == pytest.approx(4.0, abs=0.1)


def test_analytics_density_with_data(analytics_client):
    r = analytics_client.get("/api/analytics/density", params={"region": "hormuz", "days": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["region"] == "hormuz"
    assert len(body["series"]) >= 1
    day = body["series"][0]
    assert "laden_count" in day
    assert "ballast_count" in day
    assert "unknown_count" in day


def test_analytics_laden_with_data(analytics_client):
    r = analytics_client.get("/api/analytics/laden", params={"kind": "tanker"})
    assert r.status_code == 200
    body = r.json()
    assert "segments" in body


def test_analytics_zones(analytics_client):
    r = analytics_client.get("/api/analytics/zones")
    assert r.status_code == 200
    zones = r.json()
    names = {z["name"] for z in zones}
    assert "singapore_east" in names   # anchorage zone
    assert "hormuz" in names           # chokepoint region
    types = {z["type"] for z in zones}
    assert "anchorage" in types
    assert "chokepoint" in types


def test_analytics_days_clamped(analytics_client):
    # days=500 should be clamped to 365
    r = analytics_client.get("/api/analytics/transits?chokepoint=suez&days=500")
    assert r.status_code == 200
    assert r.json()["days"] == 365


# --- /api/events tests -------------------------------------------------------

def test_events_all(analytics_client):
    r = analytics_client.get("/api/events")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert "total" in body
    assert body["total"] == 3
    types = {e["type"] for e in body["events"]}
    assert types == {"gap", "loiter", "sts"}


def test_events_filter_by_type(analytics_client):
    r = analytics_client.get("/api/events", params={"type": "gap"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["events"][0]["type"] == "gap"
    assert body["events"][0]["mmsi"] == 1001


def test_events_sts_has_mmsi2(analytics_client):
    r = analytics_client.get("/api/events", params={"type": "sts"})
    assert r.status_code == 200
    ev = r.json()["events"][0]
    assert ev["mmsi"] == 1003
    assert ev["mmsi2"] == 1004
    assert "duration_hours" in ev["details"]


def test_events_details_parsed(analytics_client):
    r = analytics_client.get("/api/events", params={"type": "loiter"})
    assert r.status_code == 200
    ev = r.json()["events"][0]
    assert isinstance(ev["details"], dict)
    assert "duration_hours" in ev["details"]


def test_events_limit_clamped(analytics_client):
    r = analytics_client.get("/api/events", params={"limit": 1000})
    assert r.status_code == 200  # 1000 clamped to 500, still returns OK


def test_events_empty_outside_days(analytics_client):
    # days=0 should be clamped to 1; seeded events are within last 1 day so still return
    r = analytics_client.get("/api/events", params={"type": "gap", "days": 1})
    assert r.status_code == 200


# ---- /api/feed.xml + /api/feed.json (syndication) ----

def test_feed_atom_well_formed(analytics_client):
    import xml.etree.ElementTree as ET

    r = analytics_client.get("/api/feed.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/atom+xml")
    root = ET.fromstring(r.text)  # raises on malformed XML
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    # seed has gap + loiter + sts (all high-risk), no reroute
    assert len(entries) == 3
    titles = [e.findtext("a:title", default="", namespaces=ns) for e in entries]
    assert any("STS Candidate" in t for t in titles)
    # entry ids are stable urns built from event_id
    ids = [e.findtext("a:id", default="", namespaces=ns) for e in entries]
    assert any(i.startswith("urn:freight-event:") for i in ids)


def test_feed_atom_excludes_reroute_by_default(analytics_client):
    # seed has no reroute; assert the default filter is the high-risk set, not all
    from app.feed import HIGH_RISK_TYPES

    assert "reroute" not in HIGH_RISK_TYPES
    r = analytics_client.get("/api/feed.xml")
    assert "reroute" not in r.text


def test_feed_types_override(analytics_client):
    import xml.etree.ElementTree as ET

    r = analytics_client.get("/api/feed.xml", params={"types": "sts"})
    assert r.status_code == 200
    root = ET.fromstring(r.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    assert len(entries) == 1
    assert "STS Candidate" in entries[0].findtext("a:title", default="", namespaces=ns)


def test_feed_json_structure(analytics_client):
    r = analytics_client.get("/api/feed.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/feed+json")
    doc = r.json()
    assert doc["version"] == "https://jsonfeed.org/version/1.1"
    assert doc["title"]
    assert isinstance(doc["items"], list)
    assert len(doc["items"]) == 3
    item = doc["items"][0]
    for field in ("id", "url", "title", "date_published"):
        assert field in item
    assert item["url"].startswith("https://freight.lbzgiu.xyz/?mmsi=")


def test_feed_empty_db_valid(client, tmp_path, monkeypatch):
    # Point analytics DB at a missing file -> empty event set -> still a valid empty feed.
    # (analytics_db_path reads ANALYTICS_DB at request time, so post-fixture setenv works.)
    import xml.etree.ElementTree as ET

    monkeypatch.setenv("ANALYTICS_DB", str(tmp_path / "missing_analytics.duckdb"))
    r = client.get("/api/feed.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    assert root.findall("a:entry", ns) == []
    rj = client.get("/api/feed.json")
    assert rj.status_code == 200
    assert rj.json()["items"] == []


# ---- /api/vessels/{mmsi}/state ----

def test_vessel_state_known(analytics_client):
    r = analytics_client.get("/api/vessels/1003/state")
    assert r.status_code == 200
    d = r.json()
    assert d["mmsi"] == 1003
    assert d["laden"] == "laden"
    assert d["last_draught"] == 20.5
    assert d["max_draught_seen"] == 22.0
    assert d["updated_ts"] is not None


def test_vessel_state_unknown_returns_null(analytics_client):
    r = analytics_client.get("/api/vessels/9999/state")
    assert r.status_code == 200
    assert r.json() is None


# ---- /api/vessels/{mmsi}/voyages ----

def test_vessel_voyages_returns_structure(analytics_client):
    r = analytics_client.get("/api/vessels/1003/voyages", params={"days": 30})
    assert r.status_code == 200
    d = r.json()
    assert d["mmsi"] == 1003
    assert isinstance(d["events"], list)


def test_vessel_voyages_includes_transit(analytics_client):
    r = analytics_client.get("/api/vessels/1003/voyages", params={"days": 30})
    assert r.status_code == 200
    events = r.json()["events"]
    transit_events = [e for e in events if e["type"] == "transit"]
    assert len(transit_events) >= 1
    te = transit_events[0]
    assert te["zone"] == "hormuz"
    assert te["direction"] == "outbound"
    assert te["laden"] is True


def test_vessel_voyages_includes_port_call(analytics_client):
    r = analytics_client.get("/api/vessels/1002/voyages", params={"days": 30})
    assert r.status_code == 200
    events = r.json()["events"]
    port_calls = [e for e in events if e["type"] == "port_call"]
    assert len(port_calls) >= 1
    pc = port_calls[0]
    assert pc["zone"] == "singapore_east"
    assert pc["dwell_hours"] is not None and pc["dwell_hours"] > 0


def test_vessel_voyages_sorted_by_ts(analytics_client):
    r = analytics_client.get("/api/vessels/1003/voyages", params={"days": 30})
    events = r.json()["events"]
    ts_list = [e["ts"] for e in events]
    assert ts_list == sorted(ts_list)


def test_vessel_voyages_days_clamped(analytics_client):
    r = analytics_client.get("/api/vessels/1003/voyages", params={"days": 200})
    assert r.status_code == 200  # clamped to 90


def test_vessel_voyages_unknown_mmsi(analytics_client):
    r = analytics_client.get("/api/vessels/9999/voyages")
    assert r.status_code == 200
    assert r.json()["events"] == []


# ---- /api/analytics/ports ----

def test_analytics_ports_structure(analytics_client):
    r = analytics_client.get("/api/analytics/ports")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "total_with_dest" in d
    assert isinstance(d["ports"], list)


def test_analytics_ports_counts_destinations(analytics_client):
    r = analytics_client.get("/api/analytics/ports")
    assert r.status_code == 200
    d = r.json()
    # Seed has vessels with destinations: CNSHA (bulk), AEFJR (tanker), SGSIN (bulk)
    # STALE vessel (EGPSD) should be excluded
    dests = {p["destination"] for p in d["ports"]}
    assert "CNSHA" in dests
    assert "AEFJR" in dests
    # Stale vessel excluded
    assert "EGPSD" not in dests


def test_analytics_ports_kind_filter(analytics_client):
    r = analytics_client.get("/api/analytics/ports", params={"kind": "tanker"})
    assert r.status_code == 200
    d = r.json()
    for port in d["ports"]:
        assert port["bulkers"] == 0


def test_analytics_ports_top_n_clamped(analytics_client):
    r = analytics_client.get("/api/analytics/ports", params={"top_n": 1})
    assert r.status_code == 200
    # top_n=1 is clamped to 5, so <=5 ports returned
    assert len(r.json()["ports"]) <= 5


# ---- /api/analytics/speed ----

def test_analytics_speed_structure(client):
    r = client.get("/api/analytics/speed")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "total_vessels" in d
    assert isinstance(d["rows"], list)
    for row in d["rows"]:
        assert "segment" in row
        assert "kind" in row
        assert "underway" in row
        assert "anchored" in row
        assert "moored" in row
        assert "total" in row
        assert "pct_underway" in row


def test_analytics_speed_counts(client):
    r = client.get("/api/analytics/speed")
    assert r.status_code == 200
    rows = {f"{row['kind']}-{row['segment']}": row for row in r.json()["rows"]}
    # CAPE A nav=0, CAPE B nav=1 -> Capesize bulk: underway=1, anchored=1
    capes = rows.get("bulk-Capesize")
    assert capes is not None
    assert capes["underway"] == 1
    assert capes["anchored"] == 1
    assert capes["total"] == 2
    # VLCC A nav=0 -> tanker VLCC: underway=1
    vlcc = rows.get("tanker-VLCC")
    assert vlcc is not None
    assert vlcc["underway"] == 1
    assert vlcc["total"] == 1


def test_analytics_speed_avg_sog(client):
    r = client.get("/api/analytics/speed")
    d = r.json()
    rows = {f"{row['kind']}-{row['segment']}": row for row in d["rows"]}
    vlcc = rows.get("tanker-VLCC")
    assert vlcc is not None
    # VLCC SOG = 14.0 kn, nav=0
    assert vlcc["avg_sog_underway"] == pytest.approx(14.0, abs=0.2)


def test_analytics_speed_total_vessels(client):
    r = client.get("/api/analytics/speed")
    d = r.json()
    # 4 fresh vessels (stale excluded): CAPE A, CAPE B, VLCC A, COASTER
    # COASTER has region=None but kind=bulk -> included (kind IS NOT NULL)
    assert d["total_vessels"] == 4


# ---- /api/analytics/region-util ----

def test_region_util_structure(client):
    r = client.get("/api/analytics/region-util")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert isinstance(d["rows"], list)
    for row in d["rows"]:
        assert "region" in row
        assert "total" in row
        assert "underway" in row
        assert "anchored" in row
        assert "pct_underway" in row


def test_region_util_singapore(client):
    r = client.get("/api/analytics/region-util")
    d = r.json()
    rows = {row["region"]: row for row in d["rows"]}
    # CAPE A and CAPE B are in singapore_malacca; COASTER has region=None (excluded)
    sg = rows.get("singapore_malacca")
    assert sg is not None
    assert sg["total"] == 2
    # CAPE A nav=0, CAPE B nav=1
    assert sg["underway"] == 1
    assert sg["anchored"] == 1


def test_region_util_excludes_null_region(client):
    r = client.get("/api/analytics/region-util")
    d = r.json()
    regions = {row["region"] for row in d["rows"]}
    # COASTER has region=None -> not in results
    assert "None" not in regions
    assert None not in regions


# ---- /api/analytics/speed-trend ----

def test_speed_trend_structure(analytics_client):
    r = analytics_client.get("/api/analytics/speed-trend?kind=tanker&segment=VLCC")
    assert r.status_code == 200
    d = r.json()
    assert d["kind"] == "tanker"
    assert d["segment"] == "VLCC"
    assert "days" in d
    assert isinstance(d["series"], list)
    for pt in d["series"]:
        assert "date" in pt
        assert "avg_sog" in pt
        assert "underway_count" in pt
        assert "total_count" in pt


def test_speed_trend_returns_data(analytics_client):
    # _SNAP_SEED has VLCC snapshots: 3 at SOG=14.0 today, 1 at SOG=12.0 yesterday (30h ago)
    # Both days should appear in the 30-day window
    r = analytics_client.get("/api/analytics/speed-trend?kind=tanker&segment=VLCC&days=30")
    assert r.status_code == 200
    d = r.json()
    assert len(d["series"]) >= 1
    # All data points should be underway VLCCs (nav=0)
    for pt in d["series"]:
        assert pt["underway_count"] > 0
        # SOG values are either 14.0 or 12.0 depending on the day
        if pt["avg_sog"] is not None:
            assert 11.0 <= pt["avg_sog"] <= 15.0


def test_speed_trend_filters_by_kind(analytics_client):
    r = analytics_client.get("/api/analytics/speed-trend?kind=bulk&days=30")
    assert r.status_code == 200
    d = r.json()
    assert d["kind"] == "bulk"
    # No bulk snapshots in seed -> empty series
    assert d["series"] == []


def test_speed_trend_days_clamped(analytics_client):
    r = analytics_client.get("/api/analytics/speed-trend?kind=tanker&days=200")
    assert r.status_code == 200
    # days clamped to 90
    assert r.json()["days"] == 90


# ---- /api/analytics/sts-risk ------------------------------------------------


def test_sts_risk_structure(analytics_client):
    r = analytics_client.get("/api/analytics/sts-risk?days=30")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "days", "total_events", "enriched_events", "rows"):
        assert key in d
    # conftest seeds one STS event (sts0000001)
    assert d["total_events"] >= 1


def test_sts_risk_event_fields(analytics_client):
    r = analytics_client.get("/api/analytics/sts-risk?days=30")
    d = r.json()
    assert len(d["rows"]) >= 1
    row = next(r for r in d["rows"] if r["event_id"] == "sts0000001")
    assert row["mmsi"] == 1003
    assert row["mmsi2"] == 1004
    assert row["region"] == "hormuz"
    assert row["kind"] == "tanker"
    assert row["duration_hours"] == pytest.approx(2.0, abs=0.1)
    assert row["co_location_fixes"] == 12
    assert row["max_risk"] >= 0


def test_sts_risk_sorted_by_max_risk(analytics_client):
    r = analytics_client.get("/api/analytics/sts-risk?days=30")
    rows = r.json()["rows"]
    risks = [row["max_risk"] for row in rows]
    assert risks == sorted(risks, reverse=True)


def test_sts_risk_days_clamped(analytics_client):
    r = analytics_client.get("/api/analytics/sts-risk?days=200")
    assert r.status_code == 200
    assert r.json()["days"] == 90


def test_sts_risk_min_risk_filter(analytics_client):
    r = analytics_client.get("/api/analytics/sts-risk?days=30&min_risk=999")
    assert r.status_code == 200
    # No vessel has risk_score >= 999 -> no rows
    assert r.json()["rows"] == []


# ---- /api/analytics/reroutes ------------------------------------------------


@pytest.fixture
def reroute_client(tmp_path, monkeypatch):
    """Client with AIS DB + analytics DB seeded with reroute events."""
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    _now = datetime.now(UTC).replace(tzinfo=None)

    ais_schema = """
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt DOUBLE, grt DOUBLE, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP,
        equasis_ok BOOLEAN
    );
    CREATE TABLE ais_snapshots (
        snapshot_ts TIMESTAMP, mmsi BIGINT,
        kind VARCHAR, segment VARCHAR, region VARCHAR,
        lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
        sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
        PRIMARY KEY (snapshot_ts, mmsi)
    );
    """

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (5001,'TANKER X',25.0,56.0,14.0,270.0,271.0,'AEFJR',80,330,'tanker','VLCC','hormuz',?,9001001,20.0,0,NULL)",
        [_now],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    an_conn.execute(
        "INSERT INTO ais_events VALUES ('rr0000001','reroute',5001,NULL,?,?,25.0,56.0,'hormuz','tanker','VLCC',?)",
        [_now - timedelta(hours=5), _now - timedelta(hours=5),
         '{"old_destination":"AEFJR","new_destination":"PKQCT","fixes_at_old":40}'],
    )
    an_conn.execute(
        "INSERT INTO ais_events VALUES ('rr0000002','reroute',5001,NULL,?,?,25.1,56.1,'hormuz','tanker','VLCC',?)",
        [_now - timedelta(hours=2), _now - timedelta(hours=2),
         '{"old_destination":"PKQCT","new_destination":"IRBIK","fixes_at_old":25}'],
    )
    an_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    from app.main import app
    return TestClient(app)


def test_reroutes_structure(reroute_client):
    r = reroute_client.get("/api/analytics/reroutes?days=7")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "days", "total_events", "rows"):
        assert key in d
    assert d["total_events"] == 2


def test_reroutes_event_fields(reroute_client):
    r = reroute_client.get("/api/analytics/reroutes?days=7")
    rows = {row["event_id"]: row for row in r.json()["rows"]}
    assert "rr0000001" in rows
    row = rows["rr0000001"]
    assert row["mmsi"] == 5001
    assert row["old_destination"] == "AEFJR"
    assert row["new_destination"] == "PKQCT"
    assert row["fixes_at_old"] == 40
    assert row["region"] == "hormuz"
    assert row["kind"] == "tanker"


def test_reroutes_segment_filter(reroute_client):
    r = reroute_client.get("/api/analytics/reroutes?days=7&segment=VLCC")
    assert r.status_code == 200
    assert r.json()["total_events"] == 2

    r2 = reroute_client.get("/api/analytics/reroutes?days=7&segment=Capesize")
    assert r2.status_code == 200
    assert r2.json()["total_events"] == 0


def test_reroutes_days_clamped(reroute_client):
    r = reroute_client.get("/api/analytics/reroutes?days=200")
    assert r.status_code == 200
    assert r.json()["days"] == 90


# ---- /api/analytics/transit-risk --------------------------------------------


def test_transit_risk_structure(analytics_client):
    r = analytics_client.get("/api/analytics/transit-risk?chokepoint=hormuz&days=30")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "days", "chokepoint", "total_transits", "enriched", "rows"):
        assert key in d
    assert d["chokepoint"] == "hormuz"
    # conftest seeds one hormuz transit (mmsi 1003, VLCC A)
    assert d["total_transits"] >= 1


def test_transit_risk_event_fields(analytics_client):
    r = analytics_client.get("/api/analytics/transit-risk?chokepoint=hormuz&days=30")
    rows = r.json()["rows"]
    assert len(rows) >= 1
    row = next(r for r in rows if r["mmsi"] == 1003)
    assert row["direction"] == "outbound"
    assert row["kind"] == "tanker"
    assert row["segment"] == "VLCC"
    assert row["laden"] is True


def test_transit_risk_wrong_chokepoint(analytics_client):
    r = analytics_client.get("/api/analytics/transit-risk?chokepoint=bosphorus&days=30")
    assert r.status_code == 200
    assert r.json()["total_transits"] == 0
    assert r.json()["rows"] == []


def test_transit_risk_min_risk_filter(analytics_client):
    r = analytics_client.get("/api/analytics/transit-risk?chokepoint=hormuz&days=30&min_risk=999")
    assert r.status_code == 200
    # No vessel has risk_score >= 999
    assert r.json()["rows"] == []


def test_transit_risk_days_clamped(analytics_client):
    r = analytics_client.get("/api/analytics/transit-risk?days=200")
    assert r.status_code == 200
    assert r.json()["days"] == 90


# ---- /api/analytics/anchorage-dwell -----------------------------------------


@pytest.fixture
def dwell_client(tmp_path, monkeypatch):
    """Client with an open anchored episode at singapore_west."""
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    _now = datetime.now(UTC).replace(tzinfo=None)

    ais_schema = """
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt DOUBLE, grt DOUBLE, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP,
        equasis_ok BOOLEAN
    );
    CREATE TABLE ais_snapshots (
        snapshot_ts TIMESTAMP, mmsi BIGINT,
        kind VARCHAR, segment VARCHAR, region VARCHAR,
        lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
        sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
        PRIMARY KEY (snapshot_ts, mmsi)
    );
    """

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (6001,'VLCC ANCHOR',1.2,103.6,0.0,0.0,NULL,NULL,80,330,'tanker','VLCC','singapore_malacca',?,9002001,20.0,1,NULL)",
        [_now],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    # Episodes are always CLOSED (the job never leaves end_ts NULL); a vessel is
    # "currently anchored" when its latest episode ends within ~2h of the freshest
    # episode. 6001 still anchored 12h (ends now); 6002 still anchored 4h (ends now).
    an_conn.execute(
        "INSERT INTO anchored_episodes VALUES (6001,'singapore_west',?,?,'tanker','VLCC')",
        [_now - timedelta(hours=12), _now],
    )
    an_conn.execute(
        "INSERT INTO anchored_episodes VALUES (6002,'singapore_west',?,?,'bulk','Capesize')",
        [_now - timedelta(hours=4), _now],
    )
    # Departed episode (ended 5h ago, > 2h window) - should NOT appear as current.
    an_conn.execute(
        "INSERT INTO anchored_episodes VALUES (6003,'singapore_west',?,?,'tanker','Aframax')",
        [_now - timedelta(hours=20), _now - timedelta(hours=5)],
    )
    an_conn.execute("INSERT INTO vessel_state VALUES (6001, 22.0, 20.0, 'laden', ?)", [_now])
    an_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    from app.main import app
    return TestClient(app)


def test_anchorage_dwell_structure(dwell_client):
    r = dwell_client.get("/api/analytics/anchorage-dwell?zone=singapore_west")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "zone" in d
    assert d["zone"] == "singapore_west"
    assert "rows" in d
    for row in d["rows"]:
        for key in ("mmsi", "name", "zone", "kind", "segment", "start_ts",
                    "dwell_hours", "laden", "risk_score", "ofac"):
            assert key in row


def test_anchorage_dwell_only_current(dwell_client):
    """Only currently-anchored vessels appear; departed vessel (6003) excluded."""
    r = dwell_client.get("/api/analytics/anchorage-dwell?zone=singapore_west")
    d = r.json()
    mmsis = {row["mmsi"] for row in d["rows"]}
    assert 6003 not in mmsis
    assert 6001 in mmsis or 6002 in mmsis


def test_anchorage_dwell_sorted_longest_first(dwell_client):
    """Rows sorted by dwell_hours descending."""
    r = dwell_client.get("/api/analytics/anchorage-dwell?zone=singapore_west")
    rows = r.json()["rows"]
    if len(rows) >= 2:
        assert rows[0]["dwell_hours"] >= rows[1]["dwell_hours"]
    # VLCC (6001, 12h) should be before bulker (6002, 4h)
    mmsi_order = [row["mmsi"] for row in rows]
    if 6001 in mmsi_order and 6002 in mmsi_order:
        assert mmsi_order.index(6001) < mmsi_order.index(6002)


def test_anchorage_dwell_laden_state(dwell_client):
    r = dwell_client.get("/api/analytics/anchorage-dwell?zone=singapore_west")
    vlcc = next((r for r in r.json()["rows"] if r["mmsi"] == 6001), None)
    if vlcc:
        assert vlcc["laden"] == "laden"
        assert vlcc["name"] == "VLCC ANCHOR"


def test_anchorage_dwell_wrong_zone(dwell_client):
    r = dwell_client.get("/api/analytics/anchorage-dwell?zone=galveston_ltg")
    assert r.status_code == 200
    assert r.json()["rows"] == []


# ---------------------------------------------------------------------------
# Phase 22: cargo transition detection (loading / discharge events)
# ---------------------------------------------------------------------------

@pytest.fixture
def cargo_client(tmp_path, monkeypatch):
    """AIS DB seeded with two vessels showing clear draught step-changes.

    Vessel 7001 (LOADING VLCC): draught 8m in early bucket, 20m in late bucket.
    Vessel 7002 (DISCHARGING BULK): draught 18m early, 7m late.
    Vessel 7003 (STABLE): draught 5m throughout - no transition.
    All snapshots placed across two distinct 6h buckets (15h apart) to guarantee
    bucket separation regardless of when the tests run.
    """
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    _now = datetime.now(UTC).replace(tzinfo=None)

    # Two groups: early (23-20h ago) and late (5-2h ago) - always in different 6h buckets
    early = [_now - timedelta(hours=h) for h in (23, 22, 21, 20)]
    late = [_now - timedelta(hours=h) for h in (5, 4, 3, 2)]

    ais_schema = """
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt DOUBLE, grt DOUBLE, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP,
        equasis_ok BOOLEAN
    );
    CREATE TABLE ais_snapshots (
        snapshot_ts TIMESTAMP, mmsi BIGINT,
        kind VARCHAR, segment VARCHAR, region VARCHAR,
        lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
        sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
        PRIMARY KEY (snapshot_ts, mmsi)
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute(ais_schema)
    conn.execute(
        "INSERT INTO live_positions VALUES (7001,'LOADING VLCC',25.0,56.0,0.0,0.0,NULL,'DUBAI',80,330,'tanker','VLCC','hormuz',?,9001001,8.0,1,NULL)",
        [_now],
    )
    conn.execute(
        "INSERT INTO live_positions VALUES (7002,'DISCH BULK',1.3,103.7,0.5,0.0,NULL,'SINGAPORE',71,200,'bulk','Capesize','singapore_malacca',?,9001002,7.0,1,NULL)",
        [_now],
    )
    conn.execute(
        "INSERT INTO live_positions VALUES (7003,'STABLE COASTER',51.9,4.5,3.0,0.0,NULL,'ROTTERDAM',80,90,'tanker','MR','ara',?,NULL,5.0,0,NULL)",
        [_now],
    )

    # Vessel 7001: loading (8m -> 20m)
    for ts in early:
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,7001,'tanker','VLCC','hormuz',25.0,56.0,80,330,0.5,1,8.0,'DUBAI')",
            [ts],
        )
    for ts in late:
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,7001,'tanker','VLCC','hormuz',25.1,56.1,80,330,0.3,1,20.0,'FUJAIRAH')",
            [ts],
        )

    # Vessel 7002: discharging (18m -> 7m)
    for ts in early:
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,7002,'bulk','Capesize','singapore_malacca',1.3,103.7,71,200,0.2,1,18.0,'SINGAPORE')",
            [ts],
        )
    for ts in late:
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,7002,'bulk','Capesize','singapore_malacca',1.35,103.75,71,200,0.1,1,7.0,'SINGAPORE')",
            [ts],
        )

    # Vessel 7003: stable draught (no transition)
    for ts in early + late:
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,7003,'tanker','MR','ara',51.9,4.5,80,90,8.0,0,5.0,'ROTTERDAM')",
            [ts],
        )
    conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(tmp_path / "analytics.duckdb"))
    from app.main import app
    return TestClient(app)


def test_cargo_transitions_structure(cargo_client):
    r = cargo_client.get("/api/analytics/cargo-transitions")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d
    assert "days" in d
    assert "min_change" in d
    assert "as_of" in d


def test_cargo_transitions_loading_detected(cargo_client):
    r = cargo_client.get("/api/analytics/cargo-transitions?days=7&min_change=2.0")
    rows = r.json()["rows"]
    loading = [row for row in rows if row["mmsi"] == 7001]
    assert len(loading) == 1
    assert loading[0]["direction"] == "loading"
    assert loading[0]["draught_after"] > loading[0]["draught_before"]
    assert loading[0]["change_m"] >= 10.0


def test_cargo_transitions_discharging_detected(cargo_client):
    r = cargo_client.get("/api/analytics/cargo-transitions?days=7&min_change=2.0")
    rows = r.json()["rows"]
    disch = [row for row in rows if row["mmsi"] == 7002]
    assert len(disch) == 1
    assert disch[0]["direction"] == "discharging"
    assert disch[0]["draught_before"] > disch[0]["draught_after"]
    assert disch[0]["change_m"] >= 10.0


def test_cargo_transitions_stable_excluded(cargo_client):
    r = cargo_client.get("/api/analytics/cargo-transitions?days=7&min_change=2.0")
    rows = r.json()["rows"]
    stable = [row for row in rows if row["mmsi"] == 7003]
    assert len(stable) == 0


def test_cargo_transitions_min_change_filter(cargo_client):
    r = cargo_client.get("/api/analytics/cargo-transitions?days=7&min_change=20.0")
    assert r.status_code == 200
    # Both transitions are ~12m, below min_change=20 - should return empty
    assert r.json()["rows"] == []


def test_cargo_transitions_sorted_by_change(cargo_client):
    r = cargo_client.get("/api/analytics/cargo-transitions?days=7&min_change=2.0")
    rows = r.json()["rows"]
    if len(rows) >= 2:
        changes = [row["change_m"] for row in rows]
        assert changes == sorted(changes, reverse=True)


# ---------------------------------------------------------------------------
# Phase 24: slow steamer detection (fleet speed anomaly)
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_client(tmp_path, monkeypatch):
    """Client with live_positions seeded to have clear slow-steaming outliers.

    Segment 'Capesize' (bulk): 10 vessels at 11-12 kn (median ~11.5), one at 4 kn (slow).
    Segment 'VLCC' (tanker): 8 vessels at 13-14 kn, one at 3 kn (slow).
    Vessel marked MOORED (nav_status=5) at 0.5 kn is excluded.
    """
    import duckdb
    from datetime import UTC, datetime
    from fastapi.testclient import TestClient

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_schema = """
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

    f = tmp_path / "ais.duckdb"
    conn = duckdb.connect(str(f))
    conn.execute(ais_schema)

    # 10 normal Capesizes at ~11.5 kn
    for i in range(10):
        conn.execute(
            "INSERT INTO live_positions VALUES (?,?,10.0,20.0,?,90.0,NULL,'CNSHA',74,200,'bulk','Capesize','ara',?,NULL,NULL,0,NULL)",
            [8000 + i, f"CAPE_{i:02d}", 11.0 + i * 0.1, now],
        )

    # One slow Capesize at 4 kn
    conn.execute(
        "INSERT INTO live_positions VALUES (8099,'SLOW CAPE',10.5,20.5,4.0,90.0,NULL,'CNSHA',74,200,'bulk','Capesize','ara',?,NULL,NULL,0,NULL)",
        [now],
    )

    # 8 normal VLCCs at ~13.5 kn
    for i in range(8):
        conn.execute(
            "INSERT INTO live_positions VALUES (?,?,25.0,56.0,?,270.0,NULL,'AEFJR',80,330,'tanker','VLCC','hormuz',?,NULL,NULL,0,NULL)",
            [9000 + i, f"VLCC_{i:02d}", 13.0 + i * 0.1, now],
        )

    # One slow VLCC at 3 kn
    conn.execute(
        "INSERT INTO live_positions VALUES (9099,'SLOW VLCC',25.5,56.5,3.0,270.0,NULL,'AEFJR',80,330,'tanker','VLCC','hormuz',?,NULL,NULL,0,NULL)",
        [now],
    )

    # One moored vessel at slow speed - should be EXCLUDED (nav_status=5)
    conn.execute(
        "INSERT INTO live_positions VALUES (9999,'MOORED SHIP',51.9,4.5,0.5,0.0,NULL,'ROTTERDAM',80,100,'tanker','MR','ara',?,NULL,NULL,5,NULL)",
        [now],
    )

    conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(f))
    monkeypatch.setenv("ANALYTICS_DB", str(tmp_path / "analytics.duckdb"))
    from app.main import app
    return TestClient(app)


def test_slow_steamers_structure(slow_client):
    r = slow_client.get("/api/analytics/slow-steamers")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d
    assert "total_fleet_underway" in d
    assert "as_of" in d


def test_slow_steamers_detects_outliers(slow_client):
    r = slow_client.get("/api/analytics/slow-steamers")
    rows = r.json()["rows"]
    mmsis = {row["mmsi"] for row in rows}
    assert 8099 in mmsis  # slow Capesize
    assert 9099 in mmsis  # slow VLCC


def test_slow_steamers_excludes_normal(slow_client):
    r = slow_client.get("/api/analytics/slow-steamers")
    rows = r.json()["rows"]
    mmsis = {row["mmsi"] for row in rows}
    # Normal vessels should not appear as slow steamers
    for i in range(5):
        assert (8000 + i) not in mmsis


def test_slow_steamers_excludes_moored(slow_client):
    r = slow_client.get("/api/analytics/slow-steamers")
    rows = r.json()["rows"]
    assert 9999 not in {row["mmsi"] for row in rows}


def test_slow_steamers_kind_filter(slow_client):
    r = slow_client.get("/api/analytics/slow-steamers?kind=tanker")
    rows = r.json()["rows"]
    for row in rows:
        assert row["kind"] == "tanker"
    assert 8099 not in {row["mmsi"] for row in rows}


def test_slow_steamers_pct_of_median(slow_client):
    r = slow_client.get("/api/analytics/slow-steamers")
    rows = r.json()["rows"]
    slow_cape = next((row for row in rows if row["mmsi"] == 8099), None)
    if slow_cape:
        assert slow_cape["pct_of_median"] < 60.0
        assert slow_cape["segment_median_sog"] > 0
        assert slow_cape["sog"] == 4.0


# ---------------------------------------------------------------------------
# Phase 25: fleet utilization (underway vs idle by segment)
# ---------------------------------------------------------------------------

def test_fleet_utilization_structure(slow_client):
    """Reuse slow_client fixture which has a suitable live_positions seed."""
    r = slow_client.get("/api/analytics/fleet-utilization")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d
    assert "total_fleet" in d
    assert "as_of" in d
    if d["rows"]:
        row = d["rows"][0]
        assert "segment" in row
        assert "underway_pct" in row
        assert "idle_pct" in row
        assert "underway_count" in row


def test_fleet_utilization_pcts_sum_to_100(slow_client):
    r = slow_client.get("/api/analytics/fleet-utilization")
    for row in r.json()["rows"]:
        total = row["underway_count"] + row["idle_count"] + row["unknown_count"]
        assert total == row["total"]
        total_pct = round(row["underway_pct"] + row["idle_pct"], 0)
        assert total_pct <= 100.1  # small float rounding tolerance


def test_fleet_utilization_capesize_detected(slow_client):
    r = slow_client.get("/api/analytics/fleet-utilization")
    rows = r.json()["rows"]
    cape = next((r for r in rows if r["segment"] == "Capesize"), None)
    assert cape is not None
    # 10 normal + 1 slow Capesize seeded; slow one (4 kn) is below threshold
    assert cape["total"] == 11
    # 10 vessels at 11-12 kn are underway, 1 at 4 kn is unknown (between 0.5 and 2 is
    # unknown, but 4 kn > 2 kn so all 11 are classified as underway)
    # Actually wait: slow Cape is at 4.0 kn > 2.0 kn threshold -> also underway!
    assert cape["underway_count"] == 11


# ---------------------------------------------------------------------------
# Phase 26: high-risk vessel alert feed
# ---------------------------------------------------------------------------

@pytest.fixture
def risk_events_client(tmp_path, monkeypatch):
    """Three-DB fixture for /api/analytics/risk-events.

    Registry:
      IMO 2000001 -> risk_score=80, ofac=false  (MMSI 9801)
      IMO 2000002 -> risk_score=60, ofac=true   (MMSI 9802)
      IMO 2000003 -> risk_score=10, ofac=false  (MMSI 9803)  <- below min_risk=25

    Analytics events (last 24h):
      - STS between 9801 and 9803 (high + low risk)
      - Reroute for 9802 (high risk, OFAC)
      - Reroute for 9803 alone (low risk -> excluded at min_risk=25)
    """
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_schema = """
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

    reg_schema = """
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, name VARCHAR, flag VARCHAR, owner VARCHAR,
        class_society VARCHAR, pi_club VARCHAR, gross_tonnage INTEGER, dwt INTEGER,
        year_built INTEGER, risk_score INTEGER, ofac_sanctioned BOOLEAN,
        risk_indicators VARCHAR, paris_mou_detentions DOUBLE, tokyo_mou_detentions DOUBLE,
        paris_mou_status VARCHAR, tokyo_mou_status VARCHAR,
        fetched_ts TIMESTAMP, fetch_ok BOOLEAN
    );
    """

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (9801,'HIGH RISK ALPHA',25.0,56.0,14.0,270.0,NULL,'AEFJR',80,330,'tanker','VLCC','hormuz',?,2000001,20.0,0,NULL)",
        [now],
    )
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (9802,'HIGH RISK BETA',1.2,103.6,0.5,90.0,NULL,'CNSHA',74,200,'bulk','Capesize','singapore_malacca',?,2000002,12.0,1,NULL)",
        [now],
    )
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (9803,'LOW RISK GAMMA',26.0,56.5,13.0,270.0,NULL,'PKQCT',80,320,'tanker','VLCC','hormuz',?,2000003,18.0,0,NULL)",
        [now],
    )
    ais_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(reg_schema)
    reg_conn.execute(
        "INSERT INTO vessel_registry (imo, risk_score, ofac_sanctioned, fetch_ok) VALUES (2000001, 80, false, true)",
    )
    reg_conn.execute(
        "INSERT INTO vessel_registry (imo, risk_score, ofac_sanctioned, fetch_ok) VALUES (2000002, 60, true, true)",
    )
    reg_conn.execute(
        "INSERT INTO vessel_registry (imo, risk_score, ofac_sanctioned, fetch_ok) VALUES (2000003, 10, false, true)",
    )
    reg_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    # STS: high-risk vessel 9801 with low-risk 9803
    an_conn.execute(
        "INSERT INTO ais_events VALUES ('re_sts001','sts',9801,9803,?,?,25.1,56.1,'hormuz','tanker','VLCC',?)",
        [now - timedelta(hours=12), now - timedelta(hours=10),
         '{"duration_hours":2,"co_location_fixes":8}'],
    )
    # Reroute for high-risk OFAC vessel 9802
    an_conn.execute(
        "INSERT INTO ais_events VALUES ('re_rr001','reroute',9802,NULL,?,?,1.3,103.7,'singapore_malacca','bulk','Capesize',?)",
        [now - timedelta(hours=6), now - timedelta(hours=6),
         '{"old_destination":"CNSHA","new_destination":"IRBIK","fixes_at_old":15}'],
    )
    # Reroute for low-risk vessel only (score=10, below min_risk=25)
    an_conn.execute(
        "INSERT INTO ais_events VALUES ('re_rr002','reroute',9803,NULL,?,?,26.1,56.6,'hormuz','tanker','VLCC',?)",
        [now - timedelta(hours=3), now - timedelta(hours=3),
         '{"old_destination":"AEFJR","new_destination":"CNSHA","fixes_at_old":5}'],
    )
    an_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    return TestClient(app)


def test_risk_events_structure(risk_events_client):
    r = risk_events_client.get("/api/analytics/risk-events?min_risk=25&days=2")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "min_risk", "days", "total_high_risk_vessels", "rows"):
        assert key in d
    assert d["min_risk"] == 25
    assert d["days"] == 2
    assert d["total_high_risk_vessels"] == 2  # IMO 2000001 + 2000002 have score >= 25
    if d["rows"]:
        row = d["rows"][0]
        for field in ("event_id", "event_type", "event_ts", "mmsi", "max_risk"):
            assert field in row


def test_risk_events_detects_sts(risk_events_client):
    r = risk_events_client.get("/api/analytics/risk-events?min_risk=25&days=2")
    rows = {row["event_id"]: row for row in r.json()["rows"]}
    assert "re_sts001" in rows
    ev = rows["re_sts001"]
    assert ev["event_type"] == "sts"
    assert ev["mmsi"] == 9801
    assert ev["mmsi2"] == 9803
    assert ev["risk_score"] == 80
    assert ev["max_risk"] == 80
    assert ev["ofac"] is False


def test_risk_events_detects_reroute(risk_events_client):
    r = risk_events_client.get("/api/analytics/risk-events?min_risk=25&days=2")
    rows = {row["event_id"]: row for row in r.json()["rows"]}
    assert "re_rr001" in rows
    ev = rows["re_rr001"]
    assert ev["event_type"] == "reroute"
    assert ev["mmsi"] == 9802
    assert ev["risk_score"] == 60
    assert ev["ofac"] is True
    assert ev["old_destination"] == "CNSHA"
    assert ev["new_destination"] == "IRBIK"


def test_risk_events_excludes_low_risk(risk_events_client):
    r = risk_events_client.get("/api/analytics/risk-events?min_risk=25&days=2")
    event_ids = {row["event_id"] for row in r.json()["rows"]}
    # re_rr002 belongs to low-risk vessel only (score=10), must be excluded
    assert "re_rr002" not in event_ids


def test_risk_events_min_risk_filter(risk_events_client):
    # min_risk=70: only vessel 9801 (score=80) qualifies -> only re_sts001
    r = risk_events_client.get("/api/analytics/risk-events?min_risk=70&days=2")
    rows = r.json()["rows"]
    for row in rows:
        assert row["max_risk"] >= 70


def test_risk_events_sorted_by_max_risk(risk_events_client):
    r = risk_events_client.get("/api/analytics/risk-events?min_risk=25&days=2")
    rows = r.json()["rows"]
    max_risks = [row["max_risk"] for row in rows]
    assert max_risks == sorted(max_risks, reverse=True)


# ---------------------------------------------------------------------------
# Phase 27: port congestion monitor
# ---------------------------------------------------------------------------

@pytest.fixture
def congestion_client(tmp_path, monkeypatch):
    """Client seeded with anchored_episodes for congestion testing.

    Zone 'singapore_west' (tanker/VLCC):
      - 2 vessels anchored now (closed episodes ending now, 8h/10h dwell)
      - 3 completed historical episodes (24h each) -> the baseline

    Zone 'hormuz_wait' (tanker/VLCC):
      - 1 vessel arrived <2h ago (no historical overlap)
      - No baseline -> congestion_factor = 1.0
    """
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_schema = """
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

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    # 2 vessels currently anchored at singapore_west
    for i in range(2):
        ais_conn.execute(
            "INSERT INTO live_positions VALUES (?,?,1.2,103.6,0.1,0.0,NULL,NULL,80,330,'tanker','VLCC','singapore_malacca',?,NULL,NULL,1,NULL)",
            [6100 + i, f"WAIT_{i:02d}", now],
        )
    # 1 vessel at hormuz_wait
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (6200,'HORMUZ WAIT',25.5,56.0,0.0,0.0,NULL,NULL,80,330,'tanker','VLCC','hormuz',?,NULL,NULL,1,NULL)",
        [now],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)

    # Currently anchored at singapore_west: closed episodes ending now (8h, 10h dwell).
    for i in range(2):
        an_conn.execute(
            "INSERT INTO anchored_episodes VALUES (?,?,?,?,'tanker','VLCC')",
            [6100 + i, 'singapore_west', now - timedelta(hours=8 + i * 2), now],
        )
    # Historical completed episodes at singapore_west (past 7 days) - the baseline.
    for i in range(3):
        start = now - timedelta(days=3 + i, hours=12)
        end = start + timedelta(hours=24)
        an_conn.execute(
            "INSERT INTO anchored_episodes VALUES (?,?,?,?,'tanker','VLCC')",
            [7000 + i, 'singapore_west', start, end],
        )

    # Currently anchored at hormuz_wait, arrived <2h ago (entirely within the
    # current window) so it has no historical baseline -> factor falls back to 1.0.
    an_conn.execute(
        "INSERT INTO anchored_episodes VALUES (6200,'hormuz_wait',?,?,'tanker','VLCC')",
        [now - timedelta(hours=1), now],
    )

    an_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    from app.main import app
    return TestClient(app)


def test_port_congestion_structure(congestion_client):
    r = congestion_client.get("/api/analytics/port-congestion?days=14")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d
    assert "days_baseline" in d
    assert "rows" in d
    assert d["days_baseline"] == 14
    if d["rows"]:
        row = d["rows"][0]
        for field in ("zone", "current_vessels", "congestion_factor"):
            assert field in row


def test_port_congestion_current_vessels(congestion_client):
    r = congestion_client.get("/api/analytics/port-congestion?days=14")
    rows = {row["zone"]: row for row in r.json()["rows"]}
    assert "singapore_west" in rows
    assert rows["singapore_west"]["current_vessels"] == 2
    assert "hormuz_wait" in rows
    assert rows["hormuz_wait"]["current_vessels"] == 1


def test_port_congestion_no_baseline_factor(congestion_client):
    r = congestion_client.get("/api/analytics/port-congestion?days=14")
    rows = {row["zone"]: row for row in r.json()["rows"]}
    # hormuz_wait has 1 vessel but no history -> factor should be 1.0
    assert rows["hormuz_wait"]["congestion_factor"] == pytest.approx(1.0, abs=0.01)
    assert rows["hormuz_wait"]["baseline_avg_vessels"] is None


def test_port_congestion_sorted_by_factor(congestion_client):
    r = congestion_client.get("/api/analytics/port-congestion?days=14")
    rows = r.json()["rows"]
    factors = [row["congestion_factor"] for row in rows]
    assert factors == sorted(factors, reverse=True)


def test_port_congestion_dwell_hours(congestion_client):
    r = congestion_client.get("/api/analytics/port-congestion?days=14")
    rows = {row["zone"]: row for row in r.json()["rows"]}
    sg = rows.get("singapore_west")
    if sg:
        # 2 current vessels anchored 8h and 10h -> avg ~9h
        if sg["avg_current_dwell_hours"] is not None:
            assert 7.0 <= sg["avg_current_dwell_hours"] <= 12.0


# ---------------------------------------------------------------------------
# Phase 28: destination flow intelligence
# ---------------------------------------------------------------------------

@pytest.fixture
def flow_client(tmp_path, monkeypatch):
    """Client seeded with laden vessels and destinations.

    Two laden VLCCs (mmsi 8001+8002) heading CNSHA from hormuz.
    One laden Capesize (mmsi 8003) heading KRPUS from ara.
    One ballast VLCC (mmsi 8004) with destination AEFJR.
    vessel_state: 8001, 8002, 8003 = laden; 8004 = ballast.
    """
    import duckdb
    from datetime import UTC, datetime
    from fastapi.testclient import TestClient

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_schema = """
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

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    # Two laden VLCCs from hormuz -> CNSHA
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (8001,'VLCC FLOW A',25.0,56.0,14.0,90.0,NULL,'CNSHA',80,330,'tanker','VLCC','hormuz',?,9501,20.0,0,NULL)",
        [now],
    )
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (8002,'VLCC FLOW B',25.5,56.5,13.5,90.0,NULL,'CNSHA',80,330,'tanker','VLCC','hormuz',?,9502,19.5,0,NULL)",
        [now],
    )
    # One laden Capesize from ara -> KRPUS
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (8003,'CAPE FLOW',3.5,5.0,12.0,45.0,NULL,'KRPUS',74,300,'bulk','Capesize','ara',?,9503,18.0,0,NULL)",
        [now],
    )
    # One ballast VLCC from hormuz -> AEFJR (should be excluded in laden_only mode)
    ais_conn.execute(
        "INSERT INTO live_positions VALUES (8004,'VLCC BALLAST',26.0,57.0,15.0,270.0,NULL,'AEFJR',80,330,'tanker','VLCC','hormuz',?,9504,5.0,0,NULL)",
        [now],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    an_conn.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", [
        (8001, 21.0, 20.0, 'laden', now),
        (8002, 21.0, 19.5, 'laden', now),
        (8003, 19.0, 18.0, 'laden', now),
        (8004, 21.0, 5.0, 'ballast', now),
    ])
    an_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    from app.main import app
    return TestClient(app)


def test_destination_flows_structure(flow_client):
    r = flow_client.get("/api/analytics/destination-flows")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "laden_only", "total_laden", "rows"):
        assert key in d
    assert d["laden_only"] is True
    if d["rows"]:
        row = d["rows"][0]
        for field in ("origin_region", "destination", "vessel_count"):
            assert field in row


def test_destination_flows_laden_only(flow_client):
    r = flow_client.get("/api/analytics/destination-flows?laden_only=true")
    d = r.json()
    destinations = {row["destination"] for row in d["rows"]}
    assert "CNSHA" in destinations   # 2 laden VLCCs (Shanghai LOCODE, uncurated -> raw)
    assert "Busan" in destinations   # 1 laden Capesize (KRPUS folds to canonical Busan)
    # Ballast vessel destination AEFJR must be excluded
    assert "AEFJR" not in destinations


def test_destination_flows_total_laden(flow_client):
    r = flow_client.get("/api/analytics/destination-flows?laden_only=true")
    d = r.json()
    assert d["total_laden"] == 3   # 3 laden vessels in vessel_state


def test_destination_flows_includes_all(flow_client):
    r = flow_client.get("/api/analytics/destination-flows?laden_only=false")
    d = r.json()
    destinations = {row["destination"] for row in d["rows"]}
    # All 4 vessels have destinations; laden_only=false includes ballast
    assert "AEFJR" in destinations
    assert d["laden_only"] is False


def test_destination_flows_kind_filter(flow_client):
    r = flow_client.get("/api/analytics/destination-flows?laden_only=true&kind=bulk")
    d = r.json()
    destinations = {row["destination"] for row in d["rows"]}
    assert "Busan" in destinations      # only the Capesize (KRPUS -> Busan)
    assert "CNSHA" not in destinations  # VLCCs (tanker) excluded


def test_destination_flows_sorted_by_count(flow_client):
    r = flow_client.get("/api/analytics/destination-flows?laden_only=true")
    rows = r.json()["rows"]
    counts = [row["vessel_count"] for row in rows]
    assert counts == sorted(counts, reverse=True)
    # CNSHA should be first (2 vessels) before Busan (1 vessel, from KRPUS)
    if len(rows) >= 2:
        cnsha = next((r for r in rows if r["destination"] == "CNSHA"), None)
        busan = next((r for r in rows if r["destination"] == "Busan"), None)
        if cnsha and busan:
            assert cnsha["vessel_count"] >= busan["vessel_count"]


# ---------------------------------------------------------------------------
# Phase 29: market summary KPI card
# ---------------------------------------------------------------------------

def test_market_summary_structure(flow_client):
    """Reuse flow_client fixture: 3 laden + 1 ballast vessel."""
    r = flow_client.get("/api/analytics/market-summary")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "total_fleet", "total_laden", "total_ballast",
                "laden_pct", "transits_24h", "reroutes_24h", "sts_24h",
                "gaps_24h", "by_segment"):
        assert key in d


def test_market_summary_laden_count(flow_client):
    r = flow_client.get("/api/analytics/market-summary")
    d = r.json()
    assert d["total_laden"] == 3    # 8001, 8002, 8003 seeded as laden
    assert d["total_ballast"] == 1  # 8004 seeded as ballast
    assert d["laden_pct"] == pytest.approx(75.0, abs=1.0)


def test_market_summary_by_segment(flow_client):
    r = flow_client.get("/api/analytics/market-summary")
    d = r.json()
    by_seg = {f'{row["kind"]}-{row["segment"]}': row for row in d["by_segment"]}
    # 2 laden VLCCs + 1 ballast VLCC = 3 total in VLCC tanker segment
    vlcc = by_seg.get("tanker-VLCC")
    assert vlcc is not None
    assert vlcc["total"] == 3
    assert vlcc["laden"] == 2
    assert vlcc["ballast"] == 1


def test_market_summary_zero_events(flow_client):
    # flow_client has no ais_events seeded -> all event counts = 0
    r = flow_client.get("/api/analytics/market-summary")
    d = r.json()
    assert d["transits_24h"] == 0
    assert d["reroutes_24h"] == 0
    assert d["sts_24h"] == 0
    assert d["gaps_24h"] == 0


# ---------------------------------------------------------------------------
# Phase 30: vessel behavioral risk leaderboard
# ---------------------------------------------------------------------------

import pytest as _pytest_ph30


@_pytest_ph30.fixture
def risk_leaderboard_client(tmp_path, monkeypatch):
    """Fixture for vessel-risk-scores endpoint.

    Vessels:
      9001  VLCC RISK  IMO=7001  sts_count=3  reroute_count=2  reg_risk=80  ofac=False
      9002  CAPE REROUTE  IMO=7002  sts_count=0  reroute_count=5  reg_risk=None  ofac=False
      9003  TANKER OFAC  IMO=7003  sts_count=1  reroute_count=0  reg_risk=60  ofac=True
      9004  BULK CLEAN  IMO=7004  sts_count=0  reroute_count=0  reg_risk=10  ofac=False
      9005  SMALL VESSEL  (segment=Small)  - must be excluded even if events exist
    """
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    now = datetime.now(UTC).replace(tzinfo=None)
    recent = now - timedelta(days=1)

    ais_schema = """
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
    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """
    reg_schema = """
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (9001, "VLCC RISK",    25.0, 56.0, 14.0, 90.0, None, "CNSHA", 80, 330, "tanker", "VLCC",     "hormuz", now, 7001, 20.0, 0, None),
            (9002, "CAPE REROUTE", 3.5,  5.0,  12.0, 45.0, None, "ARA",   74, 300, "bulk",   "Capesize", "ara",    now, 7002, 18.0, 0, None),
            (9003, "TANKER OFAC",  1.3, 103.8, 0.5,  180.0, None, "SGSIN", 80, 300, "tanker", "VLCC",     "singapore_malacca", now, 7003, 19.0, 1, None),
            (9004, "BULK CLEAN",   51.0, 3.0,  10.0, 270.0, None, "NLRTM", 74, 290, "bulk",   "Capesize", "north_sea", now, 7004, 15.0, 0, None),
            (9005, "SMALL VESSEL", 10.0, 10.0,  5.0,   0.0, None, None,    70, 20,  "tanker", "Small",    "ara",    now, None, None, None, None),
        ],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    # STS events: 9001 is involved in 3 as mmsi, 9003 is involved in 1 as mmsi2
    an_conn.executemany(
        "INSERT INTO ais_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("sts-1", "sts", 9001, None, recent, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
            ("sts-2", "sts", 9001, None, recent, None, 25.1, 56.1, "hormuz", "tanker", "VLCC", "{}"),
            ("sts-3", "sts", 9001, None, recent, None, 25.2, 56.2, "hormuz", "tanker", "VLCC", "{}"),
            ("sts-4", "sts", 9002, 9003, recent, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            # Reroutes for 9001 (2) and 9002 (5)
            ("rr-1",  "reroute", 9001, None, recent, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
            ("rr-2",  "reroute", 9001, None, recent, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
            ("rr-3",  "reroute", 9002, None, recent, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            ("rr-4",  "reroute", 9002, None, recent, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            ("rr-5",  "reroute", 9002, None, recent, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            ("rr-6",  "reroute", 9002, None, recent, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            ("rr-7",  "reroute", 9002, None, recent, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            # Small vessel event - should be excluded from results (segment filter)
            ("rr-8",  "reroute", 9005, None, recent, None, 10.0, 10.0, "ara",    "tanker", "Small", "{}"),
        ],
    )
    an_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(reg_schema)
    reg_conn.executemany(
        "INSERT INTO vessel_registry (imo, ship_name, fetch_ok, risk_score, ofac_sanctioned) VALUES (?,?,?,?,?)",
        [
            (7001, "VLCC RISK",    True, 80,  False),
            (7003, "TANKER OFAC",  True, 60,  True),
            (7004, "BULK CLEAN",   True, 10,  False),
        ],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    return TestClient(app)


def test_vessel_risk_scores_structure(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "days", "top_n", "total_candidates", "rows"):
        assert key in d
    if d["rows"]:
        row = d["rows"][0]
        for field in ("mmsi", "sto_count", "reroute_count", "behavioral_score", "total_score", "ofac"):
            assert field in row or field.replace("sto_count", "sts_count") in row


def test_vessel_risk_scores_excludes_small(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?min_score=0")
    d = r.json()
    mmsis = {row["mmsi"] for row in d["rows"]}
    assert 9005 not in mmsis  # Small segment vessel must be excluded


def test_vessel_risk_scores_sorted_by_total(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?min_score=0")
    rows = r.json()["rows"]
    scores = [row["total_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)


def test_vessel_risk_scores_behavioral_formula(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?min_score=0")
    rows = {row["mmsi"]: row for row in r.json()["rows"]}
    # 9002: sts=1 (as mmsi2 party in sts-4), reroute=5 -> behavioral = min(1*20 + 5*5, 100) = min(45,100) = 45
    if 9002 in rows:
        row = rows[9002]
        assert row["sts_count"] == 1
        assert row["reroute_count"] == 5
        assert row["behavioral_score"] == 45
        # No registry data -> total = behavioral = 45
        assert row["total_score"] == 45


def test_vessel_risk_scores_registry_weighting(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?min_score=0")
    rows = {row["mmsi"]: row for row in r.json()["rows"]}
    # 9001: sts=3, reroute=2 -> behavioral = min(3*20 + 2*5, 100) = min(70,100) = 70
    # registry_risk=80, ofac=False -> total = round(70*0.4 + 80*0.6) = round(28+48) = 76
    if 9001 in rows:
        row = rows[9001]
        assert row["sts_count"] == 3
        assert row["reroute_count"] == 2
        assert row["behavioral_score"] == 70
        assert row["registry_risk"] == 80
        assert row["ofac"] is False
        assert row["total_score"] == 76


def test_vessel_risk_scores_ofac_bonus(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?min_score=0")
    rows = {row["mmsi"]: row for row in r.json()["rows"]}
    # 9003: sts=1 (as mmsi2 in sts-4), reroute=0 -> behavioral = min(1*20, 100) = 20
    # registry_risk=60, ofac=True -> total = min(round(20*0.4 + 60*0.6) + 25, 100) = min(round(8+36)+25, 100) = min(69, 100) = 69
    if 9003 in rows:
        row = rows[9003]
        assert row["ofac"] is True
        assert row["total_score"] == pytest.approx(69, abs=2)


def test_vessel_risk_scores_registry_only_included(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?min_score=0")
    rows = {row["mmsi"]: row for row in r.json()["rows"]}
    # 9004: no events but risk_score=10 -> should appear with total>0
    assert 9004 in rows
    row = rows[9004]
    assert row["sts_count"] == 0
    assert row["reroute_count"] == 0
    assert row["behavioral_score"] == 0
    # total = round(0*0.4 + 10*0.6) = 6, no ofac
    assert row["total_score"] == 6


def test_vessel_risk_scores_top_n_param(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?top_n=2&min_score=0")
    d = r.json()
    assert len(d["rows"]) <= 2
    assert d["top_n"] == 2
    assert d["total_candidates"] >= 2


def test_vessel_risk_scores_kind_filter(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/vessel-risk-scores?kind=bulk&min_score=0")
    d = r.json()
    for row in d["rows"]:
        assert row["kind"] == "bulk"


# ---------------------------------------------------------------------------
# Phase 31: chokepoint traffic heatmap
# ---------------------------------------------------------------------------


def test_chokepoint_heatmap_structure(analytics_client):
    r = analytics_client.get("/api/analytics/chokepoint-heatmap?days=30")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "days", "kind", "chokepoints", "cells"):
        assert key in d
    assert d["days"] == 30
    assert d["kind"] == ""
    if d["cells"]:
        cell = d["cells"][0]
        for field in ("date", "chokepoint", "total", "tanker", "bulk"):
            assert field in cell


def test_chokepoint_heatmap_has_data(analytics_client):
    r = analytics_client.get("/api/analytics/chokepoint-heatmap?days=30")
    d = r.json()
    assert len(d["cells"]) >= 1
    assert "hormuz" in d["chokepoints"]
    assert "singapore_malacca" in d["chokepoints"]


def test_chokepoint_heatmap_kind_filter(analytics_client):
    # tanker filter: only the hormuz VLCC transit (kind=tanker) should appear
    r = analytics_client.get("/api/analytics/chokepoint-heatmap?days=30&kind=tanker")
    d = r.json()
    assert d["kind"] == "tanker"
    # All returned cells must have tanker > 0 (bulk should be 0 for tanker-filtered results)
    for cell in d["cells"]:
        assert cell["bulk"] == 0


def test_chokepoint_heatmap_totals_consistent(analytics_client):
    r = analytics_client.get("/api/analytics/chokepoint-heatmap?days=30")
    d = r.json()
    for cell in d["cells"]:
        assert cell["total"] == cell["tanker"] + cell["bulk"]


def test_chokepoint_heatmap_chokepoints_ordered_by_traffic(analytics_client):
    r = analytics_client.get("/api/analytics/chokepoint-heatmap?days=30")
    d = r.json()
    # Compute per-chokepoint totals from cells
    from collections import defaultdict
    cp_totals: dict[str, int] = defaultdict(int)
    for cell in d["cells"]:
        cp_totals[cell["chokepoint"]] += cell["total"]
    ordered = d["chokepoints"]
    if len(ordered) >= 2:
        # First chokepoint must have >= total of second
        assert cp_totals[ordered[0]] >= cp_totals[ordered[1]]


def test_chokepoint_heatmap_no_data_returns_empty(tmp_path, monkeypatch):
    """Analytics DB with no transit_events returns empty payload gracefully."""
    import duckdb
    from fastapi.testclient import TestClient

    an_file = tmp_path / "empty_analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(
        "CREATE TABLE transit_events ("
        "  mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,"
        "  direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,"
        "  PRIMARY KEY (mmsi, chokepoint, entered_ts)"
        ")"
    )
    an_conn.close()
    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(
        "CREATE TABLE live_positions (mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE,"
        " lon DOUBLE, sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,"
        " ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,"
        " region VARCHAR, updated_ts TIMESTAMP, imo BIGINT, draught DOUBLE,"
        " nav_status INTEGER, eta VARCHAR)"
    )
    ais_conn.execute(
        "CREATE TABLE ais_snapshots (snapshot_ts TIMESTAMP, mmsi BIGINT, kind VARCHAR,"
        " segment VARCHAR, region VARCHAR, lat DOUBLE, lon DOUBLE, ship_type INTEGER,"
        " length_m DOUBLE, sog DOUBLE, nav_status INTEGER, draught DOUBLE,"
        " destination VARCHAR, PRIMARY KEY (snapshot_ts, mmsi))"
    )
    ais_conn.close()
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    from app.main import app
    r = TestClient(app).get("/api/analytics/chokepoint-heatmap?days=7")
    assert r.status_code == 200
    d = r.json()
    assert d["cells"] == []
    assert d["chokepoints"] == []


# ---------------------------------------------------------------------------
# Phase 32: trade lane risk matrix
# ---------------------------------------------------------------------------


@pytest.fixture
def trade_lane_client(tmp_path, monkeypatch):
    """Fixture for trade-lane-matrix endpoint.

    4 vessels:
      9101 VLCC A  region=hormuz  dest=CNSHA (Far East) laden  behavioral_score=0 registry=None
      9102 VLCC B  region=hormuz  dest=JPYOK (Far East) laden  sts=3 -> behavioral=60
      9103 CAPE A  region=ara     dest=NLRTM (NW Europe) laden registry_risk=70 ofac=False
      9104 VLCC C  region=hormuz  dest=AEFJR (Middle East) ballast (excluded when laden_only=True)
    """
    import duckdb
    from datetime import UTC, datetime, timedelta
    from fastapi.testclient import TestClient

    now = datetime.now(UTC).replace(tzinfo=None)
    recent = now - timedelta(days=1)

    ais_schema = """
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
    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR,
        mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP,
        lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """
    reg_schema = """
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """

    ais_file = tmp_path / "ais.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (9101, "VLCC A", 25.0, 56.0, 14.0, 90.0, None, "CNSHA", 80, 330, "tanker", "VLCC", "hormuz", now, 8001, 20.0, 0, None),
            (9102, "VLCC B", 25.5, 56.5, 13.5, 90.0, None, "JPYOK", 80, 330, "tanker", "VLCC", "hormuz", now, 8002, 19.5, 0, None),
            (9103, "CAPE A", 3.5,  5.0,  12.0, 45.0, None, "NLRTM", 74, 300, "bulk",   "Capesize", "ara", now, 8003, 18.0, 0, None),
            (9104, "VLCC C", 26.0, 57.0, 15.0, 270.0, None, "AEFJR", 80, 330, "tanker", "VLCC", "hormuz", now, 8004, 5.0, 0, None),
        ],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    # 9101, 9103 laden; 9104 ballast; 9102 will have STS events making it high-risk
    an_conn.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", [
        (9101, 21.0, 20.0, 'laden', now),
        (9102, 21.0, 19.5, 'laden', now),
        (9103, 19.0, 18.0, 'laden', now),
        (9104, 21.0, 5.0,  'ballast', now),
    ])
    # 3 STS events for 9102 -> behavioral_score = min(3*20, 100) = 60 >= 50 -> high_risk
    an_conn.executemany(
        "INSERT INTO ais_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("sts-tl1", "sts", 9102, None, recent, None, 25.5, 56.5, "hormuz", "tanker", "VLCC", "{}"),
            ("sts-tl2", "sts", 9102, None, recent, None, 25.6, 56.6, "hormuz", "tanker", "VLCC", "{}"),
            ("sts-tl3", "sts", 9102, None, recent, None, 25.7, 56.7, "hormuz", "tanker", "VLCC", "{}"),
        ],
    )
    an_conn.close()

    reg_file = tmp_path / "registry.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute(reg_schema)
    reg_conn.executemany(
        "INSERT INTO vessel_registry (imo, ship_name, fetch_ok, risk_score, ofac_sanctioned) VALUES (?,?,?,?,?)",
        [
            (8003, "CAPE A", True, 70, False),   # registry_risk=70, 9103 has this
        ],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))
    from app.main import app
    return TestClient(app)


def test_trade_lane_matrix_structure(trade_lane_client):
    r = trade_lane_client.get("/api/analytics/trade-lane-matrix?laden_only=false")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "kind", "laden_only", "origin_regions", "dest_regions", "cells"):
        assert key in d
    if d["cells"]:
        cell = d["cells"][0]
        for field in ("origin_region", "dest_region", "vessel_count", "high_risk_count", "laden_count"):
            assert field in cell


def test_trade_lane_matrix_laden_filter(trade_lane_client):
    r = trade_lane_client.get("/api/analytics/trade-lane-matrix?laden_only=true")
    d = r.json()
    # 9101 (hormuz->Far East), 9102 (hormuz->Far East), 9103 (ara->NW Europe) are laden
    # 9104 (hormuz->Middle East) is ballast -> excluded
    total_vessels = sum(c["vessel_count"] for c in d["cells"])
    assert total_vessels == 3
    # Middle East destination should not appear (only 9104 ballast heads there)
    dest_regions = {c["dest_region"] for c in d["cells"]}
    assert "Middle East" not in dest_regions


def test_trade_lane_matrix_high_risk_count(trade_lane_client):
    r = trade_lane_client.get("/api/analytics/trade-lane-matrix?laden_only=false")
    d = r.json()
    # 9102 has 3 STS events -> behavioral_score=60 >= 50 -> high_risk
    # It's in hormuz -> Far East (JPYOK = JP = Far East)
    hormuz_fe = next((c for c in d["cells"] if c["origin_region"] == "hormuz" and c["dest_region"] == "Far East"), None)
    assert hormuz_fe is not None
    # 9101 (CNSHA=Far East) + 9102 (JPYOK=Far East) = 2 in this cell
    assert hormuz_fe["vessel_count"] == 2
    # 9102 is high-risk (behavioral >= 50)
    assert hormuz_fe["high_risk_count"] >= 1


def test_trade_lane_matrix_dest_region_mapping(trade_lane_client):
    r = trade_lane_client.get("/api/analytics/trade-lane-matrix?laden_only=false")
    d = r.json()
    dest_regions = {c["dest_region"] for c in d["cells"]}
    # NLRTM (NL) -> NW Europe
    assert "NW Europe" in dest_regions
    # CNSHA + JPYOK (CN + JP) -> Far East
    assert "Far East" in dest_regions


def test_trade_lane_matrix_kind_filter(trade_lane_client):
    r = trade_lane_client.get("/api/analytics/trade-lane-matrix?laden_only=false&kind=bulk")
    d = r.json()
    # Only 9103 (bulk Capesize) should appear
    total = sum(c["vessel_count"] for c in d["cells"])
    assert total == 1
    assert d["cells"][0]["dest_region"] == "NW Europe"


# ---------------------------------------------------------------------------
# Phase 33: per-vessel behavioral risk
# ---------------------------------------------------------------------------


def test_vessel_behavioral_risk_structure(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/vessels/9001/behavioral-risk")
    assert r.status_code == 200
    d = r.json()
    for key in ("mmsi", "imo", "sts_count", "reroute_count", "days",
                "behavioral_score", "registry_risk", "ofac", "total_score",
                "risk_level", "recent_events"):
        assert key in d


def test_vessel_behavioral_risk_scoring(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/vessels/9001/behavioral-risk")
    d = r.json()
    # 9001: sts=3 reroute=2 -> behavioral = min(3*20+2*5, 100) = 70
    assert d["mmsi"] == 9001
    assert d["sts_count"] == 3
    assert d["reroute_count"] == 2
    assert d["behavioral_score"] == 70
    # registry_risk=80 -> total = round(70*0.4 + 80*0.6) = 76 >= 75 -> Critical
    assert d["registry_risk"] == 80
    assert d["total_score"] == 76
    assert d["risk_level"] == "Critical"
    assert d["ofac"] is False


def test_vessel_behavioral_risk_ofac(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/vessels/9003/behavioral-risk")
    d = r.json()
    assert d["ofac"] is True
    assert d["registry_risk"] == 60
    # 9003: sts=1 (as mmsi2 in sts-4) -> behavioral=20, registry=60, ofac=True
    # total = min(round(20*0.4+60*0.6)+25, 100) = min(44+25, 100) = 69
    assert d["total_score"] == pytest.approx(69, abs=2)
    assert d["risk_level"] in ("High", "Elevated")


def test_vessel_behavioral_risk_no_events(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/vessels/9004/behavioral-risk")
    d = r.json()
    assert d["sts_count"] == 0
    assert d["reroute_count"] == 0
    assert d["behavioral_score"] == 0
    # registry_risk=10 -> total = round(0*0.4+10*0.6) = 6
    assert d["total_score"] == 6
    assert d["risk_level"] == "Low"


def test_vessel_behavioral_risk_unknown_mmsi(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/vessels/99999/behavioral-risk")
    assert r.status_code == 200
    d = r.json()
    assert d["sts_count"] == 0
    assert d["reroute_count"] == 0
    assert d["total_score"] == 0
    assert d["risk_level"] == "Low"


def test_vessel_behavioral_risk_recent_events(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/vessels/9001/behavioral-risk")
    d = r.json()
    # 9001 has 2 reroutes and 3 STS events = 5 total, limited to 5
    assert len(d["recent_events"]) <= 5
    for ev in d["recent_events"]:
        assert "type" in ev
        assert "ts" in ev
        assert ev["type"] in ("sts", "reroute")


# ---------------------------------------------------------------------------
# Phase 34: anomaly watchlist
# ---------------------------------------------------------------------------


def test_anomaly_watchlist_structure(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=0")
    assert r.status_code == 200
    d = r.json()
    for key in ("as_of", "min_score", "total_flagged", "rows"):
        assert key in d
    if d["rows"]:
        row = d["rows"][0]
        for field in ("mmsi", "name", "total_score", "risk_level", "signals",
                      "sts_count_7d", "reroute_count_7d", "ofac"):
            assert field in row


def test_anomaly_watchlist_sorted_by_score(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=0")
    rows = r.json()["rows"]
    scores = [row["total_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)


def test_anomaly_watchlist_excludes_small(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=0")
    d = r.json()
    mmsis = {row["mmsi"] for row in d["rows"]}
    # 9005 is Small segment -> must be excluded
    assert 9005 not in mmsis


def test_anomaly_watchlist_min_score_filter(risk_leaderboard_client):
    r_all = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=0")
    r_high = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=70")
    all_rows = r_all.json()["rows"]
    high_rows = r_high.json()["rows"]
    # High filter should return fewer or equal rows
    assert len(high_rows) <= len(all_rows)
    for row in high_rows:
        assert row["total_score"] >= 70


def test_anomaly_watchlist_signals_populated(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=0")
    d = r.json()
    # Find 9003 (OFAC vessel) in rows
    ofac_row = next((row for row in d["rows"] if row["mmsi"] == 9003), None)
    if ofac_row:
        assert ofac_row["ofac"] is True
        # OFAC signal should appear in signals list
        assert any("OFAC" in s for s in ofac_row["signals"])


def test_anomaly_watchlist_limit_param(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/anomaly-watchlist?min_score=0&limit=2")
    d = r.json()
    assert len(d["rows"]) <= 2
    # total_flagged is the full count before limit is applied
    assert d["total_flagged"] >= len(d["rows"])


# ---------------------------------------------------------------------------
# Phase 35: STS Proximity
# ---------------------------------------------------------------------------

@pytest.fixture
def sts_proximity_client(tmp_path, monkeypatch):
    """Fixture for STS proximity endpoint.

    Vessels (all sog <= 3, nav_status=0 unless noted):
      9201  near Hormuz (25.0, 56.0)     sog=0.8  tanker/VLCC
      9202  near Hormuz (25.001, 56.001) sog=1.2  tanker/VLCC   ~149m from 9201
      9203  near Hormuz (25.005, 56.005) sog=2.0  tanker/Suezmax ~746m from 9201
      9204  north_sea   (51.0, 3.0)      sog=1.5  bulk/Capesize
      9205  north_sea   (51.001, 3.001)  sog=0.9  bulk/Capesize  ~131m from 9204
      9206  hormuz      (25.0, 56.1)     sog=0.5  tanker/VLCC   nav_status=1 ANCHORED
    """
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(tzinfo=None)
    ais_schema = """
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """
    ais_file = tmp_path / "ais_prox.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (9201, "VLCC ALPHA",  25.000, 56.000, 0.8, None, None, None, 80, 330, "tanker", "VLCC",     "hormuz",    now, 7201, 20.0, 0,    None),
            (9202, "VLCC BETA",   25.001, 56.001, 1.2, None, None, None, 80, 330, "tanker", "VLCC",     "hormuz",    now, 7202, 19.5, 0,    None),
            (9203, "SUEZMAX G",   25.005, 56.005, 2.0, None, None, None, 80, 300, "tanker", "Suezmax",  "hormuz",    now, 7203, 18.0, 0,    None),
            (9204, "CAPE NORTH",  51.000,  3.000, 1.5, None, None, None, 74, 290, "bulk",   "Capesize", "north_sea", now, 7204, 15.0, 0,    None),
            (9205, "CAPE SOUTH",  51.001,  3.001, 0.9, None, None, None, 74, 290, "bulk",   "Capesize", "north_sea", now, 7205, 14.5, 0,    None),
            (9206, "ANCH VLCC",   25.000, 56.100, 0.5, None, None, None, 80, 330, "tanker", "VLCC",     "hormuz",    now, 7206, 20.0, 1,    None),
        ],
    )
    ais_conn.close()

    # minimal analytics DB (endpoint only reads live_positions, but db.py needs a valid analytics path)
    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """
    an_file = tmp_path / "analytics_prox.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    an_conn.close()

    reg_file = tmp_path / "reg_prox.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """)
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_sts_proximity_structure(sts_proximity_client):
    r = sts_proximity_client.get("/api/analytics/sts-proximity")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d and "max_dist_m" in d and "max_sog" in d
    assert "total_pairs" in d and "pairs" in d
    assert isinstance(d["pairs"], list)


def test_sts_proximity_finds_close_vessels(sts_proximity_client):
    r = sts_proximity_client.get("/api/analytics/sts-proximity?max_dist_m=2000&max_sog=3.0")
    d = r.json()
    # 9201-9202 (~149m), 9201-9203 (~746m), 9202-9203 (~599m), 9204-9205 (~131m)
    assert d["total_pairs"] >= 4
    mmsi_sets = [{p["mmsi_a"], p["mmsi_b"]} for p in d["pairs"]]
    assert {9201, 9202} in mmsi_sets
    assert {9204, 9205} in mmsi_sets


def test_sts_proximity_excludes_anchored(sts_proximity_client):
    r = sts_proximity_client.get("/api/analytics/sts-proximity?max_dist_m=20000&max_sog=3.0")
    d = r.json()
    all_mmsis = {p["mmsi_a"] for p in d["pairs"]} | {p["mmsi_b"] for p in d["pairs"]}
    assert 9206 not in all_mmsis


def test_sts_proximity_dist_below_threshold(sts_proximity_client):
    r = sts_proximity_client.get("/api/analytics/sts-proximity?max_dist_m=200&max_sog=3.0")
    d = r.json()
    for p in d["pairs"]:
        assert p["dist_m"] <= 200


def test_sts_proximity_risk_region_flagged(sts_proximity_client):
    r = sts_proximity_client.get("/api/analytics/sts-proximity?max_dist_m=2000&max_sog=3.0")
    d = r.json()
    hormuz_pairs = [p for p in d["pairs"] if {p["mmsi_a"], p["mmsi_b"]} <= {9201, 9202, 9203}]
    assert all(p["risk_region"] for p in hormuz_pairs)
    ns_pairs = [p for p in d["pairs"] if {p["mmsi_a"], p["mmsi_b"]} == {9204, 9205}]
    assert ns_pairs
    assert not ns_pairs[0]["risk_region"]


def test_sts_proximity_sog_filter(sts_proximity_client):
    # sog cap at 0.3 - only vessels with sog <= 0.3 qualify; none in fixture
    r = sts_proximity_client.get("/api/analytics/sts-proximity?max_dist_m=2000&max_sog=0.3")
    d = r.json()
    assert d["total_pairs"] == 0


# ---------------------------------------------------------------------------
# Phase 36: Region Momentum
# ---------------------------------------------------------------------------

@pytest.fixture
def region_momentum_client(tmp_path, monkeypatch):
    """Fixture for region-momentum endpoint.

    fleet_density has two timestamps 24h apart for three regions.
      ara:         latest=500, prev=400  -> delta=+100
      suez:        latest=200, prev=250  -> delta=-50
      dover_channel: only in latest=150  -> delta=+150
    """
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    t_latest = now.replace(minute=0, second=0, microsecond=0)
    t_prev = t_latest - timedelta(hours=24)

    ais_schema = """
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """
    ais_file = tmp_path / "ais_mom.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.close()

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """
    an_file = tmp_path / "analytics_mom.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    # latest snapshot
    an_conn.executemany(
        "INSERT INTO fleet_density VALUES (?,?,?,?,?,?,?)",
        [
            (t_latest, "ara",           "tanker", "VLCC",     300, 150, 50),
            (t_latest, "suez",          "tanker", "VLCC",     120,  60, 20),
            (t_latest, "dover_channel", "bulk",   "Capesize",  90,  45, 15),
        ],
    )
    # prev snapshot
    an_conn.executemany(
        "INSERT INTO fleet_density VALUES (?,?,?,?,?,?,?)",
        [
            (t_prev, "ara",  "tanker", "VLCC", 240, 120, 40),
            (t_prev, "suez", "tanker", "VLCC",  140,  80, 30),
        ],
    )
    an_conn.close()

    reg_file = tmp_path / "reg_mom.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """)
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_region_momentum_structure(region_momentum_client):
    r = region_momentum_client.get("/api/analytics/region-momentum")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d and "hours_back" in d and "rows" in d
    assert d["hours_back"] == 24
    for row in d["rows"]:
        assert "region" in row and "current_total" in row and "delta" in row
        assert "laden_ratio_pct" in row


def test_region_momentum_delta_correct(region_momentum_client):
    r = region_momentum_client.get("/api/analytics/region-momentum?hours_back=24")
    d = r.json()
    ara = next(row for row in d["rows"] if row["region"] == "ara")
    # latest=300+150+50=500, prev=240+120+40=400, delta=+100
    assert ara["current_total"] == 500
    assert ara["delta"] == 100


def test_region_momentum_negative_delta(region_momentum_client):
    r = region_momentum_client.get("/api/analytics/region-momentum?hours_back=24")
    d = r.json()
    suez = next(row for row in d["rows"] if row["region"] == "suez")
    # latest=200, prev=250, delta=-50
    assert suez["delta"] == -50


def test_region_momentum_new_region(region_momentum_client):
    r = region_momentum_client.get("/api/analytics/region-momentum?hours_back=24")
    d = r.json()
    dc = next(row for row in d["rows"] if row["region"] == "dover_channel")
    assert dc["delta"] == 150  # no prev data -> delta = current_total
    assert dc["prev_total"] == 0


def test_region_momentum_laden_ratio(region_momentum_client):
    r = region_momentum_client.get("/api/analytics/region-momentum?hours_back=24")
    d = r.json()
    ara = next(row for row in d["rows"] if row["region"] == "ara")
    # laden=300 out of 500 total -> 60%
    assert ara["laden_ratio_pct"] == 60.0


# ---------------------------------------------------------------------------
# Phase 37: Event Rate Timeline
# ---------------------------------------------------------------------------

@pytest.fixture
def event_rate_client(tmp_path, monkeypatch):
    """Fixture for event-rate-timeline endpoint.

    ais_events has:
      - 3 reroutes at hour H and 2 reroutes at hour H-1
      - 1 STS at hour H
    """
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    h0 = now.replace(minute=0, second=0, microsecond=0)
    h1 = h0 - timedelta(hours=1)

    ais_schema = """
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """
    ais_file = tmp_path / "ais_er.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.close()

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """
    an_file = tmp_path / "analytics_er.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    an_conn.executemany(
        "INSERT INTO ais_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("er-1", "reroute", 9301, None, h0, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
            ("er-2", "reroute", 9302, None, h0, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
            ("er-3", "reroute", 9303, None, h0, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
            ("er-4", "reroute", 9304, None, h1, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            ("er-5", "reroute", 9305, None, h1, None, 3.5,  5.0,  "ara",    "bulk",   "Capesize", "{}"),
            ("er-6", "sts",     9301, 9306, h0, None, 25.0, 56.0, "hormuz", "tanker", "VLCC", "{}"),
        ],
    )
    an_conn.close()

    reg_file = tmp_path / "reg_er.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """)
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_event_rate_timeline_structure(event_rate_client):
    r = event_rate_client.get("/api/analytics/event-rate-timeline")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d and "hours" in d and "points" in d
    for pt in d["points"]:
        assert "hour" in pt and "reroute_count" in pt and "sts_count" in pt and "total_count" in pt


def test_event_rate_timeline_counts(event_rate_client):
    r = event_rate_client.get("/api/analytics/event-rate-timeline?hours=72")
    d = r.json()
    h0_pts = [p for p in d["points"] if p["reroute_count"] > 0 and p["sts_count"] > 0]
    assert h0_pts, "Expected an hour with both reroutes and STS"
    pt = h0_pts[0]
    assert pt["reroute_count"] == 3
    assert pt["sts_count"] == 1
    assert pt["total_count"] == 4


def test_event_rate_timeline_prev_hour(event_rate_client):
    r = event_rate_client.get("/api/analytics/event-rate-timeline?hours=72")
    d = r.json()
    reroute_only = [p for p in d["points"] if p["reroute_count"] > 0 and p["sts_count"] == 0]
    assert reroute_only
    assert reroute_only[0]["reroute_count"] == 2


def test_event_rate_timeline_hours_clamp(event_rate_client):
    r = event_rate_client.get("/api/analytics/event-rate-timeline?hours=6")
    d = r.json()
    assert d["hours"] == 6


# ---------------------------------------------------------------------------
# Phase 38: Transit Rate Timeline
# ---------------------------------------------------------------------------

@pytest.fixture
def transit_rate_client(tmp_path, monkeypatch):
    """Fixture for transit-rate-timeline endpoint.

    transit_events:
      - dover_channel: 3 transits at hour H (2 laden, 1 ballast)
      - suez: 1 transit at hour H, 2 at hour H-1
    """
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    h0 = now.replace(minute=0, second=0, microsecond=0)
    h1 = h0 - timedelta(hours=1)

    ais_schema = """
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """
    ais_file = tmp_path / "ais_tr.duckdb"
    duckdb.connect(str(ais_file)).execute(ais_schema).close()

    an_schema = """
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """
    an_file = tmp_path / "analytics_tr.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute(an_schema)
    an_conn.executemany(
        "INSERT INTO transit_events VALUES (?,?,?,?,?,?,?,?)",
        [
            (9401, "dover_channel", h0, None, "northbound", "tanker", "VLCC", True),
            (9402, "dover_channel", h0, None, "southbound", "bulk",   "Capesize", True),
            (9403, "dover_channel", h0, None, "northbound", "tanker", "Suezmax", False),
            (9404, "suez",          h0, None, "northbound", "tanker", "VLCC", True),
            (9405, "suez",          h1, None, "northbound", "tanker", "VLCC", False),
            (9406, "suez",          h1, None, "southbound", "bulk",   "Capesize", True),
        ],
    )
    an_conn.close()

    reg_file = tmp_path / "reg_tr.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """)
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_transit_rate_structure(transit_rate_client):
    r = transit_rate_client.get("/api/analytics/transit-rate-timeline")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d and "hours" in d and "chokepoints" in d and "points" in d
    assert isinstance(d["chokepoints"], list)
    for pt in d["points"]:
        assert "hour" in pt and "chokepoint" in pt and "count" in pt and "laden_count" in pt


def test_transit_rate_dover_counts(transit_rate_client):
    r = transit_rate_client.get("/api/analytics/transit-rate-timeline?hours=72")
    d = r.json()
    dover_pts = [p for p in d["points"] if p["chokepoint"] == "dover_channel"]
    assert dover_pts
    pt = dover_pts[0]
    assert pt["count"] == 3
    assert pt["laden_count"] == 2


def test_transit_rate_suez_prev_hour(transit_rate_client):
    r = transit_rate_client.get("/api/analytics/transit-rate-timeline?hours=72")
    d = r.json()
    suez_pts = [p for p in d["points"] if p["chokepoint"] == "suez"]
    assert len(suez_pts) == 2
    total = sum(p["count"] for p in suez_pts)
    assert total == 3


def test_transit_rate_chokepoint_filter(transit_rate_client):
    r = transit_rate_client.get("/api/analytics/transit-rate-timeline?hours=72&chokepoints_csv=suez")
    d = r.json()
    assert all(p["chokepoint"] == "suez" for p in d["points"])
    assert "dover_channel" not in d["chokepoints"]


def test_transit_rate_chokepoints_list(transit_rate_client):
    r = transit_rate_client.get("/api/analytics/transit-rate-timeline?hours=72")
    d = r.json()
    assert "dover_channel" in d["chokepoints"]
    assert "suez" in d["chokepoints"]


# ---------------------------------------------------------------------------
# Phase 39: Anchorage Occupancy Timeline
# ---------------------------------------------------------------------------

@pytest.fixture
def anchorage_occ_client(tmp_path, monkeypatch):
    """Fixture for anchorage-occupancy endpoint.

    anchored_episodes at singapore_west:
      Episode 1: h0-3h to h0-1h (2 hours overlap with last 24h window)
      Episode 2: h0-1h to h0+1h (open right side, current hour)
      Episode 3: h0-5h to h0-4h (1 hour, earlier)
    rotterdam:
      Episode 4: h0-2h to h0 (2 hours)
    """
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    h0 = now.replace(minute=30, second=0, microsecond=0)  # mid-hour, floor will align

    ais_file = tmp_path / "ais_ao.duckdb"
    duckdb.connect(str(ais_file)).execute("""
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """).close()

    an_file = tmp_path / "analytics_ao.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    an_conn.executemany(
        "INSERT INTO anchored_episodes VALUES (?,?,?,?,?,?)",
        [
            (9501, "singapore_west", h0 - timedelta(hours=3), h0 - timedelta(hours=1), "tanker", "VLCC"),
            (9502, "singapore_west", h0 - timedelta(hours=1), h0 + timedelta(hours=1), "tanker", "Suezmax"),
            (9503, "singapore_west", h0 - timedelta(hours=5), h0 - timedelta(hours=4), "bulk",   "Capesize"),
            (9504, "rotterdam",      h0 - timedelta(hours=2), h0, "tanker", "VLCC"),
        ],
    )
    an_conn.close()

    reg_file = tmp_path / "reg_ao.duckdb"
    duckdb.connect(str(reg_file)).execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """).close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_anchorage_occupancy_structure(anchorage_occ_client):
    r = anchorage_occ_client.get("/api/analytics/anchorage-occupancy")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d and "hours" in d and "zones" in d and "points" in d
    for pt in d["points"]:
        assert "hour" in pt and "zone" in pt and "vessel_count" in pt


def test_anchorage_occupancy_returns_data(anchorage_occ_client):
    r = anchorage_occ_client.get("/api/analytics/anchorage-occupancy?hours=72&zones_csv=singapore_west,rotterdam")
    d = r.json()
    assert len(d["points"]) > 0
    zones = {p["zone"] for p in d["points"]}
    assert "singapore_west" in zones
    assert "rotterdam" in zones


def test_anchorage_occupancy_zone_filter(anchorage_occ_client):
    r = anchorage_occ_client.get("/api/analytics/anchorage-occupancy?hours=72&zones_csv=rotterdam")
    d = r.json()
    assert all(p["zone"] == "rotterdam" for p in d["points"])


def test_anchorage_occupancy_nonzero_counts(anchorage_occ_client):
    r = anchorage_occ_client.get("/api/analytics/anchorage-occupancy?hours=72&zones_csv=singapore_west")
    d = r.json()
    assert all(p["vessel_count"] > 0 for p in d["points"])


# ---------------------------------------------------------------------------
# Phase 40: STS Offenders
# ---------------------------------------------------------------------------

def test_sts_offenders_structure(risk_leaderboard_client):
    """STS offenders uses risk_leaderboard_client which has STS events."""
    r = risk_leaderboard_client.get("/api/analytics/sts-offenders?days=30&limit=50")
    assert r.status_code == 200
    d = r.json()
    assert "as_of" in d and "days" in d and "total_vessels" in d and "rows" in d
    for row in d["rows"]:
        assert "mmsi" in row and "sts_events" in row
        assert "as_initiator" in row and "as_counterpart" in row
        assert row["sts_events"] == row["as_initiator"] + row["as_counterpart"]


def test_sts_offenders_counts(risk_leaderboard_client):
    """Vessel 9001 has 3 STS events as mmsi, vessel 9003 has 1 as mmsi2."""
    r = risk_leaderboard_client.get("/api/analytics/sts-offenders?days=30&limit=50")
    d = r.json()
    mmsi_map = {row["mmsi"]: row for row in d["rows"]}
    if 9001 in mmsi_map:
        assert mmsi_map[9001]["as_initiator"] == 3
    if 9003 in mmsi_map:
        assert mmsi_map[9003]["as_counterpart"] >= 1


def test_sts_offenders_excludes_small(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/sts-offenders?days=30&limit=50")
    d = r.json()
    for row in d["rows"]:
        assert row.get("segment") != "Small"


def test_sts_offenders_sorted_desc(risk_leaderboard_client):
    r = risk_leaderboard_client.get("/api/analytics/sts-offenders?days=30&limit=50")
    d = r.json()
    events = [row["sts_events"] for row in d["rows"]]
    assert events == sorted(events, reverse=True)


# ---------------------------------------------------------------------------
# Phase 41: Fleet Historical Query (fleet-at-time)
# ---------------------------------------------------------------------------

@pytest.fixture
def fleet_history_client(tmp_path, monkeypatch):
    """Fixture for fleet-at-time endpoint.

    ais_snapshots has 3 vessels at a known timestamp.
    """
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    snap_ts = now - timedelta(hours=23)  # within 30min of 24h ago query

    ais_schema = """
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """
    ais_file = tmp_path / "ais_fh.duckdb"
    ais_conn = duckdb.connect(str(ais_file))
    ais_conn.execute(ais_schema)
    ais_conn.executemany(
        "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (snap_ts, 9601, "tanker", "VLCC",     "hormuz",    25.0, 56.0, 80, 330, 12.0, 0, 18.0, "CNSHA"),
            (snap_ts, 9602, "bulk",   "Capesize",  "ara",       3.5,  5.0, 74, 300,  8.0, 0,  7.0, "NLRTM"),
            (snap_ts, 9603, "tanker", "VLCC",     "hormuz",    25.1, 56.1, 80, 330,  0.5, 5,  4.0, None),
        ],
    )
    ais_conn.close()

    an_file = tmp_path / "analytics_fh.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    an_conn.close()

    reg_file = tmp_path / "reg_fh.duckdb"
    duckdb.connect(str(reg_file)).execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """).close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_fleet_history_structure(fleet_history_client):
    r = fleet_history_client.get("/api/analytics/fleet-at-time")
    assert r.status_code == 200
    d = r.json()
    assert "queried_ts" in d and "actual_ts" in d and "total_vessels" in d and "segments" in d
    for seg in d["segments"]:
        assert "kind" in seg and "segment" in seg and "count" in seg


def test_fleet_history_finds_snapshots(fleet_history_client):
    from datetime import datetime, timedelta, UTC
    target_ts = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=23)).isoformat()
    r = fleet_history_client.get(f"/api/analytics/fleet-at-time?ts={target_ts}")
    d = r.json()
    assert d["total_vessels"] == 3


def test_fleet_history_region_filter(fleet_history_client):
    from datetime import datetime, timedelta, UTC
    target_ts = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=23)).isoformat()
    r = fleet_history_client.get(f"/api/analytics/fleet-at-time?ts={target_ts}&region=hormuz")
    d = r.json()
    assert d["total_vessels"] == 2
    assert d["region"] == "hormuz"


def test_fleet_history_segment_breakdown(fleet_history_client):
    from datetime import datetime, timedelta, UTC
    target_ts = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=23)).isoformat()
    r = fleet_history_client.get(f"/api/analytics/fleet-at-time?ts={target_ts}")
    d = r.json()
    vlcc = next((s for s in d["segments"] if s["segment"] == "VLCC"), None)
    assert vlcc is not None
    assert vlcc["count"] == 2


# ---------------------------------------------------------------------------
# Phase 42: Destination Change Intelligence
# ---------------------------------------------------------------------------

@pytest.fixture
def dest_changes_client(tmp_path, monkeypatch):
    """Fixture: 3 vessels with destination changes in ais_snapshots."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    t1 = now - timedelta(hours=10)
    t2 = now - timedelta(hours=5)

    ais_file = tmp_path / "ais_dc.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """)
    # Vessel 1: changed from NLROT -> CNSHA (genuine reroute)
    conn.executemany(
        "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (t1, 9701, "tanker", "VLCC", "ara", 52.0, 4.0, 80, 330, 12.0, 0, 18.0, "NLROT"),
            (t2, 9701, "tanker", "VLCC", "ara", 52.1, 4.1, 80, 330, 13.0, 0, 17.5, "CNSHA"),
            # Vessel 2: same destination both snapshots (no change)
            (t1, 9702, "bulk",   "Capesize", "hormuz", 25.0, 56.0, 74, 300, 8.0, 0, 7.0, "JPUKB"),
            (t2, 9702, "bulk",   "Capesize", "hormuz", 25.1, 56.1, 74, 300, 8.0, 0, 7.0, "JPUKB"),
            # Vessel 3: changed from GBSOU -> NLROT
            (t1, 9703, "bulk",   "Handysize", "dover_channel", 50.9, -1.4, 74, 100, 6.0, 0, 4.0, "GBSOU"),
            (t2, 9703, "bulk",   "Handysize", "dover_channel", 50.8, -1.3, 74, 100, 6.0, 0, 4.0, "NLROT"),
        ],
    )
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (9701, "VESSEL A", 52.1, 4.1, 13.0, 90.0, 90.0, "CNSHA", 80, 330.0, "tanker", "VLCC", "ara", t2, 9000001, 17.5, 0, None),
        (9703, "VESSEL C", 50.8, -1.3, 6.0, 180.0, 180.0, "NLROT", 74, 100.0, "bulk", "Handysize", "dover_channel", t2, 9000003, 4.0, 0, None),
    ])
    conn.close()

    an_file = tmp_path / "analytics_dc.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    an_conn.close()

    reg_file = tmp_path / "reg_dc.duckdb"
    duckdb.connect(str(reg_file)).execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """).close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_dest_changes_structure(dest_changes_client):
    r = dest_changes_client.get("/api/analytics/destination-changes")
    assert r.status_code == 200
    d = r.json()
    assert "total_changes" in d and "rows" in d and "hours" in d


def test_dest_changes_finds_reroutes(dest_changes_client):
    r = dest_changes_client.get("/api/analytics/destination-changes?hours=72")
    d = r.json()
    assert d["total_changes"] == 2
    mmsis = {row["mmsi"] for row in d["rows"]}
    assert 9701 in mmsis and 9703 in mmsis
    assert 9702 not in mmsis


def test_dest_changes_normalized_destinations(dest_changes_client):
    r = dest_changes_client.get("/api/analytics/destination-changes?hours=72")
    d = r.json()
    vessel_a = next(row for row in d["rows"] if row["mmsi"] == 9701)
    assert vessel_a["from_dest"] == "NLROT"
    assert vessel_a["to_dest"] == "CNSHA"


def test_dest_changes_enriched_with_position(dest_changes_client):
    r = dest_changes_client.get("/api/analytics/destination-changes?hours=72")
    d = r.json()
    vessel_a = next(row for row in d["rows"] if row["mmsi"] == 9701)
    assert vessel_a["name"] == "VESSEL A"
    assert vessel_a["lat"] is not None


def test_dest_changes_kind_filter(dest_changes_client):
    r = dest_changes_client.get("/api/analytics/destination-changes?hours=72&kind=tanker")
    d = r.json()
    assert d["total_changes"] == 1
    assert d["rows"][0]["mmsi"] == 9701


# ---------------------------------------------------------------------------
# Phase 43: Owner Intelligence
# ---------------------------------------------------------------------------

@pytest.fixture
def owner_intel_client(tmp_path, monkeypatch):
    """Fixture: vessel_registry with 3 owners; live_positions with kind data."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_file = tmp_path / "ais_oi.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (9801, "V1", 25.0, 56.0, 12.0, 90.0, 90.0, "NLROT", 80, 330.0, "tanker", "VLCC", "hormuz", now, 1000001, 18.0, 0, None),
        (9802, "V2", 25.1, 56.1, 13.0, 91.0, 91.0, "CNSHA", 80, 330.0, "tanker", "VLCC", "hormuz", now, 1000002, 17.0, 0, None),
        (9803, "V3", 3.5, 5.0, 8.0, 180.0, 180.0, "NLRTM", 74, 300.0, "bulk", "Capesize", "ara", now, 1000003, 7.0, 0, None),
    ])
    conn.close()

    an_file = tmp_path / "analytics_oi.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    an_conn.close()

    reg_file = tmp_path / "reg_oi.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """)
    reg_conn.executemany(
        "INSERT INTO vessel_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1000001, "V1", "Russia", "RU", None, 200000, 300000, "Tanker", 2005, "In Service", "SHADOW FLEET LLC", None, None, "RMRS", None, None, "Black", "Black", None, None, True, 72, None, True),
            (1000002, "V2", "Nauru", "NR", None, 200000, 300000, "Tanker", 2004, "In Service", "SHADOW FLEET LLC", None, None, "RMRS", None, None, "Black", "Black", None, None, True, 75, None, False),
            (1000003, "V3", "Marshall Islands", "MH", None, 150000, 200000, "Bulk Carrier", 2018, "In Service", "CLEAN OWNER PTE", None, None, "ABS (IACS)", None, None, "White", "White", None, None, True, 10, None, False),
        ],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_owner_intel_structure(owner_intel_client):
    r = owner_intel_client.get("/api/analytics/owner-intelligence")
    assert r.status_code == 200
    d = r.json()
    assert "total_owners" in d and "rows" in d


def test_owner_intel_finds_owners(owner_intel_client):
    r = owner_intel_client.get("/api/analytics/owner-intelligence?min_vessels=1")
    d = r.json()
    owner_names = {row["owner"] for row in d["rows"]}
    assert "SHADOW FLEET LLC" in owner_names
    assert "CLEAN OWNER PTE" in owner_names


def test_owner_intel_high_risk_count(owner_intel_client):
    r = owner_intel_client.get("/api/analytics/owner-intelligence?min_vessels=1")
    d = r.json()
    shadow = next(row for row in d["rows"] if row["owner"] == "SHADOW FLEET LLC")
    assert shadow["vessel_count"] == 2
    assert shadow["high_risk_count"] == 2
    assert shadow["tanker_count"] == 2


def test_owner_intel_sorted_by_risk_weighted(owner_intel_client):
    r = owner_intel_client.get("/api/analytics/owner-intelligence?min_vessels=1")
    d = r.json()
    weights = [row["risk_weighted"] for row in d["rows"]]
    assert weights == sorted(weights, reverse=True)


def test_owner_intel_min_vessels_filter(owner_intel_client):
    r = owner_intel_client.get("/api/analytics/owner-intelligence?min_vessels=2")
    d = r.json()
    for row in d["rows"]:
        assert row["vessel_count"] >= 2


# ---------------------------------------------------------------------------
# Phase 44: Chokepoint Throughput Anomaly Detection
# ---------------------------------------------------------------------------

@pytest.fixture
def chokepoint_anomaly_client(tmp_path, monkeypatch):
    """Fixture: transit_events with high activity at suez (recent spike) and normal elsewhere."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_file = tmp_path / "ais_ca.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """)
    conn.close()

    an_file = tmp_path / "analytics_ca.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    # Baseline: suez with varying hourly counts (1-3) to produce non-zero std
    # so that z_score can be computed
    baseline_entries = []
    mmsi_counter = 9000
    for h in range(3, 27):  # 3-27h ago = inside baseline (outside 2h recent window)
        # alternate 1, 2, 3 vessels per hour to produce variance
        count = (h % 3) + 1
        for v in range(count):
            baseline_entries.append((
                mmsi_counter, "suez", now - timedelta(hours=h + 0.1 + v * 0.01),
                now - timedelta(hours=h - 0.1 + 0.5 + v * 0.01), "N", "tanker", "VLCC", True
            ))
            mmsi_counter += 1
    # Recent: suez 20 vessels in last 2h (massive spike vs baseline avg of 2)
    for v in range(20):
        baseline_entries.append((
            8000 + v, "suez", now - timedelta(hours=1.0, minutes=v),
            now - timedelta(hours=0.5, minutes=v), "N", "tanker", "VLCC", True
        ))
    # Dover: varying 2-4 per hour for baseline, 0 recent (should show as low)
    for h in range(3, 27):
        count = (h % 3) + 2
        for v in range(count):
            baseline_entries.append((
                7000 + h * 10 + v, "dover_channel", now - timedelta(hours=h + 0.1 + v * 0.01),
                now - timedelta(hours=h - 0.1 + 0.5 + v * 0.01), "N", "bulk", "Capesize", False
            ))
    an_conn.executemany(
        "INSERT INTO transit_events VALUES (?,?,?,?,?,?,?,?)",
        baseline_entries,
    )
    an_conn.close()

    reg_file = tmp_path / "reg_ca.duckdb"
    duckdb.connect(str(reg_file)).execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """).close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_chokepoint_anomaly_structure(chokepoint_anomaly_client):
    r = chokepoint_anomaly_client.get("/api/analytics/chokepoint-anomaly")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d and "window_hours" in d and "baseline_hours" in d
    for row in d["rows"]:
        assert "chokepoint" in row and "recent_count" in row and "direction" in row


def test_chokepoint_anomaly_detects_spike(chokepoint_anomaly_client):
    r = chokepoint_anomaly_client.get("/api/analytics/chokepoint-anomaly?window_hours=2&baseline_hours=24")
    d = r.json()
    suez = next(row for row in d["rows"] if row["chokepoint"] == "suez")
    assert suez["recent_count"] == 20
    assert suez["z_score"] is not None
    assert suez["z_score"] > 2.0
    assert suez["direction"] == "high"


def test_chokepoint_anomaly_detects_low(chokepoint_anomaly_client):
    r = chokepoint_anomaly_client.get("/api/analytics/chokepoint-anomaly?window_hours=2&baseline_hours=24")
    d = r.json()
    dover = next(row for row in d["rows"] if row["chokepoint"] == "dover_channel")
    assert dover["recent_count"] == 0
    assert dover["z_score"] is not None
    assert dover["z_score"] < -2.0
    assert dover["direction"] == "low"


def test_chokepoint_anomaly_sorted_by_magnitude(chokepoint_anomaly_client):
    r = chokepoint_anomaly_client.get("/api/analytics/chokepoint-anomaly?window_hours=2&baseline_hours=24")
    d = r.json()
    rows_with_z = [row for row in d["rows"] if row["z_score"] is not None]
    if len(rows_with_z) >= 2:
        z_magnitudes = [abs(row["z_score"]) for row in rows_with_z]
        assert z_magnitudes == sorted(z_magnitudes, reverse=True)


# ---------------------------------------------------------------------------
# Phase 45: Cargo State Transition Detection
# ---------------------------------------------------------------------------

@pytest.fixture
def cargo_state_client(tmp_path, monkeypatch):
    """Fixture: tanker anchored at rotterdam; draught drops from 15 to 9 = discharge."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    start_ep = now - timedelta(hours=30)
    end_ep = now - timedelta(hours=20)
    snap_entry = start_ep + timedelta(minutes=15)
    snap_exit = end_ep - timedelta(minutes=15)

    ais_file = tmp_path / "ais_csc.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (9901, "TANKER X", 51.9, 4.1, 0.5, 0.0, 0.0, "NLROT", 80, 330.0, "tanker", "Aframax", "ara", now, 5000001, 9.0, 1, None),
    ])
    conn.executemany(
        "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (snap_entry, 9901, "tanker", "Aframax", "ara", 51.9, 4.1, 80, 330.0, 0.5, 1, 15.0, "NLROT"),
            (snap_exit,  9901, "tanker", "Aframax", "ara", 51.9, 4.1, 80, 330.0, 0.5, 1,  9.0, "NLROT"),
        ],
    )
    conn.close()

    an_file = tmp_path / "analytics_csc.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    an_conn.executemany(
        "INSERT INTO anchored_episodes VALUES (?,?,?,?,?,?)",
        [(9901, "rotterdam", start_ep, end_ep, "tanker", "Aframax")],
    )
    an_conn.close()

    reg_file = tmp_path / "reg_csc.duckdb"
    duckdb.connect(str(reg_file)).execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """).close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_cargo_state_structure(cargo_state_client):
    r = cargo_state_client.get("/api/analytics/cargo-state-changes?days=7")
    assert r.status_code == 200
    d = r.json()
    assert "total_events" in d and "rows" in d


def test_cargo_state_detects_discharge(cargo_state_client):
    r = cargo_state_client.get("/api/analytics/cargo-state-changes?days=7&kind=tanker&min_change_m=2.0")
    d = r.json()
    assert d["total_events"] >= 1
    row = d["rows"][0]
    assert row["cargo_state"] == "discharged"
    assert row["draught_change_m"] < 0
    assert abs(row["draught_change_m"]) >= 2.0


def test_cargo_state_draught_values(cargo_state_client):
    r = cargo_state_client.get("/api/analytics/cargo-state-changes?days=7&min_change_m=1.0")
    d = r.json()
    row = next(r for r in d["rows"] if r["mmsi"] == 9901)
    assert row["draught_entry"] == 15.0
    assert row["draught_exit"] == 9.0
    assert row["zone"] == "rotterdam"


def test_cargo_state_min_change_filter(cargo_state_client):
    r = cargo_state_client.get("/api/analytics/cargo-state-changes?days=7&min_change_m=10.0")
    d = r.json()
    assert d["total_events"] == 0


# ---------------------------------------------------------------------------
# Phase 46: Speed Anomaly Detection
# ---------------------------------------------------------------------------

@pytest.fixture
def speed_anomaly_client(tmp_path, monkeypatch):
    """Fixture: fleet of VLCC tankers with one very fast and one very slow outlier."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_file = tmp_path / "ais_sa.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    """)
    # 8 VLCCs at typical 14 kn, 1 fast outlier at 22 kn, 1 slow outlier at 5 kn
    normal_vessels = [
        (100 + i, f"VLCC NORMAL {i}", 10.0 + i, 50.0 + i, 14.0, 90.0, 90.0,
         "AEJEA", 80, 340.0, "tanker", "VLCC", "arabian_gulf", now,
         None, 20.0, 0, None)
        for i in range(8)
    ]
    fast_vessel = (200, "VLCC FAST", 20.0, 55.0, 22.0, 90.0, 90.0,
                   "USHOU", 80, 340.0, "tanker", "VLCC", "us_gulf", now,
                   None, 18.0, 0, None)
    slow_vessel = (201, "VLCC SLOW", 21.0, 56.0, 5.0, 90.0, 90.0,
                   "CNSHA", 80, 340.0, "tanker", "VLCC", "east_china", now,
                   None, 22.0, 0, None)
    all_vessels = normal_vessels + [fast_vessel, slow_vessel]
    conn.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        all_vessels,
    )
    conn.close()

    registry_file = tmp_path / "registry_sa.duckdb"
    reg_conn = duckdb.connect(str(registry_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, risk_score INTEGER, fetch_ok BOOLEAN
    );
    """)
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("REGISTRY_DB", str(registry_file))
    from app.main import app
    return TestClient(app)


def test_speed_anomaly_structure(speed_anomaly_client):
    r = speed_anomaly_client.get("/api/analytics/speed-anomalies?kind=tanker&min_z=2.0&limit=50")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d
    assert "total_vessels_checked" in d
    assert "anomaly_count" in d
    assert d["total_vessels_checked"] == 10
    assert d["anomaly_count"] >= 1


def test_speed_anomaly_detects_fast(speed_anomaly_client):
    r = speed_anomaly_client.get("/api/analytics/speed-anomalies?kind=tanker&min_z=2.0&limit=50")
    d = r.json()
    fast = [row for row in d["rows"] if row["mmsi"] == 200]
    assert len(fast) == 1
    assert fast[0]["anomaly_type"] == "fast"
    assert fast[0]["z_score"] > 2.0


def test_speed_anomaly_detects_slow(speed_anomaly_client):
    r = speed_anomaly_client.get("/api/analytics/speed-anomalies?kind=tanker&min_z=2.0&limit=50")
    d = r.json()
    slow = [row for row in d["rows"] if row["mmsi"] == 201]
    assert len(slow) == 1
    assert slow[0]["anomaly_type"] == "slow"
    assert slow[0]["z_score"] < -2.0


def test_speed_anomaly_sorted_by_z(speed_anomaly_client):
    r = speed_anomaly_client.get("/api/analytics/speed-anomalies?kind=tanker&min_z=2.0&limit=50")
    d = r.json()
    scores = [abs(row["z_score"]) for row in d["rows"]]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Phase 47: Port Arrival Forecast
# ---------------------------------------------------------------------------

@pytest.fixture
def port_arrival_client(tmp_path, monkeypatch):
    """Fixture: tanker heading for Rotterdam (NLRTM) with ETA ~20h."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(tzinfo=None)
    ais_file = tmp_path / "ais_pa.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    """)
    # Vessel heading to Rotterdam from ~220 nm away at 12 kn -> ETA ~18h
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (5001, "TANKER A", 49.0, 3.5, 12.0, 60.0, 60.0, "NLRTM", 80, 330.0, "tanker", "Aframax", "north_sea", now, None, 20.0, 0, None),
        (5002, "TANKER B", 50.0, 2.0, 10.0, 60.0, 60.0, "ROTTERDAM", 80, 300.0, "tanker", "Suezmax", "north_sea", now, None, 18.0, 0, None),
        # This one is > 48h away (very slow and far)
        (5003, "TANKER C", 20.0, 0.0, 2.0, 30.0, 30.0, "NLRTM", 80, 300.0, "tanker", "VLCC", "west_africa", now, None, 22.0, 0, None),
        # Not a tanker - should be excluded by kind filter
        (5004, "BULKER X", 49.5, 3.0, 11.0, 60.0, 60.0, "NLRTM", 70, 200.0, "bulk", "Capesize", "north_sea", now, None, 15.0, 0, None),
    ])
    conn.close()

    analytics_file = tmp_path / "analytics_pa.duckdb"
    ac = duckdb.connect(str(analytics_file))
    ac.execute("CREATE TABLE vessel_state (mmsi BIGINT PRIMARY KEY, laden VARCHAR, last_draught DOUBLE, max_draught_seen DOUBLE, updated_ts TIMESTAMP)")
    ac.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", [
        (5001, "laden", 20.0, 21.0, now),
        (5002, "ballast", 12.0, 21.0, now),
    ])
    ac.close()

    registry_file = tmp_path / "registry_pa.duckdb"
    rc = duckdb.connect(str(registry_file))
    rc.execute("CREATE TABLE vessel_registry (imo BIGINT PRIMARY KEY, risk_score INTEGER, fetch_ok BOOLEAN)")
    rc.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_file))
    monkeypatch.setenv("REGISTRY_DB", str(registry_file))
    from app.main import app
    return TestClient(app)


def test_port_arrival_structure(port_arrival_client):
    r = port_arrival_client.get("/api/analytics/port-arrivals?kind=tanker&horizon_h=48")
    assert r.status_code == 200
    d = r.json()
    assert "ports" in d
    assert "total_inbound" in d
    assert d["total_inbound"] >= 1


def test_port_arrival_matches_rotterdam(port_arrival_client):
    r = port_arrival_client.get("/api/analytics/port-arrivals?kind=tanker&horizon_h=48")
    d = r.json()
    rdam = next((p for p in d["ports"] if p["port"] == "Rotterdam"), None)
    assert rdam is not None
    assert rdam["arrivals_48h"] >= 1
    mmsis = [v["mmsi"] for v in rdam["vessels"]]
    assert 5001 in mmsis or 5002 in mmsis


def test_port_arrival_horizon_filter(port_arrival_client):
    # With a tight 5h horizon, slow vessel far away should not appear
    r = port_arrival_client.get("/api/analytics/port-arrivals?kind=tanker&horizon_h=12")
    d = r.json()
    rdam = next((p for p in d["ports"] if p["port"] == "Rotterdam"), None)
    # VLCC at sog=2 from lat=20 is ~1650nm away -> ETA=825h >> 12h
    if rdam:
        assert all(v["mmsi"] != 5003 for v in rdam["vessels"])


def test_port_arrival_kind_filter(port_arrival_client):
    r = port_arrival_client.get("/api/analytics/port-arrivals?kind=tanker&horizon_h=48")
    d = r.json()
    all_kinds = [v["kind"] for p in d["ports"] for v in p["vessels"]]
    assert all(k == "tanker" for k in all_kinds), "non-tanker appeared in tanker-filtered response"


# ---------------------------------------------------------------------------
# Phase 48: Crude Oil on Water
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture
def crude_client(tmp_path, monkeypatch):
    """Fixture: 3 laden tankers (VLCC, Aframax, Small) + 1 ballast Suezmax."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(tzinfo=None)
    ais_file = tmp_path / "ais_cow.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    """)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (6001, "VLCC ALPHA", 25.0, 57.0, 13.0, 270.0, 270.0, "NLRTM", 80, 330.0, "tanker", "VLCC", "indian_ocean", now, None, 21.0, 0, None),
        (6002, "AFRA BETA", 48.0, -5.0, 12.0, 90.0, 90.0, "ESBCN", 80, 250.0, "tanker", "Aframax", "atlantic", now, None, 12.5, 0, None),
        (6003, "SMALL GAMMA", 35.0, 15.0, 10.0, 180.0, 180.0, "ITGOA", 80, 120.0, "tanker", "Small", "med", now, None, 5.5, 0, None),
        (6004, "SUEZ DELTA", 20.0, 60.0, 11.0, 0.0, 0.0, "AEFJR", 80, 280.0, "tanker", "Suezmax", "indian_ocean", now, None, 7.0, 0, None),
        # Bulk carrier - should NOT count
        (6005, "BULK ETA", 40.0, 20.0, 8.0, 90.0, 90.0, "DEHAM", 70, 200.0, "bulk", "Capesize", "med", now, None, 10.0, 0, None),
    ])
    conn.close()

    analytics_file = tmp_path / "analytics_cow.duckdb"
    ac = duckdb.connect(str(analytics_file))
    ac.execute("CREATE TABLE vessel_state (mmsi BIGINT PRIMARY KEY, laden VARCHAR, last_draught DOUBLE, max_draught_seen DOUBLE, updated_ts TIMESTAMP)")
    ac.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", [
        (6001, "laden", 21.0, 22.0, now),   # VLCC laden
        (6002, "laden", 12.5, 14.0, now),   # Aframax laden
        (6003, "laden", 5.5, 6.0, now),     # Small laden
        (6004, "ballast", 7.0, 20.0, now),  # Suezmax ballast
    ])
    ac.close()

    registry_file = tmp_path / "registry_cow.duckdb"
    rc = duckdb.connect(str(registry_file))
    rc.execute("CREATE TABLE vessel_registry (imo BIGINT PRIMARY KEY, risk_score INTEGER, fetch_ok BOOLEAN)")
    rc.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_file))
    monkeypatch.setenv("REGISTRY_DB", str(registry_file))
    from app.main import app
    return TestClient(app)


def test_crude_on_water_structure(crude_client):
    r = crude_client.get("/api/analytics/crude-on-water")
    assert r.status_code == 200
    d = r.json()
    assert "total_laden_tankers" in d
    assert "estimated_mb_on_water" in d
    assert "by_segment" in d
    assert "inbound_regions" in d


def test_crude_on_water_counts(crude_client):
    r = crude_client.get("/api/analytics/crude-on-water")
    d = r.json()
    assert d["total_laden_tankers"] == 3, "expected 3 laden tankers"
    assert d["total_ballast_tankers"] == 1, "expected 1 ballast tanker"
    # Bulk carrier must not be counted
    assert d["total_laden_tankers"] + d["total_ballast_tankers"] <= 4


def test_crude_on_water_mb_positive(crude_client):
    r = crude_client.get("/api/analytics/crude-on-water")
    d = r.json()
    assert d["estimated_mb_on_water"] > 0
    # VLCC 2.0 MB + Aframax 0.75 MB + Small 0.30 MB = ~3.05 MB
    assert d["estimated_mb_on_water"] > 2.0


def test_crude_on_water_inbound_regions(crude_client):
    r = crude_client.get("/api/analytics/crude-on-water")
    d = r.json()
    regions = [reg["region"] for reg in d["inbound_regions"]]
    # NL -> Europe, ES -> Europe, IT -> Europe
    assert "Europe" in regions


# ---------------------------------------------------------------------------
# Phase 49: Chokepoint Live Status
# ---------------------------------------------------------------------------


@pytest.fixture
def chokepoint_status_client(tmp_path, monkeypatch):
    """Fixture: vessels spread across suez + dover_channel regions with varying SOG."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    ais_file = tmp_path / "ais_cps.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    """)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        # suez: 2 transiting (sog>4), 3 waiting (sog<=0.5)
        (7001, "A", 30.0, 33.0, 12.0, 0.0, 0.0, None, 80, 330.0, "tanker", "VLCC", "suez", now, None, 20.0, 0, None),
        (7002, "B", 30.1, 33.1, 9.0, 0.0, 0.0, None, 80, 250.0, "tanker", "Suezmax", "suez", now, None, 18.0, 0, None),
        (7003, "C", 29.8, 32.8, 0.1, 0.0, 0.0, None, 80, 330.0, "tanker", "VLCC", "suez", now, None, 5.0, 0, None),
        (7004, "D", 29.9, 32.9, 0.0, 0.0, 0.0, None, 80, 250.0, "bulk", "Capesize", "suez", now, None, 15.0, 0, None),
        (7005, "E", 30.2, 33.2, 0.3, 0.0, 0.0, None, 80, 200.0, "bulk", "Supramax", "suez", now, None, 12.0, 0, None),
        # dover_channel: 1 transiting, 1 slow (not waiting, not transiting)
        (7006, "F", 50.5, 1.0, 15.0, 90.0, 90.0, None, 70, 200.0, "bulk", "Capesize", "dover_channel", now, None, 10.0, 0, None),
        (7007, "G", 50.6, 1.1, 2.0, 90.0, 90.0, None, 70, 180.0, "bulk", "Panamax", "dover_channel", now, None, 8.0, 0, None),
    ])
    conn.close()

    analytics_file = tmp_path / "analytics_cps.duckdb"
    ac = duckdb.connect(str(analytics_file))
    ac.execute("CREATE TABLE vessel_state (mmsi BIGINT PRIMARY KEY, laden VARCHAR, last_draught DOUBLE, max_draught_seen DOUBLE, updated_ts TIMESTAMP)")
    # transit_events for suez: 3 in last 7d (2 northbound, 1 southbound), 1 in last 24h
    ago2d = (now - __import__('datetime').timedelta(days=2)).isoformat()
    ago5d = (now - __import__('datetime').timedelta(days=5)).isoformat()
    ago1h = (now - __import__('datetime').timedelta(hours=1)).isoformat()
    ago2h = (now - __import__('datetime').timedelta(hours=2)).isoformat()
    ac.execute("""CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN
    )""")
    ac.executemany("INSERT INTO transit_events VALUES (?,?,?,?,?,?,?,?)", [
        (7001, "suez", ago5d, ago5d, "northbound", "tanker", "VLCC", True),
        (7002, "suez", ago2d, ago2d, "northbound", "tanker", "Suezmax", True),
        (7003, "suez", ago1h, ago2h, "southbound", "tanker", "VLCC", False),
        (7006, "dover_channel", ago2d, ago2d, "eastbound", "bulk", "Capesize", None),
    ])
    ac.close()

    registry_file = tmp_path / "registry_cps.duckdb"
    rc = duckdb.connect(str(registry_file))
    rc.execute("CREATE TABLE vessel_registry (imo BIGINT PRIMARY KEY, risk_score INTEGER, fetch_ok BOOLEAN)")
    rc.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_file))
    monkeypatch.setenv("REGISTRY_DB", str(registry_file))
    from app.main import app
    return TestClient(app)


def test_chokepoint_status_structure(chokepoint_status_client):
    r = chokepoint_status_client.get("/api/analytics/chokepoint-status")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d
    assert "as_of" in d
    assert len(d["rows"]) > 0


def test_chokepoint_status_suez_counts(chokepoint_status_client):
    r = chokepoint_status_client.get("/api/analytics/chokepoint-status")
    d = r.json()
    suez = next((row for row in d["rows"] if row["chokepoint"] == "suez"), None)
    assert suez is not None
    assert suez["live_total"] == 5
    assert suez["live_transiting"] == 2   # sog > 4
    assert suez["live_waiting"] == 3      # sog <= 0.5


def test_chokepoint_status_transit_counts(chokepoint_status_client):
    r = chokepoint_status_client.get("/api/analytics/chokepoint-status")
    d = r.json()
    suez = next((row for row in d["rows"] if row["chokepoint"] == "suez"), None)
    assert suez["n_transits_7d"] == 3
    # 1 in last 24h (ago1h)
    assert suez["n_transits_24h"] == 1
    # 2 northbound out of 3 = 66.7%
    assert suez["pct_fwd_direction"] is not None
    assert 60 <= suez["pct_fwd_direction"] <= 70


# ---------------------------------------------------------------------------
# fleet-trend (Phase 51)
# ---------------------------------------------------------------------------


def test_fleet_trend_response_shape(client):
    r = client.get("/api/analytics/fleet-trend")
    assert r.status_code == 200
    d = r.json()
    assert "series" in d
    assert "days" in d
    assert d["days"] == 30
    assert isinstance(d["series"], list)


def test_fleet_trend_with_data(analytics_client):
    r = analytics_client.get("/api/analytics/fleet-trend?days=30")
    assert r.status_code == 200
    d = r.json()
    assert "series" in d
    assert "as_of" in d
    assert d["days"] == 30
    # Seed has 2 fleet_density rows for hormuz/VLCC: laden=2+2=4, ballast=1+0=1, unknown=0+1=1
    assert len(d["series"]) == 1  # both seed rows are on the same day
    day = d["series"][0]
    assert day["laden"] == 4
    assert day["ballast"] == 1
    assert day["unknown"] == 1
    assert day["total"] == 6


def test_fleet_trend_region_filter(analytics_client):
    r = analytics_client.get("/api/analytics/fleet-trend?days=30&region=hormuz")
    assert r.status_code == 200
    d = r.json()
    assert d["region"] == "hormuz"
    assert len(d["series"]) == 1


def test_fleet_trend_no_region_filter(analytics_client):
    r = analytics_client.get("/api/analytics/fleet-trend?days=30&region=suez")
    assert r.status_code == 200
    d = r.json()
    # No suez data in seed
    assert d["series"] == []


# Phase 54: Pipeline disruption layer (reads PostgreSQL - live server only)
def test_pipelines_disrupted_only(client):
    r = client.get("/api/pipelines")
    assert r.status_code == 200
    body = r.json()
    assert "pipelines" in body
    assert "total_offline" in body
    assert "total_reduced" in body
    assert isinstance(body["pipelines"], list)
    for p in body["pipelines"]:
        assert p["physical_state"] in ("offline", "reduced")
        assert p["start_lat"] is not None
        assert p["end_lat"] is not None


def test_pipelines_response_schema(client):
    r = client.get("/api/pipelines")
    assert r.status_code == 200
    body = r.json()
    assert body["disrupted_only"] is True
    assert body["total_offline"] >= 0
    assert body["total_reduced"] >= 0
    assert body["total_offline_mbd"] >= 0
    assert body["total_offline_bcm"] >= 0
    # Pipeline count consistent with totals
    n_offline = sum(1 for p in body["pipelines"] if p["physical_state"] == "offline")
    n_reduced = sum(1 for p in body["pipelines"] if p["physical_state"] == "reduced")
    assert n_offline == body["total_offline"]
    assert n_reduced == body["total_reduced"]


def test_pipelines_all_mode(client):
    r = client.get("/api/pipelines?disrupted_only=false")
    assert r.status_code == 200
    body = r.json()
    assert body["disrupted_only"] is False
    # All-pipelines mode returns more than disrupted-only mode
    r2 = client.get("/api/pipelines")
    body2 = r2.json()
    assert len(body["pipelines"]) > len(body2["pipelines"])


# ---------------------------------------------------------------------------
# Phase 55: Owner Fleet Status
# ---------------------------------------------------------------------------

@pytest.fixture
def owner_fleet_client(tmp_path, monkeypatch):
    """Seed: 3 live vessels (2 tankers for SHADOW FLEET LLC, 1 bulk for CLEAN OWNER)
    with laden/ballast state; owner-fleet-status should aggregate correctly."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(tzinfo=None)

    ais_file = tmp_path / "ais_ofs.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
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
    CREATE TABLE vessels (
        mmsi BIGINT PRIMARY KEY, imo BIGINT, name VARCHAR, flag VARCHAR,
        flag_iso2 VARCHAR, mid INTEGER, call_sign VARCHAR, vessel_type VARCHAR,
        dwt INTEGER, grt INTEGER, build_year INTEGER, length_m DOUBLE, beam_m DOUBLE,
        owner VARCHAR, manager VARCHAR, class_society VARCHAR, enriched_at TIMESTAMP, equasis_ok BOOLEAN
    );
    """)
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (7001, "T1", 25.0, 56.0, 12.0, 90.0, 90.0, "CNSHA", 80, 330.0, "tanker", "VLCC", "hormuz", now, 2000001, 18.5, 0, None),
        (7002, "T2", 25.1, 56.1, 13.0, 91.0, 91.0, "NLRTM", 80, 330.0, "tanker", "VLCC", "hormuz", now, 2000002, 8.5, 0, None),
        (7003, "B1", 3.5, 5.0, 8.0, 180.0, 180.0, "CNQIN", 74, 300.0, "bulk", "Capesize", "ara", now, 2000003, 15.0, 0, None),
    ])
    conn.close()

    an_file = tmp_path / "analytics_ofs.duckdb"
    an_conn = duckdb.connect(str(an_file))
    an_conn.execute("""
    CREATE TABLE meta_watermark (key VARCHAR PRIMARY KEY, ts TIMESTAMP);
    CREATE TABLE ais_events (
        event_id VARCHAR PRIMARY KEY, type VARCHAR, mmsi BIGINT, mmsi2 BIGINT,
        start_ts TIMESTAMP, end_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
        region VARCHAR, kind VARCHAR, segment VARCHAR, details VARCHAR
    );
    CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    );
    CREATE TABLE anchored_episodes (
        mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
        kind VARCHAR, segment VARCHAR, PRIMARY KEY (mmsi, zone, start_ts)
    );
    CREATE TABLE fleet_density (
        ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
        laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
        PRIMARY KEY (ts, region, kind, segment)
    );
    CREATE TABLE vessel_state (
        mmsi BIGINT PRIMARY KEY, max_draught_seen DOUBLE, last_draught DOUBLE,
        laden VARCHAR, updated_ts TIMESTAMP
    );
    """)
    # T1 laden, T2 ballast, B1 laden
    an_conn.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", [
        (7001, 19.0, 18.5, "laden", now),
        (7002, 19.0, 8.5, "ballast", now),
        (7003, 16.0, 15.0, "laden", now),
    ])
    an_conn.close()

    reg_file = tmp_path / "reg_ofs.duckdb"
    reg_conn = duckdb.connect(str(reg_file))
    reg_conn.execute("""
    CREATE TABLE vessel_registry (
        imo BIGINT PRIMARY KEY, ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR,
        call_sign VARCHAR, gross_tonnage INTEGER, dwt INTEGER, ship_type VARCHAR,
        year_built INTEGER, ship_status VARCHAR, owner VARCHAR, ism_manager VARCHAR,
        ship_manager VARCHAR, class_society VARCHAR, pi_club VARCHAR,
        detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR,
        uscg_targeting VARCHAR, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
        risk_score INTEGER, risk_indicators VARCHAR, ofac_sanctioned BOOLEAN
    );
    """)
    reg_conn.executemany(
        "INSERT INTO vessel_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (2000001, "T1", "Russia", "RU", None, 200000, 300000, "Tanker", 2005, "In Service", "SHADOW FLEET LLC", None, None, "RMRS", None, None, "Black", "Black", None, None, True, 72, None, True),
            (2000002, "T2", "Nauru", "NR", None, 200000, 300000, "Tanker", 2004, "In Service", "SHADOW FLEET LLC", None, None, "RMRS", None, None, "Black", "Black", None, None, True, 75, None, False),
            (2000003, "B1", "Marshall Islands", "MH", None, 150000, 200000, "Bulk Carrier", 2018, "In Service", "CLEAN OWNER PTE", None, None, "ABS (IACS)", None, None, "White", "White", None, None, True, 10, None, False),
        ],
    )
    reg_conn.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("REGISTRY_DB", str(reg_file))

    from app.main import app as freight_app
    return TestClient(freight_app)


def test_owner_fleet_status_structure(owner_fleet_client):
    r = owner_fleet_client.get("/api/analytics/owner-fleet-status")
    assert r.status_code == 200
    d = r.json()
    assert "total_owners" in d
    assert "rows" in d
    assert d["total_owners"] >= 1
    row = d["rows"][0]
    for key in ("owner", "live_count", "laden", "ballast", "unknown", "flags", "regions"):
        assert key in row


def test_owner_fleet_status_laden_ballast(owner_fleet_client):
    r = owner_fleet_client.get("/api/analytics/owner-fleet-status?min_vessels=1")
    d = r.json()
    owners = {row["owner"]: row for row in d["rows"]}
    assert "SHADOW FLEET LLC" in owners
    sf = owners["SHADOW FLEET LLC"]
    assert sf["live_count"] == 2
    assert sf["laden"] == 1
    assert sf["ballast"] == 1


def test_owner_fleet_status_kind_filter(owner_fleet_client):
    r = owner_fleet_client.get("/api/analytics/owner-fleet-status?kind=tanker&min_vessels=1")
    d = r.json()
    # Bulk owner should not appear when filtering to tankers
    owner_names = {row["owner"] for row in d["rows"]}
    assert "SHADOW FLEET LLC" in owner_names
    assert "CLEAN OWNER PTE" not in owner_names


# ---------------------------------------------------------------------------
# Phase 54: European Supply Intelligence
# ---------------------------------------------------------------------------

@pytest.fixture
def eur_inbound_client(tmp_path, monkeypatch):
    """Fixture: tankers heading to Rotterdam (NW Europe) and Trieste (Med)."""
    import duckdb
    from fastapi.testclient import TestClient
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    ais_file = tmp_path / "ais_eur.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute("""
    CREATE TABLE live_positions (
        mmsi BIGINT PRIMARY KEY, name VARCHAR, lat DOUBLE, lon DOUBLE,
        sog DOUBLE, cog DOUBLE, heading DOUBLE, destination VARCHAR,
        ship_type INTEGER, length_m DOUBLE, kind VARCHAR, segment VARCHAR,
        region VARCHAR, updated_ts TIMESTAMP,
        imo BIGINT, draught DOUBLE, nav_status INTEGER, eta VARCHAR
    );
    """)
    # Aframax heading Rotterdam from ~200nm, should appear
    # VLCC heading Rotterdam, far out (>48h at 8kn) - excluded by default 48h horizon
    # Suezmax heading Trieste
    conn.executemany("INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (7001, "EUR A", 49.5, 3.5, 12.0, 60.0, 60.0, "NLRTM", 80, 330.0, "tanker", "Aframax", "north_sea", now, 9001, 20.0, 0, None),
        (7002, "EUR B", 30.0, -5.0, 8.0, 45.0, 45.0, "ROTTERDAM", 80, 300.0, "tanker", "VLCC", "west_africa", now, 9002, 15.0, 0, None),
        (7003, "EUR C", 40.0, 12.0, 11.0, 90.0, 90.0, "ITTRS",  80, 200.0, "tanker", "Suezmax", "med", now, 9003, 22.0, 0, None),
    ])
    conn.close()

    analytics_file = tmp_path / "analytics_eur.duckdb"
    ac = duckdb.connect(str(analytics_file))
    ac.execute("CREATE TABLE vessel_state (mmsi BIGINT PRIMARY KEY, laden VARCHAR, last_draught DOUBLE, max_draught_seen DOUBLE, updated_ts TIMESTAMP)")
    ac.executemany("INSERT INTO vessel_state VALUES (?,?,?,?,?)", [
        (7001, "laden",   20.0, 22.0, now),
        (7002, "laden",   15.0, 22.0, now),
        (7003, "ballast", 10.0, 22.0, now),
    ])
    # Add transit event: vessel 7001 transited Suez northbound laden 10 days ago
    ac.execute("""CREATE TABLE transit_events (
        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
        direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN,
        PRIMARY KEY (mmsi, chokepoint, entered_ts)
    )""")
    suez_ts = now - timedelta(days=10)
    ac.executemany("INSERT INTO transit_events VALUES (?,?,?,?,?,?,?,?)", [
        (7001, "suez", suez_ts, suez_ts + timedelta(hours=12), "northbound", "tanker", "Aframax", True),
    ])
    ac.close()

    registry_file = tmp_path / "registry_eur.duckdb"
    rc = duckdb.connect(str(registry_file))
    rc.execute("CREATE TABLE vessel_registry (imo BIGINT PRIMARY KEY, risk_score INTEGER, fetch_ok BOOLEAN)")
    rc.close()

    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_file))
    monkeypatch.setenv("REGISTRY_DB", str(registry_file))
    from app.main import app
    return TestClient(app)


def test_european_inbound_structure(eur_inbound_client):
    r = eur_inbound_client.get("/api/analytics/european-inbound?horizon_h=48")
    assert r.status_code == 200
    d = r.json()
    for key in ("total_vessels", "total_laden", "total_dwt_laden", "vessels", "by_origin", "by_port", "eta_buckets"):
        assert key in d


def test_european_inbound_rotterdam_appears(eur_inbound_client):
    r = eur_inbound_client.get("/api/analytics/european-inbound?horizon_h=48")
    d = r.json()
    mmsis = [v["mmsi"] for v in d["vessels"]]
    assert 7001 in mmsis, "Aframax heading Rotterdam should appear within 48h horizon"
    rdam_vessel = next(v for v in d["vessels"] if v["mmsi"] == 7001)
    assert rdam_vessel["port"] == "Rotterdam"
    assert rdam_vessel["port_region"] == "NW Europe"
    assert rdam_vessel["laden"] == "laden"


def test_european_inbound_origin_inference(eur_inbound_client):
    r = eur_inbound_client.get("/api/analytics/european-inbound?horizon_h=48")
    d = r.json()
    aframax = next((v for v in d["vessels"] if v["mmsi"] == 7001), None)
    assert aframax is not None
    assert aframax["inferred_origin"] == "Middle East", "Suez NB transit should infer Middle East origin"
    assert aframax["inferred_via"] == "Suez NB"


def test_european_inbound_horizon_excludes_far(eur_inbound_client):
    # VLCC at 8kn from lat=30 is ~1500nm -> ~187h, far beyond any horizon
    r = eur_inbound_client.get("/api/analytics/european-inbound?horizon_h=48")
    d = r.json()
    mmsis = [v["mmsi"] for v in d["vessels"]]
    assert 7002 not in mmsis, "VLCC > 48h should be excluded"


def test_european_inbound_dwt_estimate(eur_inbound_client):
    r = eur_inbound_client.get("/api/analytics/european-inbound?horizon_h=48")
    d = r.json()
    aframax = next((v for v in d["vessels"] if v["mmsi"] == 7001), None)
    assert aframax is not None
    assert aframax["dwt_estimate"] == 105_000, "Aframax DWT should be 105000"


def test_european_inbound_laden_only_filter(eur_inbound_client):
    r = eur_inbound_client.get("/api/analytics/european-inbound?horizon_h=120&laden_only=true")
    d = r.json()
    for v in d["vessels"]:
        assert v["laden"] == "laden", "laden_only filter should exclude non-laden vessels"


# ===========================================================================
# Phase 55: LNG Intelligence tests
# ===========================================================================

@pytest.fixture
def lng_client(tmp_path):
    """Seeded TestClient with a known LNG tanker heading to Gate LNG Rotterdam."""
    ais_db = tmp_path / "ais.duckdb"
    ana_db = tmp_path / "analytics.duckdb"
    reg_db = tmp_path / "registry.duckdb"

    import duckdb
    from datetime import datetime, UTC

    now = datetime.now(UTC).replace(tzinfo=None)
    recently = now.strftime("%Y-%m-%d %H:%M:%S")

    # AIS: one LNG tanker heading to Gate Rotterdam (~150nm from Rotterdam at 14kn -> 10.7h)
    ais = duckdb.connect(str(ais_db))
    ais.execute("""
        CREATE TABLE live_positions (
            mmsi BIGINT, name TEXT, lat DOUBLE, lon DOUBLE,
            sog DOUBLE, cog DOUBLE, heading DOUBLE,
            destination TEXT, kind TEXT, segment TEXT,
            region TEXT, updated_ts TIMESTAMP, imo BIGINT,
            draught DOUBLE, nav_status INT, eta TEXT
        )
    """)
    # LNG tanker QATARI STAR heading to Rotterdam (NLRTM), ~150nm away
    ais.execute("""
        INSERT INTO live_positions VALUES
        (9001001, 'QATARI STAR', 50.0, 2.5, 14.0, 45.0, 45.0,
         'NLRTM', 'tanker', 'LNG Tanker', 'english_channel',
         ?, 9451818, 12.0, 0, NULL)
    """, [now])
    ais.close()

    # Analytics: vessel_state + a Suez NB transit 15 days ago
    ana = duckdb.connect(str(ana_db))
    ana.execute("""
        CREATE TABLE vessel_state (mmsi BIGINT, max_draught_seen DOUBLE, last_draught DOUBLE, laden TEXT, updated_ts TIMESTAMP)
    """)
    ana.execute("INSERT INTO vessel_state VALUES (9001001, 12.0, 12.0, 'laden', ?)", [now])
    ana.execute("""
        CREATE TABLE transit_events (
            mmsi BIGINT, chokepoint TEXT, entered_ts TIMESTAMP, exited_ts TIMESTAMP,
            direction TEXT, kind TEXT, segment TEXT, laden BOOLEAN
        )
    """)
    fifteen_days_ago = (now - __import__('datetime').timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
    ana.execute("""
        INSERT INTO transit_events VALUES
        (9001001, 'suez', ?, ?, 'northbound', 'tanker', 'LNG Tanker', true)
    """, [fifteen_days_ago, fifteen_days_ago])
    ana.close()

    # Registry: QATARI STAR is an LNG tanker
    reg = duckdb.connect(str(reg_db))
    reg.execute("""
        CREATE TABLE vessel_registry (
            imo INT, ship_name TEXT, flag TEXT, flag_code TEXT, call_sign TEXT,
            gross_tonnage INT, dwt INT, ship_type TEXT, year_built INT, ship_status TEXT,
            owner TEXT, ism_manager TEXT, ship_manager TEXT, class_society TEXT,
            pi_club TEXT, detention_rate_pct DOUBLE, paris_mou DOUBLE, tokyo_mou DOUBLE,
            uscg_targeting DOUBLE, fetched_ts TIMESTAMP, fetch_ok BOOLEAN,
            risk_score INT, risk_indicators TEXT, ofac_sanctioned BOOLEAN
        )
    """)
    reg.execute("""
        INSERT INTO vessel_registry (imo, ship_name, ship_type, owner)
        VALUES (9451818, 'QATARI STAR', 'LNG Tanker', 'Qatar Gas Transport')
    """)
    reg.close()

    import os
    from fastapi.testclient import TestClient
    env = {
        "AIS_POSITIONS_DB": str(ais_db),
        "ANALYTICS_DB": str(ana_db),
        "REGISTRY_DB": str(reg_db),
    }
    with __import__('unittest.mock', fromlist=['patch']).patch.dict(os.environ, env):
        from importlib import reload
        from app import db
        reload(db)
        from app import main as m
        reload(m)
        client = TestClient(m.app)
        yield client


def test_lng_inbound_structure(lng_client):
    r = lng_client.get("/api/analytics/lng-inbound?horizon_h=72")
    assert r.status_code == 200
    d = r.json()
    assert "total_lng_visible" in d
    assert "inbound_to_europe" in d
    assert "bcm_inbound" in d
    assert "vessels" in d
    assert "by_origin" in d
    assert "by_terminal" in d


def test_lng_inbound_vessel_found(lng_client):
    r = lng_client.get("/api/analytics/lng-inbound?horizon_h=72")
    d = r.json()
    assert d["total_lng_visible"] >= 1, "Should detect at least the seeded LNG tanker"
    assert d["inbound_to_europe"] >= 1, "QATARI STAR heading to NLRTM should be inbound"


def test_lng_inbound_terminal_match(lng_client):
    r = lng_client.get("/api/analytics/lng-inbound?horizon_h=72")
    d = r.json()
    v = next((x for x in d["vessels"] if x["mmsi"] == 9001001), None)
    assert v is not None, "QATARI STAR should appear in vessel list"
    assert v["terminal"] == "Gate LNG Rotterdam", f"Expected Gate LNG Rotterdam, got {v['terminal']}"
    assert v["terminal_country"] == "Netherlands"


def test_lng_inbound_origin_inference(lng_client):
    r = lng_client.get("/api/analytics/lng-inbound?horizon_h=72")
    d = r.json()
    v = next((x for x in d["vessels"] if x["mmsi"] == 9001001), None)
    assert v is not None
    assert v["inferred_origin"] == "Qatar / ME", f"Suez NB laden should infer Qatar/ME, got {v['inferred_origin']}"
    assert v["inferred_via"] == "Suez NB"


def test_lng_inbound_bcm_estimate(lng_client):
    r = lng_client.get("/api/analytics/lng-inbound?horizon_h=72")
    d = r.json()
    # 1 inbound vessel * 0.099 bcm
    assert abs(d["bcm_inbound"] - 0.099) < 0.001, f"bcm should be ~0.099, got {d['bcm_inbound']}"
