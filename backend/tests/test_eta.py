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
from analytics import eta_routing as rt
from analytics import eta_samples as es

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


def test_chokepoints_anchored_to_real_gates():
    # Each chokepoint target sits on its published gate coordinate (a fact, not a
    # basin centroid) with the uniform transit-capture reach.
    by_id = {t["target_id"]: t for t in el.build_targets()}
    for cp, (lat, lon) in el._CHOKEPOINT_GATES.items():
        t = by_id[f"cp:{cp}"]
        assert (t["lat"], t["lon"]) == (lat, lon)
        assert t["reach_nm"] == el._CHOKEPOINT_CAPTURE_NM


def test_ports_deduped_but_chokepoints_exempt():
    targets = el.build_targets()
    ports = [t for t in targets if t["target_type"] == "port"]
    # Ports are mutually de-duped...
    for i, a in enumerate(ports):
        for b in ports[i + 1 :]:
            d = el.haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"])
            assert d >= el._TARGET_DEDUPE_NM, f"{a['target_id']} ~ {b['target_id']} = {d:.1f} nm"
    # ...but a chokepoint near a port (e.g. Singapore gate vs Singapore anchorage)
    # is intentionally NOT de-duped - they are distinct ETA targets.
    assert "cp:singapore_malacca" in {t["target_id"] for t in targets}
    assert any(t["target_id"].startswith("zone:singapore") for t in targets)


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


def test_laden_uses_global_max_draught_not_per_approach(tmp_path, analytics_conn):
    # A ballast VLCC arrives at 12 m draught but has historically loaded to 22 m.
    # Per-approach max (12) would wrongly read laden; the global max (22) reads
    # ballast (12/22 = 0.55 <= 0.65). Two history fixes far away set the max.
    db = tmp_path / "ais_positions.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(_AIS_SCHEMA)
    approach = []
    for i in range(11):
        approach.append((
            _T0 + timedelta(hours=i), 2001, "tanker", "VLCC", None,
            0.0, -2.0 + 0.2 * i, 80, 330, 12.0, 0, 12.0, None,
        ))
    # Laden history elsewhere (outside the target radius) at 22 m draught.
    history = [
        (_T0 - timedelta(days=10), 2001, "tanker", "VLCC", None,
         40.0, 40.0, 80, 330, 13.0, 0, 22.0, None),
        (_T0 - timedelta(days=9), 2001, "tanker", "VLCC", None,
         40.0, 40.0, 80, 330, 13.0, 0, 22.0, None),
    ]
    conn.executemany(
        "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", approach + history
    )
    conn.close()

    el.mine_arrivals(analytics_conn, _ais_query_for(db), targets=[_TARGET])
    laden = analytics_conn.execute("SELECT laden FROM eta_arrivals WHERE mmsi = 2001").fetchone()[0]
    assert laden is False  # ballast: 12 m against a 22 m design proxy


def test_cross_check_chokepoints(analytics_conn):
    # Seed a chokepoint arrival + a transit_events row for the same vessel/strait;
    # the cross-check should report perfect agreement (rel_diff 0).
    analytics_conn.execute(
        "INSERT OR REPLACE INTO eta_targets VALUES "
        "('cp:hormuz','chokepoint','hormuz',26.57,56.25,30.0,false)"
    )
    analytics_conn.execute(
        "INSERT OR REPLACE INTO eta_arrivals VALUES "
        "(3001,'cp:hormuz',?,5.0,'VLCC',true,?)",
        [_T0, _T0 - timedelta(hours=3)],
    )
    analytics_conn.execute(
        "CREATE TABLE IF NOT EXISTS transit_events ("
        "mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, exited_ts TIMESTAMP, "
        "direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN)"
    )
    analytics_conn.execute(
        "INSERT INTO transit_events VALUES "
        "(3001,'hormuz',?,?,'outbound','tanker','VLCC',true)",
        [_T0 - timedelta(hours=2), _T0 - timedelta(hours=1)],
    )
    df = el.cross_check_chokepoints(analytics_conn)
    hormuz = df[df["cp"] == "hormuz"].iloc[0]
    assert hormuz["eta_vessels"] == 1
    assert hormuz["transit_vessels"] == 1
    assert hormuz["rel_diff"] == 0.0


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


# ---------------------------------------------------------------------------
# Phase B: sea-route distance + cache
# ---------------------------------------------------------------------------


def test_snap_cell_centres_and_keys():
    # A 0.25deg cell snaps to its centre; two nearby fixes share a cell, a fix in
    # the next cell does not.
    assert rt.snap_cell(0.10, 0.10) == (0.125, 0.125)
    assert rt.cell_key(0.10, 0.10) == rt.cell_key(0.20, 0.05)
    assert rt.cell_key(0.10, 0.10) != rt.cell_key(0.40, 0.10)
    # Negative coordinates floor toward the lower cell.
    assert rt.snap_cell(-0.10, -0.10) == (-0.125, -0.125)


