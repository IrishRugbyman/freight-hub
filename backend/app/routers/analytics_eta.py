"""Analytics: True ETA, predicted destination, accuracy scoreboard and upcoming arrivals."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd
from fastapi import APIRouter

from .. import db
from .. import runner_destination as _runner_destination
from .. import runner_eta as _runner_eta
from ..common import iso, str_or_none
from ..schemas import (
    ArrivalsResponse,
    ArrivalTarget,
    DestinationCandidate,
    DestinationResponse,
    EtaAccuracyResponse,
    EtaAccuracyRow,
    EtaByTargetResponse,
    EtaByTargetRow,
    EtaDriftAlert,
    EtaPrediction,
    EtaResponse,
    EtaTrendPoint,
    EtaTrendResponse,
    UpcomingArrivalsResponse,
    UpcomingVessel,
)

router = APIRouter()


@router.get("/api/analytics/eta", response_model=EtaResponse, tags=["analytics"])
def analytics_eta(mmsi: int):
    """True ETA for one vessel to every target it is resolvably heading toward.

    Serves the physics ETA + calibrated [P10, P90] interval the analytics job
    precomputed into `eta_predictions` (True ETA Phase E), one entry per resolved
    chokepoint / port target, soonest arrival first. Each entry carries the naive
    great-circle baseline (`eta_naive_h`) and the method label for transparency.
    The free-text destination is never used: targets are geometric.
    """
    preds = _runner_eta.vessel_predictions(mmsi)
    now_iso = datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat()
    return EtaResponse(
        mmsi=int(mmsi),
        as_of=now_iso,
        n=len(preds),
        predictions=[
            EtaPrediction(**{k: p.get(k) for k in EtaPrediction.model_fields}) for p in preds
        ],
    )


@router.get("/api/analytics/destination", response_model=DestinationResponse, tags=["analytics"])
def analytics_destination(mmsi: int):
    """Predicted destination for one vessel: ranked candidate ports + probability.

    Serves the candidate shortlist the analytics job precomputed into
    `destination_predictions` (heuristic geometry+history score, or the LightGBM
    challenger where it has earned promotion over the heuristic on held-out
    accuracy). The AIS-reported destination is one candidate among several, never
    trusted blindly - `disagrees_with_reported` is True when the model's top pick
    is a *different* real port than what the crew reported, the reroute signal.
    """
    cands = _runner_destination.vessel_destination(mmsi)
    now_iso = datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat()
    disagrees = (
        bool(cands) and not cands[0]["reported_match"] and any(c["reported_match"] for c in cands)
    )
    return DestinationResponse(
        mmsi=int(mmsi),
        as_of=now_iso,
        n=len(cands),
        disagrees_with_reported=disagrees,
        candidates=[
            DestinationCandidate(**{k: c.get(k) for k in DestinationCandidate.model_fields})
            for c in cands
        ],
    )


_ETA_LEAD_ORDER = ["0-6h", "6-12h", "12-24h", "24-48h", "48h+", "all"]
# Baseline-first model order so the scoreboard reads naive -> routed -> physics.
_ETA_MODEL_ORDER = ["naive", "naive+route", "physics_v1", "ml"]


@router.get("/api/analytics/eta-accuracy", response_model=EtaAccuracyResponse, tags=["analytics"])
def analytics_eta_accuracy(target_type: str = "all", lead_basis: str = "actual"):
    """Latest leakage-free backtest scoreboard: model error by lead bucket.

    Serves `eta_model_metrics` from the most recent scored run (True ETA Phases
    A-C), the credibility centerpiece: median |err|, bias, P90 |err| and interval
    coverage for each model (naive -> +route -> physics) across lead buckets.
    `target_type` filters to 'chokepoint' | 'port' | 'all'. `lead_basis` selects how
    the per-bucket rows are conditioned: 'actual' (true remaining time, the original
    framing) or 'predicted' (the model's own served ETA, what a user sees at decision
    time). The unconditional overall rows (lead_bucket='all') are returned regardless.
    """
    lead_basis = lead_basis if lead_basis in ("actual", "predicted") else "actual"
    # Each model's most recent scored run (the three baselines may carry slightly
    # different run_ts on older data, so a single global max() would drop some).
    try:
        df = db.query(
            "SELECT m.model, m.lead_bucket, m.target_type, m.lead_basis, m.n, m.med_abs_err_h, "
            "       m.bias_h, m.p90_abs_err_h, m.interval_coverage "
            "FROM eta_model_metrics m "
            "JOIN (SELECT model, max(run_ts) AS rt FROM eta_model_metrics GROUP BY model) latest "
            "  ON m.model = latest.model AND m.run_ts = latest.rt "
            "WHERE m.target_type = ? AND m.lead_basis IN ('all', ?)",
            [target_type, lead_basis],
            db=db.analytics_db_path(),
        )
    except duckdb.BinderException:
        # Live DB predates the lead_basis migration (next build adds it). Serve the
        # legacy by-actual scoreboard so the endpoint never 500s during that window.
        df = db.query(
            "SELECT m.model, m.lead_bucket, m.target_type, m.n, m.med_abs_err_h, "
            "       m.bias_h, m.p90_abs_err_h, m.interval_coverage "
            "FROM eta_model_metrics m "
            "JOIN (SELECT model, max(run_ts) AS rt FROM eta_model_metrics GROUP BY model) latest "
            "  ON m.model = latest.model AND m.run_ts = latest.rt "
            "WHERE m.target_type = ?",
            [target_type],
            db=db.analytics_db_path(),
        )
        if not df.empty:
            df["lead_basis"] = df["lead_bucket"].map(lambda b: "all" if b == "all" else "actual")
    if df.empty:
        return EtaAccuracyResponse(run_ts=None, models=[], lead_order=_ETA_LEAD_ORDER, rows=[])

    run_ts_df = db.query(
        "SELECT max(run_ts) AS rt FROM eta_model_metrics",
        db=db.analytics_db_path(),
    )
    run_ts = None
    if not run_ts_df.empty and run_ts_df.iloc[0]["rt"] is not None:
        rt = run_ts_df.iloc[0]["rt"]
        run_ts = rt.isoformat() if hasattr(rt, "isoformat") else str(rt)

    present = list(df["model"].unique())
    models = [m for m in _ETA_MODEL_ORDER if m in present] + [
        m for m in present if m not in _ETA_MODEL_ORDER
    ]

    def _num(v):
        return float(v) if v is not None and not pd.isna(v) else None

    rows = [
        EtaAccuracyRow(
            model=str(r["model"]),
            lead_bucket=str(r["lead_bucket"]),
            target_type=str(r["target_type"]),
            lead_basis=str(r.get("lead_basis", "actual")),
            n=int(r["n"]),
            med_abs_err_h=_num(r["med_abs_err_h"]),
            bias_h=_num(r["bias_h"]),
            p90_abs_err_h=_num(r["p90_abs_err_h"]),
            interval_coverage=_num(r["interval_coverage"]),
        )
        for _, r in df.iterrows()
    ]
    # Order rows by model then chronological lead bucket for a stable scoreboard.
    lead_rank = {b: i for i, b in enumerate(_ETA_LEAD_ORDER)}
    model_rank = {m: i for i, m in enumerate(models)}
    rows.sort(key=lambda x: (model_rank.get(x.model, 99), lead_rank.get(x.lead_bucket, 99)))

    return EtaAccuracyResponse(
        run_ts=run_ts,
        models=models,
        lead_order=_ETA_LEAD_ORDER,
        rows=rows,
        lead_basis=lead_basis,
        drift=_eta_drift_alerts(),
    )


def _eta_drift_alerts() -> list[EtaDriftAlert]:
    """Active drift alerts from the most recent run (True ETA Phase G).

    Returns an empty list if the monitoring table does not exist yet (older DBs)
    or the latest run was clean. Only the latest run's alerts are surfaced, so a
    transient past degradation that has since recovered does not linger.
    """
    try:
        drift_df = db.query(
            "SELECT run_ts, model, kind, severity, metric, reference, detail "
            "FROM eta_drift_alerts "
            "WHERE run_ts = (SELECT max(run_ts) FROM eta_drift_alerts) "
            "ORDER BY severity, kind",
            db=db.analytics_db_path(),
        )
    except Exception:
        return []
    out = []
    for _, r in drift_df.iterrows():
        rt = r["run_ts"]
        out.append(
            EtaDriftAlert(
                run_ts=rt.isoformat() if hasattr(rt, "isoformat") else str(rt),
                model=str(r["model"]),
                kind=str(r["kind"]),
                severity=str(r["severity"]),
                metric=float(r["metric"]),
                reference=float(r["reference"]),
                detail=str(r["detail"]),
            )
        )
    return out


@router.get("/api/analytics/eta-trend", response_model=EtaTrendResponse, tags=["analytics"])
def analytics_eta_trend():
    """Accuracy trend: overall MAE for each model per run (choronological).

    Shows how the physics model's accuracy evolves as the sample set grows.
    Uses the ``all`` lead-bucket + ``all`` target-type aggregate row from
    ``eta_model_metrics``, so each run contributes one data point per model.
    Useful for visualizing the data flywheel building toward the ML unlock.
    """
    try:
        df = db.query(
            "SELECT run_ts, model, n, med_abs_err_h "
            "FROM eta_model_metrics "
            "WHERE lead_bucket = 'all' AND target_type = 'all' "
            "ORDER BY run_ts",
            db=db.analytics_db_path(),
        )
    except Exception:
        return EtaTrendResponse(points=[])
    if df.empty:
        return EtaTrendResponse(points=[])

    # Pivot: one row per run_ts with one column per model
    grouped = df.groupby("run_ts")
    points: list[EtaTrendPoint] = []
    for run_ts_val, g in grouped:
        by_model = g.set_index("model")

        # by_model is bound as a default rather than closed over: it is rebound
        # every iteration, and a closure that captured it by reference would read
        # whichever group happened to be current at call time. Correct today only
        # because every call happens inside the same iteration - which is exactly
        # the kind of accident that survives until someone defers the call.
        def _mae(model_name: str, by_model: pd.DataFrame = by_model) -> float | None:
            if model_name not in by_model.index:
                return None
            v = by_model.at[model_name, "med_abs_err_h"]
            try:
                return float(v) if v == v else None  # NaN -> None
            except (TypeError, ValueError):
                return None

        n_val = 0
        if "physics_v1" in by_model.index:
            n_val = int(by_model.at["physics_v1", "n"] or 0)
        elif "naive" in by_model.index:
            n_val = int(by_model.at["naive", "n"] or 0)

        rt_str = run_ts_val.isoformat() if hasattr(run_ts_val, "isoformat") else str(run_ts_val)
        points.append(
            EtaTrendPoint(
                run_ts=rt_str,
                naive_mae=_mae("naive"),
                route_mae=_mae("naive+route"),
                physics_mae=_mae("physics_v1"),
                n=n_val,
            )
        )
    return EtaTrendResponse(points=points)


@router.get("/api/analytics/eta-by-target", response_model=EtaByTargetResponse, tags=["analytics"])
def analytics_eta_by_target():
    """Per-target physics_v1 accuracy vs naive baseline.

    Reads ``eta_metrics_by_target`` (populated by the hourly analytics build once
    at least one scored run has landed). Returns physics_v1 rows enriched with the
    naive baseline MAE for the same target so the frontend can render the
    improvement delta. Rows sorted best -> worst by physics median absolute error.
    """
    try:
        df = db.query(
            "SELECT model, target_id, n, med_abs_err_h, bias_h, p90_abs_err_h, "
            "       mape, interval_coverage, run_ts "
            "FROM eta_metrics_by_target "
            "WHERE run_ts = (SELECT max(run_ts) FROM eta_metrics_by_target) "
            "ORDER BY model, target_id",
            db=db.analytics_db_path(),
        )
    except Exception:
        return EtaByTargetResponse(run_ts=None, rows=[])
    if df.empty:
        return EtaByTargetResponse(run_ts=None, rows=[])

    run_ts_val = df["run_ts"].iloc[0]
    run_ts_str = run_ts_val.isoformat() if hasattr(run_ts_val, "isoformat") else str(run_ts_val)

    try:
        targets_df = db.query(
            "SELECT target_id, name, target_type, is_canal FROM eta_targets",
            db=db.analytics_db_path(),
        )
        target_meta = {r["target_id"]: r for _, r in targets_df.iterrows()}
    except Exception:
        target_meta = {}

    naive_rows = df[df["model"] == "naive"].set_index("target_id")
    physics_rows = df[df["model"] == "physics_v1"]

    rows: list[EtaByTargetRow] = []
    for _, r in physics_rows.iterrows():
        tid = str(r["target_id"])
        meta = target_meta.get(tid, {})
        naive_mae: float | None = None
        if tid in naive_rows.index:
            v = naive_rows.at[tid, "med_abs_err_h"]
            naive_mae = (
                float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
            )

        def _float(x) -> float | None:
            try:
                v = float(x)
                return None if math.isnan(v) else v
            except (TypeError, ValueError):
                return None

        rows.append(
            EtaByTargetRow(
                target_id=tid,
                name=str(meta.get("name", tid.split(":")[-1])),
                target_type=str(meta.get("target_type", "port")),
                is_canal=bool(meta.get("is_canal", False)),
                n=int(r["n"]),
                med_abs_err_h=_float(r["med_abs_err_h"]),
                bias_h=_float(r["bias_h"]),
                p90_abs_err_h=_float(r["p90_abs_err_h"]),
                mape=_float(r["mape"]),
                interval_coverage=_float(r["interval_coverage"]),
                naive_med_abs_err_h=naive_mae,
            )
        )

    rows.sort(key=lambda x: x.med_abs_err_h if x.med_abs_err_h is not None else float("inf"))
    return EtaByTargetResponse(run_ts=run_ts_str, rows=rows)


@router.get(
    "/api/analytics/eta-upcoming", response_model=UpcomingArrivalsResponse, tags=["analytics"]
)
def analytics_eta_upcoming(
    horizon_h: int = 96, target_id: str | None = None, target_type: str = "all"
):
    """Predicted inbound vessels arriving within ``horizon_h`` hours.

    Reads live ``eta_predictions`` (updated hourly) and joins with ``eta_targets``
    for names, plus the live AIS feed for current position / SOG. Only includes
    vessels whose physics_v1 P50 ETA falls within the horizon.

    ``target_id`` filters to a single target (e.g. 'cp:suez'). ``target_type``
    filters by type ('chokepoint' | 'port' | 'all').
    """
    try:
        now_dt = datetime.now(UTC).replace(tzinfo=None)

        pred_df = db.query(
            "SELECT p.mmsi, p.target_id, p.as_of, p.route_dist_nm, "
            "       p.eta_low_h AS eta_p10_h, p.eta_p50_h, p.eta_high_h AS eta_p90_h, "
            "       (epoch(p.as_of) + p.eta_p50_h * 3600 - epoch(now()))::DOUBLE / 3600 "
            "           AS remaining_p50_h "
            "FROM eta_predictions p "
            "WHERE p.eta_p50_h IS NOT NULL "
            "  AND (epoch(p.as_of) + p.eta_p50_h * 3600 - epoch(now())) / 3600.0 >= 0.25 "
            "  AND epoch(p.as_of) + p.eta_p50_h * 3600 - epoch(now()) <= ? * 3600",
            [float(horizon_h)],
            db=db.analytics_db_path(),
        )
    except Exception:
        return UpcomingArrivalsResponse(
            as_of=datetime.now().isoformat(timespec="seconds"),
            horizon_h=horizon_h,
            target_id=target_id,
            total=0,
            rows=[],
        )

    if pred_df.empty:
        return UpcomingArrivalsResponse(
            as_of=datetime.now().isoformat(timespec="seconds"),
            horizon_h=horizon_h,
            target_id=target_id,
            total=0,
            rows=[],
        )

    # Join with target metadata
    try:
        targets_df = db.query(
            "SELECT target_id, name, target_type FROM eta_targets",
            db=db.analytics_db_path(),
        )
        target_meta = {r["target_id"]: r for _, r in targets_df.iterrows()}
    except Exception:
        target_meta = {}

    # Apply target_id / target_type filters
    if target_id:
        pred_df = pred_df[pred_df["target_id"] == target_id]
    if target_type != "all":
        valid_tids = {tid for tid, m in target_meta.items() if m.get("target_type") == target_type}
        pred_df = pred_df[pred_df["target_id"].isin(valid_tids)]

    # Join live AIS early to filter out Small segment before building rows
    try:
        stale_cutoff = now_dt - timedelta(hours=db.STALE_HOURS)
        _live_pre = db.query(
            "SELECT mmsi, segment FROM live_positions WHERE updated_ts >= ?",
            [stale_cutoff],
            db=db.db_path(),
        )
        if not _live_pre.empty:
            _small_mmsis = set(
                _live_pre[_live_pre["segment"] == "Small"]["mmsi"].dropna().astype(int).tolist()
            )
            if _small_mmsis:
                pred_df = pred_df[~pred_df["mmsi"].astype(int).isin(_small_mmsis)]
    except Exception:
        pass

    if pred_df.empty:
        return UpcomingArrivalsResponse(
            as_of=now_dt.isoformat(timespec="seconds"),
            horizon_h=horizon_h,
            target_id=target_id,
            total=0,
            rows=[],
        )

    # Join live AIS for vessel metadata + current position
    try:
        stale_cutoff = now_dt - timedelta(hours=db.STALE_HOURS)
        live_df = db.query(
            "SELECT mmsi, name, kind, segment, draught, lat, lon, sog "
            "FROM live_positions "
            "WHERE updated_ts >= ?",
            [stale_cutoff],
            db=db.db_path(),
        )
        live_meta: dict[int, dict] = {}
        for _, r in live_df.iterrows():
            live_meta[int(r["mmsi"])] = {
                "name": r.get("name"),
                "kind": r.get("kind"),
                "segment": r.get("segment"),
                "draught": r.get("draught"),
                "lat": r.get("lat"),
                "lon": r.get("lon"),
                "sog": r.get("sog"),
            }
    except Exception:
        live_meta = {}

    rows: list[UpcomingVessel] = []
    for _, r in pred_df.iterrows():
        mmsi = int(r["mmsi"])
        tid = str(r["target_id"])
        meta = target_meta.get(tid, {})
        ais = live_meta.get(mmsi, {})

        def _fn(x) -> float | None:
            try:
                v = float(x)
                return None if math.isnan(v) else v
            except (TypeError, ValueError):
                return None

        # Compute remaining hours for P10/P90 by offsetting P50 prediction width.
        # P10/P90 remaining = remaining_p50 +/- the original quantile width.
        # Clamp P10 to 0 (remaining can't be negative) but keep P90 unclamped
        # so callers can see the full uncertainty range.
        rem = _fn(r["remaining_p50_h"])
        p50_orig = _fn(r["eta_p50_h"])
        p10_orig = _fn(r["eta_p10_h"])
        p90_orig = _fn(r["eta_p90_h"])
        if rem is not None and p50_orig is not None:
            p10_rem = max(0.0, rem - (p50_orig - p10_orig)) if p10_orig is not None else None
            p90_rem = rem + (p90_orig - p50_orig) if p90_orig is not None else None
        else:
            p10_rem = None
            p90_rem = None

        # Derive laden from draught + segment-specific threshold (bool | None)
        seg_s = str_or_none(ais.get("segment")) or ""
        d_raw = ais.get("draught")
        d_f = float(d_raw) if d_raw is not None and str(d_raw) not in ("", "nan") else None
        from analytics.zones import DESIGN_DRAUGHT as _DD2

        design = float(_DD2.get(seg_s, 0) or 0)
        if d_f is not None and d_f > 0:
            if design > 0:
                laden_b: bool | None = (
                    True if d_f >= 0.80 * design else (False if d_f <= 0.65 * design else None)
                )
            else:
                laden_b = d_f > 5
        else:
            laden_b = None

        rows.append(
            UpcomingVessel(
                mmsi=mmsi,
                name=ais.get("name") or None,
                segment=seg_s or None,
                laden=laden_b,
                target_id=tid,
                target_name=str(meta.get("name", tid.split(":")[-1])),
                target_type=str(meta.get("target_type", "port")),
                remaining_h=rem if rem is not None else 0.0,
                eta_p10_h=p10_rem,
                eta_p90_h=p90_rem,
                route_dist_nm=_fn(r["route_dist_nm"]),
                sog=_fn(ais.get("sog")),
                lat=_fn(ais.get("lat")),
                lon=_fn(ais.get("lon")),
            )
        )

    # Sort by remaining hours ascending (soonest first)
    rows.sort(key=lambda v: v.remaining_h)

    return UpcomingArrivalsResponse(
        as_of=now_dt.isoformat(timespec="seconds"),
        horizon_h=horizon_h,
        target_id=target_id,
        total=len(rows),
        rows=rows,
    )


@router.get("/api/analytics/arrivals", response_model=ArrivalsResponse, tags=["analytics"])
def analytics_arrivals(days: int = 14, target_type: str = "all", top_n: int = 20):
    """Ground-truth arrival ranking: where vessels *actually* arrived, mined from AIS.

    Distinct from `/api/analytics/ports`, which ranks the free-text *stated*
    destination (garbage-in). This reads `eta_arrivals` (True ETA Phase A): per
    resolved chokepoint/port target, one closest-approach arrival per voyage
    episode, deduplicated. Returns the busiest targets over the window with their
    distinct-vessel count, laden share, dominant segment and last-seen arrival,
    plus window totals. `target_type` filters to 'chokepoint' | 'port' | 'all'.
    """
    days = max(1, min(90, days))
    top_n = max(1, min(100, top_n))
    if target_type not in ("all", "chokepoint", "port"):
        target_type = "all"
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    _db = db.analytics_db_path()

    type_clause = "" if target_type == "all" else "AND t.target_type = ?"
    params: list = [cutoff]
    if target_type != "all":
        params.append(target_type)
    params.append(top_n)

    try:
        df = db.query(
            "SELECT a.target_id, t.name, t.target_type, t.is_canal, "
            "       COUNT(*) AS arrivals, "
            "       COUNT(DISTINCT a.mmsi) AS vessels, "
            "       AVG(CASE WHEN a.laden THEN 1.0 WHEN a.laden IS NOT NULL THEN 0.0 END) AS laden_share, "
            "       mode(a.segment) AS top_segment, "
            "       MAX(a.arrival_ts) AS last_arrival_ts "
            "FROM eta_arrivals a JOIN eta_targets t USING(target_id) "
            f"WHERE a.arrival_ts >= ? AND a.segment != 'Small' {type_clause} "
            "GROUP BY a.target_id, t.name, t.target_type, t.is_canal "
            "ORDER BY arrivals DESC, vessels DESC "
            "LIMIT ?",
            params,
            db=_db,
        )
    except Exception:
        # eta_arrivals/eta_targets absent on an older analytics DB
        df = pd.DataFrame()

    now_iso = datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat()
    if df.empty:
        return ArrivalsResponse(
            as_of=now_iso,
            window_days=days,
            target_type=target_type,
            total_arrivals=0,
            total_vessels=0,
            rows=[],
        )

    def _share(v):
        return round(float(v), 3) if v is not None and not pd.isna(v) else None

    rows = [
        ArrivalTarget(
            target_id=str(r["target_id"]),
            name=str(r["name"]),
            target_type=str(r["target_type"]),
            is_canal=bool(r["is_canal"]),
            arrivals=int(r["arrivals"]),
            vessels=int(r["vessels"]),
            laden_share=_share(r["laden_share"]),
            top_segment=str_or_none(r["top_segment"]),
            last_arrival_ts=iso(r["last_arrival_ts"]),
        )
        for _, r in df.iterrows()
    ]

    # Window totals respect the same target_type filter but are NOT capped by top_n.
    tot_params: list = [cutoff]
    if target_type != "all":
        tot_params.append(target_type)
    tot = db.query(
        "SELECT COUNT(*) AS arrivals, COUNT(DISTINCT a.mmsi) AS vessels "
        "FROM eta_arrivals a JOIN eta_targets t USING(target_id) "
        f"WHERE a.arrival_ts >= ? {type_clause}",
        tot_params,
        db=_db,
    )
    total_arrivals = int(tot.iloc[0]["arrivals"]) if not tot.empty else 0
    total_vessels = int(tot.iloc[0]["vessels"]) if not tot.empty else 0

    return ArrivalsResponse(
        as_of=now_iso,
        window_days=days,
        target_type=target_type,
        total_arrivals=total_arrivals,
        total_vessels=total_vessels,
        rows=rows,
    )
