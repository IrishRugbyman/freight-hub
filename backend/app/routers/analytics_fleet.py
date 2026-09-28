"""Analytics: fleet-wide speed, utilisation, density, trends and market summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter

from quant_lib.freight import flag_from_mmsi

from .. import db
from ..common import fresh_cutoff, iso, str_or_none, valid_imo
from ..live import live_all
from ..schemas import (
    AnalyticsZone,
    DensityDay,
    DensityResponse,
    EventRatePoint,
    EventRateTimelineResponse,
    FleetFlagRow,
    FleetFlagsResponse,
    FleetHistoryResponse,
    FleetHistorySegmentRow,
    FleetTrendDay,
    FleetTrendResponse,
    FleetUtilizationResponse,
    FleetUtilizationRow,
    MarketSegmentSummary,
    MarketSummaryResponse,
    RegionMomentumResponse,
    RegionMomentumRow,
    RegionUtilResponse,
    RegionUtilRow,
    SlowSteamerEvent,
    SlowSteamersResponse,
    SpeedAnalyticsResponse,
    SpeedSegmentRow,
    SpeedTrendPoint,
    SpeedTrendResponse,
)

router = APIRouter()


@router.get("/api/analytics/speed", response_model=SpeedAnalyticsResponse)
def analytics_speed():
    """Fleet speed and utilization by segment, computed from live positions.

    Nav status codes: 0=under way engine, 1=at anchor, 5=moored.
    avg_sog_underway is the mean SOG of nav_status=0 vessels with SOG > 0.2 kn.
    Useful as a demand signal: rising average speed = tighter freight market.
    """
    cutoff = fresh_cutoff()
    df = db.query(
        "SELECT kind, segment, nav_status, sog "
        "FROM live_positions "
        "WHERE updated_ts > ? AND kind IS NOT NULL AND segment IS NOT NULL AND segment != 'Small'",
        [cutoff],
    )
    if df.empty:
        return SpeedAnalyticsResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            total_vessels=0,
            rows=[],
        )

    rows = []
    for (kind, segment), grp in df.groupby(["kind", "segment"]):
        total = len(grp)
        underway = int((grp["nav_status"] == 0).sum())
        anchored = int((grp["nav_status"] == 1).sum())
        moored = int((grp["nav_status"] == 5).sum())
        other = total - underway - anchored - moored
        underway_sog = grp.loc[(grp["nav_status"] == 0) & (grp["sog"] > 0.2), "sog"]
        avg_sog_uw = round(float(underway_sog.mean()), 1) if len(underway_sog) > 0 else None
        p50 = grp["sog"].dropna()
        p50_sog = round(float(p50.median()), 1) if len(p50) > 0 else None
        rows.append(
            SpeedSegmentRow(
                segment=str(segment),
                kind=str(kind),
                underway=underway,
                anchored=anchored,
                moored=moored,
                other=other,
                total=total,
                avg_sog_underway=avg_sog_uw,
                p50_sog=p50_sog,
                pct_underway=round(underway / total * 100, 1) if total > 0 else 0.0,
            )
        )

    rows.sort(key=lambda r: r.total, reverse=True)
    return SpeedAnalyticsResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        total_vessels=len(df),
        rows=rows,
    )


@router.get("/api/analytics/speed-trend", response_model=SpeedTrendResponse)
def analytics_speed_trend(kind: str = "tanker", segment: str | None = None, days: int = 14):
    """Daily average SOG trend for a vessel segment from snapshot history.

    kind: 'tanker' or 'bulk'
    segment: optional filter (VLCC, Suezmax, Capesize, etc.)
    days: clamped 1-90; daily avg computed from ais_snapshots (underway vessels, SOG > 0.2 kn)

    This is a real demand signal: rising VLCC speed indicates stronger crude tanker demand.
    """
    days = max(1, min(90, days))
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    params: list = [since, kind]
    seg_clause = ""
    if segment:
        seg_clause = "AND segment = ?"
        params.append(segment)

    df = db.query(
        f"SELECT CAST(snapshot_ts AS DATE) AS day, sog, nav_status "
        f"FROM ais_snapshots "
        f"WHERE snapshot_ts > ? AND kind = ? {seg_clause}",
        params,
    )

    if df.empty:
        return SpeedTrendResponse(kind=kind, segment=segment, days=days, series=[])

    series = []
    for day, grp in df.groupby("day"):
        total = len(grp)
        underway = grp[(grp["nav_status"] == 0) & (grp["sog"] > 0.2)]
        avg_sog = round(float(underway["sog"].mean()), 2) if len(underway) > 0 else None
        series.append(
            SpeedTrendPoint(
                date=str(day),
                avg_sog=avg_sog,
                underway_count=len(underway),
                total_count=total,
            )
        )

    series.sort(key=lambda p: p.date)
    return SpeedTrendResponse(kind=kind, segment=segment, days=days, series=series)


@router.get("/api/analytics/region-util", response_model=RegionUtilResponse)
def analytics_region_util():
    """Fleet utilization (underway/anchored/moored) per maritime region.

    Aggregates from live positions. High anchor ratios = congestion signal.
    """
    cutoff = fresh_cutoff()
    df = db.query(
        "SELECT region, nav_status, sog "
        "FROM live_positions "
        "WHERE updated_ts > ? AND region IS NOT NULL AND segment != 'Small'",
        [cutoff],
    )
    if df.empty:
        return RegionUtilResponse(as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "", rows=[])

    rows = []
    for region, grp in df.groupby("region"):
        total = len(grp)
        underway = int((grp["nav_status"] == 0).sum())
        anchored = int((grp["nav_status"] == 1).sum())
        moored = int((grp["nav_status"] == 5).sum())
        sog_vals = grp["sog"].dropna()
        avg_sog = round(float(sog_vals.mean()), 1) if len(sog_vals) > 0 else None
        rows.append(
            RegionUtilRow(
                region=str(region),
                total=total,
                underway=underway,
                anchored=anchored,
                moored=moored,
                pct_underway=round(underway / total * 100, 1) if total > 0 else 0.0,
                avg_sog=avg_sog,
            )
        )

    rows.sort(key=lambda r: r.total, reverse=True)
    return RegionUtilResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        rows=rows,
    )


@router.get("/api/analytics/fleet-flags", response_model=FleetFlagsResponse)
def fleet_flags(top_n: int = 40):
    """Live fleet grouped by flag state derived from the MMSI MID.

    Free, ~100%-coverage complement to the Equasis-gated /api/fleet/flag-risk.
    top_n clamped to 5-100. Unresolved MMSIs (coast/SAR/aids, unassigned MIDs)
    are counted, not grouped.
    """
    top_n = max(5, min(100, top_n))
    as_of = iso(datetime.now(UTC).replace(tzinfo=None)) or ""
    df = live_all()
    if not df.empty:
        df = df[df["segment"] != "Small"]
    if df.empty:
        return FleetFlagsResponse(
            as_of=as_of,
            total_with_flag=0,
            total_unresolved=0,
            foc_count=0,
            shadow_count=0,
            rows=[],
        )
    flags = {int(m): flag_from_mmsi(int(m)) for m in df["mmsi"].dropna().unique()}
    unresolved = int(sum(flags.get(int(m)) is None for m in df["mmsi"] if pd.notna(m)))

    rows: list[FleetFlagRow] = []
    foc_total = shadow_total = with_flag = 0
    # Group by resolved flag code.
    grouped: dict[str, list] = {}
    for r in df.itertuples():
        f = flags.get(int(r.mmsi)) if pd.notna(r.mmsi) else None
        if f is None:
            continue
        grouped.setdefault(f.code, []).append((f, r))

    for code, items in grouped.items():
        f0 = items[0][0]
        seg_counts: dict[str, int] = {}
        length_sum = 0.0
        for _f, r in items:
            seg = str_or_none(getattr(r, "segment", None))
            if seg:
                seg_counts[seg] = seg_counts.get(seg, 0) + 1
            lm = getattr(r, "length_m", None)
            if lm is not None and pd.notna(lm):
                length_sum += float(lm)
        n = len(items)
        with_flag += n
        if f0.is_foc:
            foc_total += n
        if f0.is_shadow:
            shadow_total += n
        rows.append(
            FleetFlagRow(
                flag=f0.country,
                flag_code=code,
                vessel_count=n,
                length_sum_m=round(length_sum, 1),
                is_foc=f0.is_foc,
                is_shadow=f0.is_shadow,
                by_segment=seg_counts,
            )
        )

    rows.sort(key=lambda r: r.vessel_count, reverse=True)
    return FleetFlagsResponse(
        as_of=as_of,
        total_with_flag=with_flag,
        total_unresolved=unresolved,
        foc_count=foc_total,
        shadow_count=shadow_total,
        rows=rows[:top_n],
    )


@router.get("/api/analytics/density", response_model=DensityResponse)
def analytics_density(region: str = "singapore_malacca", days: int = 30):
    """Fleet density series per region: daily laden/ballast/unknown counts by segment."""
    d = max(1, min(days, 365))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)
    df = db.query(
        "SELECT ts, kind, segment, laden_count, ballast_count, unknown_count "
        "FROM fleet_density WHERE region = ? AND ts >= ? AND segment != 'Small' ORDER BY ts",
        [region, cutoff],
        db=db.analytics_db_path(),
    )
    series = []
    if not df.empty:
        df["date"] = pd.to_datetime(df["ts"]).dt.date.astype(str)
        agg = (
            df.groupby(["date", "kind", "segment"])[
                ["laden_count", "ballast_count", "unknown_count"]
            ]
            .sum()
            .reset_index()
        )

        for _, r in agg.iterrows():
            series.append(
                DensityDay(
                    date=str(r["date"]),
                    kind=str(r["kind"]),
                    segment=str(r["segment"]),
                    laden_count=int(r["laden_count"]),
                    ballast_count=int(r["ballast_count"]),
                    unknown_count=int(r["unknown_count"]),
                )
            )
        series.sort(key=lambda r: r.date)
    return DensityResponse(region=region, days=d, series=series)


@router.get("/api/analytics/event-rate-timeline", response_model=EventRateTimelineResponse)
def analytics_event_rate_timeline(hours: int = 72):
    """Hourly count of reroute and STS events over the last N hours.

    Shows whether AIS anomaly event rates are accelerating or decelerating -
    a proxy for route disruption intensity.
    """
    h = max(6, min(hours, 336))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=h)
    df = db.query(
        "SELECT start_ts, type FROM ais_events "
        "WHERE type IN ('reroute', 'sts') AND start_ts >= ? AND segment != 'Small' ORDER BY start_ts",
        [cutoff],
        db=db.analytics_db_path(),
    )
    now = datetime.now(UTC)
    as_of = iso(now) or ""
    if df.empty:
        return EventRateTimelineResponse(as_of=as_of, hours=h, points=[])

    df["start_ts"] = pd.to_datetime(df["start_ts"])
    df["hour"] = df["start_ts"].dt.floor("h")
    pivot = (
        df.groupby(["hour", "type"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["reroute", "sts"], fill_value=0)
        .reset_index()
    )
    points = [
        EventRatePoint(
            hour=iso(r["hour"]) or str(r["hour"]),
            reroute_count=int(r["reroute"]),
            sts_count=int(r["sts"]),
            total_count=int(r["reroute"]) + int(r["sts"]),
        )
        for _, r in pivot.iterrows()
    ]
    return EventRateTimelineResponse(as_of=as_of, hours=h, points=points)


@router.get("/api/analytics/region-momentum", response_model=RegionMomentumResponse)
def analytics_region_momentum(hours_back: int = 24, ocean_only: bool = True):
    """Net change in vessel count per region vs hours_back hours ago.

    Reads fleet_density for the latest snapshot and the closest snapshot to
    hours_back hours prior. Returns per-region deltas sorted by absolute delta.

    ocean_only (default True): exclude the 'Small' segment, which comprises
    inland waterway vessels (river barges in ARA, etc.) that would otherwise
    dominate the delta chart and obscure ocean-going fleet movements.
    """
    h = max(1, min(hours_back, 168))
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_dt - timedelta(hours=h + 1)

    seg_filter = "AND segment != 'Small'" if ocean_only else ""
    df = db.query(
        f"SELECT ts, region, laden_count, ballast_count, unknown_count "
        f"FROM fleet_density WHERE ts >= ? {seg_filter} ORDER BY ts DESC",
        [cutoff],
        db=db.analytics_db_path(),
    )
    as_of = iso(now_dt) or ""
    if df.empty:
        return RegionMomentumResponse(as_of=as_of, hours_back=h, rows=[])

    df["ts"] = pd.to_datetime(df["ts"])
    df["total"] = df["laden_count"] + df["ballast_count"] + df["unknown_count"]

    latest_ts = df["ts"].max()
    target_prev_ts = latest_ts - timedelta(hours=h)
    # Pick closest snapshot to target_prev_ts
    unique_ts = df["ts"].unique()
    prev_ts = unique_ts[abs(unique_ts - target_prev_ts).argmin()]

    curr = (
        df[df["ts"] == latest_ts]
        .groupby("region")[["laden_count", "ballast_count", "unknown_count", "total"]]
        .sum()
    )
    prev = df[df["ts"] == prev_ts].groupby("region")["total"].sum().rename("prev_total")

    merged = curr.join(prev, how="outer").fillna(0)
    merged.columns = [
        "laden_count",
        "ballast_count",
        "unknown_count",
        "current_total",
        "prev_total",
    ]
    merged["delta"] = (merged["current_total"] - merged["prev_total"]).astype(int)
    merged["laden_ratio_pct"] = merged.apply(
        lambda r: round(100.0 * r["laden_count"] / max(r["current_total"], 1), 1), axis=1
    )
    merged = merged.sort_values("delta", key=abs, ascending=False)

    rows = [
        RegionMomentumRow(
            region=str(region),
            current_total=int(r["current_total"]),
            prev_total=int(r["prev_total"]),
            delta=int(r["delta"]),
            laden_count=int(r["laden_count"]),
            ballast_count=int(r["ballast_count"]),
            laden_ratio_pct=float(r["laden_ratio_pct"]),
        )
        for region, r in merged.iterrows()
    ]
    return RegionMomentumResponse(as_of=as_of, hours_back=h, ocean_only=ocean_only, rows=rows)


@router.get("/api/analytics/fleet-at-time", response_model=FleetHistoryResponse)
def analytics_fleet_at_time(ts: str = "", region: str = ""):
    """Fleet composition at a historical timestamp from ais_snapshots.

    ts: ISO 8601 datetime string (e.g. 2026-06-10T12:00:00). Defaults to 24h ago.
    Finds the snapshot closest to the requested timestamp (within 30 minutes).
    Returns segment breakdown with laden/ballast/underway counts and avg SOG.
    """
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    if ts:
        try:
            queried_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            queried_dt = now_dt - timedelta(hours=24)
    else:
        queried_dt = now_dt - timedelta(hours=24)

    queried_dt = max(queried_dt, now_dt - timedelta(days=30))  # cap 30d lookback

    region_clause = ""
    if region:
        region_clause = "AND region = ? "
        params: list = [queried_dt, region, queried_dt]
    else:
        params = [queried_dt, queried_dt]

    df = db.query(
        f"SELECT snapshot_ts, mmsi, kind, segment, sog, nav_status, draught, destination "
        f"FROM ais_snapshots "
        f"WHERE ABS(EPOCH(snapshot_ts) - EPOCH(?)) <= 1800 "
        f"AND segment != 'Small' "
        f"{region_clause}"
        f"ORDER BY ABS(EPOCH(snapshot_ts) - EPOCH(?)) LIMIT 10000",
        params,
    )
    if df.empty:
        return FleetHistoryResponse(
            queried_ts=queried_dt.isoformat(),
            actual_ts=queried_dt.isoformat(),
            region=region or None,
            total_vessels=0,
            segments=[],
        )

    actual_ts = pd.to_datetime(df["snapshot_ts"]).mode().iloc[0].to_pydatetime()
    df = df[pd.to_datetime(df["snapshot_ts"]) == actual_ts]

    from analytics.zones import DESIGN_DRAUGHT as _DD

    def _laden_ballast(grp_df: pd.DataFrame, seg: str) -> tuple[int, int]:
        """Segment-specific draught threshold: laden >= 80%, ballast <= 65% of design."""
        if "draught" not in grp_df:
            return 0, 0
        d = grp_df["draught"].to_numpy(dtype=float)
        design = float(_DD.get(str(seg), 0) or 0)
        if design <= 0:
            laden = int((d > 5).sum())
            ballast = int((d <= 5).sum())
        else:
            laden = int((d >= 0.80 * design).sum())
            ballast = int((d <= 0.65 * design).sum())
        return laden, ballast

    rows: list[FleetHistorySegmentRow] = []
    for (kind_val, seg_val), grp in df.groupby(["kind", "segment"]):
        laden, ballast = _laden_ballast(grp, str(seg_val))
        underway = int((grp["sog"].fillna(0) > 2).sum())
        avg_sog = (
            round(float(grp["sog"].dropna().mean()), 1) if len(grp["sog"].dropna()) > 0 else None
        )
        rows.append(
            FleetHistorySegmentRow(
                kind=str(kind_val),
                segment=str(seg_val),
                count=len(grp),
                laden_count=laden,
                ballast_count=ballast,
                underway_count=underway,
                avg_sog=avg_sog,
            )
        )
    rows.sort(key=lambda r: r.count, reverse=True)
    return FleetHistoryResponse(
        queried_ts=queried_dt.isoformat(),
        actual_ts=actual_ts.isoformat(),
        region=region or None,
        total_vessels=len(df),
        segments=rows,
    )


@router.get("/api/analytics/fleet-trend", response_model=FleetTrendResponse)
def analytics_fleet_trend(days: int = 30, region: str = ""):
    """Daily fleet composition trend from fleet_density table.

    Aggregates laden/ballast/unknown counts per day over the last N days (clamped 7-90).
    Optionally filter by region. Returns a time-series suitable for line/area charts.
    """
    days = max(7, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    region_clause = "AND region = ? " if region else ""
    params: list = [cutoff]
    if region:
        params.append(region)

    # Average hourly snapshot counts per day (not sum, which would inflate by 24x).
    # Inner query: total fleet size at each hourly snapshot.
    # Outer query: average across all snapshots in the day.
    df = db.query(
        f"SELECT CAST(ts AS DATE) AS day, "
        f"       ROUND(AVG(hourly_laden)) AS laden, "
        f"       ROUND(AVG(hourly_ballast)) AS ballast, "
        f"       ROUND(AVG(hourly_unknown)) AS unknown "
        f"FROM ("
        f"  SELECT ts, "
        f"    SUM(laden_count) AS hourly_laden, "
        f"    SUM(ballast_count) AS hourly_ballast, "
        f"    SUM(unknown_count) AS hourly_unknown "
        f"  FROM fleet_density "
        f"  WHERE ts >= ? {region_clause}AND segment != 'Small' "
        f"  GROUP BY ts"
        f") "
        f"GROUP BY day ORDER BY day",
        params,
        db=db.analytics_db_path(),
    )

    series: list[FleetTrendDay] = []
    if not df.empty:
        df["day"] = pd.to_datetime(df["day"])
        for _, row in df.iterrows():
            laden = int(row["laden"] or 0)
            ballast = int(row["ballast"] or 0)
            unknown = int(row["unknown"] or 0)
            series.append(
                FleetTrendDay(
                    date=row["day"].strftime("%Y-%m-%d"),
                    laden=laden,
                    ballast=ballast,
                    unknown=unknown,
                    total=laden + ballast + unknown,
                )
            )

    return FleetTrendResponse(
        as_of=now_dt.isoformat(),
        days=days,
        region=region or None,
        series=series,
    )


@router.get("/api/analytics/zones", response_model=list[AnalyticsZone])
def analytics_zones():
    """All anchorage bboxes and chokepoint region bboxes for frontend overlay."""
    from ais.regions import REGIONS
    from analytics.zones import ANCHORAGE_ZONES

    out: list[AnalyticsZone] = []
    for name, ((lat_min, lon_min), (lat_max, lon_max)) in ANCHORAGE_ZONES.items():
        out.append(
            AnalyticsZone(
                name=name,
                bbox=[[lat_min, lon_min], [lat_max, lon_max]],
                type="anchorage",
            )
        )
    for name, bbox in REGIONS.items():
        out.append(AnalyticsZone(name=name, bbox=bbox, type="chokepoint"))
    return out


@router.get("/api/analytics/slow-steamers", response_model=SlowSteamersResponse)
def analytics_slow_steamers(kind: str = "", limit: int = 50):
    """Vessels currently underway at less than 60% of their segment's median SOG.

    Slow steaming is a freight market signal: vessels reduce speed when cargo demand
    falls (fuel savings > waiting-for-cargo cost). Anchored and moored vessels are
    excluded. Segment medians are computed from the live fleet.
    """
    limit = max(5, min(200, limit))
    now = datetime.now(UTC).replace(tzinfo=None)

    # All underway vessels with reliable SOG - use in-process cache
    lp_df = live_all()
    if not lp_df.empty:
        sog_num = pd.to_numeric(lp_df["sog"], errors="coerce")
        ns_num = pd.to_numeric(lp_df.get("nav_status", pd.Series()), errors="coerce")
        ns_ok = ns_num.isna() | ~ns_num.isin([1, 5])
        lp_df = lp_df[
            (sog_num > 0.5) & (sog_num < 25.0) & ns_ok & (lp_df["segment"] != "Small")
        ].copy()
        needed = ["mmsi", "name", "kind", "segment", "region", "sog", "imo"]
        lp_df = lp_df[[c for c in needed if c in lp_df.columns]]
        if kind:
            lp_df = lp_df[lp_df["kind"] == kind]

    if lp_df.empty:
        return SlowSteamersResponse(as_of=iso(now) or "", total_fleet_underway=0, rows=[])

    total_underway = len(lp_df)

    # Segment medians from vessels actually underway (sog >= 2 kn)
    underway_mask = lp_df["sog"] >= 2.0
    seg_medians: dict[str, float] = {}
    for seg, grp in lp_df[underway_mask].groupby("segment"):
        if len(grp) >= 5:  # require at least 5 vessels for a reliable median
            seg_medians[str(seg)] = float(grp["sog"].median())

    if not seg_medians:
        return SlowSteamersResponse(
            as_of=iso(now) or "", total_fleet_underway=total_underway, rows=[]
        )

    # Find vessels at < 60% of their segment median
    candidates: list[dict] = []
    for _, v in lp_df.iterrows():
        seg = str_or_none(v.get("segment"))
        if not seg:
            continue
        median_sog = seg_medians.get(seg)
        if not median_sog or median_sog < 1.0:
            continue
        ratio = float(v["sog"]) / median_sog
        if ratio >= 0.6:
            continue
        candidates.append(
            {
                "mmsi": int(v["mmsi"]),
                "name": str_or_none(v.get("name")),
                "kind": str_or_none(v.get("kind")),
                "segment": seg,
                "region": str_or_none(v.get("region")),
                "sog": round(float(v["sog"]), 1),
                "segment_median_sog": round(median_sog, 1),
                "pct_of_median": round(ratio * 100, 1),
                "imo": valid_imo(v.get("imo")),
            }
        )

    candidates.sort(key=lambda c: c["pct_of_median"])
    candidates = candidates[:limit]

    if not candidates:
        return SlowSteamersResponse(
            as_of=iso(now) or "", total_fleet_underway=total_underway, rows=[]
        )

    # Enrich with risk scores
    all_imos = list({c["imo"] for c in candidates if c["imo"]})
    imo_risk: dict[int, dict] = {}
    if all_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            "FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [all_imos],
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac_sanctioned"]),
            }

    rows = []
    for c in candidates:
        risk_info = imo_risk.get(c["imo"], {}) if c["imo"] else {}
        rows.append(
            SlowSteamerEvent(
                mmsi=c["mmsi"],
                name=c["name"],
                kind=c["kind"],
                segment=c["segment"],
                region=c["region"],
                sog=c["sog"],
                segment_median_sog=c["segment_median_sog"],
                pct_of_median=c["pct_of_median"],
                risk_score=risk_info.get("risk_score"),
                ofac=bool(risk_info.get("ofac", False)),
            )
        )

    return SlowSteamersResponse(
        as_of=iso(now) or "", total_fleet_underway=total_underway, rows=rows
    )


@router.get("/api/analytics/market-summary", response_model=MarketSummaryResponse)
def analytics_market_summary():
    """Current market state: fleet laden/ballast split, 24h event counts, per-segment breakdown.

    Three-DB fan-out: analytics (vessel_state + event counts + transits),
    AIS (live_positions for segment/underway classification).
    """
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    since_24h = now_ts - timedelta(hours=24)

    # Event counts from analytics DB (reroutes exclude Small to filter inland barges)
    ev_df = db.query(
        "SELECT type, COUNT(*) AS cnt FROM ais_events WHERE start_ts >= ? AND segment != 'Small' GROUP BY type",
        [since_24h],
        db=db.analytics_db_path(),
    )
    ev_counts: dict[str, int] = {}
    if not ev_df.empty:
        for _, r in ev_df.iterrows():
            ev_counts[str(r["type"])] = int(r["cnt"])

    tr_df = db.query(
        "SELECT COUNT(*) AS cnt FROM transit_events WHERE entered_ts >= ? AND segment != 'Small'",
        [since_24h],
        db=db.analytics_db_path(),
    )
    transits_24h = int(tr_df.iloc[0]["cnt"]) if not tr_df.empty else 0

    # Vessel laden/ballast state from analytics DB
    vs_df = db.query(
        "SELECT mmsi, laden FROM vessel_state",
        db=db.analytics_db_path(),
    )
    laden_mmsi: set[int] = set()
    ballast_mmsi: set[int] = set()
    if not vs_df.empty:
        for _, r in vs_df.iterrows():
            m = int(r["mmsi"])
            if r["laden"] == "laden":
                laden_mmsi.add(m)
            elif r["laden"] == "ballast":
                ballast_mmsi.add(m)

    # Live fleet from in-process cache (avoids AIS DB lock contention)
    lp_df = live_all()
    if not lp_df.empty:
        lp_df = lp_df[lp_df["segment"].notna() & (lp_df["segment"] != "Small")]
        lp_df = lp_df[["mmsi", "segment", "kind", "nav_status", "sog"]].copy()

    total_fleet = len(lp_df) if not lp_df.empty else 0
    # Restrict laden/ballast counts to non-Small fleet for consistency with by_segment
    fleet_mmsis: set[int] = {int(m) for m in lp_df["mmsi"]} if not lp_df.empty else set()
    total_laden = len(laden_mmsi & fleet_mmsis)
    total_ballast = len(ballast_mmsi & fleet_mmsis)
    laden_pct = round(total_laden / max(total_laden + total_ballast, 1) * 100, 1)

    # Per-segment breakdown
    seg_rows: list[MarketSegmentSummary] = []
    if not lp_df.empty:
        for (segment, kind), grp in lp_df.groupby(["segment", "kind"]):
            seg_total = len(grp)
            seg_mmsis = {int(m) for m in grp["mmsi"]}
            seg_laden = len(seg_mmsis & laden_mmsi)
            seg_ballast = len(seg_mmsis & ballast_mmsi)
            seg_unknown = seg_total - seg_laden - seg_ballast

            nav_grp = grp["nav_status"]
            sog_grp = grp["sog"]
            underway = int(((nav_grp == 0) | (pd.to_numeric(sog_grp, errors="coerce") > 2.0)).sum())

            seg_rows.append(
                MarketSegmentSummary(
                    segment=str(segment),
                    kind=str(kind),
                    total=seg_total,
                    laden=seg_laden,
                    ballast=seg_ballast,
                    unknown=seg_unknown,
                    laden_pct=round(seg_laden / max(seg_laden + seg_ballast, 1) * 100, 1),
                    underway_pct=round(underway / max(seg_total, 1) * 100, 1),
                )
            )

        seg_rows.sort(key=lambda r: r.total, reverse=True)

    return MarketSummaryResponse(
        as_of=iso(now_ts) or "",
        total_fleet=total_fleet,
        total_laden=total_laden,
        total_ballast=total_ballast,
        laden_pct=laden_pct,
        transits_24h=transits_24h,
        reroutes_24h=ev_counts.get("reroute", 0),
        sts_24h=ev_counts.get("sts", 0),
        gaps_24h=ev_counts.get("gap", 0),
        by_segment=seg_rows,
    )


@router.get("/api/analytics/fleet-utilization", response_model=FleetUtilizationResponse)
def analytics_fleet_utilization():
    """Fleet utilization by segment: % underway vs idle across the live fleet.

    Underway = nav_status 0 (or unknown) AND sog > 2 kn.
    Idle = sog < 0.5 kn OR nav_status in (1=anchored, 5=moored).
    Unknown = everything else (slow but not confirmed idle).
    Excludes 'Small' segment (too noisy for freight signals).
    Sorted by underway_pct ascending (most-idle segments first).
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    lp_df = live_all()
    if not lp_df.empty:
        lp_df = lp_df[lp_df["segment"].notna() & (lp_df["segment"] != "Small")]
        lp_df = lp_df[["segment", "kind", "sog", "nav_status"]].copy()

    if lp_df.empty:
        return FleetUtilizationResponse(as_of=iso(now) or "", total_fleet=0, rows=[])

    lp_df["sog_num"] = pd.to_numeric(lp_df["sog"], errors="coerce")
    ns = pd.to_numeric(lp_df["nav_status"], errors="coerce")
    # Vectorized classification
    anchored = ns.isin([1, 5])
    underway_mask = (~anchored) & (lp_df["sog_num"] > 2.0)
    idle_mask = anchored | (~underway_mask & (lp_df["sog_num"] < 0.5))
    lp_df["status"] = "unknown"
    lp_df.loc[underway_mask, "status"] = "underway"
    lp_df.loc[idle_mask & ~underway_mask, "status"] = "idle"

    rows_out: list[FleetUtilizationRow] = []
    for (segment, kind), grp in lp_df.groupby(["segment", "kind"]):
        total = len(grp)
        underway = int((grp["status"] == "underway").sum())
        idle = int((grp["status"] == "idle").sum())
        unknown = int((grp["status"] == "unknown").sum())
        underway_sog = grp[grp["status"] == "underway"]["sog_num"]
        avg_sog = round(float(underway_sog.mean()), 1) if not underway_sog.empty else None
        rows_out.append(
            FleetUtilizationRow(
                segment=str(segment),
                kind=str(kind),
                total=total,
                underway_count=underway,
                idle_count=idle,
                unknown_count=unknown,
                underway_pct=round(100.0 * underway / total, 1) if total > 0 else 0.0,
                idle_pct=round(100.0 * idle / total, 1) if total > 0 else 0.0,
                avg_sog_underway=avg_sog,
            )
        )

    rows_out.sort(key=lambda r: r.underway_pct)  # most idle first
    total_fleet = len(lp_df)
    return FleetUtilizationResponse(as_of=iso(now) or "", total_fleet=total_fleet, rows=rows_out)
