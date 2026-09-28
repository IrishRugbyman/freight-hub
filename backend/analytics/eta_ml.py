"""Phase D of the True ETA build: the LightGBM quantile ETA challenger.

Physics (`eta_physics.physics_v1`) is the shipped champion. It is excellent at
short lead (kinematics dominate) but structurally *optimistic* at long lead: a
vessel loitering / passing near a target divides a small route distance by its
speed and reports a near-arrival, so 24-48h+ forecasts carry a large negative
bias no position+speed model can remove. That residual is *learnable* - it lives
in the target, the approach bearing, the draught and the trailing-speed decay -
which is exactly what a gradient-boosted model captures.

This module trains three LightGBM quantile regressors (alpha 0.1 / 0.5 / 0.9) on
`eta_samples` for each of two targets (see ``TARGETS``: raw remaining hours, and
the log-ratio of the truth to the physics estimate), calibrates the P10-P90 band with split-conformal (CQR) so the
served interval hits its nominal coverage, and runs a leakage-free, **time-based
voyage-grouped** walk-forward against physics. ML is promoted to `method='ml'`
only per (target_type, predicted-lead-bucket) cell where it beats physics on
held-out median |err| *and* keeps interval coverage in [0.75, 0.85] - everywhere
else physics stays champion. Where both targets qualify, the cell goes to the one
with the lower error. The result is a blended model: physics for the short-lead
cells it owns, the better ML target for the cells it fixes.

Leakage control mirrors the rest of the build: the split is by voyage arrival
time (no voyage straddles train/calibration/test, and the test set is strictly
*later* than train - a real walk-forward, not a random shuffle). Every feature is
serve-time-known; nothing is derived from a future fix.

    python -m analytics.eta_ml            # train + walk-forward + (gated) promote
    python -m analytics.eta_ml --dry-run  # evaluate + print, never write artifacts
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.eta_backtest import (
    _LEAD_LABELS,
    _MIN_SOG_KN,
    _metric_rows,
    _metric_rows_by_target,
    lead_buckets,
    write_metrics,
    write_metrics_by_target,
)
from analytics.eta_labels import ANALYTICS_DB
from analytics.eta_physics import vectorized_physics_p50

log = logging.getLogger(__name__)

# --- feature set (all serve-time-known; no future-fix derivation) -----------
NUMERIC_FEATURES = [
    "route_dist_nm",
    "gc_dist_nm",
    "sog",
    "sog_trail6h",
    "service_speed",
    "draught",
    "dest_queue_h",
    "approach_bearing",
]
CATEGORICAL_FEATURES = ["segment", "target_id", "target_type", "is_canal", "laden"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
LABEL = "remaining_h"
# The physics estimate as a feature. Computed from the columns above by
# `vectorized_physics_p50`, the same function serving uses, so it is serve-time
# known and identical at train and serve.
PHYS_FEATURE = "phys_p50"

# Training targets. Each trains its own three quantile heads, and the champion
# gate picks per (target_type, lead) cell between physics and every target.
#
# - ``raw`` learns remaining hours directly. Pinball loss in hours weights a 10h
#   miss at 60h the same as a 10h miss at 3h, which is what wins the long-lead
#   cells.
# - ``logratio`` learns ``log(remaining_h / physics_p50)``: the multiplicative
#   correction physics needs. Quantiles are equivariant under the monotone map
#   ``q -> physics * exp(q)``, so the heads transform back to hour quantiles
#   exactly. The loss is relative, which is what physics gets wrong near a target
#   (a vessel slowing into an anchorage divides a small distance by a speed that
#   is about to fall).
#
# Measured 2026-09-27 on 1.46M samples, same walk-forward split as the gate,
# 95% voyage-grouped bootstrap CIs on the median |err| difference: logratio beat
# raw in 8 of 10 cells with CIs excluding zero - port|0-6h 12.22h -> 11.27h
# (n=128k), port|12-24h 13.51 -> 12.89, port|24-48h 12.03 -> 11.71 - and lost
# only the two 48h+ cells, by 2-3h. An *additive* residual on physics
# (``remaining_h - physics_p50``) was also measured and was no better than raw
# (11.30h vs 11.35h overall), so it is not carried. Neither target dominates,
# hence both, with the gate choosing.
TARGETS = ("raw", "logratio")
_TARGET_FEATURES = {"raw": FEATURES, "logratio": [*FEATURES, PHYS_FEATURE]}
# Champion-map value the pre-2026-09-27 artifacts used for the only target there
# was. Read as ``raw`` on load.
_LEGACY_ML = "ml"

# Quantile heads. `lo`/`hi` are the interval heads; `mid` is the P50 point
# estimate. Keys double as the saved-artifact filenames.
#
# These were P05/P95 (nominal 90%) until 2026-09-08. That was a deliberate
# small-sample fix: on the original ~3-week history a P10-P90 head was
# under-dispersed out-of-time and realised only ~0.71 coverage, below the
# [0.75,0.85] promotion band, while P05/P95 realised ~0.83 and fit inside it.
#
# On ~8 weeks (1.05M underway samples, 112k voyages) the under-dispersion is
# gone and the compensation inverted: measured on the walk-forward test window,
# raw P05/P95 now realises 0.876 - *above* the band - and, because the CQR
# offset is clamped non-negative, nothing can narrow it back. Six of the seven
# cells where ML beat physics on median |err| were being rejected for
# over-covering, none for under-covering. Raw P10/P90 realises 0.774 on the same
# window and CQR widens it to ~0.80, so the band now lands inside the gate and
# those cells promote on their merits.
#
# P10/P90 is also the coherent choice independently of the gate: TARGET_COVERAGE
# is 0.80, physics serves an 80% band, and the API/UI fields are named
# `eta_p10_h`/`eta_p90_h`. The 90% heads made those names untrue and compared ML
# against physics at two different band widths.
QUANTILES = {"lo": 0.10, "mid": 0.50, "hi": 0.90}
_Q_ORDER = ("lo", "mid", "hi")
TARGET_COVERAGE = 0.80

# Modest, regularised hyperparameters. Chosen when history was ~3 weeks:
# shallow-ish trees, strong leaf minimums and bagging keep the model from
# memorising voyages. History is now ~8 weeks and the capacity ceiling has not
# been re-measured since - retuning is tracked as a separate change, so that a
# capacity effect is never confounded with the 2026-09-08 band fix.
LGB_PARAMS: dict = {
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 100,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "max_depth": -1,
    "seed": 0,
    "num_threads": -1,
    "verbosity": -1,
}
# Boosting rounds are NOT a tuned constant. They were: `NUM_BOOST_ROUND = 400`,
# chosen when history was ~3 weeks. Measured on 2026-09-09 against ~8 weeks, the
# P50 head was still improving at a 4000-round cap, and the fixed 400 cost 6.2%
# of held-out median |err| (10.640 -> 9.979 on an inner validation slice).
#
# The sweep's other result is why capacity is left alone: across 63/127/255/511
# leaves and min_child_samples 50/100, every early-stopped config landed between
# 9.928 and 9.995 - a 0.7% spread, i.e. noise - while the shipped config sat 6%
# behind all of them. The bottleneck was never model capacity, it was the round
# cap. Raising the constant would just reset the same trap for whoever reads this
# at 16 weeks of history, so the cap is now large and early stopping picks the
# round count from the data each retrain.
MAX_BOOST_ROUND = 8000
EARLY_STOPPING_ROUNDS = 100
# Fraction of `train` held out (by voyage arrival time) to early-stop on. It is
# carved from `train`, never from `calib` or `test`: `calib` must stay clean for
# the conformal band and `test` for the promotion gate.
_EARLY_STOP_FRAC = 0.20
# Below this many rows the stopping slice is too small for the signal to mean
# anything - early stopping would be fitting noise in the callback rather than in
# the model - so a small fixed budget is used instead. This is also what keeps the
# unit tests fast: they train on a few thousand synthetic rows, where searching up
# to MAX_BOOST_ROUND would cost minutes and prove nothing.
_MIN_ROWS_FOR_EARLY_STOP = 50_000
_FALLBACK_BOOST_ROUND = 400

# Promotion band for interval coverage (roadmap): a challenger cell is only
# promoted if its realised P10-P90 coverage stays honest.
_COVERAGE_BAND = (0.75, 0.85)

# Which conformal offsets the production refit serves: "insample" (recalibrated
# on `calib`, which the refit trained on) or "heldout" (the evaluation model's,
# calibrated on data it never saw). Every run reports the realised per-cell test
# coverage of both, so this choice is re-checkable from any retrain log.
#
# Was "insample" until 2026-09-27. Measured that day on the test window (a
# hold-out for the refit too), in-sample offsets under-covered in all 20
# (target, cell) pairs - e.g. 0.718 on port|24-48h logratio, outside the gate's
# [0.75, 0.85] band, i.e. the served band failed the check the promotion had
# passed on. Held-out offsets were nearer 0.80 in all 20. The 2026-09-08
# measurement that kept "insample" (0.801 vs 0.822) was a single pooled number,
# which the port majority dominated.
PROD_OFFSETS = "heldout"

# Artifact locations. Models + champion map live outside the (gitignored,
# hourly-rebuilt) analytics DB so the hourly serving build only ever *reads*
# them; they are refreshed by the deliberate training run / weekly retrain.
MODEL_DIR = Path(__file__).resolve().parent / "models"
CHAMPION_MAP_PATH = MODEL_DIR / "eta_champion_map.json"


def _with_physics(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` carrying the ``phys_p50`` column, computing it if absent."""
    if PHYS_FEATURE in df.columns:
        return df
    return df.assign(**{PHYS_FEATURE: vectorized_physics_p50(df)})


