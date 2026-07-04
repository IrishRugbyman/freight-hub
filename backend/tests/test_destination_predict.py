"""Destination predictor: heuristic scorer (Phase 1) + LightGBM reranker (Phase 2).

Covers the heuristic's monotonicity (closer/aligned/reported/history-favoured
candidates score higher, and probabilities sum to 1 per vessel), the training-set
reconstruction from completed voyages (`build_training_candidates`), and the
challenger's defensibility properties: deterministic fit, an artifact round-trip,
and a champion/challenger gate that only promotes when it genuinely beats the
heuristic on a held-out split.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest
from analytics import destination_predict as dp
from analytics.eta_labels import ETA_SCHEMA

# ---------------------------------------------------------------------------
# Heuristic scorer
# ---------------------------------------------------------------------------


def test_heuristic_score_rewards_alignment_and_proximity():
    near_aligned = {"gc_dist_nm": 20.0, "bearing_align": 1.0}
    far_offaxis = {"gc_dist_nm": 900.0, "bearing_align": 0.1}
    assert dp.heuristic_raw_score(near_aligned) > dp.heuristic_raw_score(far_offaxis)


def test_heuristic_score_rewards_reported_match_and_history():
    base = {"gc_dist_nm": 200.0, "bearing_align": 0.5}
    with_reported = {**base, "reported_match": True}
    with_history = {**base, "transition_prior": 0.8, "visit_freq": 0.9}
    plain = dp.heuristic_raw_score(base)
    assert dp.heuristic_raw_score(with_reported) > plain
    assert dp.heuristic_raw_score(with_history) > plain


def test_heuristic_score_penalizes_canal_backtrack():
    base = {"gc_dist_nm": 200.0, "bearing_align": 0.5}
    with_backtrack = {**base, "canal_backtrack": 1}
    assert dp.heuristic_raw_score(with_backtrack) < dp.heuristic_raw_score(base)


def test_heuristic_score_degrades_gracefully_on_missing_fields():
    # No KeyError even though optional columns (route_dist_nm, priors) are absent.
    score = dp.heuristic_raw_score({"gc_dist_nm": 100.0})
    assert np.isfinite(score)


def test_softmax_by_group_sums_to_one_per_vessel():
    df = pd.DataFrame({"mmsi": [1, 1, 1, 2, 2], "score": [3.0, 1.0, 0.0, 5.0, 5.0]})
    probs = dp.softmax_by_group(df, "score", "mmsi")
    df = df.assign(prob=probs)
    for _mmsi, g in df.groupby("mmsi"):
        assert g["prob"].sum() == pytest.approx(1.0)
    # Vessel 1's highest raw score gets the highest probability.
    v1 = df[df["mmsi"] == 1].sort_values("prob", ascending=False)
    assert v1["score"].iloc[0] == 3.0


def test_heuristic_score_candidates_end_to_end():
    candidates = pd.DataFrame(
        [
            {
                "mmsi": 1,
                "target_id": "a",
                "gc_dist_nm": 20.0,
                "bearing_align": 1.0,
                "reported_match": True,
            },
            {
                "mmsi": 1,
                "target_id": "b",
                "gc_dist_nm": 900.0,
                "bearing_align": 0.1,
                "reported_match": False,
            },
        ]
    )
    scored = dp.heuristic_score_candidates(candidates)
    assert (scored["method"] == "heuristic").all()
    assert scored["prob"].sum() == pytest.approx(1.0)
    top = scored.sort_values("prob", ascending=False).iloc[0]
    assert top["target_id"] == "a"


def test_heuristic_score_candidates_empty_input():
    out = dp.heuristic_score_candidates(pd.DataFrame())
    assert out.empty


# ---------------------------------------------------------------------------
# Training-set reconstruction from completed voyages
# ---------------------------------------------------------------------------


def _seed_voyage_db(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(ETA_SCHEMA)
    targets = [
        ("port:a", "port", "Port A", 10.0, 10.0, 15.0, False),
        ("port:b", "port", "Port B", 10.0, 12.0, 15.0, False),
        ("port:c", "port", "Port C", -10.0, -10.0, 15.0, False),
    ]
    for t in targets:
        conn.execute(
            "INSERT INTO eta_targets (target_id, target_type, name, lat, lon, reach_nm, is_canal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(t),
        )

    from analytics.eta_samples import ETA_SAMPLES_SCHEMA

    conn.execute(ETA_SAMPLES_SCHEMA)

    base = pd.Timestamp("2026-06-01")
    rows = []
    arrivals = []
    # 30 voyages alternating destination port:a / port:b, always approaching from
    # the south (course points north, toward whichever port is the true one).
    for v in range(30):
        arrival = base + pd.Timedelta(hours=6 * v)
        true_tid, true_lat, true_lon = (
            ("port:a", 10.0, 10.0) if v % 2 == 0 else ("port:b", 10.0, 12.0)
        )
        mmsi = 1000 + v
        for k in range(5):
            remaining = float(5 - k) * 4.0 + 1.0  # 21,17,13,9,5 hours out
            obs_lat = true_lat - 0.5 * (5 - k)  # farther south early on, closing in as k grows
            obs_lon = true_lon
            rows.append(
                {
                    "voyage_id": v,
                    "mmsi": mmsi,
                    "target_id": true_tid,
                    "arrival_ts": arrival,
                    "obs_ts": arrival - pd.Timedelta(hours=remaining),
                    "obs_lat": obs_lat,
                    "obs_lon": obs_lon,
                    "remaining_h": remaining,
                    "route_dist_nm": remaining * 12.0,
                    "gc_dist_nm": remaining * 12.0,
                    "route_method": "gc",
                    "sog": 12.0,
                    "sog_trail6h": 12.0,
                    "service_speed": 12.0,
                    "draught": 15.0,
                    "dest_queue_h": 0.0,
                    "approach_bearing": 0.0,
                    "segment": "VLCC",
                    "laden": True,
                    "target_type": "port",
                    "is_canal": False,
                    "lead_bucket": "0-6h",
                }
            )
        arrivals.append((mmsi, true_tid, arrival, "VLCC"))

    frame = pd.DataFrame(rows)
    conn.register("_f", frame)
    cols = [
        "voyage_id",
        "mmsi",
        "target_id",
        "arrival_ts",
        "obs_ts",
        "obs_lat",
        "obs_lon",
        "remaining_h",
        "route_dist_nm",
        "gc_dist_nm",
        "route_method",
        "sog",
        "sog_trail6h",
        "service_speed",
        "draught",
        "dest_queue_h",
        "approach_bearing",
        "segment",
        "laden",
        "target_type",
        "is_canal",
        "lead_bucket",
    ]
    conn.execute(f"INSERT INTO eta_samples ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _f")
    conn.unregister("_f")

    for mmsi, tid, arrival, seg in arrivals:
        conn.execute(
            "INSERT INTO eta_arrivals (mmsi, target_id, arrival_ts, min_dist_nm, segment, laden, approach_start_ts) "
            "VALUES (?, ?, ?, 1.0, ?, TRUE, ?)",
            [mmsi, tid, arrival, seg, arrival - pd.Timedelta(hours=21)],
        )


def test_chokepoint_as_of_returns_most_recent_within_window():
    history = [(pd.Timestamp("2026-06-01"), "suez"), (pd.Timestamp("2026-06-10"), "panama")]
    assert dp._chokepoint_as_of(history, pd.Timestamp("2026-06-15"), 21.0) == "panama"


def test_chokepoint_as_of_ignores_future_transit():
    # A transit dated after obs_ts must never be visible - the leakage guard.
    history = [(pd.Timestamp("2026-06-20"), "suez")]
    assert dp._chokepoint_as_of(history, pd.Timestamp("2026-06-15"), 21.0) is None


def test_chokepoint_as_of_ignores_stale_transit():
    history = [(pd.Timestamp("2026-05-01"), "suez")]
    assert dp._chokepoint_as_of(history, pd.Timestamp("2026-06-15"), 21.0) is None


def test_chokepoint_as_of_empty_history():
    assert dp._chokepoint_as_of([], pd.Timestamp("2026-06-15"), 21.0) is None


def test_build_training_candidates_labels_true_target_positive(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    cands = dp.build_training_candidates(conn)
    assert not cands.empty
    # Every voyage/observation group has exactly one positive.
    for _, g in cands.groupby(["voyage_id", "obs_ts"]):
        assert g["is_destination"].sum() == 1
    # At most _MAX_OBS_PER_VOYAGE distinct observations kept per voyage.
    per_voyage = cands.groupby("voyage_id")["obs_ts"].nunique()
    assert (per_voyage <= dp._MAX_OBS_PER_VOYAGE).all()


def test_build_training_candidates_bearing_favours_true_target(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    cands = dp.build_training_candidates(conn)
    # The vessel steams due north toward its true target every time (by
    # construction); the reconstructed course should align best with the true
    # candidate more often than with an off-axis one (port:c, far to the SW).
    true_align = cands[cands["is_destination"] == 1]["bearing_align"].mean()
    other_align = cands[cands["is_destination"] == 0]["bearing_align"].mean()
    assert true_align > other_align


def test_build_training_candidates_populates_canal_backtrack(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    conn.execute(ETA_SCHEMA)
    from analytics.eta_samples import ETA_SAMPLES_SCHEMA

    conn.execute(ETA_SAMPLES_SCHEMA)
    conn.execute(
        "CREATE TABLE transit_events (mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, "
        "exited_ts TIMESTAMP, direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN)"
    )
    # Two targets straddling the Suez gate (lat 30.50): one north (reachable
    # without re-transiting), one south (would require backtracking through
    # the gate the vessel just cleared).
    for t in [
        ("port:north", "port", "North Port", 45.0, 20.0, 15.0, False),
        ("port:south", "port", "South Port", 20.0, 40.0, 15.0, False),
    ]:
        conn.execute(
            "INSERT INTO eta_targets (target_id, target_type, name, lat, lon, reach_nm, is_canal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(t),
        )
    arrival = pd.Timestamp("2026-06-10")
    obs_ts = arrival - pd.Timedelta(hours=5)
    conn.execute(
        "INSERT INTO eta_samples (voyage_id, mmsi, target_id, arrival_ts, obs_ts, obs_lat, obs_lon, "
        "remaining_h, segment, target_type) VALUES (0, 2000, 'port:north', ?, ?, 32.0, 30.0, 5.0, 'VLCC', 'port')",
        [arrival, obs_ts],
    )
    conn.execute(
        "INSERT INTO eta_arrivals (mmsi, target_id, arrival_ts, min_dist_nm, segment, laden, approach_start_ts) "
        "VALUES (2000, 'port:north', ?, 1.0, 'VLCC', TRUE, ?)",
        [arrival, arrival - pd.Timedelta(hours=5)],
    )
    # Vessel transited Suez northbound 1 day before this observation - well
    # within the 21-day backtrack window.
    conn.execute(
        "INSERT INTO transit_events VALUES (2000, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE)",
        [obs_ts - pd.Timedelta(days=2), obs_ts - pd.Timedelta(days=1)],
    )
    cands = dp.build_training_candidates(conn)
    south = cands[cands["target_id"] == "port:south"]
    north = cands[cands["target_id"] == "port:north"]
    assert not south.empty and not north.empty
    assert (south["canal_backtrack"] == 1).all()
    assert (north["canal_backtrack"] == 0).all()


def test_build_training_candidates_canal_backtrack_defaults_without_transit_events(tmp_path):
    # No transit_events table at all (fresh DB) - must not crash, and the
    # column must still exist with the neutral default.
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    cands = dp.build_training_candidates(conn)
    assert not cands.empty
    assert (cands["canal_backtrack"] == 0).all()


def test_build_training_candidates_empty_when_no_samples(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    conn.execute(ETA_SCHEMA)
    assert dp.build_training_candidates(conn).empty


# ---------------------------------------------------------------------------
# LightGBM challenger: determinism, artifact round-trip, champion gate
# ---------------------------------------------------------------------------


def test_prepare_defaults_missing_canal_backtrack_to_zero():
    # A candidate frame built before this feature existed (or a hand-built
    # test frame) may not carry canal_backtrack at all - _prepare must not
    # KeyError, and must treat it as 0 (no penalty).
    df = pd.DataFrame(
        [
            {
                "gc_dist_nm": 10.0,
                "bearing_align": 1.0,
                "transition_prior": 0.5,
                "visit_freq": 0.5,
                "target_type": "port",
                "segment": "VLCC",
            }
        ]
    )
    out = dp._prepare(df)
    assert out["canal_backtrack"].iloc[0] == 0


def test_prepare_defaults_missing_draught_to_none():
    # Mirrors test_prepare_defaults_missing_canal_backtrack_to_zero: a hand-built
    # test frame may not carry draught at all - _prepare must not KeyError, and
    # must treat it as missing (NaN), not a fabricated value.
    df = pd.DataFrame(
        [
            {
                "gc_dist_nm": 10.0,
                "bearing_align": 1.0,
                "transition_prior": 0.5,
                "visit_freq": 0.5,
                "target_type": "port",
                "segment": "VLCC",
            }
        ]
    )
    out = dp._prepare(df)
    assert pd.isna(out["draught"].iloc[0])


def test_build_training_candidates_carries_draught_from_eta_samples(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    cands = dp.build_training_candidates(conn)
    assert not cands.empty
    assert (cands["draught"] == 15.0).all()


def test_train_and_evaluate_runs_and_reports_metrics(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    report = dp.train_and_evaluate(conn, persist=False)
    assert report["n_rows"] > 0
    assert report["n_voyages"] > 0
    assert "ml_metrics" in report and "heuristic_metrics" in report
    assert 0.0 <= report["ml_metrics"]["top1_acc"] <= 1.0
    assert isinstance(report["promoted"], bool)


def test_train_and_evaluate_persists_artifact_and_metrics_when_persist(tmp_path, monkeypatch):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    # train_and_evaluate(persist=True) saves to the module-level MODEL_DIR by
    # default (mirrors eta_ml's own `.save()`) - redirect it to a scratch dir so
    # the test never writes into the real, gitignored production models/ folder.
    monkeypatch.setattr(dp, "MODEL_DIR", tmp_path / "models")
    report = dp.train_and_evaluate(conn, persist=True)
    model = report["model"]
    loaded = dp.DestinationModel.load(tmp_path / "models")
    assert loaded is not None
    assert loaded.promoted == model.promoted
    n = conn.execute("SELECT count(*) FROM destination_model_metrics").fetchone()[0]
    assert n == 2  # heuristic + ml rows


def test_destination_model_load_returns_none_when_absent(tmp_path):
    assert dp.DestinationModel.load(tmp_path) is None


def test_score_candidates_falls_back_to_heuristic_without_model():
    candidates = pd.DataFrame(
        [
            {
                "mmsi": 1,
                "target_id": "a",
                "gc_dist_nm": 10.0,
                "bearing_align": 1.0,
                "reported_match": True,
            }
        ]
    )
    scored = dp.score_candidates(candidates, None)
    assert scored["method"].iloc[0] == "heuristic"


def test_score_candidates_uses_ml_when_promoted(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    report = dp.train_and_evaluate(conn, persist=False)
    model = report["model"]
    model.promoted = True  # force-exercise the ml serving path regardless of gate outcome
    candidates = pd.DataFrame(
        [
            {
                "mmsi": 1,
                "target_id": "port:a",
                "target_type": "port",
                "segment": "VLCC",
                "gc_dist_nm": 20.0,
                "bearing_align": 0.9,
                "transition_prior": 0.5,
                "visit_freq": 0.5,
            },
            {
                "mmsi": 1,
                "target_id": "port:b",
                "target_type": "port",
                "segment": "VLCC",
                "gc_dist_nm": 900.0,
                "bearing_align": 0.1,
                "transition_prior": 0.1,
                "visit_freq": 0.1,
            },
        ]
    )
    scored = dp.score_candidates(candidates, model)
    assert (scored["method"] == "ml").all()
    assert scored["prob"].sum() == pytest.approx(1.0)


def test_score_candidates_falls_back_to_heuristic_on_stale_model_feature_mismatch():
    # Simulate a booster trained before canal_backtrack existed: it only knows
    # 4 numeric features instead of the current 5.
    old_features = ["gc_dist_nm", "bearing_align", "transition_prior", "visit_freq"]
    train = pd.DataFrame(
        [
            {
                "gc_dist_nm": 20.0,
                "bearing_align": 0.9,
                "transition_prior": 0.5,
                "visit_freq": 0.5,
                "target_type": "port",
                "segment": "VLCC",
                "is_destination": 1,
            },
            {
                "gc_dist_nm": 900.0,
                "bearing_align": 0.1,
                "transition_prior": 0.1,
                "visit_freq": 0.1,
                "target_type": "port",
                "segment": "VLCC",
                "is_destination": 0,
            },
        ]
    )
    X = train[old_features].copy()
    for c in old_features:
        X[c] = pd.to_numeric(X[c])
    X["target_type"] = train["target_type"].astype("category")
    X["segment"] = train["segment"].astype("category")
    import lightgbm as lgb

    dtrain = lgb.Dataset(
        X,
        label=train["is_destination"].to_numpy(dtype=float),
        categorical_feature=["target_type", "segment"],
        free_raw_data=False,
    )
    stale_booster = lgb.train({**dp.LGB_PARAMS, "objective": "binary"}, dtrain, num_boost_round=5)
    stale_model = dp.DestinationModel(stale_booster, promoted=True, metrics={})

    candidates = pd.DataFrame(
        [
            {
                "mmsi": 1,
                "target_id": "port:a",
                "target_type": "port",
                "segment": "VLCC",
                "gc_dist_nm": 20.0,
                "bearing_align": 0.9,
                "transition_prior": 0.5,
                "visit_freq": 0.5,
                "canal_backtrack": 0,
            }
        ]
    )
    scored = dp.score_candidates(candidates, stale_model)
    assert scored["method"].iloc[0] == "heuristic"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
