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
