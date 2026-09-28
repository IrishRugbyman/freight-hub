"""Analytics: cargo state (laden/ballast), destination flows, trade lanes, crude on water."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter

from .. import db
from ..common import iso, str_or_none, valid_imo
from ..live import live_all
from ..ports import canonical_destination
from ..schemas import (
    CargoStateChangeRow,
    CargoStateChangesResponse,
    CargoTransitionEvent,
    CargoTransitionsResponse,
    CrudeOnWaterResponse,
    CrudeSegmentRow,
    DestinationFlowRow,
    DestinationFlowsResponse,
    InboundRegionRow,
    LadenResponse,
    LadenSegment,
    TradeLaneCell,
    TradeLaneMatrixResponse,
)

router = APIRouter()


@router.get("/api/analytics/cargo-transitions", response_model=CargoTransitionsResponse)
def analytics_cargo_transitions(days: int = 7, min_change: float = 2.0, segment: str = ""):
    """Vessels with significant draught step-changes: cargo loading or discharge events.

    Groups each vessel's draught history into 6-hour buckets (median), then finds the
    largest single-bucket-to-bucket step. Loading = draught increases, discharging =
    draught decreases. Only fires when the step >= min_change metres and the bucket
    has >= 2 fixes (filters static-data noise).
    """
    days = max(1, min(30, days))
    min_change = max(0.5, min(20.0, min_change))
    since = (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)
    now = datetime.now(UTC).replace(tzinfo=None)

    seg_clause = " AND segment = ?" if segment else ""
    seg_params: list = [segment] if segment else []

    # Pre-filter: vessels with enough draught variation (fast aggregation)
    cand_df = db.query(
        "SELECT mmsi, MAX(draught) - MIN(draught) as draught_range "
        "FROM ais_snapshots "
        f"WHERE snapshot_ts >= ? AND draught > 0 AND segment != 'Small' {seg_clause} "
        "GROUP BY mmsi "
        "HAVING COUNT(*) >= 6 AND MAX(draught) - MIN(draught) >= ? "
        "ORDER BY MAX(draught) - MIN(draught) DESC "
        "LIMIT 500",
        [since] + seg_params + [min_change * 0.7],
    )
    if cand_df.empty:
        return CargoTransitionsResponse(
            as_of=iso(now) or "", days=days, min_change=min_change, rows=[]
        )

    cand_mmsis = [int(m) for m in cand_df["mmsi"].unique()]
    ph = ",".join("?" * len(cand_mmsis))

    snap_df = db.query(
        f"SELECT mmsi, snapshot_ts, draught, lat, lon, kind, segment, region "
        f"FROM ais_snapshots "
        f"WHERE snapshot_ts >= ? AND draught > 0 AND mmsi IN ({ph}) "
        f"ORDER BY mmsi, snapshot_ts",
        [since] + cand_mmsis,
    )
    if snap_df.empty:
        return CargoTransitionsResponse(
            as_of=iso(now) or "", days=days, min_change=min_change, rows=[]
        )

    snap_df["snapshot_ts"] = pd.to_datetime(snap_df["snapshot_ts"])

    # Floor timestamps to 6h buckets (dt.floor handles datetime64[us] and datetime64[ns])
    snap_df["bucket"] = snap_df["snapshot_ts"].dt.floor("6h")

    transitions: list[dict] = []
    for mmsi_val, grp in snap_df.groupby("mmsi"):
        bucket_agg = (
            grp.groupby("bucket")
            .agg(
                d_median=("draught", "median"),
                lat=("lat", "median"),
                lon=("lon", "median"),
                fix_cnt=("draught", "count"),
            )
            .reset_index()
        )
        bucket_agg = (
            bucket_agg[bucket_agg["fix_cnt"] >= 2].sort_values("bucket").reset_index(drop=True)
        )
        if len(bucket_agg) < 2:
            continue

        bucket_agg["prev_d"] = bucket_agg["d_median"].shift(1)
        bucket_agg["change"] = bucket_agg["d_median"] - bucket_agg["prev_d"]
        bucket_agg = bucket_agg.dropna(subset=["change"])
        if bucket_agg.empty:
            continue

        max_idx = bucket_agg["change"].abs().idxmax()
        best = bucket_agg.loc[max_idx]
        change_val = float(best["change"])
        if abs(change_val) < min_change:
            continue

        # Bucket timestamp directly from pd.Timestamp floor
        trans_ts = best["bucket"]
        if hasattr(trans_ts, "to_pydatetime"):
            trans_ts = trans_ts.to_pydatetime().replace(tzinfo=None)

        # Most-common kind/segment/region for this vessel
        kind_val = (
            str_or_none(grp["kind"].mode().iloc[0]) if not grp["kind"].dropna().empty else None
        )
        seg_val = (
            str_or_none(grp["segment"].mode().iloc[0])
            if not grp["segment"].dropna().empty
            else None
        )
        region_slice = grp[
            (grp["snapshot_ts"] >= pd.Timestamp(trans_ts))
            & (grp["snapshot_ts"] < pd.Timestamp(trans_ts) + pd.Timedelta(hours=6))
        ]
        region_val = str_or_none(
            region_slice["region"].mode().iloc[0]
            if not region_slice.empty and not region_slice["region"].dropna().empty
            else (grp["region"].mode().iloc[0] if not grp["region"].dropna().empty else None)
        )

        transitions.append(
            {
                "mmsi": int(mmsi_val),
                "kind": kind_val,
                "segment": seg_val,
                "region": region_val,
                "direction": "loading" if change_val > 0 else "discharging",
                "draught_before": round(float(best["prev_d"]), 1),
                "draught_after": round(float(best["d_median"]), 1),
                "change_m": round(abs(change_val), 1),
                "transition_ts": iso(trans_ts) or "",
                "lat": round(float(best["lat"]), 4),
                "lon": round(float(best["lon"]), 4),
            }
        )

    if not transitions:
        return CargoTransitionsResponse(
            as_of=iso(now) or "", days=days, min_change=min_change, rows=[]
        )

    transitions.sort(key=lambda t: t["change_m"], reverse=True)

    # Enrich with vessel names
    all_mmsis = [t["mmsi"] for t in transitions[:100]]
    ph2 = ",".join("?" * len(all_mmsis))

    mmsi_name: dict[int, str | None] = {}
    lp_df = db.query(f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({ph2})", all_mmsis)
    for _, r in lp_df.iterrows():
        mmsi_name[int(r["mmsi"])] = str_or_none(r.get("name"))
    missing = [m for m in all_mmsis if m not in mmsi_name]
    if missing:
        ph3 = ",".join("?" * len(missing))
        v_df = db.query(f"SELECT mmsi, name FROM vessels WHERE mmsi IN ({ph3})", missing)
        for _, r in v_df.iterrows():
            mmsi_name[int(r["mmsi"])] = str_or_none(r.get("name"))

    # MMSI -> IMO -> risk score
    mmsi_imo: dict[int, int | None] = {}
    lp2 = db.query(f"SELECT mmsi, imo FROM live_positions WHERE mmsi IN ({ph2})", all_mmsis)
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
    for t in transitions[:100]:
        mmsi_val = t["mmsi"]
        imo_val = mmsi_imo.get(mmsi_val)
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}
        rows.append(
            CargoTransitionEvent(
                mmsi=mmsi_val,
                name=mmsi_name.get(mmsi_val),
                kind=t["kind"],
                segment=t["segment"],
                region=t["region"],
                direction=t["direction"],
                draught_before=t["draught_before"],
                draught_after=t["draught_after"],
                change_m=t["change_m"],
                transition_ts=t["transition_ts"],
                lat=t.get("lat"),
                lon=t.get("lon"),
                risk_score=risk_info.get("risk_score"),
                ofac=bool(risk_info.get("ofac", False)),
            )
        )

    return CargoTransitionsResponse(
        as_of=iso(now) or "", days=days, min_change=min_change, rows=rows
    )


@router.get("/api/analytics/laden", response_model=LadenResponse)
def analytics_laden(kind: str = "tanker"):
    """Current fleet laden/ballast/unknown split by segment from fleet_density.

    Uses the most recent hour of fleet_density (which already aggregates
    laden/ballast counts per segment/kind) rather than a cross-DB join
    between vessel_state and live_positions.
    """
    df = db.query(
        "SELECT segment, kind, laden_count, ballast_count, unknown_count "
        "FROM fleet_density "
        "WHERE ts = (SELECT max(ts) FROM fleet_density) AND kind = ? AND segment != 'Small'",
        [kind],
        db=db.analytics_db_path(),
    )

    if df.empty:
        return LadenResponse(kind=kind, segments=[])

    result: dict[str, dict[str, int]] = {}
    for _, row in df.iterrows():
        seg = str(row["segment"])
        entry = result.setdefault(seg, {"laden": 0, "ballast": 0, "unknown": 0})
        entry["laden"] += int(row["laden_count"] or 0)
        entry["ballast"] += int(row["ballast_count"] or 0)
        entry["unknown"] += int(row["unknown_count"] or 0)

    segments = [
        LadenSegment(segment=seg, laden=v["laden"], ballast=v["ballast"], unknown=v["unknown"])
        for seg, v in sorted(result.items())
    ]
    return LadenResponse(kind=kind, segments=segments)


@router.get("/api/analytics/destination-flows", response_model=DestinationFlowsResponse)
def analytics_destination_flows(
    kind: str = "",
    segment: str = "",
    region: str = "",
    top_n: int = 20,
    laden_only: bool = True,
):
    """Cargo destination flow: where are laden (or all) vessels heading?

    Two-step pattern: vessel_state (analytics DB) provides laden/ballast classification,
    live_positions (AIS DB) provides destination, region, and segment.

    Returns top-N flows by origin_region x destination x segment, vessel_count descending.
    Filters out 'Small' segment noise. Non-standard destination strings are shown verbatim.
    """
    top_n = max(5, min(100, top_n))
    now_ts = datetime.now(UTC).replace(tzinfo=None)

    # Step 1: get laden MMSIs from vessel_state (analytics DB)
    mmsi_filter: list[int] | None = None
    total_laden = 0
    if laden_only:
        laden_df = db.query(
            "SELECT mmsi FROM vessel_state WHERE laden = 'laden'",
            db=db.analytics_db_path(),
        )
        if laden_df.empty:
            return DestinationFlowsResponse(
                as_of=iso(now_ts) or "", laden_only=laden_only, total_laden=0, rows=[]
            )
        mmsi_filter = [int(m) for m in laden_df["mmsi"].unique()]
        total_laden = len(mmsi_filter)

    # Step 2: query live_positions for flow aggregation
    conds: list[str] = ["destination IS NOT NULL", "TRIM(destination) != ''", "segment != 'Small'"]
    params: list = []

    if mmsi_filter is not None:
        ph = ",".join("?" * len(mmsi_filter))
        conds.append(f"mmsi IN ({ph})")
        params.extend(mmsi_filter)
    if kind:
        conds.append("kind = ?")
        params.append(kind)
    if segment:
        conds.append("segment = ?")
        params.append(segment)
    if region:
        conds.append("region = ?")
        params.append(region)

    # Group by RAW destination in SQL, fold onto canonical ports in Python, then
    # apply top_n (folding after the LIMIT would split one port across spellings).
    flow_df = db.query(
        "SELECT region AS origin_region, destination, segment, kind, COUNT(*) AS vessel_count "
        "FROM live_positions "
        f"WHERE {' AND '.join(conds)} "
        "GROUP BY region, destination, segment, kind",
        params,
    )

    if flow_df.empty:
        return DestinationFlowsResponse(
            as_of=iso(now_ts) or "", laden_only=laden_only, total_laden=total_laden, rows=[]
        )

    flow_agg: dict[tuple, int] = {}
    for _, r in flow_df.iterrows():
        canon = canonical_destination(str(r["destination"]))
        if canon is None:
            continue
        fkey = (
            str_or_none(r.get("origin_region")) or "unknown",
            canon,
            str_or_none(r.get("segment")),
            str_or_none(r.get("kind")),
        )
        flow_agg[fkey] = flow_agg.get(fkey, 0) + int(r["vessel_count"])

    rows_out = [
        DestinationFlowRow(
            origin_region=k[0], destination=k[1], segment=k[2], kind=k[3], vessel_count=c
        )
        for k, c in sorted(flow_agg.items(), key=lambda kv: -kv[1])[:top_n]
    ]

    return DestinationFlowsResponse(
        as_of=iso(now_ts) or "",
        laden_only=laden_only,
        total_laden=total_laden,
        rows=rows_out,
    )


_DEST_REGION_MAP: dict[str, str] = {
    # Far East
    "CN": "Far East",
    "HK": "Far East",
    "TW": "Far East",
    "KR": "Far East",
    "JP": "Far East",
    # Southeast Asia
    "SG": "SE Asia",
    "MY": "SE Asia",
    "TH": "SE Asia",
    "ID": "SE Asia",
    "PH": "SE Asia",
    "VN": "SE Asia",
    # South Asia
    "IN": "South Asia",
    "PK": "South Asia",
    "LK": "South Asia",
    "BD": "South Asia",
    # Middle East
    "AE": "Middle East",
    "SA": "Middle East",
    "KW": "Middle East",
    "IQ": "Middle East",
    "IR": "Middle East",
    "QA": "Middle East",
    "OM": "Middle East",
    "BH": "Middle East",
    "YE": "Middle East",
    # Europe (NW)
    "NL": "NW Europe",
    "BE": "NW Europe",
    "GB": "NW Europe",
    "FR": "NW Europe",
    "DE": "NW Europe",
    "DK": "NW Europe",
    "NO": "NW Europe",
    "SE": "NW Europe",
    "FI": "NW Europe",
    "PL": "NW Europe",
    "LV": "NW Europe",
    "LT": "NW Europe",
    "EE": "NW Europe",
    "IE": "NW Europe",
    # Mediterranean
    "ES": "Med",
    "IT": "Med",
    "PT": "Med",
    "GR": "Med",
    "TR": "Med",
    "EG": "Med",
    "LY": "Med",
    "TN": "Med",
    "MA": "Med",
    "DZ": "Med",
    "MT": "Med",
    "HR": "Med",
    # Americas
    "US": "Americas",
    "MX": "Americas",
    "PA": "Americas",
    "CA": "Americas",
    "CO": "Americas",
    "VE": "Americas",
    "BR": "Americas",
    "AR": "Americas",
    "CL": "Americas",
    "PE": "Americas",
    "EC": "Americas",
    "TT": "Americas",
    # West Africa
    "NG": "W Africa",
    "AO": "W Africa",
    "CI": "W Africa",
    "GH": "W Africa",
    "CM": "W Africa",
    "SN": "W Africa",
    "TG": "W Africa",
    "CD": "W Africa",
    "GA": "W Africa",
    # East Africa / Indian Ocean
    "TZ": "E Africa",
    "KE": "E Africa",
    "MZ": "E Africa",
    "MU": "E Africa",
    "ZA": "S Africa",
    # Australia / Pacific
    "AU": "Oceania",
    "NZ": "Oceania",
    # Baltic / Black Sea
    "RU": "Russia/CIS",
    "UA": "Russia/CIS",
    "KZ": "Russia/CIS",
    "BY": "Russia/CIS",
}


def _dest_to_region(dest: str | None) -> str:
    """Map a 5-char UNLOCODE destination to a macro-region using the first 2 chars."""
    if not dest or len(dest) < 2:
        return "Unknown"
    return _DEST_REGION_MAP.get(dest[:2].upper(), "Unknown")


@router.get("/api/analytics/trade-lane-matrix", response_model=TradeLaneMatrixResponse)
def analytics_trade_lane_matrix(
    kind: str | None = None,
    laden_only: bool = True,
):
    """Trade lane intensity matrix: origin AIS region -> destination macro-region.

    Maps vessel destinations (UNLOCODE) to macro-regions (Far East, NW Europe, etc.)
    and counts vessels per (origin_region, dest_region) pair.
    Enriches with high-risk vessel counts (behavioral_score >= 50 OR registry_risk >= 50 OR OFAC).
    """
    now_ts = datetime.now(UTC).replace(tzinfo=None)

    # Step 1: live positions with destination
    lp_conds = ["segment != 'Small'", "destination IS NOT NULL", "destination != ''"]
    lp_params: list = []
    if kind:
        lp_conds.append("kind = ?")
        lp_params.append(kind)

    lp_df = live_all()
    if not lp_df.empty:
        lp_df = lp_df[
            (lp_df["segment"] != "Small")
            & lp_df["destination"].notna()
            & (lp_df["destination"] != "")
        ].copy()
        if kind:
            lp_df = lp_df[lp_df["kind"] == kind]
        needed = ["mmsi", "imo", "region", "destination"]
        lp_df = lp_df[[c for c in needed if c in lp_df.columns]]
    if lp_df.empty:
        return TradeLaneMatrixResponse(
            as_of=iso(now_ts) or "",
            kind=kind or "",
            laden_only=laden_only,
            origin_regions=[],
            dest_regions=[],
            cells=[],
        )

    # Step 2: laden filter via vessel_state
    laden_mmsi: set[int] = set()
    if laden_only:
        vs_df = db.query(
            "SELECT mmsi FROM vessel_state WHERE laden = 'laden'",
            db=db.analytics_db_path(),
        )
        if not vs_df.empty:
            laden_mmsi = {int(m) for m in vs_df["mmsi"]}

    # Step 3: risk scores from analytics DB (events) + registry
    rr_df = db.query(
        "SELECT mmsi, COUNT(*) AS cnt FROM ais_events WHERE type = 'reroute' GROUP BY mmsi",
        db=db.analytics_db_path(),
    )
    reroute_map: dict[int, int] = {}
    if not rr_df.empty:
        for _, r in rr_df.iterrows():
            reroute_map[int(r["mmsi"])] = int(r["cnt"])

    sts_df = db.query(
        "SELECT mmsi, COUNT(*) AS cnt FROM ais_events WHERE type = 'sts' GROUP BY mmsi "
        "UNION ALL "
        "SELECT mmsi2 AS mmsi, COUNT(*) AS cnt FROM ais_events WHERE type = 'sts' AND mmsi2 IS NOT NULL GROUP BY mmsi2",
        db=db.analytics_db_path(),
    )
    sts_map: dict[int, int] = {}
    if not sts_df.empty:
        for _, r in sts_df.iterrows():
            m = int(r["mmsi"])
            sts_map[m] = sts_map.get(m, 0) + int(r["cnt"])

    # Registry risk
    live_imos = [
        valid_imo(r.get("imo")) for _, r in lp_df.iterrows() if valid_imo(r.get("imo")) is not None
    ]
    reg_map: dict[int, dict] = {}  # imo -> {risk_score, ofac}
    if live_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score, ofac_sanctioned FROM vessels "
            "WHERE imo = ANY(%s) AND fetch_ok = true",
            [live_imos],
        )
        if not reg_df.empty:
            for _, r in reg_df.iterrows():
                imo_v = valid_imo(r.get("imo"))
                if imo_v:
                    reg_map[imo_v] = {
                        "risk_score": int(r["risk_score"])
                        if r.get("risk_score") is not None and not pd.isna(r["risk_score"])
                        else None,
                        "ofac": bool(r["ofac_sanctioned"])
                        if r.get("ofac_sanctioned") is not None
                        and not pd.isna(r["ofac_sanctioned"])
                        else False,
                    }

    # Step 4: aggregate cells
    from collections import defaultdict

    cell_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "high_risk": 0, "laden": 0}
    )
    origin_totals: dict[str, int] = defaultdict(int)
    dest_totals: dict[str, int] = defaultdict(int)

    for _, row in lp_df.iterrows():
        mmsi = int(row["mmsi"])
        origin = str(row["region"]) if row.get("region") else "Unknown"
        dest_region = _dest_to_region(str_or_none(row.get("destination")))

        if laden_only and mmsi not in laden_mmsi:
            continue

        # Is this a high-risk vessel?
        imo = valid_imo(row.get("imo"))
        reg = reg_map.get(imo) if imo else None
        behavioral = min(sts_map.get(mmsi, 0) * 20 + reroute_map.get(mmsi, 0) * 5, 100)
        registry_risk = reg["risk_score"] if reg else None
        ofac = reg["ofac"] if reg else False
        if reg and registry_risk is not None:
            total_score = round(behavioral * 0.4 + registry_risk * 0.6) + (25 if ofac else 0)
        else:
            total_score = behavioral + (25 if ofac else 0)
        is_high_risk = (total_score >= 50) or ofac

        key = (origin, dest_region)
        cell_counts[key]["total"] += 1
        cell_counts[key]["laden"] += 1 if mmsi in laden_mmsi else 0
        if is_high_risk:
            cell_counts[key]["high_risk"] += 1
        origin_totals[origin] += 1
        dest_totals[dest_region] += 1

    if not cell_counts:
        return TradeLaneMatrixResponse(
            as_of=iso(now_ts) or "",
            kind=kind or "",
            laden_only=laden_only,
            origin_regions=[],
            dest_regions=[],
            cells=[],
        )

    origin_regions = sorted(origin_totals, key=lambda r: -origin_totals[r])
    dest_regions = sorted(dest_totals, key=lambda r: -dest_totals[r])

    cells: list[TradeLaneCell] = []
    for (orig, dest), counts in sorted(cell_counts.items(), key=lambda kv: -kv[1]["total"]):
        cells.append(
            TradeLaneCell(
                origin_region=orig,
                dest_region=dest,
                vessel_count=counts["total"],
                high_risk_count=counts["high_risk"],
                laden_count=counts["laden"],
            )
        )

    return TradeLaneMatrixResponse(
        as_of=iso(now_ts) or "",
        kind=kind or "",
        laden_only=laden_only,
        origin_regions=origin_regions,
        dest_regions=dest_regions,
        cells=cells,
    )


@router.get("/api/analytics/cargo-state-changes", response_model=CargoStateChangesResponse)
def analytics_cargo_state_changes(
    days: int = 7, kind: str = "tanker", min_change_m: float = 1.5, limit: int = 100
):
    """Detect cargo loading/discharge by comparing draught at start vs end of port stays.

    Uses anchored_episodes joined with ais_snapshots (nearest snapshot to start/end).
    draught_change_m > 0 = loaded (draught increased = cargo loaded).
    draught_change_m < 0 = discharged (draught decreased = cargo offloaded).
    min_change_m: minimum draught change to report (default 1.5m, ~typical ballast vs laden).
    kind: vessel type filter (default tanker; set to 'bulk' for dry bulk).
    limit: clamped to [1, 500].
    """
    days = max(1, min(days, 30))
    limit = max(1, min(limit, 500))
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_dt - timedelta(days=days)

    # Fetch completed anchored episodes
    kind_clause = ""
    params_an: list = [cutoff]
    if kind:
        kind_clause = "AND kind = ? "
        params_an.append(kind)

    eps_df = db.query(
        f"SELECT mmsi, zone, kind, segment, start_ts, end_ts "
        f"FROM anchored_episodes "
        f"WHERE start_ts >= ? {kind_clause}"
        f"AND end_ts IS NOT NULL AND segment != 'Small' "
        f"ORDER BY start_ts DESC LIMIT 2000",
        params_an,
        db=db.analytics_db_path(),
    )
    if eps_df.empty:
        return CargoStateChangesResponse(
            as_of=now_dt.isoformat(), days=days, total_events=0, rows=[]
        )

    eps_df["start_ts"] = pd.to_datetime(eps_df["start_ts"])
    eps_df["end_ts"] = pd.to_datetime(eps_df["end_ts"])

    # For each episode, find the nearest draught snapshot near start and end
    # Batch query: get all snapshots for these MMSIs in the time window
    mmsi_list = [int(m) for m in eps_df["mmsi"].unique().tolist()]
    if not mmsi_list:
        return CargoStateChangesResponse(
            as_of=now_dt.isoformat(), days=days, total_events=0, rows=[]
        )

    snaps_df = db.query(
        "SELECT mmsi, snapshot_ts, draught, lat, lon "
        "FROM ais_snapshots "
        "WHERE mmsi IN (" + ",".join("?" * len(mmsi_list)) + ") "
        "AND snapshot_ts >= ? "
        "AND draught IS NOT NULL AND draught > 0.5 "
        "ORDER BY mmsi, snapshot_ts",
        mmsi_list + [cutoff],
    )

    if snaps_df.empty:
        return CargoStateChangesResponse(
            as_of=now_dt.isoformat(), days=days, total_events=0, rows=[]
        )

    snaps_df["snapshot_ts"] = pd.to_datetime(snaps_df["snapshot_ts"])

    # Enrich with live position and registry
    live_df = db.query(
        "SELECT mmsi, name, imo, region FROM live_positions "
        "WHERE mmsi IN (" + ",".join("?" * len(mmsi_list)) + ")",
        mmsi_list,
    )
    live_map: dict[int, dict] = {int(r["mmsi"]): r.to_dict() for _, r in live_df.iterrows()}

    # Registry risk scores
    imo_list = [
        int(v["imo"]) for v in live_map.values() if v.get("imo") and not pd.isna(v.get("imo"))
    ]
    risk_map: dict[int, int] = {}
    if imo_list:
        reg_df = db.pg_query(
            "SELECT imo, risk_score FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [imo_list],
        )
        if not reg_df.empty:
            for _, r in reg_df.iterrows():
                imo_v = r.get("imo")
                risk_v = r.get("risk_score")
                if imo_v and not pd.isna(imo_v) and risk_v and not pd.isna(risk_v):
                    risk_map[int(imo_v)] = int(risk_v)

    rows = []
    for _, ep in eps_df.iterrows():
        mmsi_int = int(ep["mmsi"])
        start_ts = ep["start_ts"].replace(tzinfo=None)
        end_ts = ep["end_ts"].replace(tzinfo=None)

        # Get snapshots for this vessel
        v_snaps = snaps_df[snaps_df["mmsi"] == mmsi_int].sort_values("snapshot_ts")
        if v_snaps.empty:
            continue

        # Find nearest snapshot to start and end (within 2 hours)
        start_ts_ns = pd.Timestamp(start_ts)
        end_ts_ns = pd.Timestamp(end_ts)

        start_idx = (v_snaps["snapshot_ts"] - start_ts_ns).abs().idxmin()
        end_idx = (v_snaps["snapshot_ts"] - end_ts_ns).abs().idxmin()

        start_snap = v_snaps.loc[start_idx]
        end_snap = v_snaps.loc[end_idx]

        start_dt_diff = abs(
            (start_snap["snapshot_ts"].replace(tzinfo=None) - start_ts).total_seconds()
        )
        end_dt_diff = abs((end_snap["snapshot_ts"].replace(tzinfo=None) - end_ts).total_seconds())

        # Only use snapshots within 2 hours of episode start/end
        if start_dt_diff > 7200 or end_dt_diff > 7200:
            continue

        d_entry = float(start_snap["draught"]) if not pd.isna(start_snap["draught"]) else None
        d_exit = float(end_snap["draught"]) if not pd.isna(end_snap["draught"]) else None

        if d_entry is None or d_exit is None:
            continue

        change_m = round(d_exit - d_entry, 2)
        if abs(change_m) < min_change_m:
            continue

        if change_m > 0:
            cargo_state = "loaded"
        else:
            cargo_state = "discharged"

        dwell_hours = round((end_ts - start_ts).total_seconds() / 3600, 1)
        live = live_map.get(mmsi_int)
        imo_val = valid_imo(live.get("imo")) if live else None
        risk_score = risk_map.get(imo_val) if imo_val else None

        rows.append(
            CargoStateChangeRow(
                mmsi=mmsi_int,
                name=str_or_none(live.get("name")) if live else None,
                imo=imo_val,
                kind=str_or_none(ep.get("kind")),
                segment=str_or_none(ep.get("segment")),
                zone=str(ep["zone"]),
                region=str_or_none(live.get("region")) if live else None,
                start_ts=start_ts.isoformat(),
                end_ts=end_ts.isoformat(),
                dwell_hours=dwell_hours,
                draught_entry=d_entry,
                draught_exit=d_exit,
                draught_change_m=change_m,
                cargo_state=cargo_state,
                lat=float(end_snap["lat"]) if not pd.isna(end_snap["lat"]) else None,
                lon=float(end_snap["lon"]) if not pd.isna(end_snap["lon"]) else None,
                registry_risk=risk_score,
            )
        )

    rows.sort(key=lambda r: abs(r.draught_change_m or 0), reverse=True)
    rows = rows[:limit]

    return CargoStateChangesResponse(
        as_of=now_dt.isoformat(),
        days=days,
        total_events=len(rows),
        rows=rows,
    )


# Average laden DWT per tanker segment (industry proxy, million barrels at 7.33 bbl/tonne)
# MB = DWT * load_factor * 7.33 / 1_000_000
_SEGMENT_DWT: dict[str, float] = {
    "ULCC": 400_000,
    "VLCC": 300_000,
    "Suezmax": 157_000,
    "Aframax": 105_000,
    "Panamax": 74_000,
    "Small": 45_000,
}
_LOAD_FACTOR = 0.90  # vessels are ~90% full when laden
_BBL_PER_TONNE = 7.33

# ISO 2-letter country code to broad import/export region
_CC_TO_REGION: dict[str, str] = {
    # Europe
    "NL": "Europe",
    "BE": "Europe",
    "GB": "Europe",
    "DE": "Europe",
    "FR": "Europe",
    "ES": "Europe",
    "IT": "Europe",
    "PT": "Europe",
    "GR": "Europe",
    "NO": "Europe",
    "SE": "Europe",
    "DK": "Europe",
    "FI": "Europe",
    "PL": "Europe",
    "RO": "Europe",
    "HR": "Europe",
    "SI": "Europe",
    "MT": "Europe",
    "CY": "Europe",
    "IE": "Europe",
    "TR": "Europe",
    "LV": "Europe",
    "LT": "Europe",
    "EE": "Europe",
    "IS": "Europe",
    "BG": "Europe",
    "AL": "Europe",
    "MK": "Europe",
    "ME": "Europe",
    # China
    "CN": "China",
    # NE Asia
    "KR": "NE Asia",
    "JP": "NE Asia",
    "TW": "NE Asia",
    # India / S Asia
    "IN": "India / S Asia",
    "PK": "India / S Asia",
    "BD": "India / S Asia",
    "LK": "India / S Asia",
    # SE Asia
    "SG": "SE Asia",
    "MY": "SE Asia",
    "TH": "SE Asia",
    "ID": "SE Asia",
    "VN": "SE Asia",
    "PH": "SE Asia",
    # Americas
    "US": "Americas",
    "CA": "Americas",
    "MX": "Americas",
    "BR": "Americas",
    "AR": "Americas",
    "CL": "Americas",
    "PA": "Americas",
    "VE": "Americas",
    "CO": "Americas",
    "PE": "Americas",
    "EC": "Americas",
    "UY": "Americas",
    "TT": "Americas",
    "CU": "Americas",
    # Middle East / Gulf
    "SA": "Middle East",
    "AE": "Middle East",
    "IQ": "Middle East",
    "KW": "Middle East",
    "OM": "Middle East",
    "QA": "Middle East",
    "BH": "Middle East",
    "YE": "Middle East",
    "IR": "Middle East",
    # Africa
    "ZA": "Africa",
    "NG": "Africa",
    "EG": "Africa",
    "DZ": "Africa",
    "LY": "Africa",
    "MA": "Africa",
    "TZ": "Africa",
    "MZ": "Africa",
    "AO": "Africa",
    "CI": "Africa",
    "SN": "Africa",
    "GH": "Africa",
    "GA": "Africa",
    "CM": "Africa",
    "KE": "Africa",
    # Oceania
    "AU": "Oceania",
    "NZ": "Oceania",
}


def _crude_import_region(destination: str | None) -> str:
    """Map an AIS destination string to a broad import region (crude-on-water card)."""
    if not destination:
        return "Unknown"
    d = destination.strip().upper()
    if len(d) >= 2 and d[:2].isalpha():
        cc = d[:2]
        region = _CC_TO_REGION.get(cc)
        if region:
            return region
    return "Unknown"


def _segment_mb(segment: str | None) -> float:
    """Return estimated million barrels for one laden vessel of given segment."""
    dwt = _SEGMENT_DWT.get(segment or "", _SEGMENT_DWT["Small"])
    return round(dwt * _LOAD_FACTOR * _BBL_PER_TONNE / 1_000_000, 3)


_CRUDE_SEGMENTS: frozenset[str] = frozenset({"ULCC", "VLCC", "Suezmax", "Aframax", "Panamax"})


@router.get("/api/analytics/crude-on-water", response_model=CrudeOnWaterResponse)
def analytics_crude_on_water(crude_only: bool = True):
    """Live laden tanker count and estimated million barrels on water.

    crude_only (default True): restrict to crude-carrying vessel classes
    (ULCC, VLCC, Suezmax, Aframax, Panamax). Excludes 'Small' segment which
    includes inland waterway barges (predominantly ARA river traffic) and
    coastal product tankers that do not carry crude oil.
    """
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    # 1. Get laden state from analytics DB (vessel_state)
    vs_df = db.query(
        "SELECT mmsi, laden FROM vessel_state",
        db=db.analytics_db_path(),
    )
    laden_map: dict[int, str] = {}
    if not vs_df.empty:
        for _, r in vs_df.iterrows():
            m = r.get("mmsi")
            laden_val = r.get("laden")
            if m is not None and laden_val is not None:
                laden_map[int(m)] = str(laden_val)

    # 2. Get live tankers (use cache to avoid DB lock contention)
    live_df = live_all()
    if not live_df.empty:
        live_df = live_df[live_df["kind"] == "tanker"]
        if crude_only and not live_df.empty:
            live_df = live_df[live_df["segment"].isin(_CRUDE_SEGMENTS)]
    if live_df.empty:
        return CrudeOnWaterResponse(
            as_of=now_dt.isoformat(),
            total_laden_tankers=0,
            total_ballast_tankers=0,
            estimated_mb_on_water=0.0,
            by_segment=[],
            inbound_regions=[],
        )

    # 3. Merge laden state onto live_positions
    live_df = live_df.copy()
    live_df["mmsi_int"] = live_df["mmsi"].apply(lambda x: int(x) if x is not None else None)
    live_df["laden_state"] = live_df["mmsi_int"].apply(lambda m: laden_map.get(m, "unknown"))

    # 4. Aggregate by segment
    segment_buckets: dict[str, dict] = {}
    for _, r in live_df.iterrows():
        raw_seg = r.get("segment")
        seg = (
            str(raw_seg)
            if raw_seg and not (isinstance(raw_seg, float) and pd.isna(raw_seg))
            else "Small"
        )
        if seg not in segment_buckets:
            segment_buckets[seg] = {"laden": 0, "ballast": 0, "unknown": 0}
        state = r.get("laden_state", "unknown")
        if state == "laden":
            segment_buckets[seg]["laden"] += 1
        elif state == "ballast":
            segment_buckets[seg]["ballast"] += 1
        else:
            segment_buckets[seg]["unknown"] += 1

    # Sort segments by DWT descending
    seg_order = list(_SEGMENT_DWT.keys())
    by_segment_rows: list[CrudeSegmentRow] = []
    for seg in seg_order:
        if seg not in segment_buckets:
            continue
        b = segment_buckets[seg]
        est_mb = round(b["laden"] * _segment_mb(seg), 2)
        by_segment_rows.append(
            CrudeSegmentRow(
                segment=seg,
                laden_count=b["laden"],
                ballast_count=b["ballast"],
                unknown_count=b["unknown"],
                estimated_mb=est_mb,
            )
        )
    # Append any segments not in _SEGMENT_DWT (shouldn't happen, but be safe)
    for seg, b in segment_buckets.items():
        if seg not in seg_order:
            by_segment_rows.append(
                CrudeSegmentRow(
                    segment=seg,
                    laden_count=b["laden"],
                    ballast_count=b["ballast"],
                    unknown_count=b["unknown"],
                    estimated_mb=round(b["laden"] * _segment_mb(seg), 2),
                )
            )

    # 5. Destination region breakdown for laden vessels only
    laden_df = live_df[live_df["laden_state"] == "laden"]
    region_buckets: dict[str, dict] = {}
    for _, r in laden_df.iterrows():
        dest = r.get("destination")
        region = _crude_import_region(str(dest) if dest is not None else None)
        raw_seg = r.get("segment")
        seg = (
            str(raw_seg)
            if raw_seg and not (isinstance(raw_seg, float) and pd.isna(raw_seg))
            else "Small"
        )
        if region not in region_buckets:
            region_buckets[region] = {"count": 0, "mb": 0.0, "segments": {}}
        region_buckets[region]["count"] += 1
        region_buckets[region]["mb"] += _segment_mb(seg)
        region_buckets[region]["segments"][seg] = region_buckets[region]["segments"].get(seg, 0) + 1

    inbound_rows: list[InboundRegionRow] = []
    for region, b in region_buckets.items():
        if region == "Unknown":
            continue
        top_segs = sorted(b["segments"], key=lambda s: b["segments"][s], reverse=True)[:3]
        inbound_rows.append(
            InboundRegionRow(
                region=region,
                vessel_count=b["count"],
                estimated_mb=round(b["mb"], 2),
                top_segments=top_segs,
            )
        )
    inbound_rows.sort(key=lambda r: r.estimated_mb, reverse=True)

    total_laden = int(live_df[live_df["laden_state"] == "laden"].shape[0])
    total_ballast = int(live_df[live_df["laden_state"] == "ballast"].shape[0])
    total_mb = round(sum(row.estimated_mb for row in by_segment_rows), 2)

    return CrudeOnWaterResponse(
        as_of=now_dt.isoformat(),
        total_laden_tankers=total_laden,
        total_ballast_tankers=total_ballast,
        estimated_mb_on_water=total_mb,
        by_segment=by_segment_rows,
        inbound_regions=inbound_rows,
    )