def _prepare(df: pd.DataFrame, target: str = "raw") -> pd.DataFrame:
    """Return the feature frame for ``target``, with categoricals typed for LightGBM.

    LightGBM consumes pandas ``category`` dtype natively (handles unseen levels
    and NaN). Numeric NaNs are left as-is - the tree learner splits on missing.
    """
    features = _TARGET_FEATURES[target]
    if PHYS_FEATURE in features:
        df = _with_physics(df)
    out = df[features].copy()
    # Coerce numeric features to float: a serving obs frame can carry object dtype
    # when a column holds Nones (e.g. an unknown trailing speed or draught), which
    # LightGBM rejects. to_numeric turns those into NaN, which the tree splits on.
    for col in (*NUMERIC_FEATURES, *([PHYS_FEATURE] if PHYS_FEATURE in features else [])):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].astype("category")
    return out


def _physics_base(df: pd.DataFrame) -> np.ndarray:
    """Physics P50 per row, NaN where it is missing or non-positive."""
    phys = _with_physics(df)[PHYS_FEATURE].to_numpy(dtype=float)
    return np.where(np.isfinite(phys) & (phys > 0), phys, np.nan)


def _physics_buckets(df: pd.DataFrame) -> np.ndarray:
    """Physics predicted-lead bucket per row: the key the gate and serving route on.

    A row with no physics estimate lands in the last bucket, the same convention
    as ``serving_choice``.
    """
    phys = _physics_base(df)
    return lead_buckets(np.where(np.isfinite(phys), phys, np.inf))


def _label(df: pd.DataFrame, target: str) -> np.ndarray:
    """Training label for ``target``; NaN where it is undefined."""
    y = df[LABEL].to_numpy(dtype=float)
    if target == "raw":
        return y
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(y) - np.log(_physics_base(df))


def _trainable(df: pd.DataFrame, target: str) -> pd.DataFrame:
    """Rows whose ``target`` label is finite. Only ``logratio`` ever drops any:
    a row with no physics estimate has nothing to be a ratio of."""
    if target == "raw" or df.empty:
        return df
    return df[np.isfinite(_label(df, target))]


def _to_hours(raw: np.ndarray, df: pd.DataFrame, target: str) -> np.ndarray:
    """Map head outputs (n, k) in ``target`` space back to hours."""
    if target == "raw":
        return raw
    return _physics_base(df)[:, None] * np.exp(raw)


# ---------------------------------------------------------------------------
# Time-based, voyage-grouped split
# ---------------------------------------------------------------------------


