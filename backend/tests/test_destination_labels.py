"""Destination predictor Phase 0: the port-call transition graph + visit frequency.

Covers the smoothing/fallback behaviour that makes `TransitionPriors` and
`VisitFrequency` defensible: additive smoothing gives unseen pairs a small
non-zero probability, an unknown prior port falls back to the marginal, and
visit share is honestly 0 for a vessel/target never observed.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from analytics import destination_labels as dl


def _arrivals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"mmsi": 1, "target_id": "port:a", "arrival_ts": pd.Timestamp("2026-01-01"), "segment": "VLCC"},
            {"mmsi": 1, "target_id": "port:b", "arrival_ts": pd.Timestamp("2026-01-05"), "segment": "VLCC"},
            {"mmsi": 1, "target_id": "port:a", "arrival_ts": pd.Timestamp("2026-01-10"), "segment": "VLCC"},
            {"mmsi": 2, "target_id": "port:b", "arrival_ts": pd.Timestamp("2026-01-02"), "segment": "Capesize"},
        ]
    )


def test_build_transitions_counts_consecutive_pairs_and_rollup():
    trans = dl.build_transitions(_arrivals())
    # a -> b (VLCC) observed once, plus its segment-agnostic rollup.
    row = trans[
        (trans.prev_target_id == "port:a") & (trans.next_target_id == "port:b") & (trans.segment == "VLCC")
    ]
    assert row["cnt"].iloc[0] == 1
    rollup = trans[
        (trans.prev_target_id == "port:a") & (trans.next_target_id == "port:b") & (trans.segment == "__all__")
    ]
    assert rollup["cnt"].iloc[0] == 1
    # First-ever arrival for mmsi=1 (port:a) has no prior port -> only the marginal.
    any_row = trans[(trans.prev_target_id == "__any__") & (trans.next_target_id == "port:a")]
    assert not any_row.empty


def test_transition_prior_smoothing_and_fallback():
    trans = dl.build_transitions(_arrivals())
    tp = dl.TransitionPriors(trans, vocab_size=3)
    # Observed pair scores higher than an unseen one from the same prev/segment.
    seen = tp.prior("port:a", "port:b", "VLCC")
    unseen = tp.prior("port:a", "port:x", "VLCC")
    assert seen > unseen > 0.0  # smoothing: never exactly zero
    # Unknown prior port falls back to the marginal (__any__) rather than erroring.
    marginal = tp.prior(None, "port:b", "VLCC")
    assert 0.0 < marginal < 1.0


def test_transition_prior_falls_back_to_all_segments_when_segment_unseen():
    trans = dl.build_transitions(_arrivals())
    tp = dl.TransitionPriors(trans, vocab_size=3)
    # "Suezmax" was never observed after port:a; must fall back to the __all__
    # rollup for that (prev, segment) rather than the global marginal.
    p = tp.prior("port:a", "port:b", "Suezmax")
    assert p > 0.0


def test_visit_frequency_shares_sum_to_one_per_vessel():
    visits = dl.build_visit_freq(_arrivals())
    vf = dl.VisitFrequency(visits)
    assert vf.freq(1, "port:a") == pytest.approx(2 / 3)
    assert vf.freq(1, "port:b") == pytest.approx(1 / 3)
    assert vf.freq(1, "port:a") + vf.freq(1, "port:b") == pytest.approx(1.0)


def test_visit_frequency_unseen_is_honestly_zero():
    vf = dl.VisitFrequency(dl.build_visit_freq(_arrivals()))
    assert vf.freq(999, "port:a") == 0.0
    assert vf.freq(1, "port:z") == 0.0


def test_empty_arrivals_produce_empty_tables_not_errors():
    empty = pd.DataFrame(columns=["mmsi", "target_id", "arrival_ts", "segment"])
    assert dl.build_transitions(empty).empty
    assert dl.build_visit_freq(empty).empty
    tp = dl.TransitionPriors(dl.build_transitions(empty), vocab_size=5)
    assert 0.0 < tp.prior(None, "port:a", None) <= 1.0  # still a valid (smoothed) prior


def test_run_in_conn_persists_both_tables(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    from analytics.eta_labels import ETA_SCHEMA

    conn.execute(ETA_SCHEMA)
    for r in _arrivals().itertuples():
        conn.execute(
            "INSERT INTO eta_arrivals (mmsi, target_id, arrival_ts, min_dist_nm, segment, laden, approach_start_ts) "
            "VALUES (?, ?, ?, 1.0, ?, NULL, ?)",
            [r.mmsi, r.target_id, r.arrival_ts, r.segment, r.arrival_ts],
        )
    n_t, n_v = dl.run_in_conn(conn)
    assert n_t > 0 and n_v > 0
    assert conn.execute("SELECT count(*) FROM dest_transitions").fetchone()[0] == n_t
    assert conn.execute("SELECT count(*) FROM dest_port_visits").fetchone()[0] == n_v


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
