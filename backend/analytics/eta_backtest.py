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


def lead_bucket(remaining_h: float) -> str:
    """Return the lead-time bucket label for an actual remaining time (hours)."""
    for i in range(len(_LEAD_LABELS)):
        if _LEAD_EDGES[i] <= remaining_h < _LEAD_EDGES[i + 1]:
            return _LEAD_LABELS[i]
    return _LEAD_LABELS[-1]


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
        "SELECT mmsi, snapshot_ts, lat, lon, sog FROM ais_snapshots "
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
            # Thin to ~1 fix per cadence bucket (keep first fix in each bucket).
            bucket = np.floor(remaining_h / _SAMPLE_CADENCE_H).astype(int)
            keep = np.concatenate(([True], np.diff(bucket) != 0))
            w = window[keep]
            rem = remaining_h[keep]
            gc = haversine_nm_vec(w["lat"].values, w["lon"].values, arr.t_lat, arr.t_lon)
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
    """Aggregate signed/abs error into the lead-bucket x target-type table."""
    out: list[dict] = []
    if scored.empty:
        return out

    def agg(g: pd.DataFrame, lead: str, ttype: str) -> dict:
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
            "n": int(len(g)),
            "med_abs_err_h": float(np.median(abs_err)),
            "bias_h": float(np.median(err)),
            "mape": mape,
            "p90_abs_err_h": float(np.percentile(abs_err, 90)),
            "interval_coverage": float(cov.mean()) if not cov.empty else float("nan"),
        }

    for ttype in ["chokepoint", "port"]:
        sub = scored[scored["target_type"] == ttype]
        for lead in _LEAD_LABELS:
            g = sub[sub["lead_bucket"] == lead]
            if not g.empty:
                out.append(agg(g, lead, ttype))
    # 'all' target_type rollup per lead bucket + an overall row.
    for lead in _LEAD_LABELS:
        g = scored[scored["lead_bucket"] == lead]
        if not g.empty:
            out.append(agg(g, lead, "all"))
    out.append(agg(scored, "all", "all"))
    return out


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
        return pd.DataFrame()
    scored["err_h"] = scored["pred_h"] - scored["remaining_h"]
    if has_interval:
        scored["covered"] = (
            (scored["remaining_h"] >= scored["_lo"]) & (scored["remaining_h"] <= scored["_hi"])
        ).astype(float)
    else:
        scored["covered"] = np.nan

    return pd.DataFrame(_metric_rows(scored, model, run_ts))


def write_metrics(conn: duckdb.DuckDBPyConnection, metrics: pd.DataFrame) -> None:
    """Persist a metric table into eta_model_metrics (idempotent per run_ts)."""
    conn.execute(ETA_SCHEMA)
    for r in metrics.to_dict("records"):
        conn.execute(
            "INSERT OR REPLACE INTO eta_model_metrics "
            "(run_ts, model, lead_bucket, target_type, n, med_abs_err_h, bias_h, "
            " mape, p90_abs_err_h, interval_coverage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                r["run_ts"],
                r["model"],
                r["lead_bucket"],
                r["target_type"],
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