def time_voyage_split(
    samples: pd.DataFrame,
    fracs: tuple[float, float, float] = (0.60, 0.15, 0.25),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split samples into (train, calib, test) by voyage arrival time.

    Voyages are ordered by their arrival timestamp and partitioned so the *test*
    set is strictly later than *calib*, which is later than *train* - a genuine
    walk-forward. Grouping is by ``voyage_id`` so no voyage's observations span a
    boundary (the one leakage an interviewer checks first). ``calib`` is the
    conformal calibration slice for the interval.
    """
    if samples.empty:
        empty = samples.iloc[0:0]
        return empty, empty, empty
    order = samples.groupby("voyage_id")["arrival_ts"].min().sort_values().index.to_numpy()
    n = len(order)
    i_train = int(round(n * fracs[0]))
    i_calib = int(round(n * (fracs[0] + fracs[1])))
    train_ids = set(order[:i_train].tolist())
    calib_ids = set(order[i_train:i_calib].tolist())
    test_ids = set(order[i_calib:].tolist())
    vid = samples["voyage_id"]
    return (
        samples[vid.isin(train_ids)],
        samples[vid.isin(calib_ids)],
        samples[vid.isin(test_ids)],
    )


# ---------------------------------------------------------------------------
# Training + prediction
# ---------------------------------------------------------------------------


def _early_stop_split(train: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split `train` into (earlier fit, later stopping slice) by voyage arrival.

    Voyage-grouped and time-ordered for the same reason as `time_voyage_split`:
    a shuffled stopping slice would share voyages with the fit set and stop late
    on a leaked signal. Returns two empty frames when there is too little data to
    split (fewer than 2 voyages), so callers can fall back to a fixed budget.
    """
    if train.empty:
        return train, train
    order = train.groupby("voyage_id")["arrival_ts"].min().sort_values().index.to_numpy()
    if len(order) < 2:
        return train.iloc[0:0], train.iloc[0:0]
    cut = int(round(len(order) * (1.0 - _EARLY_STOP_FRAC)))
    cut = min(max(cut, 1), len(order) - 1)
    fit_ids = set(order[:cut].tolist())
    vid = train["voyage_id"]
    return train[vid.isin(fit_ids)], train[~vid.isin(fit_ids)]


def train_quantiles(
    train: pd.DataFrame,
    params: dict | None = None,
    target: str = "raw",
    rounds: dict[str, int] | None = None,
) -> dict:
    """Train the three quantile boosters for ``target``. Returns {'lo','mid','hi': Booster}.

    Each head is fitted twice, which is the standard way to spend a round budget
    without either hard-coding it or throwing away data:

    1. **Find the round count.** Fit on the earlier ``1 - _EARLY_STOP_FRAC`` of
       ``train`` (split by voyage arrival time, so the stopping signal is a
       genuine forward hold-out rather than a shuffle) and early-stop on the
       later slice against the head's own quantile loss.
    2. **Refit on all of ``train``** for exactly that many rounds.

    The round count is therefore re-derived from the data on every retrain rather
    than being a constant that silently goes stale as history grows - which is
    what the old ``NUM_BOOST_ROUND = 400`` did. Using step 1's iteration count
    unchanged on step 2's slightly larger set errs toward under-fitting, which is
    the safe direction: the promotion gate can only reject a weaker challenger,
    never serve one that was not measured.

    The split is carved out of ``train`` alone. ``calib`` stays untouched so the
    conformal band keeps whatever validity it has, and ``test`` stays untouched so
    the champion map is decided on a hold-out nothing has been tuned against.

    Passing ``rounds`` (head name -> round count, e.g. from ``fitted_rounds`` of
    an earlier fit) skips step 1 and fits once at those counts. ``run`` uses it
    for the production refit: it trains on train+calib, a superset of the
    evaluation model's data, so the evaluation model's early-stopped counts are
    the same slightly conservative choice step 2 already makes, at half the cost.

    Deterministic under the fixed seed in ``LGB_PARAMS`` (``num_threads`` affects
    speed, not the fit, for this objective).
    """
    import lightgbm as lgb

    p = {**LGB_PARAMS, **(params or {})}
    train = _trainable(train, target)
    X_all = _prepare(train, target)
    y_all = _label(train, target)

    inner_fit, inner_val = _early_stop_split(train)
    use_es = (
        rounds is None
        and len(train) >= _MIN_ROWS_FOR_EARLY_STOP
        and not inner_val.empty
        and not inner_fit.empty
    )
    if use_es:
        Xf, yf = _prepare(inner_fit, target), _label(inner_fit, target)
        Xv, yv = _prepare(inner_val, target), _label(inner_val, target)

    models: dict = {}
    for name, alpha in QUANTILES.items():
        head = {**p, "objective": "quantile", "alpha": alpha}
        if rounds is not None:
            n_rounds = int(rounds[name])
        elif use_es:
            # feature_pre_filter must be off: a Dataset built under one
            # min_data_in_leaf cannot be reused under a smaller one, and this
            # module's params are meant to stay tunable across runs.
            dfit = lgb.Dataset(
                Xf,
                label=yf,
                categorical_feature=CATEGORICAL_FEATURES,
                free_raw_data=False,
                params={"feature_pre_filter": False},
            )
            dval = lgb.Dataset(
                Xv,
                label=yv,
                reference=dfit,
                categorical_feature=CATEGORICAL_FEATURES,
                free_raw_data=False,
                # Must match the reference Dataset's params or LightGBM warns
                # that it is overriding them.
                params={"feature_pre_filter": False},
            )
            probe = lgb.train(
                head,
                dfit,
                num_boost_round=MAX_BOOST_ROUND,
                valid_sets=[dval],
                callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
            )
            n_rounds = int(probe.best_iteration) or MAX_BOOST_ROUND
            if n_rounds >= MAX_BOOST_ROUND:
                log.warning(
                    "eta_ml: %s/%s head hit the %d-round cap without early-stopping; "
                    "the model is still improving and MAX_BOOST_ROUND is now the binding "
                    "constraint - re-measure before trusting it as converged",
                    target,
                    name,
                    MAX_BOOST_ROUND,
                )
        else:
            # Too little data to hold out a meaningful stopping slice (unit tests,
            # a cold start). Fall back to a fixed modest budget.
            n_rounds = _FALLBACK_BOOST_ROUND
        dall = lgb.Dataset(
            X_all,
            label=y_all,
            categorical_feature=CATEGORICAL_FEATURES,
            free_raw_data=False,
            params={"feature_pre_filter": False},
        )
        models[name] = lgb.train(head, dall, num_boost_round=n_rounds)
        log.info("eta_ml: %s/%s head trained for %d rounds", target, name, n_rounds)
    return models


def fitted_rounds(models: dict) -> dict[str, int]:
    """Round count each head was fitted for, to reuse via ``train_quantiles(rounds=)``."""
    return {name: int(b.num_trees()) for name, b in models.items()}


def predict_quantiles(models: dict, df: pd.DataFrame, target: str = "raw") -> np.ndarray:
    """Predict monotone (P10, P50, P90) hours for each row. Shape (n, 3).

    Quantile heads are trained independently so they can *cross*; a per-row sort
    restores monotonicity (the standard, distribution-free fix), guaranteeing
    P10 <= P50 <= P90 for every served interval. The sort happens after the map
    back to hours, which is monotone, so it is the same sort either side of it.
    Rows the target cannot predict (``logratio`` with no physics) come back NaN.
    """
    X = _prepare(df, target)
    raw = np.vstack([models[q].predict(X) for q in _Q_ORDER]).T
    return np.sort(_to_hours(raw, df, target), axis=1)


def _cqr_level(n: int) -> float:
    """Finite-sample-adjusted conformal quantile level ceil((n+1)*cov)/n."""
    return min(1.0, np.ceil((n + 1) * TARGET_COVERAGE) / n)


_MIN_CQR_ROWS = 100


def _cqr_offset(scores: np.ndarray) -> float:
    """Non-negative finite-sample conformal offset for a set of conformity scores."""
    return max(0.0, float(np.quantile(scores, _cqr_level(scores.size), method="higher")))


def calibrate_cqr(models: dict, calib: pd.DataFrame, target: str = "raw") -> dict[str, float]:
    """Group-conditional split-conformal (CQR) half-width offsets for the P10-P90 band.

    Conformity score per calibration row is ``max(p10 - y, y - p90)`` - how far
    outside the raw interval the truth fell (negative when inside). The offset is
    the ``TARGET_COVERAGE`` empirical quantile of those scores; widening the band
    to ``[p10 - offset, p90 + offset]`` gives finite-sample coverage *within each
    group the quantile is taken over*.

    Groups are serve-time-known and nested, most specific first:
    ``"{target_type}|{bucket}"``, then ``bucket``, then ``"__global__"``, where
    ``bucket`` is the *physics* predicted-lead bucket - the same key the champion
    gate judges coverage on and serving routes on. A group with fewer than
    ``_MIN_CQR_ROWS`` calibration rows gets no key and falls back to the next
    level.

    The bucket used to be the model's *own* P50 bucket. That is also
    serve-time-known, but it is not the partition anything is judged on: a row the
    gate counts in ``chokepoint|48h+`` (by physics) is often in a shorter ML
    bucket, so it was calibrated against the wrong group. Measured 2026-09-27 with
    per-target_type offsets keyed that way, chokepoint|48h+ still realised 0.47
    coverage. Conformal guarantees coverage over exactly the groups it is
    calibrated on, so those have to be the gate's cells.

    The ``target_type`` level exists because pooling hid a miscalibration. With
    one offset per lead bucket, ports (~80% of rows) set every offset and
    chokepoints inherited it: measured 2026-09-27, chokepoint cells realised
    0.68-0.74 coverage against the [0.75, 0.85] gate while ports sat at
    0.77-0.80, so four chokepoint cells where ML beat physics on error were
    refused on coverage alone. Conformal only guarantees coverage marginally over
    the set it is calibrated on; conditioning on target_type (a "Mondrian"
    partition) makes the guarantee hold per group the gate judges.

    Offsets are clamped to be **non-negative: the band is only ever widened,
    never shrunk.** The clamp is a guard against a forward distribution shift
    shrinking the served band and making it overconfident out-of-time; worst case
    we keep the raw head width. With the P10/P90 heads (since 2026-09-08) it is
    non-binding in practice - measured offsets run from ~0.0h at 0-6h to ~1.4h at
    48h+, i.e. conformal widens an under-dispersed 80% band up to nominal. A run
    whose offsets are all exactly 0.0 means the clamp is binding again and the
    band is wider than TARGET_COVERAGE asks for.
    """
    calib = _trainable(calib, target)
    if calib.empty:
        return {"__global__": 0.0}
    q = predict_quantiles(models, calib, target)
    y = calib[LABEL].to_numpy(dtype=float)
    scores = np.maximum(q[:, 0] - y, y - q[:, 2])
    buckets = _physics_buckets(calib)
    ttypes = calib["target_type"].astype(str).to_numpy()
    out: dict[str, float] = {"__global__": _cqr_offset(scores)}
    for lead in _LEAD_LABELS:
        in_bucket = buckets == lead
        if in_bucket.sum() >= _MIN_CQR_ROWS:
            out[lead] = _cqr_offset(scores[in_bucket])
        for ttype in np.unique(ttypes):
            cell = in_bucket & (ttypes == ttype)
            if cell.sum() >= _MIN_CQR_ROWS:
                out[f"{ttype}|{lead}"] = _cqr_offset(scores[cell])
    return out


def _apply_cqr(
    q: np.ndarray, offsets: dict[str, float], target_types: np.ndarray, buckets: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Apply CQR offsets to raw quantiles. Returns (low, high) arrays.

    Each row takes the most specific calibrated offset available: its
    ``"{target_type}|{bucket}"`` group, else its bucket, else the global offset.
    ``buckets`` must be the physics buckets `calibrate_cqr` keyed on (see
    ``_physics_buckets``).
    """
    g = offsets.get("__global__", 0.0)
    per_row = [
        offsets.get(f"{t}|{b}", offsets.get(b, g))
        for t, b in zip(target_types, buckets, strict=True)
    ]
    off = np.clip(np.asarray(per_row, dtype=float), 0.0, None)
    low = np.maximum(0.0, q[:, 0] - off)
    high = q[:, 2] + off
    return low, high


def _apply_cqr_to(q: np.ndarray, offsets: dict[str, float], df: pd.DataFrame):
    """``_apply_cqr`` with the group keys taken from ``df``."""
    return _apply_cqr(q, offsets, df["target_type"].astype(str).to_numpy(), _physics_buckets(df))


@dataclass
class QuantileHeads:
    """One target's three quantile boosters plus its conformal offsets."""

    target: str
    models: dict
    cqr_offsets: dict[str, float]

    def quantiles(self, df: pd.DataFrame) -> np.ndarray:
        """Monotone (P10, P50, P90) hours with the conformal band applied. Shape (n,3)."""
        q = predict_quantiles(self.models, df, self.target)
        lo, hi = _apply_cqr_to(q, self.cqr_offsets, df)
        return np.vstack([lo, q[:, 1], hi]).T


def _artifact_prefix(target: str) -> str:
    # ``raw`` keeps the pre-2026-09-27 filenames so an older artifact directory
    # and a newer one never disagree about which file is the raw target.
    return "eta_lgbm" if target == "raw" else f"eta_lgbm_{target}"


class ETAModel:
    """Trained quantile heads per target + champion map.

    The champion map assigns each promoted ``"{target_type}|{lead_bucket}"`` cell
    (lead bucket of the *physics* P50) to the target that won it; unlisted cells
    stay physics. ``default_target`` is the target with the lower overall
    hold-out error, used for the ML scoreboard rows of cells nothing was
    promoted in.
    """

    def __init__(
        self,
        heads: dict[str, QuantileHeads],
        champion_map: dict[str, str],
        default_target: str = "raw",
    ) -> None:
        """Wrap the per-target heads and the cell -> target champion map."""
        self.heads = heads
        self.champion_map = champion_map
        self.default_target = default_target if default_target in heads else next(iter(heads), "")
        self.fitted = bool(heads) and all(h.models for h in heads.values())

    # -- serving -----------------------------------------------------------
    def target_for(self, target_type: str, lead_bucket: str) -> str | None:
        """The target promoted in this (target_type, lead) cell, or None for physics."""
        t = self.champion_map.get(f"{target_type}|{lead_bucket}")
        return t if t in self.heads else None

    def uses_ml(self, target_type: str, lead_bucket: str) -> bool:
        """True if the champion map routes this (target_type, lead) cell to ML."""
        return self.target_for(target_type, lead_bucket) is not None

    def routed_quantiles(self, df: pd.DataFrame, phys_p50: np.ndarray) -> np.ndarray:
        """(P10, P50, P90) per row from the target its physics cell is assigned to.

        Rows in cells with no promotion use ``default_target``. Each target is
        evaluated only on the rows routed to it. Shape (n, 3).
        """
        n = len(df)
        out = np.full((n, 3), np.nan)
        if n == 0 or not self.fitted:
            return out
        buckets = lead_buckets(np.where(np.isfinite(phys_p50), phys_p50, np.inf))
        ttypes = df["target_type"].astype(str).to_numpy()
        chosen = np.array(
            [
                self.target_for(t, b) or self.default_target
                for t, b in zip(ttypes, buckets, strict=True)
            ]
        )
        for target, heads in self.heads.items():
            rows = np.flatnonzero(chosen == target)
            if rows.size:
                out[rows] = heads.quantiles(df.iloc[rows])
        return out

    # -- persistence -------------------------------------------------------
    def save(self, model_dir: Path = MODEL_DIR) -> None:
        """Persist every target's boosters + meta (offsets, champion map) to disk."""
        model_dir.mkdir(parents=True, exist_ok=True)
        for target, heads in self.heads.items():
            for name, booster in heads.models.items():
                booster.save_model(str(model_dir / f"{_artifact_prefix(target)}_{name}.txt"))
        payload = {
            "targets": {
                t: {"cqr_offsets": h.cqr_offsets, "features": _TARGET_FEATURES[t]}
                for t, h in self.heads.items()
            },
            "champion_map": self.champion_map,
            "default_target": self.default_target,
            "trained_ts": datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(),
        }
        (model_dir / "eta_ml_meta.json").write_text(json.dumps(payload, indent=2))
        (model_dir / CHAMPION_MAP_PATH.name).write_text(json.dumps(self.champion_map, indent=2))
        log.info("saved ETA ML model + champion map to %s", model_dir)

    @classmethod
    def load(cls, model_dir: Path = MODEL_DIR) -> ETAModel | None:
        """Load a saved model, or None if artifacts are absent/incomplete.

        Reads both layouts: the current one (``targets`` in the meta) and the
        single-target one written before 2026-09-27, whose top-level
        ``cqr_offsets`` and ``"ml"`` champion values mean the ``raw`` target.
        """
        meta_path = model_dir / "eta_ml_meta.json"
        if not meta_path.exists():
            return None
        try:
            import lightgbm as lgb

            meta = json.loads(meta_path.read_text())
            if "targets" in meta:
                specs = {t: v["cqr_offsets"] for t, v in meta["targets"].items()}
            else:
                offsets = meta.get("cqr_offsets")
                if offsets is None:  # oldest layout: a single scalar offset
                    offsets = {"__global__": float(meta.get("cqr_offset", 0.0))}
                specs = {"raw": offsets}
            heads: dict[str, QuantileHeads] = {}
            for target, offsets in specs.items():
                if target not in _TARGET_FEATURES:
                    log.warning("ETA ML artifact names unknown target %r; ignoring it", target)
                    continue
                models = {}
                for name in QUANTILES:
                    f = model_dir / f"{_artifact_prefix(target)}_{name}.txt"
                    if not f.exists():
                        return None
                    models[name] = lgb.Booster(model_file=str(f))
                heads[target] = QuantileHeads(
                    target, models, {k: float(v) for k, v in offsets.items()}
                )
            champ = {k: ("raw" if v == _LEGACY_ML else v) for k, v in meta["champion_map"].items()}
            return cls(heads, champ, meta.get("default_target", "raw"))
        except Exception as exc:  # noqa: BLE001 - serving must never crash on a bad artifact
            log.warning("failed to load ETA ML model: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Serving blend
# ---------------------------------------------------------------------------


def serving_choice(
    model: ETAModel | None, obsdf: pd.DataFrame, phys_p50: np.ndarray
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    """Decide ml-vs-physics per serving row; return (use_ml, p50, low, high).

    The routing key is the *physics* predicted-lead bucket (physics is always
    computed and shared, so the decision is serve-time deterministic and matches
    how the champion map was built). ``use_ml[i]`` is True only where the map
    assigns an ML target to that (target_type, bucket) cell *and* both that
    target's and physics' estimates for the row are finite. ML arrays are None
    when no model is loaded.
    """
    n = len(obsdf)
    use_ml = np.zeros(n, dtype=bool)
    if model is None or not model.fitted or n == 0:
        return use_ml, None, None, None
    q = model.routed_quantiles(obsdf, phys_p50)  # (n, 3): low, p50, high
    ml_low, ml_p50, ml_high = q[:, 0], q[:, 1], q[:, 2]
    safe_phys = np.where(np.isfinite(phys_p50), phys_p50, np.inf)
    buckets = lead_buckets(safe_phys)
    ttypes = obsdf["target_type"].astype(str).to_numpy()
    finite = np.isfinite(ml_p50) & np.isfinite(phys_p50)
    for i in range(n):
        if finite[i] and model.uses_ml(ttypes[i], buckets[i]):
            use_ml[i] = True
    return use_ml, ml_p50, ml_low, ml_high


# ---------------------------------------------------------------------------
# Champion / challenger
# ---------------------------------------------------------------------------


def _score_frame(
    test: pd.DataFrame, pred_p50: np.ndarray, low: np.ndarray, high: np.ndarray
) -> pd.DataFrame:
    """Attach pred/err/coverage columns and drop non-finite predictions."""
    scored = test.copy()
    scored["pred_h"] = pred_p50
    scored["_lo"] = low
    scored["_hi"] = high
    scored = scored[np.isfinite(scored["pred_h"])].copy()
    if scored.empty:
        return scored
    scored["err_h"] = scored["pred_h"] - scored["remaining_h"]
    fin = np.isfinite(scored["_lo"]) & np.isfinite(scored["_hi"])
    scored["covered"] = np.where(
        fin,
        (
            (scored["remaining_h"] >= scored["_lo"]) & (scored["remaining_h"] <= scored["_hi"])
        ).astype(float),
        np.nan,
    )
    return scored


def build_champion_map(
    ml_scored: dict[str, pd.DataFrame], phys_scored: pd.DataFrame
) -> dict[str, str]:
    """Decide physics-vs-each-ML-target per (target_type, predicted-lead-bucket) cell.

    ``ml_scored`` maps target name -> that target's scored hold-out frame.
    Bucketing is by the *physics* predicted lead (serve-time-known and shared by
    every model, so the routing decision is deterministic at serve time). A target
    qualifies for a cell only if it lowers median |err| versus physics on the same
    rows AND its realised interval coverage there stays in ``_COVERAGE_BAND``;
    among qualifying targets the lowest median |err| wins, and the cell maps to
    that target's name. Everything else stays physics - the conservative default
    that keeps physics champion wherever ML has not earned promotion.

    Choosing among targets on the same hold-out the gate is judged on is a mild
    selection effect. It is small here because the cells are large (thousands to
    100k+ rows) and the 2026-09-27 differences between targets sat well outside
    their voyage-bootstrap CIs, and it cannot promote anything that has not also
    beaten physics.
    """
    champ: dict[str, str] = {}
    if phys_scored.empty:
        return champ
    phys_bucket = lead_buckets(phys_scored["pred_h"].to_numpy(dtype=float))
    phys_scored = phys_scored.assign(_pbucket=phys_bucket)
    # Align ML rows to the same physics bucket via the shared sample index.
    aligned = {
        t: m.assign(_pbucket=phys_scored["_pbucket"].reindex(m.index))
        for t, m in ml_scored.items()
        if not m.empty
    }

    for ttype in ("chokepoint", "port"):
        for lead in _LEAD_LABELS:
            key = f"{ttype}|{lead}"
            p = phys_scored[
                (phys_scored["target_type"] == ttype) & (phys_scored["_pbucket"] == lead)
            ]
            if len(p) < 200:
                continue  # too thin to judge; leave physics
            best_err = float(np.median(np.abs(p["err_h"].to_numpy())))
            for target, ms in aligned.items():
                m = ms[(ms["target_type"] == ttype) & (ms["_pbucket"] == lead)]
                if len(m) < 200:
                    continue
                ml_err = float(np.median(np.abs(m["err_h"].to_numpy())))
                cov = m["covered"].dropna()
                ml_cov = float(cov.mean()) if not cov.empty else float("nan")
                if ml_err < best_err and _COVERAGE_BAND[0] <= ml_cov <= _COVERAGE_BAND[1]:
                    champ[key], best_err = target, ml_err
    return champ


def demote_uncovered(
    champ: dict[str, str], served_coverage: dict[str, dict[str, float]]
) -> tuple[dict[str, str], dict[str, float]]:
    """Drop promoted cells whose *served* interval misses ``_COVERAGE_BAND``.

    ``build_champion_map`` judges the evaluation model, but serving uses the
    refit on train+calib, whose band can land elsewhere. ``served_coverage`` is
    target -> cell -> realised coverage of the served model on the hold-out.
    Returns (kept map, demoted cell -> its served coverage). A cell with no
    served measurement is demoted: unmeasured is not in band.

    Why it exists: on 2026-09-27 chokepoint|48h+ passed the gate at 0.759 on the
    evaluation model while the refit it would have served realised 0.659 there.
    """
    kept: dict[str, str] = {}
    demoted: dict[str, float] = {}
    lo, hi = _COVERAGE_BAND
    for cell, target in champ.items():
        cov = served_coverage.get(target, {}).get(cell, float("nan"))
        if lo <= cov <= hi:
            kept[cell] = target
        else:
            demoted[cell] = cov
            log.warning(
                "eta_ml: demoting %s (%s): served coverage %.3f is outside %s",
                cell,
                target,
                cov,
                _COVERAGE_BAND,
            )
    return kept, demoted


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def score_and_write_ml(
    conn: duckdb.DuckDBPyConnection, samples: pd.DataFrame, run_ts: datetime
) -> pd.DataFrame:
    """Score the promoted ML champion and persist model='ml' scoreboard rows.

    Called by the hourly build (`eta_samples.score_baselines`) with the same
    ``run_ts`` as the naive/route/physics baselines so the public scoreboard
    surfaces all models from one run. ML is scored on its *own* leakage-free
    time-based hold-out (the latest voyages by arrival), not the baselines' random
    split: the frozen model trained only on voyages strictly earlier than any in
    this test window, so no test voyage was ever seen in training. (Physics is
    deterministic and split-invariant, so scoring it on the random split remains a
    fair comparator.) No-op (empty frame) when no artifact is present.
    """
    required = {"voyage_id", "arrival_ts", "remaining_h", *FEATURES}
    if samples.empty or not required.issubset(samples.columns):
        return pd.DataFrame()
    model = ETAModel.load()
    if model is None or not model.fitted:
        return pd.DataFrame()
    _, _, test = time_voyage_split(samples)
    if test.empty:
        return pd.DataFrame()
    # The same routing serving applies: each row takes the target its physics
    # cell was promoted to, else the model's default target.
    q = model.routed_quantiles(test, vectorized_physics_p50(test))
    scored = _score_frame(test, q[:, 1], q[:, 0], q[:, 2])
    if scored.empty:
        return pd.DataFrame()
    metrics = pd.DataFrame(_metric_rows(scored, "ml", run_ts))
    if not metrics.empty:
        write_metrics(conn, metrics)
        by_tgt = _metric_rows_by_target(scored, "ml", run_ts)
        if by_tgt:
            write_metrics_by_target(conn, by_tgt)
    return metrics


def _load_samples(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load underway, positive-lead training samples from eta_samples."""
    try:
        df = conn.execute(
            "SELECT voyage_id, mmsi, target_id, target_type, arrival_ts, obs_ts, "
            "       remaining_h, route_dist_nm, gc_dist_nm, sog, sog_trail6h, "
            "       service_speed, segment, laden, draught, is_canal, dest_queue_h, "
            "       approach_bearing "
            "FROM eta_samples WHERE sog >= ? AND remaining_h > 0",
            [_MIN_SOG_KN],
        ).df()
    except duckdb.CatalogException:
        return pd.DataFrame()
    return df


def _routed_columns(
    model: ETAModel, df: pd.DataFrame, phys_p50: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(p50, low, high) columns for ``_score_frame`` from the model's routing."""
    q = model.routed_quantiles(df, phys_p50)
    return q[:, 1], q[:, 0], q[:, 2]


def _cells(phys_scored: pd.DataFrame) -> pd.Series:
    """``"{target_type}|{physics lead bucket}"`` per row of a scored frame."""
    buckets = lead_buckets(phys_scored["pred_h"].to_numpy(dtype=float))
    return (
        phys_scored["target_type"].astype(str) + "|" + pd.Series(buckets, index=phys_scored.index)
    )


def _coverage_by_cell(scored: pd.DataFrame, phys_scored: pd.DataFrame) -> dict[str, float]:
    """Realised interval coverage per gate cell (diagnostic)."""
    cells = _cells(phys_scored).reindex(scored.index)
    return {k: round(float(v), 3) for k, v in scored["covered"].groupby(cells).mean().items()}


def _target_cell_table(
    scored_by_target: dict[str, pd.DataFrame], phys_scored: pd.DataFrame
) -> pd.DataFrame:
    """Per gate cell: n, physics |err|, and each target's |err| and coverage."""
    cells = _cells(phys_scored)
    out = pd.DataFrame(
        {
            "n": cells.value_counts(),
            "physics": phys_scored["err_h"].abs().groupby(cells).median(),
        }
    )
    for t, sc in scored_by_target.items():
        c = cells.reindex(sc.index)
        out[f"{t}_err"] = sc["err_h"].abs().groupby(c).median()
        out[f"{t}_cov"] = sc["covered"].groupby(c).mean()
    return out.sort_index()


def train_and_evaluate(
    conn: duckdb.DuckDBPyConnection,
    persist: bool = True,
    run_ts: datetime | None = None,
) -> dict:
    """Train, walk-forward-evaluate, gate-promote, and (optionally) save.

    Returns a report dict: per-bucket ML vs physics table, the champion map, the
    fitted production ``ETAModel`` (refit on train+calib), and coverage. Writes
    ``model='ml'`` rows to ``eta_model_metrics`` when ``persist`` is set (so the
    scoreboard carries the challenger next to naive/route/physics) and saves the
    model artifact + champion map for serving.
    """
    run_ts = run_ts or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    samples = _load_samples(conn)
    report: dict = {"n_samples": int(len(samples)), "n_voyages": 0, "champion_map": {}}
    if samples.empty:
        log.warning("eta_ml: no samples; skipping")
        return report
    report["n_voyages"] = int(samples["voyage_id"].nunique())

    train, calib, test = time_voyage_split(samples)
    if train.empty or test.empty:
        log.warning("eta_ml: insufficient voyages for a walk-forward split")
        return report

    # --- evaluation models: train on the earliest voyages only, so `test` is a
    # genuine future hold-out; calibrate each interval on the middle slice. ---
    phys_p50 = vectorized_physics_p50(test)
    phys_scored = _score_frame(
        test, phys_p50, np.full(len(test), np.nan), np.full(len(test), np.nan)
    )
    eval_heads: dict[str, QuantileHeads] = {}
    scored_by_target: dict[str, pd.DataFrame] = {}
    for target in TARGETS:
        models = train_quantiles(train, target=target)
        heads = QuantileHeads(
            target, models, calibrate_cqr(models, calib if not calib.empty else train, target)
        )
        eval_heads[target] = heads
        q = predict_quantiles(models, test, target)
        low, high = _apply_cqr_to(q, heads.cqr_offsets, test)
        scored_by_target[target] = _score_frame(test, q[:, 1], low, high)

    champ = build_champion_map(scored_by_target, phys_scored)
    report["cqr_offsets"] = {t: h.cqr_offsets for t, h in eval_heads.items()}
    default_target = min(
        TARGETS,
        key=lambda t: (
            float(np.median(np.abs(scored_by_target[t]["err_h"])))
            if not scored_by_target[t].empty
            else np.inf
        ),
    )
    report["default_target"] = default_target
    report["target_cells"] = _target_cell_table(scored_by_target, phys_scored)

    # --- production models: refit on train+calib, so serving uses all the data
    # the gate was decided on; the champion map, decided on the honest hold-out,
    # is carried onto them. Each refit reuses its evaluation fit's early-stopped
    # round counts (see `train_quantiles`), which is what pays for training a
    # second target.
    #
    # Conformal offsets: the refit's own offsets are in-sample, because `calib`
    # is inside `prod_train`, and split-conformal's guarantee needs a held-out
    # calibration set. So the refit serves the evaluation model's held-out
    # offsets (PROD_OFFSETS, with the measurement that decided it). Both choices
    # are still scored on `test`, a hold-out for the refit too, and reported. ---
    prod_train = pd.concat([train, calib], ignore_index=True) if not calib.empty else train
    prod_heads: dict[str, QuantileHeads] = {}
    report["prod_offset_coverage"] = {}
    for target in TARGETS:
        models = train_quantiles(
            prod_train, target=target, rounds=fitted_rounds(eval_heads[target].models)
        )
        insample = calibrate_cqr(models, calib if not calib.empty else train, target)
        q = predict_quantiles(models, test, target)
        cov_by_choice = {}
        for choice, offsets in (
            ("insample", insample),
            ("heldout", eval_heads[target].cqr_offsets),
        ):
            low, high = _apply_cqr_to(q, offsets, test)
            cov_by_choice[choice] = _score_frame(test, q[:, 1], low, high)
        report["prod_offset_coverage"][target] = {
            c: _coverage_by_cell(f, phys_scored) for c, f in cov_by_choice.items()
        }
        prod_heads[target] = QuantileHeads(
            target,
            models,
            eval_heads[target].cqr_offsets if PROD_OFFSETS == "heldout" else insample,
        )
    # The gate judged the evaluation model; serving uses the refit. Both are
    # scored on the same hold-out, so hold the served band to the same check.
    served_cov = {t: report["prod_offset_coverage"][t][PROD_OFFSETS] for t in TARGETS}
    champ, report["demoted"] = demote_uncovered(champ, served_cov)
    report["champion_map"] = champ

    model = ETAModel(prod_heads, champ, default_target)
    report["model"] = model

    # The "ml" scoreboard rows score what is served - the refit, routed each row
    # to the target its physics cell was promoted to, the default elsewhere -
    # exactly as the hourly `score_and_write_ml` does from the saved artifact.
    ml_scored = _score_frame(test, *_routed_columns(model, test, phys_p50))
    cov = ml_scored["covered"].dropna()
    report["ml_coverage"] = float(cov.mean()) if not cov.empty else float("nan")
    ml_metrics = pd.DataFrame(_metric_rows(ml_scored, "ml", run_ts))
    report["ml_metrics"] = ml_metrics
    report["phys_metrics"] = pd.DataFrame(_metric_rows(phys_scored, "physics_v1", run_ts))
    report["ml_metrics_by_target"] = _metric_rows_by_target(ml_scored, "ml", run_ts)

    if persist:
        persist_run(conn, report)

    return report


def persist_run(conn: duckdb.DuckDBPyConnection, report: dict) -> None:
    """Write the scoreboard rows and (if ML won a cell) the model artifacts.

    Split out of ``train_and_evaluate`` so the caller can hold an exclusive
    connection for only as long as these writes take, instead of for the whole
    training run - see ``run`` for why that matters.
    """
    ml_metrics = report.get("ml_metrics")
    if ml_metrics is not None and not ml_metrics.empty:
        write_metrics(conn, ml_metrics)
        by_tgt = report.get("ml_metrics_by_target")
        if by_tgt:
            write_metrics_by_target(conn, by_tgt)

    champ, model = report.get("champion_map"), report.get("model")
    if champ and model is not None:  # only save artifacts if ML actually won somewhere
        model.save()
    else:
        log.info("eta_ml: challenger won no cells; not promoting (physics stays champion)")


def _print_report(report: dict) -> None:
    print(f"\nsamples={report['n_samples']}  voyages={report['n_voyages']}")
    for target, offsets in report.get("cqr_offsets", {}).items():
        offs = ", ".join(f"{k}={v:+.1f}" for k, v in offsets.items())
        print(f"CQR offsets [{target}]: {offs}")
    if "ml_coverage" in report:
        print(
            f"routed ML P10-P90 coverage={report['ml_coverage']:.3f}  "
            f"default target={report.get('default_target')}"
        )
    ml, ph = report.get("ml_metrics"), report.get("phys_metrics")
    if ml is not None and not ml.empty and ph is not None:
        m = ml[(ml["lead_basis"] == "actual") & (ml["target_type"] == "all")].set_index(
            "lead_bucket"
        )
        p = ph[(ph["lead_basis"] == "actual") & (ph["target_type"] == "all")].set_index(
            "lead_bucket"
        )
        print("\nby actual lead (target_type=all)   physics -> routed ML")
        for lead in _LEAD_LABELS:
            if lead in m.index and lead in p.index:
                print(
                    f"  {lead:8s}  |err| {p.loc[lead, 'med_abs_err_h']:6.2f} -> {m.loc[lead, 'med_abs_err_h']:6.2f}   "
                    f"bias {p.loc[lead, 'bias_h']:+7.2f} -> {m.loc[lead, 'bias_h']:+7.2f}"
                )
    cells = report.get("target_cells")
    if cells is not None and not cells.empty:
        print("\ngate cells (physics lead bucket): median |err| h, coverage")
        with pd.option_context("display.width", 160, "display.float_format", "{:.3f}".format):
            print(cells.to_string())
    for target, by_choice in report.get("prod_offset_coverage", {}).items():
        print(
            f"\nproduction refit [{target}] test coverage by offset choice (serving: {PROD_OFFSETS})"
        )
        print(pd.DataFrame(by_choice).to_string())
    for cell, cov in report.get("demoted", {}).items():
        print(f"\ndemoted {cell}: served coverage {cov:.3f} outside {_COVERAGE_BAND}")
    champ = report.get("champion_map", {})
    print(
        f"\npromoted ML cells ({len(champ)}): "
        + (", ".join(f"{k}={v}" for k, v in sorted(champ.items())) if champ else "(none)")
    )


def run(dry_run: bool = False) -> dict:
    """Standalone entry: train + walk-forward + (gated) promote against the live DB.

    Two phases, deliberately. Training used to run against a read-WRITE connection
    held for its whole duration, which takes DuckDB's exclusive lock on the live
    analytics DB for minutes. That is not a loud failure: ``app.db.query`` opens a
    fresh read-only connection per request and retries a locked file for only
    ~9s before returning an **empty DataFrame**, so every analytics endpoint would
    serve HTTP 200 with no rows for the length of a retrain. Measured on
    2026-09-09: with a training run holding the lock,
    ``GET /api/analytics/eta-accuracy`` returned ``{"run_ts": null, "rows": []}``
    and the scoreboard rendered blank - nothing that UptimeRobot or Sentry can see.

    So phase 1 loads and trains through a READ-ONLY connection (DuckDB allows
    concurrent readers, so the API keeps serving throughout), and phase 2 opens
    the exclusive connection only for the metric writes, which take well under a
    second. The model artifacts are plain files and need no DB lock at all.
    """
    conn = duckdb.connect(str(ANALYTICS_DB), read_only=True)
    try:
        report = train_and_evaluate(conn, persist=False)
    finally:
        conn.close()

    if not dry_run:
        wconn = duckdb.connect(str(ANALYTICS_DB))
        try:
            persist_run(wconn, report)
        finally:
            wconn.close()

    _print_report(report)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Train + walk-forward the ETA ML challenger")
    ap.add_argument(
        "--dry-run", action="store_true", help="evaluate + print, never write artifacts/metrics"
    )
    args = ap.parse_args()
    run(dry_run=args.dry_run)
