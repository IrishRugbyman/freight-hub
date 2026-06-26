"""Phase C of the True ETA build: the deterministic "physics" ETA + intervals.

The Phase-B routing baseline (`route_dist / instantaneous_SOG`) still carries the
naive model's long-lead optimism: it assumes a vessel sails straight to the
target at its current speed. Two refinements live here.

1. **Effective speed** (`quant_lib.freight.effective_speed`): the instantaneous
   SOG is noisy and momentary, so the point estimate uses the vessel's *trailing
   6 h* speed blended with a segment cruise prior, with a proximity-gated canal
   staging allowance. This is `model='physics_v1'`.

2. **Calibrated intervals**: the dominant residual error at long lead is *not*
   distance or cruise speed - it is anchorage / loiter / near-pass time that no
   position+speed model can resolve (a vessel 15 nm off a strait at 13 kn has a
   real remaining-time distribution of p10 ~ 0.7 h but p90 ~ 60 h, because a fast
   near-pass is often a vessel transiting *past* whose true closest approach
   comes much later). Rather than fake a precise point estimate, we attach an
   honest interval: residual quantiles learned on a held-out train split, bucketed
   by the predicted ETA, applied on test. The band is asymmetric and widens with
   lead - the correct representation of that irreducible uncertainty, and the
   explicit motivation for the history-gated ML phase.

`IntervalModel` is fit leakage-free (voyage-grouped split, residuals binned by a
serve-time-known quantity). The point-estimate functions are thin wrappers over
the pure `quant_lib.freight.eta` primitives.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from quant_lib.freight import effective_speed, physics_eta, queue_wait

from analytics.eta_backtest import _MIN_SOG_KN

log = logging.getLogger(__name__)

# Interval buckets: residual quantiles are learned per predicted-ETA band. These
# are the model's *own* P50 (serve-time known), not the actual remaining time, so
# bucketing introduces no leakage.
_PRED_EDGES = [0.0, 6.0, 12.0, 24.0, 48.0, np.inf]
_PRED_LABELS = ["p0-6h", "p6-12h", "p12-24h", "p24-48h", "p48h+"]

# Target central interval (P10..P90 -> ~80% nominal coverage).
_Q_LOW = 0.10
_Q_HIGH = 0.90


def _route_distance(obs: dict) -> float:
    """Sea-route distance for a sample, degrading to great-circle if unrouted."""
    dist = obs.get("route_dist_nm")
    if dist is None or not np.isfinite(dist):
        return float(obs["gc_dist_nm"])
    return float(dist)


def physics_p50(obs: dict) -> float:
    """Deterministic physics ETA (hours) for one observation.

    effective_speed(trailing 6 h, segment prior, laden) -> route_time, plus a
    proximity-gated canal staging wait. Returns ``nan`` when not underway, gated on
    the *instantaneous* SOG exactly like the kinematic baselines so all three
    models score the identical underway sample set (the trailing speed could
    otherwise produce an estimate for a momentarily-stopped fix the baselines skip,
    making the comparison unfair).
    """
    sog = obs.get("sog") or 0.0
    if sog < _MIN_SOG_KN:
        return float("nan")
    eff = effective_speed(
        obs.get("sog"),
        obs.get("sog_trail6h"),
        obs.get("segment"),
        obs.get("laden"),
    )
    if not np.isfinite(eff):
        return float("nan")
    dist = _route_distance(obs)
    qw = queue_wait(bool(obs.get("is_canal")), dist, obs.get("target_id"))
    return physics_eta(dist, eff, qw)


def _pred_bucket(p50: float) -> str:
    for i in range(len(_PRED_LABELS)):
        if _PRED_EDGES[i] <= p50 < _PRED_EDGES[i + 1]:
            return _PRED_LABELS[i]
    return _PRED_LABELS[-1]


class IntervalModel:
    """Empirical residual-quantile interval, bucketed by predicted ETA.

    `fit(samples)` computes the physics P50 for each (underway) training row, then
    stores the P10/P90 of the residual `actual - p50` per predicted-ETA bucket
    (plus a global fallback). `offsets(p50)` returns the additive (low, high)
    offsets for a prediction in that bucket. Low offsets are clamped so the band
    never implies a negative ETA at apply time.
    """

    def __init__(self) -> None:
        self._lo: dict[str, float] = {}
        self._hi: dict[str, float] = {}
        self._lo_global = 0.0
        self._hi_global = 0.0
        self.fitted = False

    def fit(self, samples: pd.DataFrame) -> IntervalModel:
        if samples.empty:
            return self
        recs = samples.to_dict("records")
        p50 = np.array([physics_p50(r) for r in recs], dtype=float)
        actual = samples["remaining_h"].to_numpy(dtype=float)
        ok = np.isfinite(p50)
        p50, actual = p50[ok], actual[ok]
        if p50.size == 0:
            return self
        resid = actual - p50
        self._lo_global = float(np.quantile(resid, _Q_LOW))
        self._hi_global = float(np.quantile(resid, _Q_HIGH))
        buckets = np.array([_pred_bucket(v) for v in p50])
        for b in _PRED_LABELS:
            r = resid[buckets == b]
            if r.size >= 50:  # enough to estimate a tail quantile
                self._lo[b] = float(np.quantile(r, _Q_LOW))
                self._hi[b] = float(np.quantile(r, _Q_HIGH))
        self.fitted = True
        return self

    def offsets(self, p50: float) -> tuple[float, float]:
        b = _pred_bucket(p50)
        return self._lo.get(b, self._lo_global), self._hi.get(b, self._hi_global)


def make_physics_fn(interval: IntervalModel | None = None):
    """Build the `eta_fn` the harness scores.

    Without an interval -> returns the bare P50 (float). With a fitted interval ->
    returns ``{"p50", "low", "high"}`` so the harness records interval coverage.
    """

    def fn(obs: dict):
        p50 = physics_p50(obs)
        if interval is None or not interval.fitted or not np.isfinite(p50):
            return p50
        lo_off, hi_off = interval.offsets(p50)
        return {"p50": p50, "low": max(0.0, p50 + lo_off), "high": p50 + hi_off}

    return fn
