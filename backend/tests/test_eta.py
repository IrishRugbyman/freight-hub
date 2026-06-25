"""Phase A True ETA tests: target seeding, arrival miner, scoring harness math.

Uses a seeded temp ais_positions.duckdb with two synthetic-but-real-shaped
approaches (one that reaches a target, one that never does), and a temp analytics
DuckDB. Asserts the miner finds exactly the real arrival and that the harness
error/bias math is correct on an ideal constant-speed straight-line approach
(where naive ETA must equal the true remaining time).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest
from analytics import eta_backtest as bt
from analytics import eta_labels as el

# A custom target at the origin keeps the geometry trivial and decoupled from the
# real chokepoint/port coordinates: at lat 0, 1 deg lon == 60 nm exactly.
_TARGET = {
    "target_id": "test:origin",
    "target_type": "port",
    "name": "origin",
    "lat": 0.0,
    "lon": 0.0,
    "reach_nm": 30.0,
    "is_canal": False,
}

_AIS_SCHEMA = """
CREATE TABLE ais_snapshots (
    snapshot_ts TIMESTAMP, mmsi BIGINT,
    kind VARCHAR, segment VARCHAR, region VARCHAR,
    lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
    sog DOUBLE, nav_status INTEGER, draught DOUBLE, destination VARCHAR,
    PRIMARY KEY (snapshot_ts, mmsi)
);
"""

_T0 = datetime(2026, 6, 20, 0, 0, 0)


def _approach_rows(mmsi, lon_start, lon_step, n, sog, draught):
    """Hourly fixes along the equator moving east toward lon 0 at `sog` knots."""
    rows = []
    for i in range(n):
        lon = lon_start + lon_step * i
        rows.append(
            (
                _T0 + timedelta(hours=i),
                mmsi,
                "tanker",
                "VLCC",
                None,
                0.0,
                lon,
                80,
                330,
                sog,
                0,
                draught,
                None,
            )
        )
    return rows


@pytest.fixture
def ais_db(tmp_path):
    db = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(_AIS_SCHEMA)
    # Vessel 1: 12 kn straight in, 11 hourly fixes lon -2.0 -> 0.0 (12 nm/h),
    # arrival at the origin (lon 0). draught 18 of a 18 m max => laden.
    v1 = _approach_rows(1001, -2.0, 0.2, 11, 12.0, 18.0)
    # Vessel 2: loiters 120 nm west (lon -2.0) the whole time => never arrives.
    v2 = [
        (
            _T0 + timedelta(hours=i),
            1002,
            "tanker",
            "VLCC",
            None,
            0.0,
            -2.0,
            80,
            330,
            0.5,
            0,
            10.0,
            None,
        )
        for i in range(11)
    ]
    conn.executemany("INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", v1 + v2)
    conn.close()
    return db


def _ais_query_for(db):
    def q(sql, params=None):
        c = duckdb.connect(str(db), read_only=True)
        try:
            return c.execute(sql, params or []).df()
        finally:
            c.close()

    return q


@pytest.fixture
def analytics_conn(tmp_path):
    conn = duckdb.connect(str(tmp_path / "freight_analytics.duckdb"))
    conn.execute(el.ETA_SCHEMA)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Target seeding
# ---------------------------------------------------------------------------


def test_build_targets_chokepoints_and_canals():
    targets = el.build_targets()
    ids = {t["target_id"] for t in targets}
    # All 9 chokepoints present.
    for cp in el._CHOKEPOINTS:
        assert f"cp:{cp}" in ids
    # Only Suez + Panama are flagged as canals.
    canals = {t["target_id"] for t in targets if t["is_canal"]}
    assert canals == {"cp:suez", "cp:panama"}
    # Reach is positive everywhere.
    assert all(t["reach_nm"] > 0 for t in targets)


def test_build_targets_dedupes_nearby():
    targets = el.build_targets()
    # No two kept targets sit within the dedupe radius of each other.
    for i, a in enumerate(targets):
        for b in targets[i + 1 :]:
            d = el.haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            assert d >= el._TARGET_DEDUPE_NM, f"{a['target_id']} ~ {b['target_id']} = {d:.1f} nm"


# ---------------------------------------------------------------------------
# Arrival miner
# ---------------------------------------------------------------------------


def test_miner_finds_the_real_arrival(ais_db, analytics_conn):
    n = el.mine_arrivals(analytics_conn, _ais_query_for(ais_db), targets=[_TARGET])
    assert n == 1  # vessel 1 arrives; vessel 2 never reaches the radius
    row = analytics_conn.execute(
        "SELECT mmsi, target_id, min_dist_nm, laden, approach_start_ts, arrival_ts "
        "FROM eta_arrivals"
    ).fetchone()
    mmsi, target_id, min_dist, laden, start_ts, arr_ts = row
    assert mmsi == 1001
    assert target_id == "test:origin"
    assert min_dist < 1.0  # closest approach is the origin itself
    assert laden is True  # draught 18 == max 18
    assert start_ts < arr_ts  # approach precedes arrival
    # Arrival is the last (lon 0) fix at T0 + 10h.
    assert arr_ts == _T0 + timedelta(hours=10)


def test_miner_idempotent(ais_db, analytics_conn):
    q = _ais_query_for(ais_db)
    el.mine_arrivals(analytics_conn, q, targets=[_TARGET])
    el.mine_arrivals(analytics_conn, q, targets=[_TARGET])
    assert analytics_conn.execute("SELECT count(*) FROM eta_arrivals").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Scoring harness
# ---------------------------------------------------------------------------


def test_harness_math_on_ideal_approach(ais_db, analytics_conn):
    q = _ais_query_for(ais_db)
    # Seed the target row (build_samples joins eta_targets) + mine arrivals.
    analytics_conn.execute(
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
    el.mine_arrivals(analytics_conn, q, targets=[_TARGET])

    samples = bt.build_samples(analytics_conn, q)
    assert not samples.empty
    # One voyage, samples at decreasing remaining time, all underway.
    assert samples["voyage_id"].nunique() == 1
    assert (samples["sog"] >= bt._MIN_SOG_KN).all()
    assert (samples["remaining_h"] > 0).all()

    metrics = bt.score(
        samples, bt.naive_eta_fn, model="naive", run_ts=datetime(2026, 6, 25, 0, 0, 0)
    )
    overall = metrics[(metrics["lead_bucket"] == "all") & (metrics["target_type"] == "all")]
    assert len(overall) == 1
    # Constant-speed straight-line approach => naive ETA == true remaining.
    assert overall.iloc[0]["med_abs_err_h"] < 0.25
    assert abs(overall.iloc[0]["bias_h"]) < 0.25
    # Naive baseline has no interval.
    assert np.isnan(overall.iloc[0]["interval_coverage"])


def test_lead_bucket_edges():
    assert bt.lead_bucket(0.0) == "0-6h"
    assert bt.lead_bucket(5.9) == "0-6h"
    assert bt.lead_bucket(6.0) == "6-12h"
    assert bt.lead_bucket(23.9) == "12-24h"
    assert bt.lead_bucket(47.0) == "24-48h"
    assert bt.lead_bucket(100.0) == "48h+"


def test_voyage_split_no_leakage():
    # Two voyages, several samples each; a split must keep each voyage whole.
    import pandas as pd

    df = pd.DataFrame(
        {
            "voyage_id": [1, 1, 1, 2, 2, 2],
            "remaining_h": [1, 2, 3, 1, 2, 3],
        }
    )
    train, test = bt.voyage_split(df, test_frac=0.5, seed=1)
    train_ids = set(train["voyage_id"])
    test_ids = set(test["voyage_id"])
    assert train_ids.isdisjoint(test_ids)
    assert train_ids | test_ids == {1, 2}


def test_metrics_written_to_db(ais_db, analytics_conn):
    q = _ais_query_for(ais_db)
    analytics_conn.execute(
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
    el.mine_arrivals(analytics_conn, q, targets=[_TARGET])
    samples = bt.build_samples(analytics_conn, q)
    metrics = bt.score(
        samples, bt.naive_eta_fn, model="naive", run_ts=datetime(2026, 6, 25, 0, 0, 0)
    )
    bt.write_metrics(analytics_conn, metrics)
    n = analytics_conn.execute(
        "SELECT count(*) FROM eta_model_metrics WHERE model = 'naive'"
    ).fetchone()[0]
    assert n == len(metrics) > 0
