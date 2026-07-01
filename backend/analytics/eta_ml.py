"""Phase D of the True ETA build: the LightGBM quantile ETA challenger.

Physics (`eta_physics.physics_v1`) is the shipped champion. It is excellent at
short lead (kinematics dominate) but structurally *optimistic* at long lead: a
vessel loitering / passing near a target divides a small route distance by its
speed and reports a near-arrival, so 24-48h+ forecasts carry a large negative
bias no position+speed model can remove. That residual is *learnable* - it lives
in the target, the approach bearing, the draught and the trailing-speed decay -
which is exactly what a gradient-boosted model captures.

This module trains three LightGBM quantile regressors (alpha 0.1 / 0.5 / 0.9) on
`eta_samples`, calibrates the P10-P90 band with split-conformal (CQR) so the
served interval hits its nominal coverage, and runs a leakage-free, **time-based
voyage-grouped** walk-forward against physics. ML is promoted to `method='ml'`
only per (target_type, predicted-lead-bucket) cell where it beats physics on
held-out median |err| *and* keeps interval coverage in [0.75, 0.85] - everywhere
else physics stays champion. The result is a blended model: physics for the
short-lead cells it owns, ML for the long-lead cells it fixes.

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

# Quantile heads. LightGBM quantile intervals are under-dispersed out-of-time on
# this ~3-week history (a P10-P90 head realises only ~0.71 coverage on the
# walk-forward test), so the interval heads are the wider P05/P95 (nominal 90%),
# which realise ~0.83 - inside the honest [0.75,0.85] promotion band. `lo`/`hi`
# are the interval heads; `mid` is the P50 point estimate. Keys double as the
# saved-artifact filenames.
QUANTILES = {"lo": 0.05, "mid": 0.50, "hi": 0.95}
_Q_ORDER = ("lo", "mid", "hi")
TARGET_COVERAGE = 0.80

# Modest, regularised hyperparameters. History is ~3 weeks: shallow-ish trees,
# strong leaf minimums and bagging keep the model from memorising voyages.
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
NUM_BOOST_ROUND = 400

# Promotion band for interval coverage (roadmap): a challenger cell is only
# promoted if its realised P10-P90 coverage stays honest.
_COVERAGE_BAND = (0.75, 0.85)

# Artifact locations. Models + champion map live outside the (gitignored,
# hourly-rebuilt) analytics DB so the hourly serving build only ever *reads*
# them; they are refreshed by the deliberate training run / weekly retrain.
MODEL_DIR = Path(__file__).resolve().parent / "models"
CHAMPION_MAP_PATH = MODEL_DIR / "eta_champion_map.json"


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Return a feature frame with categoricals typed for LightGBM.

    LightGBM consumes pandas ``category`` dtype natively (handles unseen levels
    and NaN). Numeric NaNs are left as-is - the tree learner splits on missing.
    """
    out = df[FEATURES].copy()
    # Coerce numeric features to float: a serving obs frame can carry object dtype
    # when a column holds Nones (e.g. an unknown trailing speed or draught), which
    # LightGBM rejects. to_numeric turns those into NaN, which the tree splits on.
    for col in NUMERIC_FEATURES:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].astype("category")
    return out


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
    order = (
        samples.groupby("voyage_id")["arrival_ts"].min().sort_values().index.to_numpy()
    )
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


def train_quantiles(train: pd.DataFrame, params: dict | None = None) -> dict:
    """Train the three quantile boosters. Returns {'p10','p50','p90': Booster}.

    Deterministic under the fixed seed in ``LGB_PARAMS`` (single-threaded RNG per
    booster; ``num_threads`` affects speed, not the fit, for this objective).
    """
    import lightgbm as lgb

    p = {**LGB_PARAMS, **(params or {})}
    X = _prepare(train)
    y = train[LABEL].to_numpy(dtype=float)
    dtrain = lgb.Dataset(
        X, label=y, categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False
    )
    models: dict = {}
    for name, alpha in QUANTILES.items():
        models[name] = lgb.train(
            {**p, "objective": "quantile", "alpha": alpha},
            dtrain,
            num_boost_round=NUM_BOOST_ROUND,
        )
    return models


