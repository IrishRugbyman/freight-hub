"""Chunked track loading in `build_samples`.

The bug this guards against: the loader took the *minimum* arrival time over the
whole table as its scan lower bound, so it read from the start of collected
history every run while each arrival only ever uses its trailing 72h. The scan
therefore grew by a day for every day the collector ran, and by 2026-08 the
hourly analytics job was pulling all 25.2M snapshot rows into pandas and being
OOM-killed against its 5 GB cgroup, every hour.

The fix must not change a single output row - it is purely about when rows are
read - so the central test here compares chunked output against the whole-history
load it replaced.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest
from analytics import eta_backtest as bt
from analytics import eta_labels as el

_AIS_SCHEMA = """
CREATE TABLE ais_snapshots (
    snapshot_ts TIMESTAMP, mmsi BIGINT,
    kind VARCHAR, segment VARCHAR, region VARCHAR,
    lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
    sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
    PRIMARY KEY (snapshot_ts, mmsi)
);
"""

_T0 = datetime(2026, 6, 1, 0, 0, 0)
_TARGET = {
    "target_id": "test:origin",
    "target_type": "port",
    "name": "origin",
    "lat": 0.0,
    "lon": 0.0,
    "reach_nm": 30.0,
    "is_canal": False,
}


def _approach(mmsi: int, start: datetime, n: int = 11, speed_kn: float = 12.0) -> list[tuple]:
    """A straight 12 kn run-in from 2 degrees west to the origin, hourly fixes."""
    rows = []
    for i in range(n):
        lon = -2.0 + (2.0 * i / (n - 1))
        rows.append(
            (
                start + timedelta(hours=i),
                mmsi,
                "tanker",
                "VLCC",
                None,
                0.0,
                lon,
                80,
                330.0,
                speed_kn,
                0,
                18.0,
                "ORIGIN",
            )
        )
    return rows


@pytest.fixture
def ais_db(tmp_path):
    """Arrivals spread over five weeks, so any chunk width under 35 days splits them."""
    db = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(_AIS_SCHEMA)
    rows = []
    for week, mmsi in enumerate([1001, 1002, 1003, 1004, 1005]):
        rows += _approach(mmsi, _T0 + timedelta(days=7 * week))
    conn.executemany("INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.close()
    return db


def _query_for(db, calls: list | None = None):
    def q(sql, params=None):
        if calls is not None:
            calls.append((sql, params))
        c = duckdb.connect(str(db), read_only=True)
        try:
            return c.execute(sql, params or []).df()
        finally:
            c.close()

    return q


@pytest.fixture
def seeded(tmp_path, ais_db):
    conn = duckdb.connect(str(tmp_path / "freight_analytics.duckdb"))
    conn.execute(el.ETA_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO eta_targets VALUES (?,?,?,?,?,?,?)",
        [
            _TARGET["target_id"],
            _TARGET["target_type"],
            _TARGET["name"],
            _TARGET["lat"],
            _TARGET["lon"],
            _TARGET["reach_nm"],
            _TARGET["is_canal"],
        ],
    )
    el.mine_arrivals(conn, _query_for(ais_db), targets=[_TARGET])
    yield conn, ais_db
    conn.close()


def _load_whole_history(conn, ais_query) -> pd.DataFrame:
    """The pre-fix loader, kept here purely as the equivalence reference."""
    arrivals = conn.execute(
        "SELECT a.mmsi, a.target_id, a.arrival_ts, a.approach_start_ts, a.segment, a.laden, "
        "       t.lat AS t_lat, t.lon AS t_lon, t.target_type, t.is_canal "
        "FROM eta_arrivals a JOIN eta_targets t USING (target_id)"
    ).df()
    if arrivals.empty:
        return pd.DataFrame()
    arrivals["arrival_ts"] = pd.to_datetime(arrivals["arrival_ts"])
    arrivals["approach_start_ts"] = pd.to_datetime(arrivals["approach_start_ts"])
    earliest = (arrivals["arrival_ts"] - pd.Timedelta(hours=bt._MAX_LEAD_H)).min()
    tracks = ais_query(
        "SELECT mmsi, snapshot_ts, lat, lon, sog, draught FROM ais_snapshots "
        "WHERE snapshot_ts >= ? ORDER BY mmsi, snapshot_ts",
        [earliest.to_pydatetime()],
    )
    tracks = tracks[tracks["mmsi"].isin(arrivals["mmsi"].astype("int64").unique().tolist())].copy()
    tracks["snapshot_ts"] = pd.to_datetime(tracks["snapshot_ts"])
    by_mmsi = {int(m): g for m, g in tracks.groupby("mmsi", sort=False)}
    return pd.DataFrame(bt._samples_for_chunk(arrivals, by_mmsi))


class TestOutputIsUnchanged:
    def test_chunked_matches_the_whole_history_load_exactly(self, seeded, monkeypatch):
        conn, db = seeded
        reference = _load_whole_history(conn, _query_for(db))
        assert not reference.empty, "fixture produced no samples; the test proves nothing"

        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 7.0)
        chunked = bt.build_samples(conn, _query_for(db))

        key = ["voyage_id", "obs_ts"]
        a = reference.sort_values(key).reset_index(drop=True)
        b = chunked.sort_values(key).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_like=True)

    @pytest.mark.parametrize("chunk_days", [1.0, 3.0, 7.0, 400.0])
    def test_the_result_does_not_depend_on_the_chunk_width(self, seeded, monkeypatch, chunk_days):
        # Chunk width is a memory knob, never a correctness one. A width larger
        # than the whole span must give the same answer as a width of one day.
        conn, db = seeded
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 400.0)
        one_shot = bt.build_samples(conn, _query_for(db))
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", chunk_days)
        split = bt.build_samples(conn, _query_for(db))
        key = ["voyage_id", "obs_ts"]
        pd.testing.assert_frame_equal(
            one_shot.sort_values(key).reset_index(drop=True),
            split.sort_values(key).reset_index(drop=True),
        )

    def test_every_arrival_still_gets_samples(self, seeded, monkeypatch):
        conn, db = seeded
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 1.0)
        samples = bt.build_samples(conn, _query_for(db))
        n_arrivals = conn.execute("SELECT count(*) FROM eta_arrivals").fetchone()[0]
        assert samples["voyage_id"].nunique() == n_arrivals


class TestItActuallyBoundsTheScan:
    def test_the_scan_no_longer_starts_at_the_beginning_of_history(self, seeded, monkeypatch):
        # The regression itself: with a chunk narrower than the span, no single
        # query may reach back to the first arrival minus the lead window.
        conn, db = seeded
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 7.0)
        calls: list = []
        bt.build_samples(conn, _query_for(db, calls))

        track_calls = [c for c in calls if "ais_snapshots" in c[0]]
        assert len(track_calls) > 1, "expected one track query per chunk"
        span_days = max((c[1][1] - c[1][0]).total_seconds() for c in track_calls) / 86400
        assert span_days < 35, f"a single query still spanned {span_days:.1f} days"

    def test_both_bounds_and_the_mmsi_filter_are_pushed_into_sql(self, seeded, monkeypatch):
        # The mmsi filter used to run in pandas after loading every vessel's rows.
        conn, db = seeded
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 7.0)
        calls: list = []
        bt.build_samples(conn, _query_for(db, calls))
        sql = next(c[0] for c in calls if "ais_snapshots" in c[0])
        assert "snapshot_ts >= ?" in sql
        assert "snapshot_ts <= ?" in sql
        assert "mmsi IN (" in sql

    def test_the_inlined_mmsi_list_contains_only_digits(self, seeded, monkeypatch):
        # The list is inlined rather than parameterised, so this pins that nothing
        # but integers can reach the SQL text.
        conn, db = seeded
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 7.0)
        calls: list = []
        bt.build_samples(conn, _query_for(db, calls))
        sql = next(c[0] for c in calls if "mmsi IN (" in c[0])
        inner = sql.split("mmsi IN (")[1].split(")")[0]
        assert inner.replace(",", "").isdigit()


class TestDegenerateInputs:
    def test_no_arrivals_yields_an_empty_frame_and_no_ais_read(self, tmp_path):
        conn = duckdb.connect(str(tmp_path / "empty.duckdb"))
        conn.execute(el.ETA_SCHEMA)
        calls: list = []

        def q(sql, params=None):
            calls.append(sql)
            return pd.DataFrame()

        assert bt.build_samples(conn, q).empty
        assert not calls, "queried the AIS store with nothing to look for"
        conn.close()

    def test_a_chunk_whose_vessels_have_no_track_is_skipped(self, seeded, monkeypatch):
        conn, _ = seeded
        monkeypatch.setattr(bt, "_TRACK_CHUNK_DAYS", 7.0)

        def empty_q(sql, params=None):
            return pd.DataFrame()

        assert bt.build_samples(conn, empty_q).empty
