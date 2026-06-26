"""True ETA Phase E: the live serving scorer + the /api/analytics/eta endpoint.

Covers the scorer (`analytics.eta_serving`) end-to-end against a seeded analytics
DB + a fake AIS reader, then the read path (`app.runner_eta` + the endpoint and
the inbound-card enrichment) against a seeded `eta_predictions` snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from analytics.eta_labels import ETA_SCHEMA
from analytics.eta_samples import ETA_SAMPLES_SCHEMA
from analytics.eta_serving import build_predictions, run_in_conn
from fastapi.testclient import TestClient

_NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

# Two targets: the Suez canal gate and the Rotterdam point terminal.
_TARGETS = [
    ("cp:suez", "chokepoint", "suez", 30.50, 32.34, 30.0, True),
    ("port:rotterdam", "port", "Rotterdam", 51.96, 4.10, 15.0, False),
]


def _seed_targets(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(ETA_SCHEMA)
    for t in _TARGETS:
        conn.execute(
            "INSERT OR REPLACE INTO eta_targets "
            "(target_id, target_type, name, lat, lon, reach_nm, is_canal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(t),
        )


def _seed_samples(conn: duckdb.DuckDBPyConnection) -> None:
    """A small eta_samples set so the interval model has residuals to fit."""
    conn.execute(ETA_SAMPLES_SCHEMA)
    rows = []
    for i in range(120):
        # remaining_h spread around a physics-ish p50 so residual quantiles exist.
        dist = 60.0 + i  # nm
        sog = 12.0
        remaining = dist / sog + (i % 7) - 3  # noisy label
        rows.append(
            (
                1000 + i,
                999_000 + i,
                "cp:suez",
                _NOW,
                _NOW - timedelta(hours=remaining),
                29.0,
                32.3,
                float(max(0.5, remaining)),
                dist,
                dist,
                "searoute",
                sog,
                sog,
                sog,
                20.0,
                0.0,
                0.0,
                "VLCC",
                True,
                "chokepoint",
                True,
                "12-24h",
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO eta_samples "
        "(voyage_id, mmsi, target_id, arrival_ts, obs_ts, obs_lat, obs_lon, "
        " remaining_h, route_dist_nm, gc_dist_nm, route_method, sog, sog_trail6h, "
        " service_speed, draught, dest_queue_h, approach_bearing, segment, laden, "
        " target_type, is_canal, lead_bucket) "
        "VALUES (" + ",".join("?" * 22) + ")",
        rows,
    )


def _fake_ais_query():
    """Return an ais_query callable serving a tiny live_positions / snapshots set."""
    mem = duckdb.connect(":memory:")
    mem.execute(
        "CREATE TABLE live_positions (mmsi BIGINT, name VARCHAR, lat DOUBLE, lon DOUBLE, "
        "sog DOUBLE, cog DOUBLE, heading DOUBLE, kind VARCHAR, segment VARCHAR, "
        "region VARCHAR, imo BIGINT, draught DOUBLE, updated_ts TIMESTAMP)"
    )
    mem.execute("CREATE TABLE ais_snapshots (snapshot_ts TIMESTAMP, mmsi BIGINT, sog DOUBLE)")
    # 7001: VLCC ~90 nm south of Suez, steering due north (toward the gate).
    # 7002: anchored (sog 0.1) - must be excluded.
    # 7003: fast vessel pointing south (away from both targets) - bearing-gated out.
    mem.executemany(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                7001,
                "VLCC NORTH",
                29.0,
                32.34,
                12.0,
                0.0,
                1.0,
                "tanker",
                "VLCC",
                "suez",
                9000001,
                20.0,
                _NOW,
            ),
            (
                7002,
                "ANCHORED",
                30.2,
                32.34,
                0.1,
                None,
                None,
                "tanker",
                "VLCC",
                "suez",
                9000002,
                20.0,
                _NOW,
            ),
            (
                7003,
                "SOUTHBOUND",
                27.5,
                32.34,
                13.0,
                180.0,
                180.0,
                "tanker",
                "Suezmax",
                "suez",
                9000003,
                12.0,
                _NOW,
            ),
        ],
    )
    mem.executemany(
        "INSERT INTO ais_snapshots VALUES (?,?,?)",
        [(_NOW - timedelta(hours=1), 7001, 12.0), (_NOW, 7001, 12.0)],
    )

    def q(sql: str, params=None):
        return mem.execute(sql, params or []).df()

    return q


def test_build_predictions_scores_underway_vessel_with_monotone_interval(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)
    _seed_samples(conn)
    preds = build_predictions(conn, _fake_ais_query(), now=_NOW)

    assert not preds.empty
    mmsis = set(preds["mmsi"].tolist())
    assert 7001 in mmsis  # northbound vessel scored
    assert 7002 not in mmsis  # anchored excluded
    # 7003 points away from Suez (its only in-range target) -> bearing-gated out.
    assert 7003 not in mmsis

    suez = preds[(preds["mmsi"] == 7001) & (preds["target_id"] == "cp:suez")].iloc[0]
    assert suez["method"] == "physics"
    assert suez["eta_low_h"] <= suez["eta_p50_h"] <= suez["eta_high_h"]
    assert suez["eta_low_h"] >= 0.0
    assert suez["eta_naive_h"] > 0
    assert suez["eta_arrival_ts"] > _NOW
    # Canal staging makes the physics P50 no earlier than the bare naive estimate.
    assert suez["eta_p50_h"] >= suez["eta_naive_h"] - 1e-6


def test_run_in_conn_persists_predictions(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)
    _seed_samples(conn)
    n = run_in_conn(conn, _fake_ais_query(), now=_NOW)
    assert n >= 1
    got = conn.execute("SELECT count(*) FROM eta_predictions WHERE mmsi = 7001").fetchone()[0]
    assert got >= 1


def test_build_predictions_empty_when_no_live(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)

    def empty_q(sql, params=None):
        import pandas as pd

        return pd.DataFrame()

    preds = build_predictions(conn, empty_q, now=_NOW)
    assert preds.empty


# ---------------------------------------------------------------------------
# Endpoint + runner_eta read path
# ---------------------------------------------------------------------------

_PRED_SCHEMA = """
CREATE TABLE eta_predictions (
    mmsi BIGINT, target_id VARCHAR, as_of TIMESTAMP,
    eta_p50_h DOUBLE, eta_low_h DOUBLE, eta_high_h DOUBLE, eta_naive_h DOUBLE,
    method VARCHAR, eta_arrival_ts TIMESTAMP,
    route_dist_nm DOUBLE, gc_dist_nm DOUBLE, route_method VARCHAR,
    sog DOUBLE, segment VARCHAR, laden BOOLEAN,
    target_type VARCHAR, target_name VARCHAR, target_lat DOUBLE, target_lon DOUBLE,
    PRIMARY KEY (mmsi, target_id)
);
"""

_PRED_SEED = [
    (
        1003,
        "cp:hormuz",
        _NOW,
        8.0,
        6.0,
        14.0,
        9.5,
        "physics",
        _NOW + timedelta(hours=8),
        112.0,
        110.0,
        "searoute",
        14.0,
        "VLCC",
        True,
        "chokepoint",
        "hormuz",
        26.57,
        56.25,
    ),
    (
        1003,
        "port:rotterdam",
        _NOW,
        300.0,
        250.0,
        360.0,
        280.0,
        "physics",
        _NOW + timedelta(hours=300),
        3600.0,
        2900.0,
        "searoute",
        14.0,
        "VLCC",
        True,
        "port",
        "Rotterdam",
        51.96,
        4.10,
    ),
]


@pytest.fixture
def eta_client(tmp_path, monkeypatch) -> TestClient:
    an_file = tmp_path / "freight_analytics.duckdb"
    conn = duckdb.connect(str(an_file))
    conn.execute(_PRED_SCHEMA)
    conn.executemany("INSERT INTO eta_predictions VALUES (" + ",".join("?" * 19) + ")", _PRED_SEED)
    conn.close()
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing_ais.duckdb"))
    from app.main import app

    return TestClient(app)


def test_eta_endpoint_returns_sorted_predictions(eta_client):
    r = eta_client.get("/api/analytics/eta?mmsi=1003")
    assert r.status_code == 200
    body = r.json()
    assert body["mmsi"] == 1003
    assert body["n"] == 2
    preds = body["predictions"]
    # Sorted soonest-first (Hormuz 8h before Rotterdam 300h).
    assert preds[0]["target_id"] == "cp:hormuz"
    p = preds[0]
    assert p["eta_low_h"] <= p["eta_p50_h"] <= p["eta_high_h"]
    assert p["method"] == "physics"
    assert p["eta_naive_h"] == 9.5
    assert p["route_dist_nm"] == 112.0


def test_eta_endpoint_unknown_vessel_empty(eta_client):
    r = eta_client.get("/api/analytics/eta?mmsi=999999")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 0
    assert body["predictions"] == []


_METRICS_SCHEMA = """
CREATE TABLE eta_model_metrics (
    run_ts TIMESTAMP, model VARCHAR, lead_bucket VARCHAR, target_type VARCHAR,
    n INTEGER, med_abs_err_h DOUBLE, bias_h DOUBLE, mape DOUBLE,
    p90_abs_err_h DOUBLE, interval_coverage DOUBLE,
    PRIMARY KEY (run_ts, model, lead_bucket, target_type)
);
"""

_OLD = _NOW - timedelta(hours=3)
_METRICS_SEED = [
    # an older run that must NOT be served (only the latest run_ts shows)
    (_OLD, "naive", "all", "all", 100, 99.0, 99.0, 0.5, 99.0, None),
    # latest run: naive vs physics across two buckets + rollup
    (_NOW, "naive", "0-6h", "all", 500, 0.65, 0.0, 0.1, 2.0, None),
    (_NOW, "naive", "all", "all", 1000, 12.7, -8.2, 0.4, 50.0, None),
    (_NOW, "physics_v1", "0-6h", "all", 500, 1.09, 0.5, 0.15, 8.1, 0.67),
    (_NOW, "physics_v1", "all", "all", 1000, 11.1, -8.0, 0.4, 49.8, 0.80),
]


@pytest.fixture
def metrics_client(tmp_path, monkeypatch) -> TestClient:
    an_file = tmp_path / "freight_analytics.duckdb"
    conn = duckdb.connect(str(an_file))
    conn.execute(_METRICS_SCHEMA)
    conn.executemany(
        "INSERT INTO eta_model_metrics VALUES (" + ",".join("?" * 10) + ")", _METRICS_SEED
    )
    conn.close()
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing_ais.duckdb"))
    from app.main import app

    return TestClient(app)


def test_eta_accuracy_serves_latest_run_only(metrics_client):
    r = metrics_client.get("/api/analytics/eta-accuracy")
    assert r.status_code == 200
    body = r.json()
    # Only the latest run's 4 rows, never the stale 99.0 row.
    assert all(row["med_abs_err_h"] != 99.0 for row in body["rows"])
    assert body["models"] == ["naive", "physics_v1"]  # baseline-first order
    assert body["lead_order"][0] == "0-6h"
    # physics carries interval coverage; naive does not.
    phys_all = next(
        x for x in body["rows"] if x["model"] == "physics_v1" and x["lead_bucket"] == "all"
    )
    assert phys_all["interval_coverage"] == 0.80
    naive_all = next(
        x for x in body["rows"] if x["model"] == "naive" and x["lead_bucket"] == "all"
    )
    assert naive_all["interval_coverage"] is None


def test_eta_accuracy_empty_when_no_metrics(eta_client):
    # eta_client has eta_predictions but no eta_model_metrics table.
    r = eta_client.get("/api/analytics/eta-accuracy")
    assert r.status_code == 200
    body = r.json()
    assert body["run_ts"] is None
    assert body["rows"] == []
