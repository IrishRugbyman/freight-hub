"""Phase A scoring harness: score any ETA function against real arrivals.

Replays the approach track of every mined `eta_arrival` (from `ais_snapshots`),
samples observation fixes at ~1h cadence up to 72h before arrival, and scores a
caller-supplied `eta_fn(obs) -> hours` against the *actual* remaining time. The
naive baseline (`great_circle_dist / SOG`) is scored here and its lead-bucket x
target-type table is written to `eta_model_metrics` (model='naive') - the
committed reference every later phase must beat.

Leakage control: each arrival is a `voyage_id` (stable hash of
mmsi+target+arrival_ts); `voyage_split` partitions on that id so no voyage ever
straddles a train/test boundary. Buckets are by *actual* remaining time, never
by the prediction.

    python -m analytics.eta_backtest          # rebuild + print the naive table
"""

from __future__ import annotations

import argparse
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.eta_labels import (
    ANALYTICS_DB,
    ETA_SCHEMA,
    _default_ais_query,
    haversine_nm_vec,
)

log = logging.getLogger(__name__)

# Observation sampling.
_MAX_LEAD_H = 72.0  # ignore fixes more than this long before arrival
_SAMPLE_CADENCE_H = 1.0  # thin the approach track to ~1 fix per hour
_MIN_SOG_KN = 1.0  # a sample must be underway for a kinematic ETA

# Lead buckets keyed to the roadmap's table (by ACTUAL remaining time).
_LEAD_EDGES = [0.0, 6.0, 12.0, 24.0, 48.0, np.inf]
_LEAD_LABELS = ["0-6h", "6-12h", "12-24h", "24-48h", "48h+"]


def _bearing_vec(lats, lons, lat0: float, lon0: float) -> np.ndarray:
    """Initial great-circle bearing (deg, 0-360) from each (lat,lon) to (lat0,lon0).

    Vectorised twin of `quant_lib.freight.initial_bearing` for the sample build.
    The `approach_bearing` feature: the heading a vessel must take to the target.
    """
    phi1 = np.radians(np.asarray(lats, dtype=float))
    phi2 = np.radians(lat0)
    dlon = np.radians(lon0 - np.asarray(lons, dtype=float))
    y = np.sin(dlon) * np.cos(phi2)
    x = np.cos(phi1) * np.sin(phi2) - np.sin(phi1) * np.cos(phi2) * np.cos(dlon)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def lead_bucket(remaining_h: float) -> str:
    """Return the lead-time bucket label for a lead time (hours)."""
    for i in range(len(_LEAD_LABELS)):
        if _LEAD_EDGES[i] <= remaining_h < _LEAD_EDGES[i + 1]:
            return _LEAD_LABELS[i]
    return _LEAD_LABELS[-1]


def lead_buckets(values) -> np.ndarray:
    """Vectorized :func:`lead_bucket` over an array of hours.

    `np.digitize` against the inner edges [6, 12, 24, 48] maps each value to its
    bucket index; non-finite or negative inputs fall into bucket 0. Used to label
    a whole scored frame at once by either the actual remaining time or the model's
    own predicted lead (the two conditioning bases of the accuracy scoreboard).
    """
    arr = np.asarray(values, dtype=float)
    idx = np.digitize(np.nan_to_num(arr, nan=0.0), _LEAD_EDGES[1:-1])
    idx = np.clip(idx, 0, len(_LEAD_LABELS) - 1)
    return np.asarray(_LEAD_LABELS, dtype=object)[idx]


def voyage_id(mmsi: int, target_id: str, arrival_ts) -> int:
    """Stable 63-bit voyage id = hash(mmsi, target_id, arrival_ts)."""
    ts = arrival_ts.isoformat() if hasattr(arrival_ts, "isoformat") else str(arrival_ts)
    key = f"{mmsi}|{target_id}|{ts}".encode()
    return int.from_bytes(hashlib.sha1(key).digest()[:8], "big") & ((1 << 63) - 1)


# ---------------------------------------------------------------------------
# Baseline ETA function
# ---------------------------------------------------------------------------


def naive_eta_fn(obs: dict) -> float:
    """Naive kinematic ETA: great-circle distance / instantaneous SOG (hours)."""
    sog = obs.get("sog") or 0.0
    if sog < _MIN_SOG_KN:
        return float("nan")
    return obs["gc_dist_nm"] / sog


