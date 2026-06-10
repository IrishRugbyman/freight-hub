"""Endpoint tests for freight-api against a seeded temp DuckDB (see conftest)."""

from __future__ import annotations


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
