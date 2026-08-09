"""Feed-status reporting.

This exists because of a specific silent failure. Every vessel read filters to
VISIBLE_HOURS, so an outage lasting more than a day empties every result set and
`last_update` goes null - the map renders blank with nothing to say why, and a
visitor reads that as a broken site rather than an upstream failure. That is
exactly what freight.lbzgiu.xyz did for three days in August 2026.

The rule these tests pin: the feed's own state is read past every freshness
filter, so it can still report when the feed last worked at the moment when
nothing else can.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from fastapi.testclient import TestClient
from tests.conftest import _SCHEMA

_NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _client_with_feed_age(tmp_path, monkeypatch, age: timedelta | None) -> TestClient:
    """A client whose only vessel was last seen ``age`` ago (None = empty store)."""
    db_file = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute(_SCHEMA)
    if age is not None:
        conn.execute(
            "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                2001,
                "OLD VLCC",
                26.0,
                56.2,
                0.0,
                None,
                None,
                "AEFJR",
                80,
                330.0,
                "tanker",
                "VLCC",
                "hormuz",
                _NOW - age,
                None,
                None,
                None,
                None,
            ],
        )
    conn.close()
    monkeypatch.setenv("AIS_POSITIONS_DB", str(db_file))
    from app.main import app

    return TestClient(app)


class TestFeedState:
    def test_a_fresh_feed_reports_live(self, tmp_path, monkeypatch):
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(minutes=5))
        feed = c.get("/api/meta").json()["feed"]
        assert feed["state"] == "live"
        assert feed["age_minutes"] < 60

    def test_a_lagging_feed_reports_stale(self, tmp_path, monkeypatch):
        # Past STALE_HOURS (3) but inside VISIBLE_HOURS (24): vessels still render,
        # greyed, so the banner warns rather than alarms.
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=6))
        assert c.get("/api/meta").json()["feed"]["state"] == "stale"

    def test_an_outage_past_the_visible_window_reports_down(self, tmp_path, monkeypatch):
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=30))
        assert c.get("/api/meta").json()["feed"]["state"] == "down"

    def test_an_empty_store_reports_unknown_rather_than_down(self, tmp_path, monkeypatch):
        # A fresh deployment has never had a feed; saying it is "down" would be a
        # false alarm on the one occasion the operator already knows what is going on.
        c = _client_with_feed_age(tmp_path, monkeypatch, None)
        feed = c.get("/api/meta").json()["feed"]
        assert feed["state"] == "unknown"
        assert feed["last_seen"] is None
        assert feed["age_minutes"] is None


class TestSnapshotFallback:
    """live_positions cannot answer this on its own.

    The collector prunes rows from live_positions once they pass its staleness
    window, so a long outage empties the table and destroys the evidence of when
    the feed died. That is not hypothetical: on 2026-08-09, three days into an
    aisstream outage, live_positions held 0 rows while ais_snapshots held 25.2M
    and knew the feed stopped at 2026-08-06 02:28:54.
    """

    def _client(self, tmp_path, monkeypatch, snapshot_age: timedelta) -> TestClient:
        db_file = tmp_path / "ais_positions.duckdb"
        conn = duckdb.connect(str(db_file))
        conn.execute(_SCHEMA)
        # live_positions deliberately left empty: this is the pruned state.
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                _NOW - snapshot_age,
                3001,
                "tanker",
                "VLCC",
                "suez",
                30.0,
                32.5,
                80,
                330.0,
                0.0,
                1,
                20.0,
                "SUEZ",
            ],
        )
        conn.close()
        monkeypatch.setenv("AIS_POSITIONS_DB", str(db_file))
        from app.main import app

        return TestClient(app)

    def test_a_pruned_live_table_still_reports_when_the_feed_died(self, tmp_path, monkeypatch):
        c = self._client(tmp_path, monkeypatch, timedelta(hours=72))
        feed = c.get("/api/meta").json()["feed"]
        assert feed["state"] == "down"
        assert feed["last_seen"] is not None
        assert feed["age_minutes"] == pytest.approx(72 * 60, abs=5)

    def test_it_does_not_report_unknown_when_history_exists(self, tmp_path, monkeypatch):
        # "No AIS positions have been collected yet" would be a flat lie next to
        # a store holding millions of snapshots.
        c = self._client(tmp_path, monkeypatch, timedelta(hours=72))
        assert c.get("/api/meta").json()["feed"]["state"] != "unknown"

    def test_live_positions_wins_when_it_has_rows(self, tmp_path, monkeypatch):
        # The fallback must not override a healthy live table with an older
        # snapshot high-water mark.
        db_file = tmp_path / "ais_positions.duckdb"
        conn = duckdb.connect(str(db_file))
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                4001,
                "FRESH",
                1.2,
                103.6,
                12.0,
                None,
                None,
                None,
                74,
                300.0,
                "bulk",
                "Capesize",
                "singapore_malacca",
                _NOW,
                None,
                None,
                None,
                None,
            ],
        )
        conn.execute(
            "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                _NOW - timedelta(hours=72),
                4001,
                "bulk",
                "Capesize",
                "singapore_malacca",
                1.2,
                103.6,
                74,
                300.0,
                12.0,
                0,
                None,
                None,
            ],
        )
        conn.close()
        monkeypatch.setenv("AIS_POSITIONS_DB", str(db_file))
        from app.main import app

        assert TestClient(app).get("/api/meta").json()["feed"]["state"] == "live"


class TestItSurvivesTheWindowThatHidesEverythingElse:
    def test_last_seen_is_reported_even_when_every_vessel_query_is_empty(
        self, tmp_path, monkeypatch
    ):
        # The whole point. At 30h the vessel list, meta counts and last_update are
        # all empty because they filter to VISIBLE_HOURS; the feed block must still
        # be able to say when the feed last delivered.
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=30))
        meta = c.get("/api/meta").json()
        assert meta["total_tracked"] == 0
        assert meta["last_update"] is None
        assert meta["feed"]["last_seen"] is not None
        assert meta["feed"]["age_minutes"] == pytest.approx(30 * 60, abs=5)

    def test_the_vessel_list_really_is_empty_at_that_age(self, tmp_path, monkeypatch):
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=30))
        assert c.get("/api/vessels").json() == []


class TestHealthEndpoint:
    def test_ok_stays_true_during_an_upstream_outage(self, tmp_path, monkeypatch):
        # `ok` is a statement about this service, which is fine. Uptime monitoring
        # watches it, and paging for an aisstream outage no deploy of ours can fix
        # would train the operator to ignore it.
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=30))
        body = c.get("/api/health").json()
        assert body["ok"] is True
        assert body["tracked"] == 0
        assert body["feed"]["state"] == "down"

    def test_health_carries_the_same_feed_block_as_meta(self, tmp_path, monkeypatch):
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=6))
        assert c.get("/api/health").json()["feed"] == c.get("/api/meta").json()["feed"]


class TestThresholdsAreReportedNotHardcodedInTheClient:
    def test_the_windows_are_published_so_the_banner_can_quote_them(self, tmp_path, monkeypatch):
        c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=6))
        feed = c.get("/api/meta").json()["feed"]
        assert feed["stale_hours"] == 3
        assert feed["visible_hours"] == 24

    def test_a_reconfigured_window_moves_the_state_boundary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FREIGHT_STALE_HOURS", "12")
        import importlib

        import app.db

        importlib.reload(app.db)
        try:
            c = _client_with_feed_age(tmp_path, monkeypatch, timedelta(hours=6))
            assert c.get("/api/meta").json()["feed"]["state"] == "live"
        finally:
            monkeypatch.delenv("FREIGHT_STALE_HOURS")
            importlib.reload(app.db)


class TestMissingStore:
    def test_a_missing_database_reports_unknown_rather_than_raising(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing.duckdb"))
        from app.main import app

        c = TestClient(app)
        assert c.get("/api/meta").json()["feed"]["state"] == "unknown"