def test_routing_avoids_landmass():
    # A real searoute path from the Gulf of Oman to Rotterdam must round Arabia and
    # transit Suez - far longer than the great circle that cuts across land.
    target = {"target_id": "port:rotterdam", "lat": 51.96, "lon": 4.10}
    nm, method = rt._route_once(25.12, 56.36, target)  # Fujairah approach
    gc = el.haversine_nm(25.12, 56.36, 51.96, 4.10)
    assert method == rt.METHOD_SEAROUTE
    assert nm > gc * 1.5  # sea route is far longer than the straight line


def test_route_cache_hit_returns_identical_and_persists(analytics_conn):
    target = {"target_id": "cp:suez", "lat": 30.50, "lon": 32.34}
    cache = rt.RouteCache(analytics_conn)
    nm1, m1 = cache.distance(25.12, 56.36, target)
    nm2, m2 = cache.distance(25.20, 56.30, target)  # same 0.25deg cell -> cache hit
    assert (nm1, m1) == (nm2, m2)
    assert cache.misses == 1 and cache.hits == 1
    cache.flush()
    # A fresh cache loads the persisted value with no further routing.
    cache2 = rt.RouteCache(analytics_conn)
    nm3, m3 = cache2.distance(25.12, 56.36, target)
    assert (nm3, m3) == (nm1, m1)
    assert cache2.misses == 0 and cache2.hits == 1


def test_route_falls_back_to_great_circle_when_searoute_unavailable(monkeypatch):
    # When searoute cannot be imported, every row degrades to great-circle and is
    # flagged 'gc' - the build still produces a populated table.
    monkeypatch.setattr(rt, "_searoute", lambda: None)
    target = {"target_id": "cp:suez", "lat": 30.50, "lon": 32.34}
    nm, method = rt._route_once(25.12, 56.36, target)  # _route_once takes a cell centre directly
    assert method == rt.METHOD_GC
    assert abs(nm - el.haversine_nm(25.12, 56.36, 30.50, 32.34)) < 1e-6


def test_route_never_shorter_than_great_circle(monkeypatch):
    # A searoute snapping artifact that returns less than the great circle is
    # clamped up to the physical floor (a sea route is never shorter than gc).
    class _Stub:
        @staticmethod
        def searoute(o, d):
            return {"properties": {"length": 1.0}}  # absurdly short (km)

    monkeypatch.setattr(rt, "_searoute", lambda: _Stub)
    target = {"target_id": "cp:suez", "lat": 30.50, "lon": 32.34}
    nm, method = rt._route_once(25.12, 56.36, target)
    gc = el.haversine_nm(25.12, 56.36, 30.50, 32.34)
    assert method == rt.METHOD_SEAROUTE
    assert nm == gc  # clamped to the great-circle floor


def test_enrich_and_persist_samples_roundtrip(ais_db, analytics_conn):
    # End-to-end: mine the test arrival, build samples, enrich with routes, persist.
    q = _ais_query_for(ais_db)
    analytics_conn.execute(
        "INSERT OR REPLACE INTO eta_targets VALUES (?,?,?,?,?,?,?)",
        [
            _TARGET["target_id"], _TARGET["target_type"], _TARGET["name"],
            _TARGET["lat"], _TARGET["lon"], _TARGET["reach_nm"], _TARGET["is_canal"],
        ],
    )
    el.mine_arrivals(analytics_conn, q, targets=[_TARGET])
    samples = bt.build_samples(analytics_conn, q)
    assert {"obs_lat", "obs_lon", "arrival_ts"}.issubset(samples.columns)

    enriched = es.enrich_routes(analytics_conn, samples)
    assert "route_dist_nm" in enriched.columns
    # Underway fixes are routed (>= great-circle by the snap-correction invariant);
    # any non-underway fix is left NULL (not routed) - here all fixes are underway.
    routed = enriched[enriched["route_dist_nm"].notna()]
    assert not routed.empty
    assert (routed["route_dist_nm"] >= routed["gc_dist_nm"] - 1e-6).all()
    assert routed["route_method"].isin([rt.METHOD_SEAROUTE, rt.METHOD_GC]).all()

    n = es.persist_samples(analytics_conn, enriched)
    assert n == len(enriched) > 0
    cols = {r[1] for r in analytics_conn.execute("PRAGMA table_info('eta_samples')").fetchall()}
    # Phase C feature columns exist (created now), even though unpopulated.
    assert {"sog_trail6h", "service_speed", "dest_queue_h", "approach_bearing"} <= cols


def test_route_eta_fn_uses_route_distance():
    # route_eta_fn divides the sea-route distance by SOG; falls back to gc if the
    # row was never routed.
    assert bt.route_eta_fn({"sog": 10.0, "route_dist_nm": 100.0, "gc_dist_nm": 50.0}) == 10.0
    assert bt.route_eta_fn({"sog": 10.0, "route_dist_nm": float("nan"), "gc_dist_nm": 50.0}) == 5.0
    assert np.isnan(bt.route_eta_fn({"sog": 0.0, "route_dist_nm": 100.0, "gc_dist_nm": 50.0}))


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
