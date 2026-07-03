"""Destination predictor live serving (`analytics.destination_serving`) + the
`/api/analytics/destination` endpoint.

Mirrors `test_eta_serving.py`: seed a tiny analytics DB + a fake AIS reader, run
the scorer end-to-end, then check the read path (`app.runner_destination` +
endpoint) against a seeded `destination_predictions` snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from analytics.destination_serving import (
    _recent_canal_transit_by_mmsi,
    build_destination_predictions,
    run_in_conn,
)
from analytics.eta_labels import ETA_SCHEMA
from fastapi.testclient import TestClient

_NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

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


def _seed_transit_events(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "CREATE TABLE transit_events (mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, "
        "exited_ts TIMESTAMP, direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN)"
    )


def test_recent_canal_transit_by_mmsi_reads_recent_suez_transit(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_transit_events(conn)
    conn.execute(
        "INSERT INTO transit_events VALUES (7001, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE)",
        [_NOW - timedelta(days=2), _NOW - timedelta(days=1)],
    )
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {7001: "suez"}


def test_recent_canal_transit_by_mmsi_ignores_stale_transit():
    conn = duckdb.connect(":memory:")
    _seed_transit_events(conn)
    conn.execute(
        "INSERT INTO transit_events VALUES (7001, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE)",
        [_NOW - timedelta(days=40), _NOW - timedelta(days=39)],
    )
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {}


def test_recent_canal_transit_by_mmsi_keeps_only_most_recent_per_vessel():
    conn = duckdb.connect(":memory:")
    _seed_transit_events(conn)
    conn.execute(
        "INSERT INTO transit_events VALUES "
        "(7001, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE), "
        "(7001, 'panama', ?, ?, 'eastbound', 'tanker', 'VLCC', TRUE)",
        [
            _NOW - timedelta(days=10),
            _NOW - timedelta(days=9),
            _NOW - timedelta(days=3),
            _NOW - timedelta(days=2),
        ],
    )
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {7001: "panama"}


def test_recent_canal_transit_by_mmsi_empty_without_table():
    conn = duckdb.connect(":memory:")
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {}


def _fake_ais_query(dest: str | None = None):
    mem = duckdb.connect(":memory:")
    mem.execute(
        "CREATE TABLE live_positions (mmsi BIGINT, name VARCHAR, lat DOUBLE, lon DOUBLE, "
        "sog DOUBLE, cog DOUBLE, heading DOUBLE, kind VARCHAR, segment VARCHAR, "
        "region VARCHAR, imo BIGINT, draught DOUBLE, updated_ts TIMESTAMP, destination VARCHAR)"
    )
    mem.execute("CREATE TABLE ais_snapshots (snapshot_ts TIMESTAMP, mmsi BIGINT, sog DOUBLE)")
    mem.execute(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
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
            dest,
        ],
    )

    def q(sql: str, params=None):
        return mem.execute(sql, params or []).df()

    return q


def test_build_destination_predictions_scores_underway_vessel(tmp_path, monkeypatch):
    # Pin to heuristic-only regardless of whatever real model artifact a live
    # deploy may have left on disk (analytics/models/ is a real, shared path).
    monkeypatch.setattr(
        "analytics.destination_serving.DestinationModel.load",
        classmethod(lambda cls, model_dir=None: None),
    )
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)
    preds = build_destination_predictions(conn, _fake_ais_query(), now=_NOW)
    assert not preds.empty
    row = preds[preds["mmsi"] == 7001].iloc[0]
    assert row["target_id"] == "cp:suez"  # only geometric candidate ahead of it
    assert row["rank"] == 1
    assert 0.0 <= row["prob"] <= 1.0
    assert row["method"] == "heuristic"  # ML explicitly disabled above


def test_build_destination_predictions_adds_reported_destination_candidate(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)
    preds = build_destination_predictions(conn, _fake_ais_query(dest="ROTTERDAM"), now=_NOW)
    ids = set(preds[preds["mmsi"] == 7001]["target_id"])
    assert "dest:NLRTM" in ids
    row = preds[(preds["mmsi"] == 7001) & (preds["target_id"] == "dest:NLRTM")].iloc[0]
    assert row["reported_match"] is True or bool(row["reported_match"]) is True


def test_build_destination_predictions_empty_when_no_live(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)

    def empty_q(sql, params=None):
        import pandas as pd

        return pd.DataFrame()

    preds = build_destination_predictions(conn, empty_q, now=_NOW)
    assert preds.empty


def test_run_in_conn_persists_predictions(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_targets(conn)
    n = run_in_conn(conn, _fake_ais_query(), now=_NOW)
    assert n >= 1
    got = conn.execute("SELECT count(*) FROM destination_predictions WHERE mmsi = 7001").fetchone()[
        0
    ]
    assert got >= 1


# ---------------------------------------------------------------------------
# Endpoint + runner_destination read path
# ---------------------------------------------------------------------------

_PRED_SCHEMA = """
CREATE TABLE destination_predictions (
    mmsi BIGINT, rank INTEGER, target_id VARCHAR, target_name VARCHAR, target_type VARCHAR,
    target_lat DOUBLE, target_lon DOUBLE, prob DOUBLE, method VARCHAR, reported_match BOOLEAN,
    gc_dist_nm DOUBLE, as_of TIMESTAMP,
    PRIMARY KEY (mmsi, rank)
);
"""

_PRED_SEED = [
    (
        1003,
        1,
        "cp:hormuz",
        "hormuz",
        "chokepoint",
        26.57,
        56.25,
        0.7,
        "heuristic",
        False,
        112.0,
        _NOW,
    ),
    (
        1003,
        2,
        "dest:AEJEA",
        "Jebel Ali",
        "destination",
        25.02,
        55.06,
        0.3,
        "heuristic",
        True,
        250.0,
        _NOW,
    ),
]


@pytest.fixture
def dest_client(tmp_path, monkeypatch) -> TestClient:
    an_file = tmp_path / "freight_analytics.duckdb"
    conn = duckdb.connect(str(an_file))
    conn.execute(_PRED_SCHEMA)
    conn.executemany(
        "INSERT INTO destination_predictions VALUES (" + ",".join("?" * 12) + ")", _PRED_SEED
    )
    conn.close()
    monkeypatch.setenv("ANALYTICS_DB", str(an_file))
    monkeypatch.setenv("AIS_POSITIONS_DB", str(tmp_path / "missing_ais.duckdb"))
    from app.main import app

    return TestClient(app)


def test_destination_endpoint_returns_ranked_candidates(dest_client):
    r = dest_client.get("/api/analytics/destination?mmsi=1003")
    assert r.status_code == 200
    body = r.json()
    assert body["mmsi"] == 1003
    assert body["n"] == 2
    cands = body["candidates"]
    assert cands[0]["target_id"] == "cp:hormuz"
    assert cands[0]["prob"] == 0.7
    assert cands[1]["reported_match"] is True


def test_destination_endpoint_disagrees_with_reported_flag(dest_client):
    # Top candidate (hormuz, prob 0.7) is NOT the reported match, but the reported
    # destination IS present among the candidates (Jebel Ali) -> disagreement flagged.
    r = dest_client.get("/api/analytics/destination?mmsi=1003")
    body = r.json()
    assert body["disagrees_with_reported"] is True


def test_destination_endpoint_unknown_vessel_empty(dest_client):
    r = dest_client.get("/api/analytics/destination?mmsi=999999")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 0
    assert body["candidates"] == []
    assert body["disagrees_with_reported"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
