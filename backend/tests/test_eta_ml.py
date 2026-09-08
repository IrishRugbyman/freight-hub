"""True ETA Phase D: the LightGBM quantile ETA challenger (`analytics.eta_ml`).

Covers the properties that make the challenger defensible rather than its exact
accuracy (which depends on live history): a leakage-free time-based split, a
deterministic fit under a fixed seed, monotone quantiles (P10<=P50<=P90) with a
non-negative conformal band, champion-map gating, artifact round-trip, and the
serving blend falling back to physics where the map does not route to ML.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from analytics import eta_ml

# Feature columns the synthetic frame must carry (mirrors eta_samples).
_LEAD_ORDER = ["0-6h", "6-12h", "12-24h", "24-48h", "48h+"]


def _synth_samples(n_voyages: int = 400, seed: int = 0) -> pd.DataFrame:
    """A learnable synthetic sample set: remaining time ~ route_dist / speed + noise.

    One voyage = one arrival; each voyage contributes a short approach track. The
    signal is real (distance/speed) so the model can fit; arrival_ts spans a range
    so the time-based split has something to order on.
    """
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp("2026-06-01")
    segments = ["VLCC", "Aframax", "Panamax", "Capesize"]
    targets = [("port:rotterdam", "port", False), ("cp:suez", "chokepoint", True)]
    for v in range(n_voyages):
        arrival = base + pd.Timedelta(hours=float(v))  # voyages ordered in time
        tid, ttype, canal = targets[v % len(targets)]
        seg = segments[v % len(segments)]
        sog = float(rng.uniform(8, 16))
        laden = bool(v % 2)
        for _k in range(rng.integers(3, 7)):
            dist = float(rng.uniform(20, 900))
            remaining = dist / sog + float(rng.normal(0, 1.5))
            if remaining <= 0:
                continue
            rows.append(
                {
                    "voyage_id": v,
                    "mmsi": 1000 + v,
                    "target_id": tid,
                    "target_type": ttype,
                    "arrival_ts": arrival,
                    "obs_ts": arrival - pd.Timedelta(hours=remaining),
                    "remaining_h": remaining,
                    "route_dist_nm": dist,
                    "gc_dist_nm": dist * 0.95,
                    "sog": sog,
                    "sog_trail6h": sog,
                    "service_speed": 13.0,
                    "segment": seg,
                    "laden": laden,
                    "draught": float(rng.uniform(8, 20)),
                    "is_canal": canal,
                    "dest_queue_h": 6.0 if canal else 0.0,
                    "approach_bearing": float(rng.uniform(0, 360)),
                }
            )
    return pd.DataFrame(rows)


def test_time_voyage_split_is_ordered_and_disjoint():
    s = _synth_samples()
    train, calib, test = eta_ml.time_voyage_split(s)
    # No voyage crosses a boundary.
    tv, cv, ev = set(train.voyage_id), set(calib.voyage_id), set(test.voyage_id)
    assert tv.isdisjoint(cv) and tv.isdisjoint(ev) and cv.isdisjoint(ev)
    # Test voyages arrive strictly after train voyages (a real walk-forward).
    assert train["arrival_ts"].max() <= test["arrival_ts"].min()


def test_training_is_deterministic_under_seed():
    s = _synth_samples()
    train, _, test = eta_ml.time_voyage_split(s)
    p1 = eta_ml.predict_quantiles(eta_ml.train_quantiles(train), test)
    p2 = eta_ml.predict_quantiles(eta_ml.train_quantiles(train), test)
    np.testing.assert_allclose(p1, p2)


def test_quantiles_are_monotone():
    s = _synth_samples()
    train, _, test = eta_ml.time_voyage_split(s)
    q = eta_ml.predict_quantiles(eta_ml.train_quantiles(train), test)
    assert np.all(q[:, 0] <= q[:, 1] + 1e-9)
    assert np.all(q[:, 1] <= q[:, 2] + 1e-9)


def test_interval_heads_span_exactly_the_target_coverage():
    """The nominal head band must equal TARGET_COVERAGE, not merely contain it.

    Regression guard for the 2026-09-08 fix. The heads were P05/P95 (nominal 90%)
    while TARGET_COVERAGE was 0.80 - a small-sample compensation for
    under-dispersion. Once the model stopped being under-dispersed that band
    over-covered, and because the conformal offset is clamped non-negative
    nothing could narrow it, so the promotion gate rejected cells for being too
    *accurate* about their own uncertainty. Widening the heads to buy coverage
    hides the miscalibration instead of measuring it; conformal is the only part
    allowed to move the band.
    """
    lo, hi = eta_ml.QUANTILES["lo"], eta_ml.QUANTILES["hi"]
    assert hi - lo == pytest.approx(eta_ml.TARGET_COVERAGE)
    assert eta_ml.QUANTILES["mid"] == pytest.approx(0.50)


def test_promotion_band_admits_a_perfectly_calibrated_model():
    """A model that hits TARGET_COVERAGE exactly must be promotable.

    If TARGET_COVERAGE fell outside _COVERAGE_BAND, the gate would be
    unsatisfiable by a correctly calibrated challenger and every promotion would
    be an artefact of miscalibration. This pins the two constants together.
    """
    lo_band, hi_band = eta_ml._COVERAGE_BAND
    assert lo_band < eta_ml.TARGET_COVERAGE < hi_band


def test_champion_map_rejects_a_cell_whose_interval_over_covers():
    """Over-covering is rejected, not just under-covering.

    Built from two frames that are identical except for the band width, so the
    only thing that can flip the verdict is coverage. The wide-band variant beats
    physics on error and covers ~1.0; it must still be refused.
    """
    n = 600
    rng = np.random.default_rng(0)
    truth = rng.uniform(30.0, 40.0, n)  # physics lands these in the 24-48h cell
    base = pd.DataFrame(
        {
            "target_type": "port",
            "remaining_h": truth,
            "route_dist_nm": 300.0,
            "sog": 10.0,
        }
    )
    # Physics is deliberately poor (|err| 8h); ML is far better on the point
    # estimate, so `error` is satisfied for both variants and coverage decides.
    phys = eta_ml._score_frame(base, truth + 8.0, np.full(n, np.nan), np.full(n, np.nan))

    # Residual is 0.2h on 80% of rows and 5h on the rest, so a +/-1h band
    # realises exactly 0.80 coverage - the target, inside the gate.
    resid = np.where(np.arange(n) < int(0.8 * n), 0.2, 5.0)
    ml_pred = truth + resid

    tight = eta_ml._score_frame(base, ml_pred, ml_pred - 1.0, ml_pred + 1.0)
    wide = eta_ml._score_frame(base, ml_pred, ml_pred - 500.0, ml_pred + 500.0)

    assert tight["covered"].mean() == pytest.approx(0.80)
    assert wide["covered"].mean() == pytest.approx(1.0)

    assert eta_ml.build_champion_map(tight, phys) == {"port|24-48h": "ml"}
    assert eta_ml.build_champion_map(wide, phys) == {}


def test_cqr_offsets_are_non_negative():
    s = _synth_samples()
    train, calib, _ = eta_ml.time_voyage_split(s)
    models = eta_ml.train_quantiles(train)
    offsets = eta_ml.calibrate_cqr(models, calib)
    assert offsets  # has at least the global key
    assert all(v >= 0.0 for v in offsets.values())


def test_model_interval_never_shrinks_below_zero_and_stays_monotone():
    s = _synth_samples()
    train, calib, test = eta_ml.time_voyage_split(s)
    models = eta_ml.train_quantiles(train)
    model = eta_ml.ETAModel(models, eta_ml.calibrate_cqr(models, calib), {})
    q = model.ml_quantiles(test)
    assert np.all(q[:, 0] >= 0.0)
    assert np.all(q[:, 0] <= q[:, 1] + 1e-9) and np.all(q[:, 1] <= q[:, 2] + 1e-9)


def test_champion_map_only_promotes_where_ml_wins_and_covers():
    s = _synth_samples()
    train, calib, test = eta_ml.time_voyage_split(s)
    models = eta_ml.train_quantiles(train)
    q = eta_ml.predict_quantiles(models, test)
    low, high = eta_ml._apply_cqr(q, eta_ml.calibrate_cqr(models, calib))
    ml_scored = eta_ml._score_frame(test, q[:, 1], low, high)
    from analytics.eta_physics import vectorized_physics_p50

    phys_scored = eta_ml._score_frame(
        test, vectorized_physics_p50(test), np.full(len(test), np.nan), np.full(len(test), np.nan)
    )
    champ = eta_ml.build_champion_map(ml_scored, phys_scored)
    # Every promoted cell key is well-formed and only ever maps to 'ml'.
    for key, val in champ.items():
        assert val == "ml"
        ttype, lead = key.split("|")
        assert ttype in ("chokepoint", "port")
        assert lead in _LEAD_ORDER


def test_artifact_round_trip(tmp_path):
    s = _synth_samples()
    train, calib, _ = eta_ml.time_voyage_split(s)
    models = eta_ml.train_quantiles(train)
    champ = {"port|24-48h": "ml"}
    model = eta_ml.ETAModel(models, eta_ml.calibrate_cqr(models, calib), champ)
    model.save(tmp_path)
    loaded = eta_ml.ETAModel.load(tmp_path)
    assert loaded is not None
    assert loaded.champion_map == champ
    # Predictions match the in-memory model exactly after a disk round-trip.
    _, _, test = eta_ml.time_voyage_split(s)
    np.testing.assert_allclose(model.ml_quantiles(test), loaded.ml_quantiles(test))


def test_load_returns_none_when_absent(tmp_path):
    assert eta_ml.ETAModel.load(tmp_path) is None


def test_serving_choice_routes_by_champion_map():
    s = _synth_samples()
    train, calib, test = eta_ml.time_voyage_split(s)
    models = eta_ml.train_quantiles(train)
    # Force ML on for the "port|24-48h" cell only.
    model = eta_ml.ETAModel(models, eta_ml.calibrate_cqr(models, calib), {"port|24-48h": "ml"})
    from analytics.eta_physics import vectorized_physics_p50

    obs = test.head(50).copy()
    phys = vectorized_physics_p50(obs)
    use_ml, ml_p50, _, _ = eta_ml.serving_choice(model, obs, phys)
    assert ml_p50 is not None
    # Any row flagged for ML must be a port row whose physics bucket is 24-48h.
    from analytics.eta_backtest import lead_buckets

    buckets = lead_buckets(np.where(np.isfinite(phys), phys, np.inf))
    for i in np.where(use_ml)[0]:
        assert obs.iloc[i]["target_type"] == "port"
        assert buckets[i] == "24-48h"


def test_serving_choice_no_model_is_all_physics():
    s = _synth_samples()
    _, _, test = eta_ml.time_voyage_split(s)
    use_ml, a, b, c = eta_ml.serving_choice(None, test, np.zeros(len(test)))
    assert not use_ml.any() and a is None and b is None and c is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
