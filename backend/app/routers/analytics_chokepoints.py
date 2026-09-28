"""Analytics: chokepoint transits, congestion, heatmaps and status."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter

from .. import db
from ..common import iso, str_or_none
from ..live import live_all
from ..schemas import (
    ChokepointAnomalyResponse,
    ChokepointAnomalyRow,
    ChokepointCongestionResponse,
    ChokepointCongestionRow,
    ChokepointHeatmapCell,
    ChokepointHeatmapResponse,
    ChokepointStatusResponse,
    ChokepointStatusRow,
    TransitDay,
    TransitRatePoint,
    TransitRateTimelineResponse,
    TransitsResponse,
)

router = APIRouter()


@router.get("/api/analytics/transits", response_model=TransitsResponse)
def analytics_transits(chokepoint: str = "suez", days: int = 30):
    """Daily chokepoint transit counts grouped by direction and vessel kind."""
    d = max(1, min(days, 365))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)
    df = db.query(
        "SELECT entered_ts, direction, kind FROM transit_events "
        "WHERE chokepoint = ? AND entered_ts >= ? AND segment != 'Small'",
        [chokepoint, cutoff],
        db=db.analytics_db_path(),
    )
    series = []
    if not df.empty:
        df["date"] = pd.to_datetime(df["entered_ts"]).dt.date.astype(str)
        for (date_s, direction, kind), grp in df.groupby(["date", "direction", "kind"]):
            series.append(TransitDay(date=date_s, direction=direction, kind=kind, count=len(grp)))
        series.sort(key=lambda r: r.date)
    return TransitsResponse(chokepoint=chokepoint, days=d, series=series)


@router.get("/api/analytics/transit-rate-timeline", response_model=TransitRateTimelineResponse)
def analytics_transit_rate_timeline(hours: int = 72, chokepoints_csv: str = ""):
    """Hourly transit counts per chokepoint over the last N hours.

    chokepoints_csv: comma-separated chokepoint names to filter (empty = all).
    Returns one point per (hour, chokepoint) combination.
    """
    h = max(6, min(hours, 336))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=h)
    df = db.query(
        "SELECT entered_ts, chokepoint, laden FROM transit_events "
        "WHERE entered_ts >= ? AND segment != 'Small' ORDER BY entered_ts",
        [cutoff],
        db=db.analytics_db_path(),
    )
    now = datetime.now(UTC)
    as_of = iso(now) or ""
    if df.empty:
        return TransitRateTimelineResponse(as_of=as_of, hours=h, chokepoints=[], points=[])

    df["entered_ts"] = pd.to_datetime(df["entered_ts"])
    df["hour"] = df["entered_ts"].dt.floor("h")
    df["laden_flag"] = df["laden"].fillna(False).astype(bool).astype(int)

    cp_filter = [c.strip() for c in chokepoints_csv.split(",") if c.strip()]
    if cp_filter:
        df = df[df["chokepoint"].isin(cp_filter)]

    agg = (
        df.groupby(["hour", "chokepoint"])
        .agg(count=("chokepoint", "size"), laden_count=("laden_flag", "sum"))
        .reset_index()
    )
    active_cps = sorted(agg["chokepoint"].unique().tolist())
    points = [
        TransitRatePoint(
            hour=iso(r["hour"]) or str(r["hour"]),
            chokepoint=str(r["chokepoint"]),
            count=int(r["count"]),
            laden_count=int(r["laden_count"]),
        )
        for _, r in agg.iterrows()
    ]
    return TransitRateTimelineResponse(as_of=as_of, hours=h, chokepoints=active_cps, points=points)


def _merged_transit_spans(since: datetime, kind: str = "") -> pd.DataFrame:
    """Merged continuous chokepoint-transit spans since `since`, computed in DuckDB.

    Mirrors `_merged_anchored_spans`: the analytics job's sliding window stores one
    continuous transit as a chain of overlapping closed fragments (entered_ts shifts
    each run for an ongoing transit), so raw transit_events rows would inflate both
    the current dwell and the baseline. Same gaps-and-islands merge, aliased onto
    transit_events' entered_ts/exited_ts/chokepoint columns so the generic
    `current_from_spans` helper (built for anchored_episodes) can be reused as-is.

    Returns columns: mmsi, zone (= chokepoint), start_ts, end_ts, kind, segment.
    """
    kind_cond = " AND kind = ?" if kind else ""
    params: list = [since] + ([kind] if kind else [])
    return db.query(
        "WITH frags AS ("
        "  SELECT mmsi, chokepoint AS zone, entered_ts AS start_ts, exited_ts AS end_ts, "
        "         kind, segment "
        "  FROM transit_events "
        f"  WHERE exited_ts >= ?{kind_cond}"
        "), ordered AS ("
        "  SELECT *, max(end_ts) OVER ("
        "      PARTITION BY mmsi, zone ORDER BY start_ts "
        "      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_max_end "
        "  FROM frags"
        "), flagged AS ("
        "  SELECT *, CASE WHEN prev_max_end IS NULL "
        "                 OR start_ts > prev_max_end + INTERVAL 2 HOUR "
        "            THEN 1 ELSE 0 END AS new_span "
        "  FROM ordered"
        "), spanned AS ("
        "  SELECT *, sum(new_span) OVER ("
        "      PARTITION BY mmsi, zone ORDER BY start_ts "
        "      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS span_id "
        "  FROM flagged"
        ") "
        "SELECT mmsi, zone, min(start_ts) AS start_ts, max(end_ts) AS end_ts, "
        "       any_value(kind) AS kind, any_value(segment) AS segment "
        "FROM spanned GROUP BY mmsi, zone, span_id",
        params,
        db=db.analytics_db_path(),
    )


@router.get("/api/analytics/chokepoint-congestion", response_model=ChokepointCongestionResponse)
def analytics_chokepoint_congestion(kind: str = "", days: int = 14):
    """Chokepoint transit congestion monitor, built from AIS dwell time.

    Same current-vs-baseline design as `/api/analytics/port-congestion`, applied to
    the 9 chokepoints (Suez, Panama, Malacca, Hormuz, etc.) instead of anchorage
    zones: "dwell time" here is how long a vessel spends inside the chokepoint's
    AIS-tagged region while transiting, not pre-transit staging. Vessels currently
    mid-transit are compared against the historical average number of concurrent
    transits and average transit duration for that chokepoint, over the last `days`
    days. Chokepoints with no historical baseline still appear if vessels are
    currently transiting: they get congestion_factor=1.0 (no comparison available).
    """
    from analytics.detect import CURRENT_ANCHOR_WINDOW_H, current_from_spans

    days = max(3, min(90, days))
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    since = now_ts - timedelta(days=days)

    span_df = _merged_transit_spans(since, kind)
    max_end = pd.to_datetime(span_df["end_ts"]).max() if not span_df.empty else None
    spans = span_df.to_dict("records") if not span_df.empty else []
    current = current_from_spans(spans, max_end) if max_end is not None else []

    kind_map: dict[int, str | None] = {}
    if not span_df.empty:
        for _, r in span_df.dropna(subset=["mmsi"]).iterrows():
            kind_map.setdefault(int(r["mmsi"]), str_or_none(r.get("kind")))

    # Build current state: chokepoint -> {vessels, avg_dwell, kind}
    zone_current: dict[str, dict] = {}
    cur_df = pd.DataFrame(current)
    if not cur_df.empty:
        for zone_key, grp in cur_df.groupby("zone"):
            mmsis = [int(m) for m in grp["mmsi"]]
            kinds = [kind_map.get(m) for m in mmsis if kind_map.get(m)]
            zone_current[str(zone_key)] = {
                "current_vessels": int(len(grp)),
                "avg_current_dwell_hours": round(float(grp["dwell_hours"].mean()), 1),
                "kind": kinds[0] if kinds else None,
            }

    # Historical baseline from merged spans: avg concurrent transiting vessels over
    # the window EXCLUDING the present (each span clipped to [since, baseline_end]).
    zone_baseline: dict[str, dict] = {}
    if not span_df.empty and max_end is not None:
        span_df["start_ts"] = pd.to_datetime(span_df["start_ts"])
        span_df["end_ts"] = pd.to_datetime(span_df["end_ts"])
        baseline_end = pd.Timestamp(max_end) - pd.Timedelta(hours=CURRENT_ANCHOR_WINDOW_H)
        since_pd = pd.Timestamp(since)
        obs_hours = max((baseline_end - since_pd).total_seconds() / 3600, 1)
        clip_start = span_df["start_ts"].clip(lower=since_pd)
        clip_end = span_df["end_ts"].clip(upper=baseline_end)
        span_df["overlap_h"] = ((clip_end - clip_start).dt.total_seconds() / 3600).clip(lower=0)
        span_df["dwell_hours"] = (span_df["end_ts"] - span_df["start_ts"]).dt.total_seconds() / 3600
        for zone_key, grp in span_df.groupby("zone"):
            overlap_sum = float(grp["overlap_h"].sum())
            hist = grp[grp["overlap_h"] > 0]["dwell_hours"]
            zone_baseline[str(zone_key)] = {
                "baseline_avg_vessels": round(overlap_sum / obs_hours, 2)
                if overlap_sum > 0
                else None,
                "baseline_avg_dwell_hours": round(float(hist.mean()), 1) if len(hist) else None,
            }

    all_zones = set(zone_current.keys()) | set(zone_baseline.keys())
    rows_out: list[ChokepointCongestionRow] = []
    for z in all_zones:
        cur = zone_current.get(z, {})
        bas = zone_baseline.get(z, {})
        cv = cur.get("current_vessels", 0)
        bav = bas.get("baseline_avg_vessels")
        if bav and bav > 0:
            factor = round(cv / bav, 2)
        else:
            factor = 1.0 if cv > 0 else 0.0

        rows_out.append(
            ChokepointCongestionRow(
                chokepoint=z,
                kind=cur.get("kind"),
                current_vessels=cv,
                avg_current_dwell_hours=cur.get("avg_current_dwell_hours"),
                baseline_avg_vessels=bav,
                baseline_avg_dwell_hours=bas.get("baseline_avg_dwell_hours"),
                congestion_factor=factor,
            )
        )

    rows_out.sort(key=lambda r: (-r.congestion_factor, -r.current_vessels))
    return ChokepointCongestionResponse(
        as_of=iso(now_ts) or "",
        days_baseline=days,
        rows=rows_out,
    )


@router.get("/api/analytics/chokepoint-heatmap", response_model=ChokepointHeatmapResponse)
def analytics_chokepoint_heatmap(
    days: int = 30,
    kind: str | None = None,
):
    """Daily transit counts per chokepoint for the last N days.

    Returns a flat list of (date, chokepoint, total, tanker, bulk) cells suitable
    for rendering as a heatmap or multi-line trend chart.
    """
    days = max(1, min(90, days))
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_ts - timedelta(days=days)

    where_clauses = ["entered_ts >= ?", "segment != 'Small'"]
    params: list = [cutoff]
    if kind:
        where_clauses.append("kind = ?")
        params.append(kind)

    sql = (
        "SELECT strftime(entered_ts, '%Y-%m-%d') AS dt, chokepoint, kind, COUNT(*) AS cnt "
        "FROM transit_events "
        "WHERE " + " AND ".join(where_clauses) + " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
    )
    df = db.query(sql, params, db=db.analytics_db_path())

    if df.empty:
        return ChokepointHeatmapResponse(
            as_of=iso(now_ts) or "",
            days=days,
            kind=kind or "",
            chokepoints=[],
            cells=[],
        )

    # Pivot into (date, chokepoint) -> {tanker, bulk}
    from collections import defaultdict

    cell_map: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"tanker": 0, "bulk": 0})
    cp_totals: dict[str, int] = defaultdict(int)

    for _, r in df.iterrows():
        dt = str(r["dt"])
        cp = str(r["chokepoint"])
        k = str(r["kind"]) if r.get("kind") else "other"
        cnt = int(r["cnt"])
        key = (dt, cp)
        if k == "tanker":
            cell_map[key]["tanker"] += cnt
        elif k == "bulk":
            cell_map[key]["bulk"] += cnt
        cp_totals[cp] += cnt

    chokepoints_ordered = sorted(cp_totals, key=lambda cp: -cp_totals[cp])

    cells: list[ChokepointHeatmapCell] = []
    for (dt, cp), counts in sorted(cell_map.items()):
        cells.append(
            ChokepointHeatmapCell(
                date=dt,
                chokepoint=cp,
                total=counts["tanker"] + counts["bulk"],
                tanker=counts["tanker"],
                bulk=counts["bulk"],
            )
        )

    return ChokepointHeatmapResponse(
        as_of=iso(now_ts) or "",
        days=days,
        kind=kind or "",
        chokepoints=chokepoints_ordered,
        cells=cells,
    )


@router.get("/api/analytics/chokepoint-anomaly", response_model=ChokepointAnomalyResponse)
def analytics_chokepoint_anomaly(window_hours: int = 6, baseline_hours: int = 48):
    """Compare recent chokepoint transit rate vs historical baseline.

    window_hours: recent period to measure (default 6h).
    baseline_hours: historical period for baseline (default 48h).
    Returns Z-score and pct_change per chokepoint; flags high/low/normal.
    """

    window_hours = max(1, min(window_hours, 24))
    baseline_hours = max(6, min(baseline_hours, 336))
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    window_start = now_dt - timedelta(hours=window_hours)
    baseline_start = now_dt - timedelta(hours=window_hours + baseline_hours)

    # Recent transits per chokepoint
    recent_df = db.query(
        "SELECT chokepoint, count(*) AS cnt "
        "FROM transit_events "
        "WHERE entered_ts >= ? AND segment != 'Small' "
        "GROUP BY chokepoint",
        [window_start],
        db=db.analytics_db_path(),
    )

    # Baseline: hourly bucket counts per chokepoint
    baseline_df = db.query(
        "SELECT chokepoint, "
        "  DATE_TRUNC('hour', entered_ts) AS hr, "
        "  count(*) AS cnt "
        "FROM transit_events "
        "WHERE entered_ts >= ? AND entered_ts < ? AND segment != 'Small' "
        "GROUP BY 1, 2",
        [baseline_start, window_start],
        db=db.analytics_db_path(),
    )

    recent_map: dict[str, int] = {}
    if not recent_df.empty:
        for _, r in recent_df.iterrows():
            recent_map[str(r["chokepoint"])] = int(r["cnt"])

    # Build per-chokepoint baseline stats
    baseline_stats: dict[str, tuple[float, float]] = {}
    if not baseline_df.empty:
        for cp, grp in baseline_df.groupby("chokepoint"):
            counts = grp["cnt"].astype(float).tolist()
            n = len(counts)
            if n == 0:
                continue
            avg = sum(counts) / n
            variance = sum((c - avg) ** 2 for c in counts) / n
            std = math.sqrt(variance)
            baseline_stats[str(cp)] = (avg, std)

    # All chokepoints seen in either window
    all_cps = sorted(set(list(recent_map.keys()) + list(baseline_stats.keys())))

    rows = []
    for cp in all_cps:
        recent_count = recent_map.get(cp, 0)
        stats = baseline_stats.get(cp)
        if stats is not None:
            baseline_avg, baseline_std = stats
            if baseline_std > 0:
                z = (recent_count - baseline_avg) / baseline_std
                z_score = round(max(-5.0, min(5.0, z)), 2)
            else:
                z_score = None
            pct_change = (
                round((recent_count - baseline_avg) / max(baseline_avg, 0.01) * 100, 1)
                if baseline_avg > 0
                else None
            )
        else:
            baseline_avg = None
            baseline_std = None
            z_score = None
            pct_change = None

        if z_score is not None:
            if z_score >= 2.0:
                direction = "high"
            elif z_score <= -2.0:
                direction = "low"
            else:
                direction = "normal"
        elif recent_count > 0:
            direction = "no_baseline"
        else:
            direction = "normal"

        rows.append(
            ChokepointAnomalyRow(
                chokepoint=cp,
                recent_count=recent_count,
                baseline_avg=round(baseline_avg, 2) if baseline_avg is not None else None,
                baseline_std=round(baseline_std, 2) if baseline_std is not None else None,
                z_score=z_score,
                pct_change=pct_change,
                direction=direction,
                window_hours=window_hours,
                baseline_hours=baseline_hours,
            )
        )

    rows.sort(key=lambda r: (r.z_score is None, -((r.z_score or 0) ** 2)))

    return ChokepointAnomalyResponse(
        as_of=now_dt.isoformat(),
        window_hours=window_hours,
        baseline_hours=baseline_hours,
        rows=rows,
    )


# Ordered chokepoints to show (only major ones with transit tracking)
_TRACKED_CHOKEPOINTS = [
    "suez",
    "hormuz",
    "singapore_malacca",
    "dover_channel",
    "bosphorus_dardanelles",
    "gibraltar",
    "cape_good_hope",
    "panama",
]

# Forward direction label per chokepoint (for fwd-direction %)
_FWD_DIRECTIONS = {
    "suez": "northbound",
    "hormuz": "eastbound",
    "singapore_malacca": "eastbound",
    "dover_channel": "eastbound",
    "bosphorus_dardanelles": "northbound",
    "gibraltar": "eastbound",
    "cape_good_hope": "eastbound",
    "panama": "northbound",
}


@router.get("/api/analytics/chokepoint-status", response_model=ChokepointStatusResponse)
def analytics_chokepoint_status():
    """
    Live vessel counts and transit statistics per major chokepoint.
    Combines live_positions (current traffic) with transit_events (historical throughput).
    """
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    cutoff_24h = now_dt - timedelta(hours=24)
    cutoff_7d = now_dt - timedelta(days=7)

    # 1. Live vessel counts per region from live_positions cache
    live_df = live_all()
    if not live_df.empty:
        live_df = live_df[live_df["region"].notna() & (live_df["segment"] != "Small")]

    live_by_region: dict[str, dict] = {}
    if not live_df.empty:
        for _, r in live_df.iterrows():
            region = r.get("region")
            if region not in _TRACKED_CHOKEPOINTS:
                continue
            sog_val = r.get("sog")
            sog = (
                float(sog_val)
                if sog_val is not None and not (isinstance(sog_val, float) and pd.isna(sog_val))
                else None
            )
            if region not in live_by_region:
                live_by_region[region] = {"total": 0, "transiting": 0, "waiting": 0}
            live_by_region[region]["total"] += 1
            if sog is not None:
                if sog > 4.0:
                    live_by_region[region]["transiting"] += 1
                elif sog <= 0.5:
                    live_by_region[region]["waiting"] += 1

    # 2. Transit statistics from analytics DB
    transit_df = db.query(
        "SELECT chokepoint, entered_ts, exited_ts, direction "
        "FROM transit_events WHERE entered_ts >= ? AND segment != 'Small' ORDER BY entered_ts",
        [cutoff_7d],
        db=db.analytics_db_path(),
    )

    transit_stats: dict[str, dict] = {}
    if not transit_df.empty:
        for _, r in transit_df.iterrows():
            cp = r.get("chokepoint")
            if cp not in _TRACKED_CHOKEPOINTS:
                continue
            if cp not in transit_stats:
                transit_stats[cp] = {"n_7d": 0, "n_24h": 0, "hours": [], "n_fwd": 0}
            transit_stats[cp]["n_7d"] += 1
            try:
                ts = pd.Timestamp(r["entered_ts"])
                if ts >= pd.Timestamp(cutoff_24h):
                    transit_stats[cp]["n_24h"] += 1
                if r["exited_ts"] is not None:
                    dur_h = (pd.Timestamp(r["exited_ts"]) - ts).total_seconds() / 3600
                    if 0 < dur_h < 200:  # sanity filter
                        transit_stats[cp]["hours"].append(dur_h)
            except Exception:
                pass
            direction = r.get("direction")
            fwd_dir = _FWD_DIRECTIONS.get(cp, "")
            if direction and direction == fwd_dir:
                transit_stats[cp]["n_fwd"] += 1

    # 3. Build response
    rows: list[ChokepointStatusRow] = []
    for cp in _TRACKED_CHOKEPOINTS:
        live = live_by_region.get(cp, {"total": 0, "transiting": 0, "waiting": 0})
        stats = transit_stats.get(cp, {"n_7d": 0, "n_24h": 0, "hours": [], "n_fwd": 0})

        avg_h = round(sum(stats["hours"]) / len(stats["hours"]), 1) if stats["hours"] else None
        n_7d = stats["n_7d"]
        pct_fwd = round(stats["n_fwd"] / n_7d * 100, 1) if n_7d > 0 else None

        rows.append(
            ChokepointStatusRow(
                chokepoint=cp,
                live_total=live["total"],
                live_transiting=live["transiting"],
                live_waiting=live["waiting"],
                avg_transit_h_7d=avg_h,
                n_transits_24h=stats["n_24h"],
                n_transits_7d=n_7d,
                pct_fwd_direction=pct_fwd,
            )
        )

    return ChokepointStatusResponse(as_of=now_dt.isoformat(), rows=rows)
