"""Phase 1 (heuristic) + Phase 2 (LightGBM reranker) of the destination predictor.

Given a vessel's shortlist of candidate destinations (`destination_features.
candidate_frame`), score each candidate with a probability and rank them. Two
scorers, champion/challenger gated exactly like the True ETA build:

  * **heuristic** (Phase 1, the shipped champion) - a transparent weighted
    combination of course alignment, inverse sea-route/great-circle distance, the
    resolved-reported-destination match, and the port-call transition prior +
    per-vessel visit frequency (`destination_labels`), softmax-normalized across a
    vessel's candidates.
  * **ml** (Phase 2, challenger) - a LightGBM binary classifier ("is this candidate
    the vessel's actual destination?") trained on completed voyages, promoted to
    serving only when it beats the heuristic's held-out top-1 accuracy (and does
    not lose on top-3) - otherwise the heuristic stays champion, mirroring
    `eta_ml.build_champion_map`.

Training-set honesty note: the ML model's feature set excludes `reported_match` /
`resolver_score` (the resolved-AIS-destination signal). Reconstructing the
historical AIS `destination` free-text at each training observation would need a
second full scan of `ais_snapshots`; the model instead learns purely from
geometry + history (course alignment, distance, transition prior, visit
frequency). The heuristic *does* use the live reported destination (it costs
nothing extra at serving time). This is a deliberate, documented scope choice, not
a silent omission - and it makes the promotion gate slightly conservative in the
ML's favour: the champion map compares both scorers on the same restricted
feature set, so ML must win even without the signal the heuristic gets for free.

Ground truth: completed voyages already mined by True ETA Phase A (`eta_arrivals`)
and Phase B (`eta_samples`, whose `obs_lat`/`obs_lon` let us reconstruct the
vessel's own course as the bearing between consecutive observations - no second
AIS scan needed). A voyage's *actual* arrival target is the positive; the nearest
other targets in the universe at that same observation are the negatives.

    python -m analytics.destination_predict            # train + evaluate + (gated) promote
    python -m analytics.destination_predict --dry-run   # evaluate + print, never persist
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

from analytics.destination_features import (
    CANAL_BACKTRACK_WINDOW_DAYS,
    bearing_alignment_vec,
    canal_backtrack,
)
from analytics.destination_labels import (
    TransitionPriors,
    VisitFrequency,
    build_transitions,
    build_visit_freq,
)
from analytics.eta_labels import ANALYTICS_DB, haversine_nm, haversine_nm_vec
from analytics.eta_ml import time_voyage_split
from analytics.eta_routing import RouteCache, snap_cell
from quant_lib.freight.eta import initial_bearing

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Heuristic scorer (Phase 1 - the shipped champion)
# ---------------------------------------------------------------------------

# Transparent, hand-set weights. Not fit to data - this is the "physics" of the
# destination predictor: a plausible, inspectable prior, exactly as
# `eta_physics.physics_v1` is for True ETA. inv_dist uses a 500nm characteristic
# scale so nearby candidates (<100nm) dominate while far ones still get *some*
# credit (a vessel can be 1000nm from its actual destination early in a voyage).
_HEURISTIC_WEIGHTS = {
    "bearing_align": 2.0,
    "inv_dist": 3.0,
    "reported_match": 2.5,
    "transition_prior": 1.5,
    "visit_freq": 1.0,
    "canal_backtrack": -2.0,
}
_DIST_SCALE_NM = 500.0


def heuristic_raw_score(row: dict) -> float:
    """Un-normalized plausibility score for one (vessel, candidate) row.

    Missing optional fields (`route_dist_nm`, `reported_match`, priors,
    `canal_backtrack`) degrade gracefully to their neutral value rather than
    raising - this lets the same function score both the full live candidate
    frame and the training frame, which does not carry `reported_match`/
    `resolver_score` (see module docstring).
    """
    dist = row.get("route_dist_nm")
    if dist is None or not np.isfinite(dist):
        dist = row.get("gc_dist_nm") or 0.0
    inv_dist = 1.0 / (1.0 + dist / _DIST_SCALE_NM)
    bearing = row.get("bearing_align")
    bearing = bearing if bearing is not None and np.isfinite(bearing) else 0.5
    reported = 1.0 if row.get("reported_match") else 0.0
    transition = row.get("transition_prior") or 0.0
    visit = row.get("visit_freq") or 0.0
    backtrack = 1.0 if row.get("canal_backtrack") else 0.0
    w = _HEURISTIC_WEIGHTS
    return (
        w["bearing_align"] * bearing
        + w["inv_dist"] * inv_dist
        + w["reported_match"] * reported
        + w["transition_prior"] * transition
        + w["visit_freq"] * visit
        + w["canal_backtrack"] * backtrack
    )


def softmax_by_group(df: pd.DataFrame, score_col: str, group_col: str = "mmsi") -> np.ndarray:
    """Softmax-normalize `score_col` within each `group_col` group (sums to 1/group)."""
    out = pd.Series(index=df.index, dtype=float)
    for _, g in df.groupby(group_col, sort=False):
        vals = g[score_col].to_numpy(dtype=float)
        e = np.exp(vals - vals.max())
        out.loc[g.index] = e / e.sum()
    return out.to_numpy()


def heuristic_score_candidates(candidates: pd.DataFrame, group_col: str = "mmsi") -> pd.DataFrame:
    """Attach `prob` (softmax-normalized) + `method='heuristic'` to a candidate frame."""
    if candidates.empty:
        return candidates.assign(prob=pd.Series(dtype=float), method=pd.Series(dtype=str))
    out = candidates.copy()
    out["_raw"] = [heuristic_raw_score(r) for r in out.to_dict("records")]
    out["prob"] = softmax_by_group(out, "_raw", group_col)
    out["method"] = "heuristic"
    return out.drop(columns=["_raw"])


# ---------------------------------------------------------------------------
# LightGBM reranker (Phase 2 - challenger)
# ---------------------------------------------------------------------------

NUMERIC_FEATURES = [
    # gc_dist_nm deliberately excluded from the ML feature set: it's a straight
    # line, route_dist_nm is the same distance sea-route-corrected, and the two
    # are highly collinear. Feature-importance analysis showed LightGBM was
    # splitting on gc_dist_nm (gain 608k) well ahead of the strictly-more-correct
    # route_dist_nm (gain 162k); an ablation dropping gc_dist_nm confirmed it was
    # actively costing accuracy, not just wasted capacity (top1 0.678->0.681,
    # top3 0.954->0.957). gc_dist_nm itself is untouched everywhere else -
    # candidate selection, canal_backtrack, and the heuristic's own distance
    # fallback all still use it.
    "bearing_align",
    "transition_prior",
    "visit_freq",
    "canal_backtrack",
    "draught",
    "sog_trail6h",
    "route_dist_nm",
]
CATEGORICAL_FEATURES = ["target_type", "segment", "laden"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
LABEL = "is_destination"

# Modest, regularised params (mirrors eta_ml.LGB_PARAMS): the tracked history is
# short, so shallow trees + strong leaf minimums guard against memorising voyages.
LGB_PARAMS: dict = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 50,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "max_depth": -1,
    "seed": 0,
    "num_threads": -1,
    "verbosity": -1,
}
NUM_BOOST_ROUND = 300

# Per-voyage observation sampling for training: evenly spaced across the approach
# (captures early/mid/late voyage-progress diversity, per the WAY/Maritime-Optima
# finding that accuracy scales with progress) rather than every hourly fix.
_MAX_OBS_PER_VOYAGE = 3
# Candidate cap per observation: the true target + nearest others by distance.
_MAX_NEG_PER_OBS = 6

# Minimum held-out observation-groups before trusting a champion/challenger
# comparison at all (below this, keep the heuristic - too little evidence).
_MIN_TEST_GROUPS = 30

MODEL_DIR = Path(__file__).resolve().parent / "models"

DEST_METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS destination_model_metrics (
    run_ts    TIMESTAMP,
    model     VARCHAR,          -- 'heuristic' | 'ml'
    n         INTEGER,          -- held-out (voyage, observation) groups scored
    top1_acc  DOUBLE,
    top3_acc  DOUBLE,
    promoted  BOOLEAN,
    PRIMARY KEY (run_ts, model)
);
"""


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "canal_backtrack" not in out.columns:
        out["canal_backtrack"] = 0
    if "laden" not in out.columns:
        out["laden"] = None
    if "draught" not in out.columns:
        out["draught"] = None
    if "sog_trail6h" not in out.columns:
        out["sog_trail6h"] = None
    if "route_dist_nm" not in out.columns:
        out["route_dist_nm"] = None
    out = out[FEATURES].copy()
    for col in NUMERIC_FEATURES:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].astype("category")
    return out