def route_eta_fn(obs: dict) -> float:
    """Routing baseline ETA: sea-route distance / instantaneous SOG (hours).

    Identical to `naive_eta_fn` except it uses the distance ships actually sail
    (Phase B `route_dist_nm`) instead of the straight line. Isolating the distance
    fix this way is the cleanest demonstration that routing alone shrinks the
    long-haul bias. Falls back to the great-circle distance if a row was never
    routed (so it can never score worse than naive for lack of a value)."""
    sog = obs.get("sog") or 0.0
    if sog < _MIN_SOG_KN:
        return float("nan")
    dist = obs.get("route_dist_nm")
    if dist is None or not np.isfinite(dist):
        dist = obs["gc_dist_nm"]
    return dist / sog


# ---------------------------------------------------------------------------
# Approach-sample reconstruction
# ---------------------------------------------------------------------------


def build_samples(
    conn: duckdb.DuckDBPyConnection,
    ais_query,
) -> pd.DataFrame:
    """Reconstruct the per-observation sample table from mined arrivals.

    For each arrival, pull its mmsi's fixes in [approach_start - margin,
    arrival], keep those within ~the target's approach window, thin to ~1h, and
    emit one row per observation with the actual `remaining_h` label and the
    great-circle distance to the target.
    """
    conn.execute(ETA_SCHEMA)
    arrivals = conn.execute(
        "SELECT a.mmsi, a.target_id, a.arrival_ts, a.approach_start_ts, a.segment, a.laden, "
        "       t.lat AS t_lat, t.lon AS t_lon, t.target_type, t.is_canal "
        "FROM eta_arrivals a JOIN eta_targets t USING (target_id)"
    ).df()
    if arrivals.empty:
        return pd.DataFrame()

    arrivals["arrival_ts"] = pd.to_datetime(arrivals["arrival_ts"])
    arrivals["approach_start_ts"] = pd.to_datetime(arrivals["approach_start_ts"])

    # Bulk-load every relevant vessel track in a SINGLE scan, then slice per
    # arrival in pandas. Per-mmsi queries would be one full-table scan each
    # (~15k scans at production scale); this is one scan + an in-memory groupby.
    earliest_global = (arrivals["arrival_ts"] - pd.Timedelta(hours=_MAX_LEAD_H)).min()
    mmsis = arrivals["mmsi"].astype("int64").unique().tolist()
    tracks = ais_query(
        "SELECT mmsi, snapshot_ts, lat, lon, sog, draught FROM ais_snapshots "
        "WHERE snapshot_ts >= ? ORDER BY mmsi, snapshot_ts",
        [earliest_global.to_pydatetime()],
    )
    if tracks is None or tracks.empty:
        return pd.DataFrame()
    tracks = tracks[tracks["mmsi"].isin(mmsis)].copy()
    tracks["snapshot_ts"] = pd.to_datetime(tracks["snapshot_ts"])
    track_by_mmsi = {int(m): g for m, g in tracks.groupby("mmsi", sort=False)}

    rows: list[dict] = []
    for mmsi, mgrp in arrivals.groupby("mmsi", sort=False):
        track = track_by_mmsi.get(int(mmsi))
        if track is None or track.empty:
            continue
        for arr in mgrp.itertuples():
            window = track[
                (track["snapshot_ts"] >= arr.arrival_ts - pd.Timedelta(hours=_MAX_LEAD_H))
                & (track["snapshot_ts"] <= arr.arrival_ts)
            ]
            if window.empty:
                continue
            remaining_h = (
                arr.arrival_ts - window["snapshot_ts"]
            ).dt.total_seconds().to_numpy() / 3600.0
            # Trailing 6h median SOG at each fix (denoised speed feature). Computed
            # on the full window (time-ordered) before thinning so the trailing
            # window sees every fix, not just the kept ~hourly ones.
            trail_full = (
                window.set_index("snapshot_ts")["sog"].rolling("6h").median().to_numpy()
            )
            # Thin to ~1 fix per cadence bucket (keep first fix in each bucket).
            bucket = np.floor(remaining_h / _SAMPLE_CADENCE_H).astype(int)
            keep = np.concatenate(([True], np.diff(bucket) != 0))
            w = window[keep]
            rem = remaining_h[keep]
            trail = trail_full[keep]
            draught_w = w["draught"].to_numpy()
            gc = haversine_nm_vec(w["lat"].values, w["lon"].values, arr.t_lat, arr.t_lon)
            bearing = _bearing_vec(w["lat"].values, w["lon"].values, arr.t_lat, arr.t_lon)
            vid = voyage_id(arr.mmsi, arr.target_id, arr.arrival_ts)
            for j in range(len(w)):
                if rem[j] <= 0:
                    continue
                rows.append(
                    {
                        "voyage_id": vid,
                        "mmsi": int(arr.mmsi),
                        "target_id": arr.target_id,
                        "target_type": arr.target_type,
                        "is_canal": bool(arr.is_canal),
                        "arrival_ts": arr.arrival_ts.to_pydatetime(),
                        "obs_ts": w["snapshot_ts"].iloc[j].to_pydatetime(),
                        "obs_lat": float(w["lat"].iloc[j]),
                        "obs_lon": float(w["lon"].iloc[j]),
                        "remaining_h": float(rem[j]),
                        "gc_dist_nm": float(gc[j]),
                        "sog": float(w["sog"].iloc[j]) if pd.notna(w["sog"].iloc[j]) else 0.0,
                        "sog_trail6h": (float(trail[j]) if pd.notna(trail[j]) else None),
                        "draught": (float(draught_w[j]) if pd.notna(draught_w[j]) else None),
                        "approach_bearing": (float(bearing[j]) if pd.notna(bearing[j]) else None),
                        "segment": (str(arr.segment) if pd.notna(arr.segment) else None),
                        "laden": (bool(arr.laden) if pd.notna(arr.laden) else None),
                        "lead_bucket": lead_bucket(float(rem[j])),
                    }
                )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Split + scoring