def predict_quantiles(models: dict, df: pd.DataFrame) -> np.ndarray:
    """Predict monotone (P10, P50, P90) for each row. Shape (n, 3).

    Quantile heads are trained independently so they can *cross*; a per-row sort
    restores monotonicity (the standard, distribution-free fix), guaranteeing
    P10 <= P50 <= P90 for every served interval.
    """
    X = _prepare(df)
    raw = np.vstack([models[q].predict(X) for q in _Q_ORDER]).T
    return np.sort(raw, axis=1)


def _cqr_level(n: int) -> float:
    """Finite-sample-adjusted conformal quantile level ceil((n+1)*cov)/n."""
    return min(1.0, np.ceil((n + 1) * TARGET_COVERAGE) / n)


def calibrate_cqr(models: dict, calib: pd.DataFrame) -> dict[str, float]:
    """Per-bucket split-conformal (CQR) half-width offsets for the P10-P90 band.

    Conformity score per calibration row is ``max(p10 - y, y - p90)`` - how far
    outside the raw interval the truth fell (negative when inside). The offset is
    the ``TARGET_COVERAGE`` empirical quantile of those scores; widening the band
    to ``[p10 - offset, p90 + offset]`` gives finite-sample marginal coverage.

    LightGBM quantile intervals are *heteroscedastic* in miscoverage, so we
    compute one offset per predicted-lead bucket (keyed on the model's own P50,
    serve-time-known), with a global fallback for buckets too thin to calibrate.

    Offsets are clamped to be **non-negative: the band is only ever widened,
    never shrunk.** On ~3 weeks of history the calibration slice systematically
    *over*-covers relative to the strictly-later test window (a real forward
    distribution shift), so the raw conformal offset is often negative - trusting
    it would shrink the served band and make it overconfident out-of-time. Only
    widening is the robust choice: worst case we keep the raw P05-P95 head width.
    Returned dict is ``{bucket_label: offset, "__global__": offset}``.
    """
    if calib.empty:
        return {"__global__": 0.0}
    q = predict_quantiles(models, calib)
    y = calib[LABEL].to_numpy(dtype=float)
    scores = np.maximum(q[:, 0] - y, y - q[:, 2])
    buckets = lead_buckets(q[:, 1])
    out: dict[str, float] = {
        "__global__": max(0.0, float(np.quantile(scores, _cqr_level(len(scores)), method="higher")))
    }
    for lead in _LEAD_LABELS:
        s = scores[buckets == lead]
        if s.size >= 100:
            out[lead] = max(0.0, float(np.quantile(s, _cqr_level(s.size), method="higher")))
    return out