def train_classifier(train: pd.DataFrame, params: dict | None = None):
    """Train the binary "is this candidate the destination?" classifier."""
    import lightgbm as lgb

    p = {**LGB_PARAMS, **(params or {})}
    X = _prepare(train)
    y = train[LABEL].to_numpy(dtype=float)
    dtrain = lgb.Dataset(X, label=y, categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False)
    return lgb.train({**p, "objective": "binary"}, dtrain, num_boost_round=NUM_BOOST_ROUND)


def predict_proba(booster, df: pd.DataFrame) -> np.ndarray:
    """Raw per-candidate probability (not yet group-normalized)."""
    return booster.predict(_prepare(df))


class DestinationModel:
    """A trained challenger + its promotion verdict + the metrics that earned it."""

    def __init__(self, booster, promoted: bool, metrics: dict) -> None:
        """Wrap a trained booster, its promotion verdict, and the metrics that earned it."""
        self.booster = booster
        self.promoted = promoted
        self.metrics = metrics
        self.fitted = booster is not None

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Raw per-candidate probability (not yet group-normalized)."""
        return predict_proba(self.booster, df)

    def save(self, model_dir: Path = MODEL_DIR) -> None:
        """Persist the booster + meta (promotion verdict, metrics) to disk."""
        model_dir.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(model_dir / "dest_lgbm.txt"))
        payload = {
            "promoted": self.promoted,
            "metrics": self.metrics,
            "features": FEATURES,
            "trained_ts": datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(),
        }
        (model_dir / "dest_ml_meta.json").write_text(json.dumps(payload, indent=2))
        log.info("saved destination ML model (promoted=%s) to %s", self.promoted, model_dir)

    @classmethod
    def load(cls, model_dir: Path = MODEL_DIR) -> DestinationModel | None:
        """Load a saved model, or None if artifacts are absent/incomplete."""
        meta_path = model_dir / "dest_ml_meta.json"
        model_path = model_dir / "dest_lgbm.txt"
        if not meta_path.exists() or not model_path.exists():
            return None
        try:
            import lightgbm as lgb

            meta = json.loads(meta_path.read_text())
            booster = lgb.Booster(model_file=str(model_path))
            return cls(booster, bool(meta.get("promoted", False)), meta.get("metrics", {}))
        except Exception as exc:  # noqa: BLE001 - serving must never crash on a bad artifact
            log.warning("failed to load destination ML model: %s", exc)
            return None


def score_candidates(
    candidates: pd.DataFrame, model: DestinationModel | None, group_col: str = "mmsi"
) -> pd.DataFrame:
    """Attach `prob` + `method` to a live candidate frame: ml if promoted, else heuristic.

    `candidates` must already carry `transition_prior`/`visit_freq` (attached by
    the caller from the persisted `dest_transitions`/`dest_port_visits` priors) in
    addition to the geometric columns `destination_features.candidate_frame`
    produces. Falls back to the heuristic whenever no model is loaded, it was
    not promoted, or the loaded booster's feature set doesn't match `_prepare`'s
    current output (e.g. a model artifact trained before a feature was added,
    the same conservative default as True ETA's physics fallback and
    `DestinationModel.load`'s own artifact-corruption guard).
    """
    if candidates.empty:
        return candidates.assign(prob=pd.Series(dtype=float), method=pd.Series(dtype=str))
    if model is not None and model.fitted and model.promoted:
        try:
            out = candidates.copy()
            out["_raw"] = model.predict_proba(out)
            sums = out.groupby(group_col)["_raw"].transform("sum")
            sizes = out.groupby(group_col)["_raw"].transform("size")
            out["prob"] = np.where(sums > 0, out["_raw"] / sums.replace(0, np.nan), 1.0 / sizes)
            out["method"] = "ml"
            return out.drop(columns=["_raw"])
        except Exception as exc:  # noqa: BLE001 - serving must never crash on a stale artifact
            log.warning(
                "destination ML model rejected the current feature set (%s); falling back", exc
            )
    return heuristic_score_candidates(candidates, group_col)


# ---------------------------------------------------------------------------
# Training-set assembly from completed voyages
# ---------------------------------------------------------------------------


def _load_arrivals_with_prev(conn: duckdb.DuckDBPyConnection, before_ts=None) -> pd.DataFrame:
    """`eta_arrivals` ordered by (mmsi, arrival_ts), each row tagged with the
    vessel's immediately-prior arrival target (`prev_target_id`, NaN if none)."""
    sql = "SELECT mmsi, target_id, arrival_ts, segment FROM eta_arrivals"
    params: list = []
    if before_ts is not None:
        sql += " WHERE arrival_ts <= ?"
        params.append(before_ts)
    sql += " ORDER BY mmsi, arrival_ts"
    try:
        df = conn.execute(sql, params).df()
    except duckdb.CatalogException:
        return pd.DataFrame()
    if df.empty:
        return df
    df["arrival_ts"] = pd.to_datetime(df["arrival_ts"])
    df["prev_target_id"] = df.groupby("mmsi")["target_id"].shift(1)
    return df


def _load_canal_transits(
    conn: duckdb.DuckDBPyConnection,
) -> dict[int, list[tuple[pd.Timestamp, str]]]:
    """Each vessel's Suez/Panama transit history, sorted by exited_ts ascending
    - `mmsi -> [(exited_ts, chokepoint), ...]`. Used to look up, for any
    historical training observation, the most recent qualifying transit
    strictly before it (leakage-free: a transit dated after `obs_ts` must never
    be visible - see `_chokepoint_as_of`)."""
    try:
        df = conn.execute(
            "SELECT mmsi, chokepoint, exited_ts FROM transit_events "
            "WHERE chokepoint IN ('suez', 'panama') ORDER BY mmsi, exited_ts"
        ).df()
    except duckdb.CatalogException:
        return {}
    if df.empty:
        return {}
    df["exited_ts"] = pd.to_datetime(df["exited_ts"])
    out: dict[int, list[tuple[pd.Timestamp, str]]] = {}
    for mmsi, g in df.groupby("mmsi", sort=False):
        out[int(mmsi)] = list(zip(g["exited_ts"], g["chokepoint"], strict=False))
    return out


def _chokepoint_as_of(
    history: list[tuple[pd.Timestamp, str]], obs_ts, window_days: float
) -> str | None:
    """Most recent chokepoint transit at/before `obs_ts` within `window_days`,
    or None. `history` must be sorted by exited_ts ascending
    (`_load_canal_transits` already returns it that way)."""
    best: str | None = None
    for ts, cp in history:
        if ts > obs_ts:
            break
        if (obs_ts - ts).total_seconds() / 86400.0 <= window_days:
            best = cp
    return best


def build_training_candidates(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Reconstruct labeled (observation, candidate) rows from completed voyages.

    For each voyage (an `eta_samples` group keyed by `voyage_id`), samples up to
    `_MAX_OBS_PER_VOYAGE` observations spread across the approach, reconstructs
    the vessel's own course as the bearing between consecutive observations (no
    second AIS scan - `eta_samples` already carries `obs_lat`/`obs_lon`), and
    scores every target in the universe against that course + position. The true
    arrival target is the positive; the `_MAX_NEG_PER_OBS` nearest others are the
    negatives. `prev_target_id` is attached per voyage for the transition-prior
    feature (added later by the caller, which fits priors on a leakage-free slice).
    """
    try:
        samples = conn.execute(
            "SELECT voyage_id, mmsi, target_id, target_type, arrival_ts, obs_ts, obs_lat, obs_lon, "
            "       remaining_h, segment, laden, draught, sog_trail6h "
            "FROM eta_samples"
        ).df()
    except duckdb.CatalogException:
        return pd.DataFrame()
    if samples.empty:
        return pd.DataFrame()
    samples["arrival_ts"] = pd.to_datetime(samples["arrival_ts"])
    samples["obs_ts"] = pd.to_datetime(samples["obs_ts"])

    targets = conn.execute("SELECT target_id, target_type, name, lat, lon FROM eta_targets").df()
    if targets.empty:
        return pd.DataFrame()
    t_lat = targets["lat"].to_numpy(dtype=float)
    t_lon = targets["lon"].to_numpy(dtype=float)
    t_ids = targets["target_id"].to_numpy(dtype=object)
    t_types = targets["target_type"].to_numpy(dtype=object)
    canal_transits = _load_canal_transits(conn)
    # Shared with eta_serving/destination_serving's own RouteCache instances via
    # the same on-disk eta_route_cache table - a (cell, target) pair routed by
    # either the live build or a prior training run is a free hit here.
    route_cache = RouteCache(conn)

    arrivals = _load_arrivals_with_prev(conn)
    prev_map = (
        {(int(r.mmsi), r.target_id, r.arrival_ts): r.prev_target_id for r in arrivals.itertuples()}
        if not arrivals.empty
        else {}
    )

    rows: list[dict] = []
    for vid, grp in samples.groupby("voyage_id", sort=False):
        grp = grp.sort_values("obs_ts").reset_index(drop=True)
        n = len(grp)
        if n == 0:
            continue
        idxs = (
            list(range(n))
            if n <= _MAX_OBS_PER_VOYAGE
            else sorted(
                set(np.linspace(0, n - 1, _MAX_OBS_PER_VOYAGE).round().astype(int).tolist())
            )
        )

        lats = grp["obs_lat"].to_numpy(dtype=float)
        lons = grp["obs_lon"].to_numpy(dtype=float)
        courses = np.full(n, np.nan)
        for i in range(n - 1):
            courses[i] = initial_bearing(lats[i], lons[i], lats[i + 1], lons[i + 1])
        if n >= 2:
            courses[n - 1] = courses[n - 2]

        true_target = grp["target_id"].iloc[0]
        mmsi = int(grp["mmsi"].iloc[0])
        arrival_ts = grp["arrival_ts"].iloc[0]
        prev_target = prev_map.get((mmsi, true_target, arrival_ts))
        if prev_target is not None and pd.isna(prev_target):
            prev_target = None

        for i in idxs:
            lat, lon = float(lats[i]), float(lons[i])
            course = float(courses[i]) if np.isfinite(courses[i]) else None
            obs_ts = grp["obs_ts"].iloc[i]
            chokepoint = _chokepoint_as_of(
                canal_transits.get(mmsi, []), obs_ts, CANAL_BACKTRACK_WINDOW_DAYS
            )
            gc = haversine_nm_vec(t_lat, t_lon, lat, lon)
            align = bearing_alignment_vec(lat, lon, course, t_lat, t_lon)

            order = np.argsort(gc)
            true_idx = np.where(t_ids == true_target)[0]
            chosen: list[int] = list(true_idx[:1])
            for j in order:
                if len(chosen) >= _MAX_NEG_PER_OBS + 1:
                    break
                if int(j) in chosen:
                    continue
                chosen.append(int(j))

            seg_val = grp["segment"].iloc[i]
            seg = str(seg_val) if pd.notna(seg_val) else None
            laden_val = grp["laden"].iloc[i]
            laden = bool(laden_val) if pd.notna(laden_val) else None
            draught_val = grp["draught"].iloc[i]
            draught = float(draught_val) if pd.notna(draught_val) else None
            trail_val = grp["sog_trail6h"].iloc[i]
            trail = float(trail_val) if pd.notna(trail_val) else None
            clat, clon = snap_cell(lat, lon)
            for j in chosen:
                target_dict = {
                    "target_id": str(t_ids[j]),
                    "lat": float(t_lat[j]),
                    "lon": float(t_lon[j]),
                }
                cell_route, _method = route_cache.distance(lat, lon, target_dict)
                gc_cell = haversine_nm(clat, clon, target_dict["lat"], target_dict["lon"])
                route_dist = max(cell_route - gc_cell + float(gc[j]), float(gc[j]))
                rows.append(
                    {
                        "voyage_id": vid,
                        "mmsi": mmsi,
                        "arrival_ts": arrival_ts,
                        "obs_ts": obs_ts,
                        "remaining_h": float(grp["remaining_h"].iloc[i]),
                        "target_id": t_ids[j],
                        "target_type": t_types[j],
                        "gc_dist_nm": float(gc[j]),
                        "bearing_align": float(align[j]),
                        "canal_backtrack": canal_backtrack(
                            lat, lon, float(t_lat[j]), float(t_lon[j]), chokepoint
                        ),
                        "segment": seg,
                        "laden": laden,
                        "draught": draught,
                        "sog_trail6h": trail,
                        "route_dist_nm": route_dist,
                        "is_destination": int(t_ids[j] == true_target),
                        "prev_target_id": prev_target,
                    }
                )

    route_cache.flush()
    return pd.DataFrame(rows)


def _attach_priors(
    df: pd.DataFrame, trans: TransitionPriors, visits: VisitFrequency
) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["transition_prior"] = pd.Series(dtype=float)
        out["visit_freq"] = pd.Series(dtype=float)
        return out
    out = df.copy()
    out["transition_prior"] = [
        trans.prior(r.prev_target_id, r.target_id, r.segment) for r in out.itertuples()
    ]
    out["visit_freq"] = [visits.freq(r.mmsi, r.target_id) for r in out.itertuples()]
    return out


def _obs_group_metrics(scored: pd.DataFrame, score_col: str) -> dict:
    """Top-1 / top-3 accuracy over held-out (voyage, observation) groups."""
    top1 = 0
    top3 = 0
    n = 0
    for _, g in scored.groupby(["voyage_id", "obs_ts"], sort=False):
        n += 1
        g = g.sort_values(score_col, ascending=False)
        if int(g["is_destination"].iloc[0]) == 1:
            top1 += 1
        if int(g["is_destination"].iloc[:3].sum()) > 0:
            top3 += 1
    return {
        "n": n,
        "top1_acc": top1 / n if n else float("nan"),
        "top3_acc": top3 / n if n else float("nan"),
    }


def _vocab_size(conn: duckdb.DuckDBPyConnection) -> int:
    try:
        return int(conn.execute("SELECT count(*) FROM eta_targets").fetchone()[0]) or 1
    except duckdb.CatalogException:
        return 1


def _write_metrics(
    conn: duckdb.DuckDBPyConnection,
    ml_metrics: dict,
    heur_metrics: dict,
    promoted: bool,
    run_ts: datetime | None = None,
) -> None:
    conn.execute(DEST_METRICS_SCHEMA)
    run_ts = run_ts or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for name, m in (("heuristic", heur_metrics), ("ml", ml_metrics)):
        conn.execute(
            "INSERT OR REPLACE INTO destination_model_metrics "
            "(run_ts, model, n, top1_acc, top3_acc, promoted) VALUES (?, ?, ?, ?, ?, ?)",
            [
                run_ts,
                name,
                m["n"],
                m["top1_acc"],
                m["top3_acc"],
                promoted if name == "ml" else False,
            ],
        )


# ---------------------------------------------------------------------------
# Orchestration: train + walk-forward + champion/challenger gate
# ---------------------------------------------------------------------------


def train_and_evaluate(conn: duckdb.DuckDBPyConnection, persist: bool = True) -> dict:
    """Train, walk-forward-evaluate vs the heuristic, gate-promote, and (optionally) save.

    Leakage control mirrors `eta_ml`: `time_voyage_split` on `voyage_id`/`arrival_ts`
    so no voyage straddles train/calib/test and test is strictly later; the
    transition/visit priors used as *features* are fit only from arrivals no later
    than the train split's cutoff, so no test-fold history leaks into a prior.
    """
    raw = build_training_candidates(conn)
    report: dict = {"n_rows": int(len(raw)), "n_voyages": 0, "promoted": False}
    if raw.empty:
        log.warning("destination_predict: no training candidates; skipping")
        return report
    report["n_voyages"] = int(raw["voyage_id"].nunique())

    train, calib, test = time_voyage_split(raw)
    if train.empty or test.empty:
        log.warning("destination_predict: insufficient voyages for a walk-forward split")
        return report

    cutoff = train["arrival_ts"].max()
    arrivals_before = _load_arrivals_with_prev(conn, before_ts=cutoff)
    trans = TransitionPriors(build_transitions(arrivals_before), _vocab_size(conn))
    visits = VisitFrequency(build_visit_freq(arrivals_before))

    train = _attach_priors(train, trans, visits)
    calib = _attach_priors(calib, trans, visits)
    test = _attach_priors(test, trans, visits)

    booster = train_classifier(train)
    ml_scored = test.assign(_score=predict_proba(booster, test))
    ml_metrics = _obs_group_metrics(ml_scored, "_score")

    heur_scored = test.assign(_score=[heuristic_raw_score(r) for r in test.to_dict("records")])
    heur_metrics = _obs_group_metrics(heur_scored, "_score")

    report["ml_metrics"] = ml_metrics
    report["heuristic_metrics"] = heur_metrics

    promote = (
        ml_metrics["n"] >= _MIN_TEST_GROUPS
        and np.isfinite(ml_metrics["top1_acc"])
        and np.isfinite(heur_metrics["top1_acc"])
        and ml_metrics["top1_acc"] > heur_metrics["top1_acc"]
        and ml_metrics["top3_acc"] >= heur_metrics["top3_acc"]
    )
    report["promoted"] = promote

    # Production booster: refit on train+calib (more data than the eval-only
    # train split), keeping the promotion verdict decided on the honest hold-out.
    prod_train = pd.concat([train, calib], ignore_index=True) if not calib.empty else train
    prod_booster = train_classifier(prod_train)
    model = DestinationModel(prod_booster, promote, {"ml": ml_metrics, "heuristic": heur_metrics})
    report["model"] = model

    if persist:
        # Pass MODEL_DIR explicitly (a module-global lookup at call time) rather
        # than relying on `save`'s own bound default, so tests can redirect the
        # artifact path via `monkeypatch.setattr(dp, "MODEL_DIR", ...)` without
        # ever writing into the real, gitignored production models/ directory.
        model.save(MODEL_DIR)
        _write_metrics(conn, ml_metrics, heur_metrics, promote)
        if not promote:
            log.info("destination_predict: challenger did not beat the heuristic; not promoting")

    return report


def _print_report(report: dict) -> None:
    print(f"\nrows={report.get('n_rows', 0)}  voyages={report.get('n_voyages', 0)}")
    ml, heur = report.get("ml_metrics"), report.get("heuristic_metrics")
    if ml and heur:
        print(
            f"heuristic: n={heur['n']} top1={heur['top1_acc']:.3f} top3={heur['top3_acc']:.3f}\n"
            f"ml:        n={ml['n']} top1={ml['top1_acc']:.3f} top3={ml['top3_acc']:.3f}"
        )
    print(f"promoted: {report.get('promoted', False)}")


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
    ap = argparse.ArgumentParser(
        description="Train + walk-forward the destination predictor challenger"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="evaluate + print, never write artifacts/metrics"
    )
    args = ap.parse_args()
    run(dry_run=args.dry_run)
