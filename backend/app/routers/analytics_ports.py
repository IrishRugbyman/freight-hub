"""Analytics: port flows, anchorages, arrivals, and the European crude / LNG inbound boards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter

from .. import db
from .. import runner_eta as _runner_eta
from ..common import fresh_cutoff, haversine_nm, iso, str_or_none, valid_imo
from ..live import live_all
from ..ports import (
    CURATED_PORTS,
    EUR_TERMINALS,
    LNG_EU_TERMINALS,
    US_LNG_LOADING_TERMINALS,
    canonical_destination,
    match_eur_port,
    match_lng_terminal,
    match_port,
)
from ..schemas import (
    AnchorageDwellResponse,
    AnchorageOccupancyPoint,
    AnchorageOccupancyResponse,
    AnchoredVessel,
    ArrivalVessel,
    CongestionDay,
    CongestionResponse,
    EuropeanInboundResponse,
    EuropeanInboundVessel,
    LngInboundResponse,
    LngLoadingVessel,
    LngVessel,
    PortArrivalForecast,
    PortArrivalResponse,
    PortCongestionResponse,
    PortCongestionRow,
    PortDestItem,
    PortFlowResponse,
)

router = APIRouter()


@router.get("/api/analytics/ports", response_model=PortFlowResponse)
def analytics_ports(kind: str | None = None, top_n: int = 20):
    """Current destination distribution across the live fleet.

    Groups live_positions by normalized destination and returns counts by vessel kind.
    top_n clamped 5-50.
    """
    top_n = max(5, min(50, top_n))
    cutoff = fresh_cutoff()
    params: list = [cutoff]
    kind_clause = ""
    if kind:
        kind_clause = "AND kind = ?"
        params.append(kind)

    # Group by RAW destination in SQL, then fold onto canonical ports in Python
    # (one port has many raw spellings, so the LIMIT must be applied after the
    # fold or tail spellings of a top port would be dropped).
    df = db.query(
        f"SELECT "
        f"  UPPER(TRIM(destination)) AS dest, "
        f"  COUNT(*) AS cnt, "
        f"  COUNT(CASE WHEN kind='tanker' THEN 1 END) AS tankers, "
        f"  COUNT(CASE WHEN kind='bulk' THEN 1 END) AS bulkers "
        f"FROM live_positions "
        f"WHERE updated_ts > ? {kind_clause} "
        f"  AND segment != 'Small' "
        f"  AND destination IS NOT NULL AND TRIM(destination) != '' "
        f"GROUP BY dest",
        params,
    )

    total_df = db.query(
        f"SELECT COUNT(*) AS n FROM live_positions "
        f"WHERE updated_ts > ? {kind_clause} "
        f"  AND segment != 'Small' "
        f"  AND destination IS NOT NULL AND TRIM(destination) != ''",
        params,
    )
    total_with_dest = int(total_df.iloc[0]["n"]) if not total_df.empty else 0

    agg: dict[str, dict[str, int]] = {}
    if not df.empty:
        for _, r in df.iterrows():
            canon = canonical_destination(str(r["dest"]))
            if canon is None:
                continue  # junk / "FOR ORDERS" / blank-after-normalise
            slot = agg.setdefault(canon, {"count": 0, "tankers": 0, "bulkers": 0})
            slot["count"] += int(r["cnt"])
            slot["tankers"] += int(r["tankers"])
            slot["bulkers"] += int(r["bulkers"])

    ports = [
        PortDestItem(destination=name, count=v["count"], tankers=v["tankers"], bulkers=v["bulkers"])
        for name, v in sorted(agg.items(), key=lambda kv: -kv[1]["count"])[:top_n]
    ]

    return PortFlowResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        total_with_dest=total_with_dest,
        ports=ports,
    )


@router.get("/api/analytics/congestion", response_model=CongestionResponse)
def analytics_congestion(zone: str = "singapore_west", days: int = 30):
    """Daily anchored vessel counts and median dwell hours per anchorage zone."""
    d = max(1, min(days, 365))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)
    df = db.query(
        "SELECT start_ts, end_ts FROM anchored_episodes WHERE zone = ? AND start_ts >= ? AND segment != 'Small'",
        [zone, cutoff],
        db=db.analytics_db_path(),
    )
    series = []
    if not df.empty:
        df["date"] = pd.to_datetime(df["start_ts"]).dt.date.astype(str)
        df["dwell_h"] = (
            pd.to_datetime(df["end_ts"]) - pd.to_datetime(df["start_ts"])
        ).dt.total_seconds() / 3600
        for date_s, grp in df.groupby("date"):
            series.append(
                CongestionDay(
                    date=date_s,
                    zone=zone,
                    vessel_count=len(grp),
                    median_dwell_hours=round(float(grp["dwell_h"].median()), 1),
                )
            )
        series.sort(key=lambda r: r.date)
    return CongestionResponse(zone=zone, days=d, series=series)


@router.get("/api/analytics/anchorage-dwell", response_model=AnchorageDwellResponse)
def analytics_anchorage_dwell(zone: str = "singapore_west", limit: int = 50):
    """Vessels currently anchored at a zone, ranked by dwell time (longest first).

    Reconstructs currently-anchored vessels from the closed-episode chains (the
    analytics job never leaves an episode open) via detect.current_anchored, then
    enriches with vessel_state + live_positions + registry. Long dwell vessels are
    likely ready to depart - a freight market timing signal.
    """
    from analytics.detect import current_from_spans

    limit = max(5, min(200, limit))
    now = datetime.now(UTC).replace(tzinfo=None)
    # 30-day lookback so a long anchoring's full chain merges into a true dwell;
    # current_from_spans then keeps only vessels still present now.
    since = now - timedelta(days=30)

    span_df = _merged_anchored_spans(since)
    max_end = pd.to_datetime(span_df["end_ts"]).max() if not span_df.empty else None
    spans = span_df.to_dict("records") if not span_df.empty else []
    current = current_from_spans(spans, max_end) if max_end is not None else []
    here = [c for c in current if c["zone"] == zone]
    if not here:
        return AnchorageDwellResponse(as_of=iso(now) or "", zone=zone, rows=[])

    # kind/segment per mmsi from this zone's spans.
    meta: dict[int, dict] = {}
    if not span_df.empty:
        zsub = span_df[span_df["zone"] == zone]
        for _, r in zsub.iterrows():
            meta[int(r["mmsi"])] = {
                "kind": str_or_none(r.get("kind")),
                "segment": str_or_none(r.get("segment")),
            }

    # Longest dwell first, capped at limit.
    here.sort(key=lambda c: c["dwell_hours"], reverse=True)
    here = here[:limit]

    all_mmsis = [int(c["mmsi"]) for c in here]
    ph = ",".join("?" * len(all_mmsis))

    # Vessel names
    mmsi_name: dict[int, str | None] = {}
    lp_df = db.query(f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({ph})", all_mmsis)
    for _, r in lp_df.iterrows():
        mmsi_name[int(r["mmsi"])] = str_or_none(r.get("name"))
    missing = [m for m in all_mmsis if m not in mmsi_name]
    if missing:
        ph2 = ",".join("?" * len(missing))
        v_df = db.query(f"SELECT mmsi, name FROM vessels WHERE mmsi IN ({ph2})", missing)
        for _, r in v_df.iterrows():
            mmsi_name[int(r["mmsi"])] = str_or_none(r.get("name"))

    # Laden state from vessel_state
    mmsi_laden: dict[int, str | None] = {}
    vs_df = db.query(
        f"SELECT mmsi, laden FROM vessel_state WHERE mmsi IN ({ph})",
        all_mmsis,
        db=db.analytics_db_path(),
    )
    for _, r in vs_df.iterrows():
        mmsi_laden[int(r["mmsi"])] = str_or_none(r.get("laden"))

    # MMSI -> IMO from live_positions / vessels
    mmsi_imo: dict[int, int | None] = {}
    lp2 = db.query(f"SELECT mmsi, imo FROM live_positions WHERE mmsi IN ({ph})", all_mmsis)
    for _, r in lp2.iterrows():
        mmsi_imo[int(r["mmsi"])] = valid_imo(r.get("imo"))
    for m in all_mmsis:
        if m not in mmsi_imo:
            mmsi_imo[m] = None

    known_imos = list({i for i in mmsi_imo.values() if i})
    imo_risk: dict[int, dict] = {}
    if known_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            "FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [known_imos],
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac_sanctioned"]),
            }

    rows = []
    for c in here:
        mmsi_val = int(c["mmsi"])
        dwell_h = float(c["dwell_hours"])
        start_ts = c["end_ts"] - timedelta(hours=dwell_h)
        m = meta.get(mmsi_val, {})

        imo_val = mmsi_imo.get(mmsi_val)
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}
        rs = risk_info.get("risk_score")

        rows.append(
            AnchoredVessel(
                mmsi=mmsi_val,
                name=mmsi_name.get(mmsi_val),
                zone=zone,
                kind=m.get("kind"),
                segment=m.get("segment"),
                start_ts=iso(start_ts) or "",
                dwell_hours=dwell_h,
                laden=mmsi_laden.get(mmsi_val),
                risk_score=rs,
                ofac=bool(risk_info.get("ofac", False)),
            )
        )

    rows.sort(key=lambda r: r.dwell_hours, reverse=True)
    return AnchorageDwellResponse(as_of=iso(now) or "", zone=zone, rows=rows)


@router.get("/api/analytics/anchorage-occupancy", response_model=AnchorageOccupancyResponse)
def analytics_anchorage_occupancy(hours: int = 72, zones_csv: str = ""):
    """Concurrent vessel count per anchorage zone, computed hourly over the last N hours.

    Uses anchored_episodes: an episode contributes to all hours from start_ts to
    end_ts (or NOW if still anchored). zones_csv: optional comma-separated filter.
    Default zones: singapore_west, rotterdam, port_said, singapore_east, suez_roads.
    """
    import numpy as np

    h = max(6, min(hours, 336))
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_dt - timedelta(hours=h)

    default_zones = ["singapore_west", "rotterdam", "port_said", "singapore_east", "suez_roads"]
    zone_filter = [z.strip() for z in zones_csv.split(",") if z.strip()] or default_zones

    df = db.query(
        "SELECT zone, start_ts, end_ts FROM anchored_episodes "
        "WHERE zone = ANY(?) AND start_ts <= ? AND (end_ts IS NULL OR end_ts >= ?)",
        [zone_filter, now_dt, cutoff],
        db=db.analytics_db_path(),
    )
    as_of = iso(now_dt) or ""
    if df.empty:
        return AnchorageOccupancyResponse(as_of=as_of, hours=h, zones=zone_filter, points=[])

    df["start_ts"] = pd.to_datetime(df["start_ts"]).dt.floor("h").clip(lower=pd.Timestamp(cutoff))
    df["end_ts"] = pd.to_datetime(df["end_ts"].fillna(pd.Timestamp(now_dt))).dt.floor("h")

    hour_range = pd.date_range(cutoff.replace(minute=0, second=0, microsecond=0), now_dt, freq="h")
    hour_index = {h_val: i for i, h_val in enumerate(hour_range)}
    n_hours = len(hour_range)

    points: list[AnchorageOccupancyPoint] = []
    for zone in zone_filter:
        counts = np.zeros(n_hours, dtype=int)
        zone_df = df[df["zone"] == zone]
        for _, row in zone_df.iterrows():
            s_idx = hour_index.get(row["start_ts"], 0)
            e_idx = hour_index.get(row["end_ts"], n_hours - 1) + 1
            counts[s_idx:e_idx] += 1
        for i, h_val in enumerate(hour_range):
            if counts[i] > 0:
                points.append(
                    AnchorageOccupancyPoint(
                        hour=iso(h_val.to_pydatetime()) or str(h_val),
                        zone=zone,
                        vessel_count=int(counts[i]),
                    )
                )
    active_zones = [z for z in zone_filter if any(p.zone == z for p in points)]
    return AnchorageOccupancyResponse(as_of=as_of, hours=h, zones=active_zones, points=points)


def _merged_anchored_spans(since: datetime, kind: str = "") -> pd.DataFrame:
    """Merged continuous anchoring spans since `since`, computed in DuckDB.

    The analytics job stores a continuous anchoring as a chain of overlapping ~6h
    closed fragments (no episode is ever left open). Counting raw fragments
    inflates dwell/baseline ~6x, so we collapse them with a gaps-and-islands
    window query (a new span begins when a fragment starts > 2h - the episode gap
    - past the running max end of its (mmsi, zone) group). Doing the merge in SQL
    returns ~15k spans instead of ~200k fragments, keeping the endpoint fast.

    Returns columns: mmsi, zone, start_ts, end_ts, kind, segment.
    """
    kind_cond = " AND kind = ?" if kind else ""
    params: list = [since] + ([kind] if kind else [])
    return db.query(
        "WITH frags AS ("
        "  SELECT mmsi, zone, start_ts, end_ts, kind, segment "
        "  FROM anchored_episodes "
        f"  WHERE end_ts >= ?{kind_cond} AND segment != 'Small'"
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


@router.get("/api/analytics/port-congestion", response_model=PortCongestionResponse)
def analytics_port_congestion(kind: str = "", days: int = 14):
    """Port and anchorage congestion monitor.

    Compares current anchored vessel counts against a historical baseline derived
    from completed episodes in the last `days` days. Returns a congestion_factor
    (current / baseline) per zone, sorted most congested first.

    Zones with no historical baseline still appear if vessels are currently anchored:
    they get congestion_factor=1.0 (no comparison available).
    """
    from analytics.detect import CURRENT_ANCHOR_WINDOW_H, current_from_spans

    days = max(3, min(90, days))
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    since = now_ts - timedelta(days=days)

    # The analytics job never leaves an episode open (end_ts IS NULL): each run's
    # sliding window stores a continuous anchoring as a chain of overlapping ~6h
    # CLOSED fragments. _merged_anchored_spans collapses them (in SQL) so both
    # current presence and the baseline come from MERGED spans - counting raw
    # fragments would inflate dwell and the baseline ~6x.
    span_df = _merged_anchored_spans(since, kind)
    max_end = pd.to_datetime(span_df["end_ts"]).max() if not span_df.empty else None
    spans = span_df.to_dict("records") if not span_df.empty else []
    current = current_from_spans(spans, max_end) if max_end is not None else []

    # Enrich currently-anchored vessels with region from live_positions (separate DB).
    region_map: dict[int, str | None] = {}
    if current:
        cur_mmsis = [int(c["mmsi"]) for c in current]
        ph_cur = ",".join("?" * len(cur_mmsis))
        region_df = db.query(
            f"SELECT mmsi, region FROM live_positions WHERE mmsi IN ({ph_cur})",
            cur_mmsis,
        )
        if not region_df.empty:
            region_map = {
                int(r["mmsi"]): str_or_none(r.get("region")) for _, r in region_df.iterrows()
            }

    kind_map: dict[int, str | None] = {}
    if not span_df.empty:
        for _, r in span_df.dropna(subset=["mmsi"]).iterrows():
            kind_map.setdefault(int(r["mmsi"]), str_or_none(r.get("kind")))

    # Build current state: zone -> {vessels, avg_dwell, region, kind}
    zone_current: dict[str, dict] = {}
    cur_df = pd.DataFrame(current)
    if not cur_df.empty:
        for zone_key, grp in cur_df.groupby("zone"):
            mmsis = [int(m) for m in grp["mmsi"]]
            regions = [region_map.get(m) for m in mmsis if region_map.get(m)]
            kinds = [kind_map.get(m) for m in mmsis if kind_map.get(m)]
            zone_current[str(zone_key)] = {
                "current_vessels": int(len(grp)),
                "avg_current_dwell_hours": round(float(grp["dwell_hours"].mean()), 1),
                "region": regions[0] if regions else None,
                "kind": kinds[0] if kinds else None,
            }

    # Build historical baseline from merged spans: avg concurrent vessels over the
    # window EXCLUDING the present (each span is clipped to [since, baseline_end],
    # where baseline_end = max_end - current-window). This keeps the factor a
    # current-vs-typical comparison rather than comparing the present to itself; a
    # zone whose only presence is right now has no baseline (factor falls back to 1).
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

    # Combine: include zones with current vessels or historical baseline
    all_zones = set(zone_current.keys()) | set(zone_baseline.keys())
    rows_out: list[PortCongestionRow] = []
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
            PortCongestionRow(
                zone=z,
                region=cur.get("region"),
                kind=cur.get("kind"),
                current_vessels=cv,
                avg_current_dwell_hours=cur.get("avg_current_dwell_hours"),
                baseline_avg_vessels=bav,
                baseline_avg_dwell_hours=bas.get("baseline_avg_dwell_hours"),
                congestion_factor=factor,
            )
        )

    rows_out.sort(key=lambda r: (-r.congestion_factor, -r.current_vessels))
    return PortCongestionResponse(
        as_of=iso(now_ts) or "",
        days_baseline=days,
        rows=rows_out,
    )


@router.get("/api/analytics/port-arrivals", response_model=PortArrivalResponse)
def analytics_port_arrivals(horizon_h: int = 48, kind: str = "tanker", ocean_only: bool = True):
    """48h port arrival forecast for major tanker/bulk terminals.

    For each underway vessel with a parseable destination, computes ETA from
    current position + SOG + great-circle distance to the matched port.
    Returns per-port arrival counts and vessel lists.
    ocean_only=true (default) excludes Small segment to filter inland waterway barges.
    """
    horizon_h = max(12, min(120, horizon_h))
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    fleet_df = live_all()
    if not fleet_df.empty:
        sog_num = pd.to_numeric(fleet_df["sog"], errors="coerce")
        fleet_df = fleet_df[
            (sog_num >= 1.5) & fleet_df["destination"].notna() & fleet_df["segment"].notna()
        ].copy()
        if kind:
            fleet_df = fleet_df[fleet_df["kind"] == kind]
        if ocean_only and not fleet_df.empty:
            fleet_df = fleet_df[fleet_df["segment"] != "Small"]
        needed = ["mmsi", "name", "lat", "lon", "sog", "destination", "segment", "kind", "imo"]
        fleet_df = fleet_df[[c for c in needed if c in fleet_df.columns]]

    if fleet_df.empty:
        return PortArrivalResponse(as_of=now_dt.isoformat(), total_inbound=0, ports=[])

    # Load laden status from vessel_state
    state_df = db.query(
        "SELECT mmsi, laden FROM vessel_state",
        db=db.analytics_db_path(),
    )
    laden_map: dict[int, str | None] = {}
    if not state_df.empty:
        for _, r in state_df.iterrows():
            laden_map[int(r["mmsi"])] = str_or_none(r.get("laden"))

    # Collect inbound vessels per port
    port_buckets: dict[str, list[dict]] = {p: [] for p in CURATED_PORTS}
    total_inbound = 0

    for _, r in fleet_df.iterrows():
        port_name = match_port(str_or_none(r.get("destination")))
        if not port_name:
            continue
        port = CURATED_PORTS[port_name]
        try:
            dist_nm = haversine_nm(float(r["lat"]), float(r["lon"]), port["lat"], port["lon"])
        except Exception:
            continue
        sog_val = float(r["sog"])
        if sog_val < 0.5:
            continue
        eta_h = dist_nm / sog_val  # hours until arrival at current speed

        # Skip vessels that are already at the port (< 10 nm) or beyond horizon
        if dist_nm < 10 or eta_h > horizon_h:
            continue

        mmsi_int = int(r["mmsi"])
        port_buckets[port_name].append(
            {
                "mmsi": mmsi_int,
                "name": str_or_none(r.get("name")),
                "segment": str_or_none(r.get("segment")),
                "kind": str_or_none(r.get("kind")),
                "laden": laden_map.get(mmsi_int),
                "eta_hours": round(eta_h, 1),
                "distance_nm": round(dist_nm, 0),
                "sog": round(sog_val, 1),
                "destination_raw": str_or_none(r.get("destination")),
                "registry_risk": None,
                "imo": valid_imo(r.get("imo")),
            }
        )
        total_inbound += 1

    # Enrich top vessels per port with registry risk
    all_imos = [d["imo"] for buckets in port_buckets.values() for d in buckets if d["imo"]]
    risk_m: dict[int, int] = {}
    if all_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [all_imos],
        )
        if not reg_df.empty:
            for _, rr in reg_df.iterrows():
                iv = valid_imo(rr.get("imo"))
                rv = rr.get("risk_score")
                if iv and rv is not None and not (isinstance(rv, float) and pd.isna(rv)):
                    risk_m[iv] = int(rv)
    for buckets in port_buckets.values():
        for d in buckets:
            if d["imo"]:
                d["registry_risk"] = risk_m.get(d["imo"])

    # Build response - only include ports with arrivals
    result_ports: list[PortArrivalForecast] = []
    for pname, buckets in port_buckets.items():
        if not buckets:
            continue
        buckets.sort(key=lambda d: d["eta_hours"])
        vessels = [ArrivalVessel(**{k: v for k, v in d.items() if k != "imo"}) for d in buckets]
        result_ports.append(
            PortArrivalForecast(
                port=pname,
                arrivals_24h=sum(1 for v in vessels if v.eta_hours <= 24),
                arrivals_48h=len(vessels),
                vessels=vessels[:20],  # cap to 20 per port for payload
            )
        )

    result_ports.sort(key=lambda p: p.arrivals_48h, reverse=True)

    return PortArrivalResponse(
        as_of=now_dt.isoformat(),
        total_inbound=total_inbound,
        ports=result_ports,
    )


# Lookback days for each chokepoint to determine origin.
# Chosen as ~1.5x the typical sailing time from load zone to that chokepoint.
_ORIGIN_CHOKEPOINTS: list[dict] = [
    # chokepoint, direction (None = any), laden_required, label, via_label, lookback_days
    # Order matters: first match wins. Higher-confidence rules go first.
    {
        "cp": "suez",
        "dir": "northbound",
        "laden": True,
        "origin": "Middle East",
        "via": "Suez NB",
        "days": 21,
    },
    {
        "cp": "bosphorus_dardanelles",
        "dir": "southbound",
        "laden": True,
        "origin": "Black Sea",
        "via": "Bosphorus S",
        "days": 14,
    },
    {
        "cp": "cape_good_hope",
        "dir": "northbound",
        "laden": True,
        "origin": "East / Long-haul",
        "via": "Cape NB",
        "days": 45,
    },
    {
        "cp": "singapore_malacca",
        "dir": "westbound",
        "laden": True,
        "origin": "Asia Pacific",
        "via": "Malacca W",
        "days": 35,
    },
    # Gibraltar E = vessel entering Med from Atlantic -> Atlantic loading (Americas, W Africa)
    {
        "cp": "gibraltar",
        "dir": "eastbound",
        "laden": True,
        "origin": "Atlantic",
        "via": "Gibraltar E",
        "days": 8,
    },
    # Dover Channel E (laden) = vessel entering North Sea from the Atlantic side
    # -> loaded in the Americas, West Africa, or directly from a North Atlantic field.
    # Less precise than Suez but useful when no other signal is available.
    {
        "cp": "dover_channel",
        "dir": "eastbound",
        "laden": True,
        "origin": "Atlantic",
        "via": "Dover E",
        "days": 3,
    },
]

# AIS region -> origin label (fallback when no transit found)
_REGION_TO_ORIGIN: dict[str, str] = {
    "west_africa": "West Africa",
    "us_gulf": "Americas",
    "us_east_coast": "Americas",
    "us_west_coast": "Americas",
    "brazil": "Americas",
    "hormuz": "Middle East",
    "persian_gulf": "Middle East",
}


def _infer_origins(mmsi_list: list[int]) -> dict[int, tuple[str | None, str | None]]:
    """Return {mmsi: (origin_label, via_label)} using recent transit history.

    Only laden transits count. Priority is determined by the order of
    _ORIGIN_CHOKEPOINTS (Suez NB is the highest-confidence signal for European imports).
    """
    if not mmsi_list:
        return {}

    now_dt = datetime.now(UTC).replace(tzinfo=None)
    max_lookback = max(r["days"] for r in _ORIGIN_CHOKEPOINTS)
    cutoff = now_dt - timedelta(days=max_lookback)

    placeholders = ",".join("?" * len(mmsi_list))
    transit_df = db.query(
        f"SELECT mmsi, chokepoint, direction, laden, entered_ts "
        f"FROM transit_events "
        f"WHERE mmsi IN ({placeholders}) AND entered_ts >= ? "
        f"ORDER BY entered_ts DESC",
        mmsi_list + [cutoff],
        db=db.analytics_db_path(),
    )

    result: dict[int, tuple[str | None, str | None]] = {}
    if transit_df.empty:
        return result

    # Group by mmsi; for each vessel pick the highest-priority matching transit
    for mmsi, grp in transit_df.groupby("mmsi"):
        mmsi_int = int(mmsi)
        for rule in _ORIGIN_CHOKEPOINTS:
            cutoff_rule = now_dt - timedelta(days=rule["days"])
            mask = grp["chokepoint"] == rule["cp"]
            if rule["dir"]:
                mask &= grp["direction"] == rule["dir"]
            if rule["laden"]:
                mask &= grp["laden"] == True  # noqa: E712
            mask &= pd.to_datetime(grp["entered_ts"]) >= cutoff_rule
            if mask.any():
                result[mmsi_int] = (rule["origin"], rule["via"])
                break  # highest-priority rule matched; stop checking

    return result


def _eur_dwt(segment: str | None) -> int | None:
    """DWT proxy for common tanker segments. Bulk carriers use similar ranges."""
    return {
        "ULCC": 400_000,
        "VLCC": 300_000,
        "Suezmax": 157_000,
        "Aframax": 105_000,
        "Panamax": 74_000,
        "LR2": 110_000,
        "LR1": 75_000,
        "MR": 50_000,
        "Handysize": 35_000,
        "Capesize": 180_000,
        "Post-Panamax": 120_000,
        "Small": 20_000,
    }.get(segment or "")


def _eta_bucket(h: float) -> str:
    if h <= 6:
        return "0-6h"
    if h <= 12:
        return "6-12h"
    if h <= 24:
        return "12-24h"
    return "24-48h"


@router.get("/api/analytics/european-inbound", response_model=EuropeanInboundResponse)
def analytics_european_inbound(horizon_h: int = 48, laden_only: bool = False):
    """Laden vessel arrival forecast for European energy import terminals.

    For each underway vessel with a destination matching a curated European port,
    computes ETA from position + SOG, then infers cargo origin by looking up
    recent chokepoint transit history (Suez NB = Middle East, Bosphorus S = Black Sea, etc.).
    Returns the fleet sorted by ETA with aggregated breakdowns by origin, port and horizon bucket.
    """
    horizon_h = max(12, min(120, horizon_h))
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    fleet_df = live_all()
    if not fleet_df.empty:
        sog_num = pd.to_numeric(fleet_df["sog"], errors="coerce")
        fleet_df = fleet_df[
            (sog_num >= 1.5)
            & fleet_df["destination"].notna()
            & fleet_df["segment"].notna()
            & (fleet_df["segment"] != "Small")
        ].copy()
        needed = [
            "mmsi",
            "name",
            "lat",
            "lon",
            "sog",
            "destination",
            "segment",
            "kind",
            "imo",
            "region",
        ]
        fleet_df = fleet_df[[c for c in needed if c in fleet_df.columns]]

    if fleet_df.empty:
        return EuropeanInboundResponse(
            as_of=now_dt.isoformat(),
            horizon_h=horizon_h,
            total_vessels=0,
            total_laden=0,
            total_dwt_laden=0,
            vessels=[],
            by_origin={},
            by_port={},
            eta_buckets={},
        )

    # Laden status from vessel_state
    state_df = db.query(
        "SELECT mmsi, laden FROM vessel_state",
        db=db.analytics_db_path(),
    )
    laden_map: dict[int, str | None] = {}
    if not state_df.empty:
        for _, r in state_df.iterrows():
            laden_map[int(r["mmsi"])] = str_or_none(r.get("laden"))

    # Match vessels to European terminals and compute ETAs
    candidates: list[dict] = []
    for _, r in fleet_df.iterrows():
        port_name = match_eur_port(str_or_none(r.get("destination")))
        if not port_name:
            continue
        terminal = EUR_TERMINALS[port_name]
        try:
            dist_nm = haversine_nm(
                float(r["lat"]), float(r["lon"]), terminal["lat"], terminal["lon"]
            )
        except Exception:
            continue
        sog_val = float(r["sog"])
        if sog_val < 0.5:
            continue
        eta_h = dist_nm / sog_val
        if dist_nm < 5 or eta_h > horizon_h:
            continue

        mmsi_int = int(r["mmsi"])
        laden_status = laden_map.get(mmsi_int)
        if laden_only and laden_status != "laden":
            continue

        candidates.append(
            {
                "mmsi": mmsi_int,
                "name": str_or_none(r.get("name")),
                "segment": str_or_none(r.get("segment")),
                "kind": str_or_none(r.get("kind")),
                "laden": laden_status,
                "eta_hours": round(eta_h, 1),
                "distance_nm": round(dist_nm, 0),
                "sog": round(sog_val, 1),
                "port": port_name,
                "port_region": terminal["region"],
                "terminal_lat": terminal["lat"],
                "terminal_lon": terminal["lon"],
                "destination_raw": str_or_none(r.get("destination")),
                "current_region": str_or_none(r.get("region")),
                "imo": valid_imo(r.get("imo")),
            }
        )

    if not candidates:
        return EuropeanInboundResponse(
            as_of=now_dt.isoformat(),
            horizon_h=horizon_h,
            total_vessels=0,
            total_laden=0,
            total_dwt_laden=0,
            vessels=[],
            by_origin={},
            by_port={},
            eta_buckets={},
        )

    # Infer cargo origins from transit history
    all_mmsis = [c["mmsi"] for c in candidates]
    origin_map = _infer_origins(all_mmsis)

    # True ETA (Phase E): look up the precomputed physics ETA + interval to the
    # terminal each vessel is matched to (nearest target centroid within ~30 nm).
    preds_by_mmsi = _runner_eta.predictions_by_mmsi(all_mmsis)

    # Registry risk enrichment
    all_imos = [c["imo"] for c in candidates if c.get("imo")]
    risk_m: dict[int, int] = {}
    if all_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [all_imos],
        )
        if not reg_df.empty:
            for _, rr in reg_df.iterrows():
                iv = valid_imo(rr.get("imo"))
                rv = rr.get("risk_score")
                if iv and rv is not None and not (isinstance(rv, float) and pd.isna(rv)):
                    risk_m[iv] = int(rv)

    # Build vessels list and aggregate stats
    vessels: list[EuropeanInboundVessel] = []
    by_origin: dict[str, int] = {}
    by_port: dict[str, int] = {}
    eta_buckets: dict[str, int] = {"0-6h": 0, "6-12h": 0, "12-24h": 0, "24-48h": 0}
    total_laden = 0
    total_dwt_laden = 0

    for c in sorted(candidates, key=lambda x: x["eta_hours"]):
        mmsi_int = c["mmsi"]
        inferred_origin, inferred_via = origin_map.get(mmsi_int, (None, None))

        # Fallback: use current AIS region as rough origin proxy
        if inferred_origin is None:
            inferred_origin = _REGION_TO_ORIGIN.get(c.get("current_region") or "")

        dwt = _eur_dwt(c.get("segment"))
        laden_status = c["laden"]

        if laden_status == "laden":
            total_laden += 1
            if dwt:
                total_dwt_laden += dwt

        # Attach the true ETA (physics + interval) for the matched terminal.
        pred = _runner_eta.nearest_prediction(
            preds_by_mmsi.get(mmsi_int, []), c["terminal_lat"], c["terminal_lon"]
        )
        eta_true = pred["eta_p50_h"] if pred else None
        # Primary ETA shown is the true estimate when resolvable, else the naive one.
        primary_eta = eta_true if eta_true is not None else c["eta_hours"]

        origin_key = inferred_origin or "Unknown"
        by_origin[origin_key] = by_origin.get(origin_key, 0) + 1
        by_port[c["port"]] = by_port.get(c["port"], 0) + 1
        bucket = _eta_bucket(primary_eta)
        eta_buckets[bucket] = eta_buckets.get(bucket, 0) + 1

        vessels.append(
            EuropeanInboundVessel(
                mmsi=mmsi_int,
                name=c["name"],
                segment=c["segment"],
                kind=c["kind"],
                laden=laden_status,
                eta_hours=round(primary_eta, 1),
                distance_nm=c["distance_nm"],
                sog=c["sog"],
                port=c["port"],
                port_region=c["port_region"],
                destination_raw=c["destination_raw"],
                inferred_origin=inferred_origin,
                inferred_via=inferred_via,
                dwt_estimate=dwt,
                registry_risk=risk_m.get(c["imo"]) if c.get("imo") else None,
                eta_true_h=round(eta_true, 1) if eta_true is not None else None,
                eta_low_h=round(pred["eta_low_h"], 1)
                if pred and pred.get("eta_low_h") is not None
                else None,
                eta_high_h=round(pred["eta_high_h"], 1)
                if pred and pred.get("eta_high_h") is not None
                else None,
                eta_naive_h=c["eta_hours"],
                eta_method=pred["method"] if pred else None,
            )
        )

    # Sort origins by count descending
    by_origin = dict(sorted(by_origin.items(), key=lambda x: x[1], reverse=True))
    by_port = dict(sorted(by_port.items(), key=lambda x: x[1], reverse=True))
    # Remove empty buckets
    eta_buckets = {k: v for k, v in eta_buckets.items() if v > 0}

    return EuropeanInboundResponse(
        as_of=now_dt.isoformat(),
        horizon_h=horizon_h,
        total_vessels=len(vessels),
        total_laden=total_laden,
        total_dwt_laden=total_dwt_laden,
        vessels=vessels,
        by_origin=by_origin,
        by_port=by_port,
        eta_buckets=eta_buckets,
    )


_US_LNG_TERMINAL_RADIUS_NM = 80.0  # generous radius to catch vessels in approach


# Origin inference rules specific to LNG trade routes
_LNG_ORIGIN_RULES: list[dict] = [
    # Qatar (Ras Laffan) -> Suez NB (no Bosphorus)
    {
        "cp": "suez",
        "dir": "northbound",
        "laden": True,
        "origin": "Qatar / ME",
        "via": "Suez NB",
        "days": 21,
    },
    # US Gulf (Sabine Pass, Corpus Christi, Freeport) -> Gibraltar E or Dover E
    {
        "cp": "gibraltar",
        "dir": "eastbound",
        "laden": True,
        "origin": "US Gulf LNG",
        "via": "Gibraltar E",
        "days": 8,
    },
    {
        "cp": "dover_channel",
        "dir": "eastbound",
        "laden": True,
        "origin": "US Gulf LNG",
        "via": "Dover E",
        "days": 3,
    },
    # West Africa (Equatorial Guinea, Mozambique) or Angola -> Cape NB
    {
        "cp": "cape_good_hope",
        "dir": "northbound",
        "laden": True,
        "origin": "Atlantic LNG",
        "via": "Cape NB",
        "days": 45,
    },
    # Australia / SE Asia -> Malacca W -> (then Suez or Cape)
    {
        "cp": "singapore_malacca",
        "dir": "westbound",
        "laden": True,
        "origin": "Asia Pacific LNG",
        "via": "Malacca W",
        "days": 35,
    },
]

# Supplement origin with live region heuristic for known loading zones
_LNG_REGION_ORIGIN: dict[str, str] = {
    "us_gulf": "US Gulf LNG",
    "us_east": "US East LNG",
    "middle_east": "Qatar / ME",
    "ara": "Qatar / ME",
    "west_africa": "Atlantic LNG",
    "russia": "Norway / Russia LNG",
}

# Typical LNG cargo size: Q-Flex = 210k m3, Q-Max = 265k m3, standard TFDE = 160k m3
# 1 m3 LNG ~ 0.6 mmBtu ~ 21.5 MJ; 160k m3 ~ 3.5 bcf ~ 0.099 bcm
_LNG_BCM_PER_CARGO = 0.099


@router.get("/api/analytics/lng-inbound", response_model=LngInboundResponse, tags=["analytics"])
async def get_lng_inbound(horizon_h: int = 72):
    """LNG carriers visible in AIS and their ETA to European regas terminals.

    Returns all LNG tankers found in the live AIS feed (cross-referenced against the
    vessel registry), with ETA estimates for European LNG import terminals derived from
    great-circle distance and current SOG. Origin is inferred from recent chokepoint
    transit history (Suez NB = Qatar, Gibraltar/Dover E laden = US Gulf, etc.).
    """
    from math import atan2, cos, radians, sin, sqrt

    def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 3440.065  # nautical miles
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    now_dt = datetime.now(UTC).replace(tzinfo=None)
    stale_cutoff = now_dt - timedelta(hours=db.STALE_HOURS)

    # --- 1. Load LNG IMOs from registry ---
    reg_df = db.pg_query(
        "SELECT imo, ship_name, owner, ship_type FROM vessels WHERE ship_type LIKE %s OR ship_type LIKE %s",
        ["%LNG%", "%Liquefied Gas%"],
    )
    lng_imos: set[int] = (
        set(reg_df["imo"].dropna().astype(int).tolist()) if not reg_df.empty else set()
    )
    reg_by_imo: dict[int, dict] = {}
    if not reg_df.empty:
        for _, row in reg_df.iterrows():
            reg_by_imo[int(row["imo"])] = {"name": row.get("ship_name"), "owner": row.get("owner")}

    if not lng_imos:
        return LngInboundResponse(
            as_of=now_dt.isoformat(),
            total_lng_visible=0,
            inbound_to_europe=0,
            bcm_inbound=0.0,
            vessels=[],
            by_origin={},
            by_terminal={},
            eta_buckets={},
            us_loading=[],
        )

    # --- 2. Fetch live positions for LNG vessels ---
    imo_list = list(lng_imos)
    live_df = db.query(
        """
        SELECT mmsi, name, lat, lon, sog, cog, heading, destination, kind,
               segment, region, updated_ts, imo, draught, nav_status, eta
        FROM live_positions
        WHERE imo IS NOT NULL
          AND CAST(imo AS BIGINT) = ANY(?)
          AND updated_ts >= ?
        """,
        params=[imo_list, stale_cutoff],
    )

    if live_df.empty:
        return LngInboundResponse(
            as_of=now_dt.isoformat(),
            total_lng_visible=0,
            inbound_to_europe=0,
            bcm_inbound=0.0,
            vessels=[],
            by_origin={},
            by_terminal={},
            eta_buckets={},
            us_loading=[],
        )

    total_lng_visible = len(live_df)

    # --- 3. Laden status from vessel_state ---
    laden_df = db.query("SELECT mmsi, laden FROM vessel_state", db=db.analytics_db_path())
    laden_m: dict[int, str] = {}
    if not laden_df.empty:
        for _, r in laden_df.iterrows():
            laden_m[int(r["mmsi"])] = r["laden"]

    # --- 4. Origin inference via transit_events ---
    mmsi_list_all = live_df["mmsi"].astype(int).tolist()
    origin_m: dict[int, tuple[str, str]] = {}
    for rule in _LNG_ORIGIN_RULES:
        cutoff_t = now_dt - timedelta(days=rule["days"])
        clause = "laden = ?" if rule["laden"] else "1=1"
        dir_clause = "AND direction = ?" if rule["dir"] else ""
        params: list = [mmsi_list_all, cutoff_t, rule["cp"]]
        if rule["dir"]:
            params.append(rule["dir"])
        if rule["laden"]:
            params.append(True)
        rows = db.query(
            f"""
            SELECT mmsi FROM transit_events
            WHERE mmsi = ANY(?)
              AND exited_ts >= ?
              AND chokepoint = ?
              {dir_clause}
              AND {clause}
            """,
            params=params,
            db=db.analytics_db_path(),
        )
        if rows.empty:
            continue
        for mmsi in rows["mmsi"].astype(int).tolist():
            if mmsi not in origin_m:
                origin_m[mmsi] = (rule["origin"], rule["via"])

    # Fill remaining from region heuristic
    for _, row in live_df.iterrows():
        mmsi = int(row["mmsi"])
        if mmsi not in origin_m:
            region = str(row.get("region") or "")
            fallback = _LNG_REGION_ORIGIN.get(region)
            if fallback:
                origin_m[mmsi] = (fallback, "region")

    # --- 5. Match destination to EU LNG terminal and compute ETA ---
    # True ETA (Phase E): precomputed physics ETA + interval to the regas terminal.
    preds_by_mmsi = _runner_eta.predictions_by_mmsi([int(m) for m in live_df["mmsi"].tolist()])
    vessels: list[LngVessel] = []
    by_origin: dict[str, int] = {}
    by_terminal: dict[str, int] = {}
    eta_buckets: dict[str, int] = {"0-6h": 0, "6-12h": 0, "12-24h": 0, "24-48h": 0, "48-72h": 0}

    for _, row in live_df.iterrows():
        mmsi = int(row["mmsi"])
        imo = int(row["imo"]) if row.get("imo") else 0
        lat = float(row["lat"])
        lon = float(row["lon"])
        sog = float(row.get("sog") or 0)
        dest_raw = str(row.get("destination") or "") or None

        # Match destination to terminal
        terminal = match_lng_terminal(dest_raw)
        terminal_country = LNG_EU_TERMINALS[terminal]["country"] if terminal else None
        terminal_lat = LNG_EU_TERMINALS[terminal]["lat"] if terminal else None
        terminal_lon = LNG_EU_TERMINALS[terminal]["lon"] if terminal else None

        # Compute ETA via haversine
        dist_nm: float | None = None
        eta_h: float | None = None
        if terminal_lat is not None:
            dist_nm = haversine_nm(lat, lon, terminal_lat, terminal_lon)
            if sog and sog > 0.5:
                eta_h = dist_nm / sog
            else:
                eta_h = None  # anchored / stopped

        # Apply horizon filter
        if terminal is not None and eta_h is not None and eta_h > horizon_h:
            terminal = None
            terminal_country = None
            dist_nm = None
            eta_h = None

        origin_tup = origin_m.get(mmsi)
        inferred_origin = origin_tup[0] if origin_tup else None
        inferred_via = origin_tup[1] if origin_tup and origin_tup[1] != "region" else None

        laden_val = laden_m.get(mmsi, "unknown")
        reg_info = reg_by_imo.get(imo, {})

        # True ETA for the matched regas terminal (nearest target centroid).
        pred = None
        if terminal is not None and terminal_lat is not None:
            pred = _runner_eta.nearest_prediction(
                preds_by_mmsi.get(mmsi, []), terminal_lat, terminal_lon
            )
        eta_true = pred["eta_p50_h"] if pred else None
        # Primary ETA shown is the true estimate when resolvable, else the naive one.
        primary_eta = eta_true if eta_true is not None else eta_h

        if terminal and primary_eta is not None:
            by_terminal[terminal] = by_terminal.get(terminal, 0) + 1
            if inferred_origin:
                by_origin[inferred_origin] = by_origin.get(inferred_origin, 0) + 1
            bucket = _eta_bucket(primary_eta) if primary_eta <= 48 else "48-72h"
            if bucket in eta_buckets:
                eta_buckets[bucket] += 1

        vessels.append(
            LngVessel(
                mmsi=mmsi,
                imo=imo,
                name=str(row.get("name") or reg_info.get("name") or ""),
                sog=round(sog, 1),
                lat=round(lat, 4),
                lon=round(lon, 4),
                region=str(row.get("region") or "") or None,
                destination_raw=dest_raw,
                terminal=terminal,
                terminal_country=terminal_country,
                eta_hours=round(primary_eta, 1) if primary_eta is not None else None,
                distance_nm=round(dist_nm, 0) if dist_nm is not None else None,
                laden=laden_val,
                inferred_origin=inferred_origin,
                inferred_via=inferred_via,
                registry_name=str(reg_info.get("name") or "") or None,
                owner=str(reg_info.get("owner") or "") or None,
                eta_true_h=round(eta_true, 1) if eta_true is not None else None,
                eta_low_h=round(pred["eta_low_h"], 1)
                if pred and pred.get("eta_low_h") is not None
                else None,
                eta_high_h=round(pred["eta_high_h"], 1)
                if pred and pred.get("eta_high_h") is not None
                else None,
                eta_naive_h=round(eta_h, 1) if eta_h is not None else None,
                eta_method=pred["method"] if pred else None,
            )
        )

    # Sort: EU-bound first (by ETA), then others by name
    vessels.sort(
        key=lambda v: (
            v.terminal is None,
            v.eta_hours if v.eta_hours is not None else 9999,
            v.name or "",
        )
    )
    inbound_to_europe = sum(1 for v in vessels if v.terminal is not None)
    bcm_inbound = round(inbound_to_europe * _LNG_BCM_PER_CARGO, 3)
    by_origin = dict(sorted(by_origin.items(), key=lambda x: x[1], reverse=True))
    by_terminal = dict(sorted(by_terminal.items(), key=lambda x: x[1], reverse=True))
    eta_buckets = {k: v for k, v in eta_buckets.items() if v > 0}

    # --- 6. US LNG loading terminal activity ---
    us_loading: list[LngLoadingVessel] = []
    for _, row in live_df.iterrows():
        lat = float(row["lat"])
        lon = float(row["lon"])
        sog = float(row.get("sog") or 0)
        # Only consider vessels in the Western Hemisphere (US terminals all lon < -60)
        if lon > -60 or lon < -110:
            continue
        # Find nearest US terminal within radius
        nearest: dict | None = None
        nearest_dist = float("inf")
        for term in US_LNG_LOADING_TERMINALS:
            dist = haversine_nm(lat, lon, term["lat"], term["lon"])
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = term
        if nearest is None or nearest_dist > _US_LNG_TERMINAL_RADIUS_NM:
            continue
        mmsi = int(row["mmsi"])
        imo = int(row["imo"]) if row.get("imo") else 0
        status = "loading" if sog < 1.5 else "departing"
        # For departing vessels, estimate ETA to nearest EU terminal
        # Approx 4700nm US Gulf -> NW Europe at 15kn -> ~313h -> ~13 days
        eu_eta_days: float | None = None
        if status == "departing" and sog > 1:
            eu_eta_days = round(4700 / sog / 24, 1)
        reg_info = reg_by_imo.get(imo, {})
        us_loading.append(
            LngLoadingVessel(
                mmsi=mmsi,
                imo=imo,
                name=str(row.get("name") or reg_info.get("name") or ""),
                sog=round(sog, 1),
                lat=round(lat, 4),
                lon=round(lon, 4),
                terminal_name=nearest["name"],
                status=status,
                destination_raw=str(row.get("destination") or "") or None,
                eu_terminal_eta_days=eu_eta_days,
            )
        )

    us_loading.sort(key=lambda v: (v.status != "loading", v.name or ""))

    return LngInboundResponse(
        as_of=now_dt.isoformat(),
        total_lng_visible=total_lng_visible,
        inbound_to_europe=inbound_to_europe,
        bcm_inbound=bcm_inbound,
        vessels=vessels,
        by_origin=by_origin,
        by_terminal=by_terminal,
        eta_buckets=eta_buckets,
        us_loading=us_loading,
    )