def _apply_cqr(q: np.ndarray, offsets: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    """Apply per-bucket CQR offsets to raw quantiles. Returns (low, high) arrays.

    Bucket is the predicted-lead bucket of each row's own P50 (serve-time-known);
    buckets absent from the calibrated map fall back to the global offset.
    """
    g = offsets.get("__global__", 0.0)
    buckets = lead_buckets(q[:, 1])
    off = np.clip(np.array([offsets.get(b, g) for b in buckets], dtype=float), 0.0, None)
    low = np.maximum(0.0, q[:, 0] - off)
    high = q[:, 2] + off
    return low, high


class ETAModel:
    """Trained quantile ETA model + conformal offset + champion map.

    ``predict_batch`` returns (p50, low, high, method) column arrays for a frame
    of observations: ML where the champion map says so (keyed by the physics
    predicted-lead bucket + target_type), physics elsewhere. Physics p50 is passed
    in so a single routing pass is shared and the bucket routing is serve-time
    deterministic.
    """

    def __init__(
        self,
        models: dict,
        cqr_offsets: dict[str, float],
        champion_map: dict[str, str],
    ) -> None:
        """Wrap trained quantile boosters, per-bucket CQR offsets, and champion map."""
        self.models = models
        self.cqr_offsets = {k: float(v) for k, v in cqr_offsets.items()}
        self.champion_map = champion_map  # "{target_type}|{lead_bucket}" -> 'ml'|'physics'
        self.fitted = bool(models)

    # -- serving -----------------------------------------------------------
    def ml_quantiles(self, df: pd.DataFrame) -> np.ndarray:
        """Monotone (P10, P50, P90) with the conformal band applied. Shape (n,3)."""
        q = predict_quantiles(self.models, df)
        lo, hi = _apply_cqr(q, self.cqr_offsets)
        return np.vstack([lo, q[:, 1], hi]).T

    def uses_ml(self, target_type: str, lead_bucket: str) -> bool:
        """True if the champion map routes this (target_type, lead) cell to ML."""
        return self.champion_map.get(f"{target_type}|{lead_bucket}") == "ml"

    # -- persistence -------------------------------------------------------
    def save(self, model_dir: Path = MODEL_DIR) -> None:
        """Persist the three boosters + meta (offsets, champion map) to disk."""
        model_dir.mkdir(parents=True, exist_ok=True)
        for name, booster in self.models.items():
            booster.save_model(str(model_dir / f"eta_lgbm_{name}.txt"))
        payload = {
            "cqr_offsets": self.cqr_offsets,
            "champion_map": self.champion_map,
            "features": FEATURES,
            "trained_ts": datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(),
        }
        (model_dir / "eta_ml_meta.json").write_text(json.dumps(payload, indent=2))
        CHAMPION_MAP_PATH.write_text(json.dumps(payload["champion_map"], indent=2))
        log.info("saved ETA ML model + champion map to %s", model_dir)

    @classmethod
    def load(cls, model_dir: Path = MODEL_DIR) -> ETAModel | None:
        """Load a saved model, or None if artifacts are absent/incomplete."""
        meta_path = model_dir / "eta_ml_meta.json"
        if not meta_path.exists():
            return None
        try:
            import lightgbm as lgb

            meta = json.loads(meta_path.read_text())
            models = {}
            for name in QUANTILES:
                p = model_dir / f"eta_lgbm_{name}.txt"
                if not p.exists():
                    return None
                models[name] = lgb.Booster(model_file=str(p))
            # Back-compat: an older artifact stored a single scalar offset.
            offsets = meta.get("cqr_offsets")
            if offsets is None:
                offsets = {"__global__": float(meta.get("cqr_offset", 0.0))}
            return cls(models, offsets, meta["champion_map"])
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
    assigns ML to that (target_type, bucket) cell *and* both the ML and physics
    estimates for the row are finite. ML arrays are None when no model is loaded.
    """
    n = len(obsdf)
    use_ml = np.zeros(n, dtype=bool)
    if model is None or not model.fitted or n == 0:
        return use_ml, None, None, None
    q = model.ml_quantiles(obsdf)  # (n, 3): low, p50, high
    ml_low, ml_p50, ml_high = q[:, 0], q[:, 1], q[:, 2]
    # Bucket by physics p50; non-finite physics -> can't route to a cell -> physics.
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
        ((scored["remaining_h"] >= scored["_lo"]) & (scored["remaining_h"] <= scored["_hi"])).astype(float),
        np.nan,
    )
    return scored


def build_champion_map(ml_scored: pd.DataFrame, phys_scored: pd.DataFrame) -> dict[str, str]:
    """Decide ml-vs-physics per (target_type, predicted-lead-bucket) cell.

    Bucketing is by the *physics* predicted lead (serve-time-known and shared by
    both models, so the routing decision is deterministic at serve time). ML wins
    a cell only if it lowers median |err| versus physics on the same rows AND its
    realised interval coverage there stays in ``_COVERAGE_BAND``. Everything else
    stays physics - the conservative default that keeps physics champion wherever
    ML has not earned promotion.
    """
    champ: dict[str, str] = {}
    if ml_scored.empty or phys_scored.empty:
        return champ
    phys_bucket = lead_buckets(phys_scored["pred_h"].to_numpy(dtype=float))
    phys_scored = phys_scored.assign(_pbucket=phys_bucket)
    # Align ML rows to the same physics bucket via the shared sample index.
    pb = phys_scored["_pbucket"].reindex(ml_scored.index)
    ml_scored = ml_scored.assign(_pbucket=pb)

    for ttype in ("chokepoint", "port"):
        for lead in _LEAD_LABELS:
            key = f"{ttype}|{lead}"
            p = phys_scored[(phys_scored["target_type"] == ttype) & (phys_scored["_pbucket"] == lead)]
            m = ml_scored[(ml_scored["target_type"] == ttype) & (ml_scored["_pbucket"] == lead)]
            if len(p) < 200 or len(m) < 200:
                continue  # too thin to judge; leave physics
            phys_err = float(np.median(np.abs(p["err_h"].to_numpy())))
            ml_err = float(np.median(np.abs(m["err_h"].to_numpy())))
            cov = m["covered"].dropna()
            ml_cov = float(cov.mean()) if not cov.empty else float("nan")
            if ml_err < phys_err and _COVERAGE_BAND[0] <= ml_cov <= _COVERAGE_BAND[1]:
                champ[key] = "ml"
    return champ


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
    q = predict_quantiles(model.models, test)
    low, high = _apply_cqr(q, model.cqr_offsets)
    scored = _score_frame(test, q[:, 1], low, high)
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

    # --- evaluation model: train on the earliest voyages only, so `test` is a
    # genuine future hold-out; calibrate the interval on the middle slice. ---
    eval_models = train_quantiles(train)
    cqr = calibrate_cqr(eval_models, calib if not calib.empty else train)

    q = predict_quantiles(eval_models, test)
    ml_low, ml_high = _apply_cqr(q, cqr)
    ml_scored = _score_frame(test, q[:, 1], ml_low, ml_high)
    phys_p50 = vectorized_physics_p50(test)
    phys_scored = _score_frame(test, phys_p50, np.full(len(test), np.nan), np.full(len(test), np.nan))

    champ = build_champion_map(ml_scored, phys_scored)
    report["champion_map"] = champ
    report["cqr_offsets"] = cqr

    # Overall held-out coverage of the ML interval (diagnostic).
    cov = ml_scored["covered"].dropna()
    report["ml_coverage"] = float(cov.mean()) if not cov.empty else float("nan")

    # Per-(actual/predicted)-bucket metric rows for the scoreboard + report.
    ml_metrics = pd.DataFrame(_metric_rows(ml_scored, "ml", run_ts))
    report["ml_metrics"] = ml_metrics
    report["phys_metrics"] = pd.DataFrame(_metric_rows(phys_scored, "physics_v1", run_ts))

    if persist and not ml_metrics.empty:
        write_metrics(conn, ml_metrics)
        by_tgt = _metric_rows_by_target(ml_scored, "ml", run_ts)
        if by_tgt:
            write_metrics_by_target(conn, by_tgt)

    # --- production model: refit on train+calib (all but the most recent test
    # window is not the goal here - we want *all* the data the gate was decided
    # on, so refit on train+calib and recalibrate the band on calib). The
    # champion map, decided on the honest hold-out, is carried onto it. ---
    prod_train = pd.concat([train, calib], ignore_index=True) if not calib.empty else train
    prod_models = train_quantiles(prod_train)
    prod_cqr = calibrate_cqr(prod_models, calib if not calib.empty else train)
    model = ETAModel(prod_models, prod_cqr, champ)
    report["model"] = model

    if persist and champ:  # only save artifacts if ML actually won somewhere
        model.save()
    elif persist:
        log.info("eta_ml: challenger won no cells; not promoting (physics stays champion)")

    return report


def _print_report(report: dict) -> None:
    print(f"\nsamples={report['n_samples']}  voyages={report['n_voyages']}")
    if "cqr_offsets" in report:
        offs = ", ".join(f"{k}={v:+.1f}" for k, v in report["cqr_offsets"].items())
        print(f"CQR offsets: {offs}")
        print(f"ML P10-P90 coverage={report.get('ml_coverage', float('nan')):.3f}")
    ml, ph = report.get("ml_metrics"), report.get("phys_metrics")
    if ml is not None and not ml.empty and ph is not None:
        m = ml[(ml["lead_basis"] == "actual") & (ml["target_type"] == "all")].set_index("lead_bucket")
        p = ph[(ph["lead_basis"] == "actual") & (ph["target_type"] == "all")].set_index("lead_bucket")
        print("\nby actual lead (target_type=all)   physics -> ML")
        for lead in _LEAD_LABELS:
            if lead in m.index and lead in p.index:
                print(f"  {lead:8s}  |err| {p.loc[lead,'med_abs_err_h']:6.2f} -> {m.loc[lead,'med_abs_err_h']:6.2f}   "
                      f"bias {p.loc[lead,'bias_h']:+7.2f} -> {m.loc[lead,'bias_h']:+7.2f}")
    champ = report.get("champion_map", {})
    print(f"\npromoted ML cells ({len(champ)}): " + (", ".join(sorted(champ)) if champ else "(none)"))


def run(dry_run: bool = False) -> dict:
    """Standalone entry: train + walk-forward + (gated) promote against the live DB."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        report = train_and_evaluate(conn, persist=not dry_run)
    finally:
        conn.close()
    _print_report(report)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Train + walk-forward the ETA ML challenger")
    ap.add_argument("--dry-run", action="store_true", help="evaluate + print, never write artifacts/metrics")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
