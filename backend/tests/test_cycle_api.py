"""Endpoint tests for the freight cycle board.

The registry is swapped for a fixture file and the live resolvers are replaced with
stubs, so these assert the API contract - tiering, gaps, staleness, caching - and
never depend on what the Baltic indices happen to be doing today.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

_SUBSECTORS = [
    {
        "id": "drybulk",
        "name": "Dry bulk",
        "stage": "Early upcycle",
        "stage_note": "Thin orderbook meets ton-miles.",
        "coverage_note": "BDI is live; the orderbook is recorded by hand.",
        "headline_signal": "bdi",
        "orderbook_signal": "drybulk_orderbook",
    },
]

_SIGNALS = [
    {
        "id": "bdi",
        "subsector": "drybulk",
        "category": "price",
        "label": "Baltic Dry Index",
        "tier": "live",
        "unit": "index",
        "resolver": "baltic:BDI",
        "threshold_value": 1500.0,
        "threshold_label": "Sustained below 1500 ends the uptrend",
        "direction": "below",
        "expected_lag": "Leads earnings 1-2 quarters",
        "falsifier": "Cargo volumes invalidate the ton-mile thesis",
        "source_label": "Baltic Exchange via akshare",
        "review_interval_days": 3,
    },
    {
        "id": "drybulk_orderbook",
        "subsector": "drybulk",
        "category": "capacity",
        "label": "Dry-bulk orderbook",
        "tier": "registered",
        "unit": "pct",
        "value": 7.0,
        "value_note": "Multi-year low",
        "threshold_value": 15.0,
        "threshold_label": "Above 15% supply discipline breaks",
        "direction": "above",
        "expected_lag": "Deliveries 2-3 years out",
        "falsifier": "A dry-bulk ordering wave",
        "source_label": "Clarksons via Benzinga",
        "as_of": date(2020, 1, 1),  # deliberately ancient: must report stale
        "review_interval_days": 90,
        "verified": False,
        "provenance": "Secondary, unverified.",
    },
    {
        "id": "scrapping",
        "subsector": "drybulk",
        "category": "capacity",
        "label": "Scrapping",
        "tier": "missing",
        "unit": "vessels",
        "threshold_value": None,
        "threshold_label": "A demolition surge would confirm the late cycle",
        "direction": "none",
        "manual_state": "unknown",
        "expected_lag": "Coincident with the rate peak",
        "falsifier": "The dark fleet absorbs old tonnage",
        "source_label": "No free feed",
        "gap_reason": "Demolition counts are sold, not published.",
    },
]


@pytest.fixture
def cycle_client(tmp_path, monkeypatch) -> TestClient:
    """App wired to a fixture registry with stubbed resolvers and a cold cache."""
    from app import cycle as _cycle
    from app import main as _main

    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "meta": {"updated": date(2026, 7, 26), "warn_band_pct": 15.0},
                "subsectors": _SUBSECTORS,
                "signals": _SIGNALS,
            }
        )
    )
    monkeypatch.setenv("CYCLE_REGISTRY", str(path))
    _cycle._cached_registry.cache_clear()

    monkeypatch.setitem(
        _cycle.DEFAULT_RESOLVERS,
        "baltic:BDI",
        lambda: _cycle.Resolved(
            value=2743.0,
            as_of=date.today(),
            note="+21% YoY",
            spark=[2700.0, 2725.0, 2743.0],
        ),
    )
    monkeypatch.setitem(_main._cycle_cache, "signals", None)
    monkeypatch.setitem(_main._cycle_cache, "ts", 0.0)

    yield TestClient(_main.app)

    _cycle._cached_registry.cache_clear()
    _main._cycle_cache["signals"] = None
    _main._cycle_cache["ts"] = 0.0


def _by_id(payload: dict) -> dict:
    return {s["id"]: s for s in payload["signals"]}


def test_signals_endpoint_returns_every_registry_entry(cycle_client):
    r = cycle_client.get("/api/cycle/signals")

    assert r.status_code == 200
    body = r.json()
    assert set(_by_id(body)) == {"bdi", "drybulk_orderbook", "scrapping"}
    assert body["warn_band_pct"] == 15.0
    assert body["registry_updated"] == "2026-07-26"


def test_signals_endpoint_resolves_a_live_signal_with_state_and_sparkline(cycle_client):
    bdi = _by_id(cycle_client.get("/api/cycle/signals").json())["bdi"]

    assert bdi["tier"] == "live"
    assert bdi["value"] == 2743.0
    assert bdi["state"] == "holding"
    assert bdi["distance_pct"] == pytest.approx(82.9, abs=0.1)
    assert bdi["spark"] == [2700.0, 2725.0, 2743.0]
    assert bdi["stale"] is False


def test_signals_endpoint_flags_a_registered_observation_past_its_review_date(cycle_client):
    ob = _by_id(cycle_client.get("/api/cycle/signals").json())["drybulk_orderbook"]

    assert ob["tier"] == "registered"
    assert ob["value"] == 7.0
    assert ob["stale"] is True
    assert ob["verified"] is False
    assert ob["provenance"]


def test_signals_endpoint_returns_gaps_rather_than_hiding_them(cycle_client):
    """A signal we cannot source is part of the answer, not an omission."""
    gap = _by_id(cycle_client.get("/api/cycle/signals").json())["scrapping"]

    assert gap["tier"] == "missing"
    assert gap["value"] is None
    assert gap["state"] == "unknown"
    assert "sold, not published" in gap["gap_reason"]


def test_signals_endpoint_always_carries_a_falsifier_and_threshold_label(cycle_client):
    for signal in cycle_client.get("/api/cycle/signals").json()["signals"]:
        assert signal["falsifier"], signal["id"]
        assert signal["threshold_label"], signal["id"]
        assert signal["expected_lag"], signal["id"]


def test_subsectors_endpoint_embeds_the_headline_and_orderbook_signals(cycle_client):
    r = cycle_client.get("/api/cycle/subsectors")

    assert r.status_code == 200
    sub = r.json()["subsectors"][0]
    assert sub["id"] == "drybulk"
    assert sub["stage"] == "Early upcycle"
    assert sub["headline"]["id"] == "bdi"
    assert sub["orderbook"]["id"] == "drybulk_orderbook"
    assert sub["coverage_note"]


def test_series_endpoint_rejects_an_unknown_series(cycle_client):
    r = cycle_client.get("/api/cycle/series?series=SCFI")

    assert r.status_code == 404


def test_series_endpoint_returns_dated_points_for_a_known_series(cycle_client):
    r = cycle_client.get("/api/cycle/series?series=bdi&years=1")

    assert r.status_code == 200
    body = r.json()
    assert body["series"] == "BDI"
    assert body["label"] == "Baltic Dry Index"
    for p in body["points"]:
        assert date.fromisoformat(p["date"])
        assert isinstance(p["value"], float)


def test_series_endpoint_survives_an_unreachable_database(cycle_client, monkeypatch):
    """A dead PostgreSQL must give an empty series, not a 500."""
    import loaders

    def _boom(*_a, **_kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(loaders, "load_baltic_index", _boom)

    r = cycle_client.get("/api/cycle/series?series=BDI")

    assert r.status_code == 200
    assert r.json()["points"] == []


def test_signals_endpoint_serves_the_second_call_from_cache(cycle_client, monkeypatch):
    """Resolution hits PostgreSQL and DuckDB; a page refresh must not re-run it."""
    from app import cycle as _cycle

    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return _cycle.Resolved(value=2743.0, as_of=date.today())

    monkeypatch.setitem(_cycle.DEFAULT_RESOLVERS, "baltic:BDI", _counting)

    cycle_client.get("/api/cycle/signals")
    cycle_client.get("/api/cycle/signals")
    cycle_client.get("/api/cycle/subsectors")

    assert calls["n"] == 1