# ---------------------------------------------------------------------------


def voyage_split(samples: pd.DataFrame, test_frac: float = 1.0, seed: int = 0):
    """Partition samples by voyage_id (no voyage crosses the boundary).

    Returns (train, test). For the naive baseline there is nothing to fit, so the
    default scores the full set as 'test'; later phases use a real fraction.
    """
    if samples.empty or test_frac >= 1.0:
        return samples.iloc[0:0], samples
    vids = np.array(sorted(samples["voyage_id"].unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(vids)
    n_test = int(round(len(vids) * test_frac))
    test_ids = set(vids[:n_test].tolist())
    is_test = samples["voyage_id"].isin(test_ids)
    return samples[~is_test], samples[is_test]


def _metric_rows(scored: pd.DataFrame, model: str, run_ts: datetime) -> list[dict]:
    """Aggregate signed/abs error into the lead-bucket x target-type table.

    Per-lead-bucket rows are emitted under two conditioning bases:
    ``lead_basis='actual'`` buckets by the true remaining time (the roadmap's
    original framing) and ``lead_basis='predicted'`` buckets by the model's own
    served ETA. The two disagree sharply at long lead because conditioning a
    signed-error mean on either variable induces a regression-to-the-mean gradient
    in opposite directions; serving both keeps that selection artifact visible
    rather than hiding it behind one scary -50h number. The unconditional
    (``lead_bucket='all'``) rollups are basis-independent and tagged ``'all'``.
    """
    out: list[dict] = []
    if scored.empty:
        return out

    scored = scored.assign(
        _lead_actual=lead_buckets(scored["remaining_h"].to_numpy(dtype=float)),
        _lead_pred=lead_buckets(scored["pred_h"].to_numpy(dtype=float)),
    )

    def agg(g: pd.DataFrame, lead: str, ttype: str, basis: str) -> dict:
        err = g["err_h"].to_numpy()
        abs_err = np.abs(err)
        actual = g["remaining_h"].to_numpy()
        mape = (
            float(np.median(abs_err[actual > 0] / actual[actual > 0]))
            if (actual > 0).any()
            else float("nan")
        )
        cov = g["covered"].dropna()
        return {
            "run_ts": run_ts,
            "model": model,
            "lead_bucket": lead,
            "target_type": ttype,
            "lead_basis": basis,
            "n": int(len(g)),
            "med_abs_err_h": float(np.median(abs_err)),
            "bias_h": float(np.median(err)),
            "mape": mape,
            "p90_abs_err_h": float(np.percentile(abs_err, 90)),
            "interval_coverage": float(cov.mean()) if not cov.empty else float("nan"),
        }

    # Unconditional rollups (basis-independent): per target type + overall.
    for ttype in ["chokepoint", "port"]:
        sub = scored[scored["target_type"] == ttype]
        if not sub.empty:
            out.append(agg(sub, "all", ttype, "all"))
    out.append(agg(scored, "all", "all", "all"))

    # Per-lead-bucket rows under each conditioning basis.
    for basis, col in (("actual", "_lead_actual"), ("predicted", "_lead_pred")):
        for ttype in ["chokepoint", "port"]:
            sub = scored[scored["target_type"] == ttype]
            for lead in _LEAD_LABELS:
                g = sub[sub[col] == lead]
                if not g.empty:
                    out.append(agg(g, lead, ttype, basis))
        for lead in _LEAD_LABELS:
            g = scored[scored[col] == lead]
            if not g.empty:
                out.append(agg(g, lead, "all", basis))
    return out


def _apply_eta_fn(
    samples: pd.DataFrame, eta_fn, has_interval: bool
) -> pd.DataFrame:
    """Run eta_fn over every row of samples; return the scored frame.

    The returned frame has ``pred_h``, ``err_h``, and ``covered`` columns added.
    Rows where the model returned NaN are dropped (e.g. vessel not underway).
    This is extracted from ``score()`` so both aggregate and per-target metrics
    can share one ETA-function pass over the full sample set.
    """
    preds, lows, highs = [], [], []
    for obs in samples.to_dict("records"):
        res = eta_fn(obs)
        if isinstance(res, dict):
            preds.append(res.get("p50", float("nan")))
            lows.append(res.get("low", float("nan")))
            highs.append(res.get("high", float("nan")))
        else:
            preds.append(float(res))
            lows.append(float("nan"))
            highs.append(float("nan"))

    scored = samples.copy()
    scored["pred_h"] = preds
    scored["_lo"] = lows
    scored["_hi"] = highs
    scored = scored[np.isfinite(scored["pred_h"])].copy()
    if scored.empty:
        return scored
    scored["err_h"] = scored["pred_h"] - scored["remaining_h"]
    if has_interval:
        scored["covered"] = (
            (scored["remaining_h"] >= scored["_lo"]) & (scored["remaining_h"] <= scored["_hi"])
        ).astype(float)
    else:
        scored["covered"] = np.nan
    return scored


def score(
    samples: pd.DataFrame,
    eta_fn,
    model: str,
    run_ts: datetime | None = None,
    has_interval: bool = False,
) -> pd.DataFrame:
    """Score `eta_fn` over `samples`, return the metric table (not yet persisted).

    `eta_fn(obs) -> hours` (NaN to skip a sample, e.g. not underway). If
    `has_interval`, the harness expects `eta_low_h`/`eta_high_h` from eta_fn via
    a dict return; the naive baseline has no interval so coverage is NaN.
    """
    run_ts = run_ts or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    if samples.empty:
        return pd.DataFrame()
    scored = _apply_eta_fn(samples, eta_fn, has_interval)
    if scored.empty:
        return pd.DataFrame()
    return pd.DataFrame(_metric_rows(scored, model, run_ts))


def _metric_rows_by_target(
    scored: pd.DataFrame, model: str, run_ts: datetime
) -> list[dict]:
    """Compute per-target_id accuracy metrics from a pre-scored frame."""
    out: list[dict] = []
    if scored.empty or "target_id" not in scored.columns:
        return out
    for target_id, g in scored.groupby("target_id"):
        err = g["err_h"].to_numpy()
        abs_err = np.abs(err)
        actual = g["remaining_h"].to_numpy()
        mape = (
            float(np.median(abs_err[actual > 0] / actual[actual > 0]))
            if (actual > 0).any()
            else float("nan")
        )
        cov = g["covered"].dropna()
        out.append(
            {
                "run_ts": run_ts,
                "model": model,
                "target_id": str(target_id),
                "n": int(len(g)),
                "med_abs_err_h": float(np.median(abs_err)),
                "bias_h": float(np.median(err)),
                "mape": mape,
                "p90_abs_err_h": float(np.percentile(abs_err, 90)),
                "interval_coverage": float(cov.mean()) if not cov.empty else float("nan"),
            }
        )
    return out


_TARGET_METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_metrics_by_target (
    run_ts            TIMESTAMP,
    model             VARCHAR,
    target_id         VARCHAR,
    n                 INTEGER,
    med_abs_err_h     DOUBLE,
    bias_h            DOUBLE,
    mape              DOUBLE,
    p90_abs_err_h     DOUBLE,
    interval_coverage DOUBLE,
    PRIMARY KEY (run_ts, model, target_id)
);
"""


def score_by_target(
    samples: pd.DataFrame,
    eta_fn,
    model: str,
    run_ts: datetime | None = None,
    has_interval: bool = False,
) -> list[dict]:
    """Score eta_fn and return per-target metric rows (not yet persisted)."""
    run_ts = run_ts or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    if samples.empty:
        return []
    scored = _apply_eta_fn(samples, eta_fn, has_interval)
    if scored.empty:
        return []
    return _metric_rows_by_target(scored, model, run_ts)


def write_metrics_by_target(
    conn: duckdb.DuckDBPyConnection, metrics: list[dict]
) -> None:
    """Persist per-target metric rows into eta_metrics_by_target."""
    conn.execute(_TARGET_METRICS_SCHEMA)
    rows = [
        (r["run_ts"], r["model"], r["target_id"], r["n"],
         r["med_abs_err_h"], r["bias_h"], r["mape"],
         r["p90_abs_err_h"], r["interval_coverage"])
        for r in metrics
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO eta_metrics_by_target "
        "(run_ts, model, target_id, n, med_abs_err_h, bias_h, "
        " mape, p90_abs_err_h, interval_coverage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def score_vectorized(
    samples: pd.DataFrame,
    model: str,
    run_ts: datetime,
    interval=None,  # eta_physics.IntervalModel | None
) -> tuple[pd.DataFrame, list[dict]]:
    """Score a model using vectorized numpy/pandas ops; return (agg, per_target).

    Equivalent to ``score() + score_by_target()`` but in a single data pass,
    avoiding the Python-loop overhead of ``_apply_eta_fn``. Handles the three
    built-in model keys: 'naive', 'naive+route', and 'physics_v1'.

    Returns a tuple of:
    - aggregate metric DataFrame (same schema as ``score()``)
    - per-target metric list (same schema as ``score_by_target()``)
    """
    if samples.empty:
        return pd.DataFrame(), []

    sog = samples["sog"].fillna(0.0).to_numpy(dtype=float)
    underway = sog >= _MIN_SOG_KN

    if model == "naive":
        gc = samples["gc_dist_nm"].to_numpy(dtype=float)
        pred_h = np.where(underway & (sog > 0), gc / sog, np.nan)
        has_interval = False

    elif model == "naive+route":
        route = samples["route_dist_nm"].to_numpy(dtype=float)
        gc = samples["gc_dist_nm"].to_numpy(dtype=float)
        dist = np.where(np.isfinite(route), route, gc)
        pred_h = np.where(underway & (sog > 0), dist / sog, np.nan)
        has_interval = False

    elif model == "physics_v1":
        from analytics.eta_physics import vectorized_physics_p50

        pred_h = vectorized_physics_p50(samples)
        has_interval = interval is not None and interval.fitted

    else:
        raise ValueError(f"score_vectorized: unknown model '{model}'")

    scored = samples.copy()
    scored["pred_h"] = pred_h
    scored = scored[np.isfinite(scored["pred_h"])].copy()
    if scored.empty:
        return pd.DataFrame(), []

    scored["err_h"] = scored["pred_h"] - scored["remaining_h"]

    if has_interval:
        p50_np = scored["pred_h"].to_numpy(dtype=float)
        lo_off, hi_off = interval.offsets_batch(p50_np)
        scored["_lo"] = np.maximum(0.0, p50_np + lo_off)
        scored["_hi"] = p50_np + hi_off
        scored["covered"] = (
            (scored["remaining_h"] >= scored["_lo"]) & (scored["remaining_h"] <= scored["_hi"])
        ).astype(float)
    else:
        scored["covered"] = np.nan

    agg = pd.DataFrame(_metric_rows(scored, model, run_ts))
    per_tgt = _metric_rows_by_target(scored, model, run_ts)
    return agg, per_tgt


def _ensure_lead_basis(conn: duckdb.DuckDBPyConnection) -> None:
    """Migrate a pre-`lead_basis` eta_model_metrics table in place, once.

    The hourly build seeds its scratch DB by copying the live one forward, so a
    table created under the old schema keeps its old four-column PK and lacks
    ``lead_basis`` even after ``CREATE TABLE IF NOT EXISTS`` runs. Recreate it with
    the new PK, tagging existing rows: the unconditional rollups
    (``lead_bucket='all'``) become ``lead_basis='all'`` and every other historical
    row was bucketed by actual remaining time, so it becomes ``lead_basis='actual'``.
    History (69+ scored runs) is preserved verbatim.
    """
    cols = [d[0] for d in conn.execute("SELECT * FROM eta_model_metrics LIMIT 0").description]
    if "lead_basis" in cols:
        return
    conn.execute("ALTER TABLE eta_model_metrics RENAME TO _eta_model_metrics_legacy")
    conn.execute(
        """
        CREATE TABLE eta_model_metrics (
            run_ts TIMESTAMP, model VARCHAR, lead_bucket VARCHAR, target_type VARCHAR,
            lead_basis VARCHAR, n INTEGER, med_abs_err_h DOUBLE, bias_h DOUBLE,
            mape DOUBLE, p90_abs_err_h DOUBLE, interval_coverage DOUBLE,
            PRIMARY KEY (run_ts, model, lead_bucket, target_type, lead_basis)
        )
        """
    )
    conn.execute(
        "INSERT INTO eta_model_metrics "
        "SELECT run_ts, model, lead_bucket, target_type, "
        "  CASE WHEN lead_bucket = 'all' THEN 'all' ELSE 'actual' END AS lead_basis, "
        "  n, med_abs_err_h, bias_h, mape, p90_abs_err_h, interval_coverage "
        "FROM _eta_model_metrics_legacy"
    )
    conn.execute("DROP TABLE _eta_model_metrics_legacy")


def write_metrics(conn: duckdb.DuckDBPyConnection, metrics: pd.DataFrame) -> None:
    """Persist a metric table into eta_model_metrics (idempotent per run_ts)."""
    conn.execute(ETA_SCHEMA)
    _ensure_lead_basis(conn)
    for r in metrics.to_dict("records"):
        conn.execute(
            "INSERT OR REPLACE INTO eta_model_metrics "
            "(run_ts, model, lead_bucket, target_type, lead_basis, n, med_abs_err_h, "
            " bias_h, mape, p90_abs_err_h, interval_coverage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                r["run_ts"],
                r["model"],
                r["lead_bucket"],
                r["target_type"],
                r.get("lead_basis", "actual"),
                r["n"],
                r["med_abs_err_h"],
                r["bias_h"],
                r["mape"],
                r["p90_abs_err_h"],
                r["interval_coverage"],
            ],
        )


# Committed reference artifact (the analytics DuckDB itself is gitignored).
_BASELINE_DIR = Path(__file__).resolve().parent / "baselines"


def export_baseline(metrics: pd.DataFrame, model: str) -> Path:
    """Write a model's metric table to a committed CSV reference artifact."""
    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    path = _BASELINE_DIR / f"eta_{model}_baseline.csv"
    cols = [
        "model",
        "target_type",
        "lead_bucket",
        "n",
        "med_abs_err_h",
        "bias_h",
        "mape",
        "p90_abs_err_h",
        "interval_coverage",
    ]
    # The committed reference snapshot is the by-actual scoreboard (its original
    # framing); keep the predicted-basis rows out of the frozen CSV.
    if "lead_basis" in metrics.columns:
        metrics = metrics[metrics["lead_basis"].isin(["actual", "all"])]
    out = metrics[cols].copy()
    for c in ["med_abs_err_h", "bias_h", "mape", "p90_abs_err_h", "interval_coverage"]:
        out[c] = out[c].round(3)
    out.to_csv(path, index=False)
    log.info("wrote baseline artifact %s", path)
    return path


def _print_table(metrics: pd.DataFrame) -> None:
    if metrics.empty:
        print("(no samples - eta_arrivals empty? run analytics.eta_labels first)")
        return
    cols = [
        "model",
        "target_type",
        "lead_bucket",
        "n",
        "med_abs_err_h",
        "bias_h",
        "mape",
        "p90_abs_err_h",
    ]
    show = metrics[cols].copy()
    for c in ["med_abs_err_h", "bias_h", "mape", "p90_abs_err_h"]:
        show[c] = show[c].round(2)
    print(show.to_string(index=False))


def run() -> pd.DataFrame:
    """Standalone entry: rebuild the naive baseline table and persist it."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        samples = build_samples(conn, _default_ais_query)
        log.info("built %d approach samples", len(samples))
        _, test = voyage_split(samples)  # naive: score the full set
        metrics = score(test, naive_eta_fn, model="naive")
        write_metrics(conn, metrics)
    finally:
        conn.close()
    if not metrics.empty:
        export_baseline(metrics, "naive")
    _print_table(metrics)
    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Score the naive ETA baseline").parse_args()
    run()
