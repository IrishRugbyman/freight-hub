"""Tests for the ground-truth arrivals ranking endpoint (/api/analytics/arrivals)."""

from fastapi.testclient import TestClient


def test_arrivals_empty_without_analytics_db(tmp_path, monkeypatch):
    # Analytics DB missing (no eta_arrivals table) -> graceful empty, not a 500.
    monkeypatch.setenv("ANALYTICS_DB", str(tmp_path / "missing.duckdb"))
    monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing_ais.duckdb"))
    from app.main import app

    r = TestClient(app).get("/api/analytics/arrivals")
    assert r.status_code == 200
    body = r.json()
    assert body["total_arrivals"] == 0
    assert body["total_vessels"] == 0
    assert body["rows"] == []


def test_arrivals_ranking_and_totals(analytics_client):
    r = analytics_client.get("/api/analytics/arrivals", params={"days": 14})
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 14
    assert body["target_type"] == "all"
    # 6 seeded arrivals, 5 distinct vessels (mmsi 1001 arrives twice).
    assert body["total_arrivals"] == 6
    assert body["total_vessels"] == 5

    rows = body["rows"]
    assert len(rows) == 3  # rotterdam, suez, singapore_malacca
    # Busiest target first: Rotterdam has 3 arrivals.
    assert rows[0]["target_id"] == "zone:rotterdam"
    assert rows[0]["arrivals"] == 3
    assert rows[0]["vessels"] == 3
    assert rows[0]["target_type"] == "port"
    # Rotterdam: 2 of 3 arrivals laden -> share ~0.667.
    assert rows[0]["laden_share"] is not None
    assert abs(rows[0]["laden_share"] - 2 / 3) < 0.01
    # VLCC appears twice at Rotterdam vs one Suezmax -> dominant segment.
    assert rows[0]["top_segment"] == "VLCC"
    assert rows[0]["last_arrival_ts"] is not None


def test_arrivals_target_type_filter(analytics_client):
    r = analytics_client.get("/api/analytics/arrivals", params={"target_type": "chokepoint"})
    assert r.status_code == 200
    body = r.json()
    assert body["target_type"] == "chokepoint"
    names = {row["target_id"] for row in body["rows"]}
    assert names == {"cp:suez", "cp:singapore_malacca"}
    assert all(row["target_type"] == "chokepoint" for row in body["rows"])
    # Totals respect the filter: 3 chokepoint arrivals (2 suez + 1 malacca).
    assert body["total_arrivals"] == 3


def test_arrivals_window_excludes_old(analytics_client):
    # A 7-day window drops the 8d Suez and 10d Malacca arrivals.
    r = analytics_client.get("/api/analytics/arrivals", params={"days": 7})
    assert r.status_code == 200
    body = r.json()
    # Rotterdam (3) + Suez (1 within 7d) = 4 arrivals; Malacca gone.
    assert body["total_arrivals"] == 4
    names = {row["target_id"] for row in body["rows"]}
    assert "cp:singapore_malacca" not in names


def test_arrivals_param_clamping(analytics_client):
    # top_n is clamped to >= 1; days clamped into [1, 90]; bad target_type -> 'all'.
    r = analytics_client.get(
        "/api/analytics/arrivals", params={"top_n": 1, "days": 999, "target_type": "bogus"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 90
    assert body["target_type"] == "all"
    assert len(body["rows"]) == 1
    assert body["rows"][0]["target_id"] == "zone:rotterdam"  # highest count survives the cap
