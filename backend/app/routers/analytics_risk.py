"""Analytics: behavioural risk - STS, reroutes, dark gaps, shadow fleet, anomalies, owners."""

from __future__ import annotations

import contextlib
import json as _json
from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter

from quant_lib.freight import flag_from_mmsi, to_iso2

from .. import db
from ..common import fresh_cutoff, iso, str_or_none, valid_imo
from ..live import live_all
from ..ports import canonical_destination, norm_dest
from ..schemas import (
    AnomalyWatchlistItem,
    AnomalyWatchlistResponse,
    DestinationChangeRow,
    DestinationChangesResponse,
    FlagMismatchResponse,
    FlagMismatchRow,
    HighRiskPosition,
    HighRiskPositionsResponse,
    OwnerFleetStatusResponse,
    OwnerFleetStatusRow,
    OwnerIntelResponse,
    OwnerIntelRow,
    RerouteRiskEvent,
    RerouteRiskResponse,
    RiskEventItem,
    RiskEventsResponse,
    ShadowFleetResponse,
    ShadowFleetRow,
    SpeedAnomalyResponse,
    SpeedAnomalyRow,
    StsOffenderRow,
    StsOffendersResponse,
    StsProximityPair,
    StsProximityResponse,
    StsRiskEvent,
    StsRiskResponse,
    TransitRiskEvent,
    TransitRiskResponse,
    VesselRiskResponse,
    VesselRiskRow,
)

router = APIRouter()


@router.get("/api/analytics/high-risk-positions", response_model=HighRiskPositionsResponse)
def analytics_high_risk_positions(min_risk: int = 60):
    """Live positions of vessels with risk_score >= min_risk from the vessel registry.

    Two-query approach: fetch scored vessels from registry, then look up their
    current live positions by IMO. min_risk clamped 0-100.
    """
    min_risk = max(0, min(100, min_risk))
    cutoff = fresh_cutoff()

    reg_df = db.pg_query(
        "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
        "FROM vessels "
        "WHERE risk_score >= %s AND fetch_ok = true AND imo IS NOT NULL",
        [min_risk],
    )
    if reg_df.empty:
        return HighRiskPositionsResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            min_risk=min_risk,
            rows=[],
        )

    ifo_list = reg_df["imo"].tolist()
    placeholders = ",".join(["?" for _ in ifo_list])
    live_df = db.query(
        f"SELECT mmsi, imo, lat, lon, name, segment, kind "
        f"FROM live_positions "
        f"WHERE updated_ts > ? AND imo IN ({placeholders}) AND segment != 'Small'",
        [cutoff, *ifo_list],
    )

    if live_df.empty:
        return HighRiskPositionsResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            min_risk=min_risk,
            rows=[],
        )

    merged = live_df.merge(reg_df, on="imo", how="inner")
    rows = []
    for _, r in merged.iterrows():
        rows.append(
            HighRiskPosition(
                mmsi=int(r["mmsi"]),
                imo=int(r["imo"]),
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                name=str(r["name"]) if r["name"] else None,
                segment=str(r["segment"]) if r["segment"] else None,
                kind=str(r["kind"]) if r["kind"] else None,
                risk_score=int(r["risk_score"]),
                ofac_sanctioned=bool(r["ofac_sanctioned"]),
            )
        )

    rows.sort(key=lambda x: -x.risk_score)
    return HighRiskPositionsResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        min_risk=min_risk,
        rows=rows,
    )


@router.get("/api/analytics/sts-risk", response_model=StsRiskResponse)
def analytics_sts_risk(days: int = 30, min_risk: int = 0):
    """Recent STS (ship-to-ship) events enriched with vessel risk scores.

    Three-database merge: analytics (events) + AIS (MMSI->IMO) + registry (risk scores).
    Sorted by max risk score descending. Use min_risk=25 for intelligence-relevant events.
    """
    import json as _json

    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    events_df = db.query(
        "SELECT event_id, mmsi, mmsi2, start_ts, region, kind, segment, details "
        "FROM ais_events "
        "WHERE type = 'sts' AND start_ts >= ? AND segment != 'Small'",
        [cutoff],
        db=db.analytics_db_path(),
    )
    if events_df.empty:
        return StsRiskResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            days=days,
            total_events=0,
            enriched_events=0,
            rows=[],
        )

    # Collect unique MMSIs
    all_mmsis = set(events_df["mmsi"].dropna().tolist())
    mmsi2s = events_df["mmsi2"].dropna().tolist()
    all_mmsis.update(int(m) for m in mmsi2s)

    # MMSI -> name + IMO from live_positions (fresh only) and vessels table
    mmsi_info: dict[int, dict] = {}
    if all_mmsis:
        mmsi_list = list(all_mmsis)
        placeholders = ",".join("?" * len(mmsi_list))
        lp_df = db.query(
            f"SELECT mmsi, name, imo FROM live_positions WHERE mmsi IN ({placeholders})",
            mmsi_list,
        )
        for _, r in lp_df.iterrows():
            mmsi_info[int(r["mmsi"])] = {"name": r.get("name"), "imo": r.get("imo")}
        # Fill gaps from vessels table
        missing = [m for m in mmsi_list if m not in mmsi_info]
        if missing:
            ph2 = ",".join("?" * len(missing))
            v_df = db.query(
                f"SELECT mmsi, name, imo FROM vessels WHERE mmsi IN ({ph2})",
                missing,
            )
            for _, r in v_df.iterrows():
                mmsi_info[int(r["mmsi"])] = {"name": r.get("name"), "imo": r.get("imo")}

    # IMO -> risk_score + ofac from registry
    imo_risk: dict[int, dict] = {}
    known_imos = [valid_imo(v.get("imo")) for v in mmsi_info.values()]
    known_imos = [i for i in known_imos if i is not None]
    if known_imos:
        imo_list = list(set(known_imos))
        reg_df = db.pg_query(
            "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            "FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [imo_list],
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac_sanctioned"]),
            }

    def _vessel_risk(mmsi_val):
        info = mmsi_info.get(int(mmsi_val), {})
        imo_val = valid_imo(info.get("imo"))
        if imo_val:
            return imo_risk.get(imo_val, {})
        return {}

    rows = []
    enriched = 0
    for _, ev in events_df.iterrows():
        det = {}
        if ev.get("details"):
            with contextlib.suppress(Exception):
                det = _json.loads(ev["details"])

        mmsi_val = int(ev["mmsi"])
        mmsi2_val = int(ev["mmsi2"]) if ev.get("mmsi2") and str(ev["mmsi2"]) != "nan" else None

        r1 = _vessel_risk(mmsi_val)
        r2 = _vessel_risk(mmsi2_val) if mmsi2_val else {}

        rs1 = r1.get("risk_score")
        rs2 = r2.get("risk_score")
        max_risk = max(rs1 or 0, rs2 or 0)

        if max_risk < min_risk:
            continue
        if rs1 is not None or rs2 is not None:
            enriched += 1

        rows.append(
            StsRiskEvent(
                event_id=str(ev["event_id"]),
                start_ts=iso(ev["start_ts"]) or "",
                region=str_or_none(ev.get("region")),
                kind=str_or_none(ev.get("kind")),
                segment=str_or_none(ev.get("segment")),
                mmsi=mmsi_val,
                mmsi2=mmsi2_val,
                name=str_or_none(mmsi_info.get(mmsi_val, {}).get("name")),
                name2=str_or_none(mmsi_info.get(mmsi2_val, {}).get("name")) if mmsi2_val else None,
                duration_hours=det.get("duration_hours"),
                co_location_fixes=det.get("co_location_fixes"),
                risk_score=rs1,
                risk_score2=rs2,
                ofac=bool(r1.get("ofac", False)),
                ofac2=bool(r2.get("ofac", False)),
                max_risk=max_risk,
            )
        )

    rows.sort(key=lambda r: r.max_risk, reverse=True)
    return StsRiskResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        days=days,
        total_events=len(events_df),
        enriched_events=enriched,
        rows=rows,
    )


@router.get("/api/analytics/sts-offenders", response_model=StsOffendersResponse)
def analytics_sts_offenders(days: int = 30, limit: int = 50):
    """Vessels ranked by STS event frequency over the last N days.

    Counts appearances as either primary (mmsi) or secondary (mmsi2) party.
    Enriched with current live position and Equasis registry risk.
    Excludes 'Small' segment noise.
    """
    d = max(1, min(days, 90))
    lim = max(10, min(200, limit))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)

    ev_df = db.query(
        "SELECT mmsi, mmsi2 FROM ais_events "
        "WHERE type = 'sts' AND start_ts >= ? AND segment != 'Small'",
        [cutoff],
        db=db.analytics_db_path(),
    )
    now = datetime.now(UTC)
    as_of = iso(now) or ""
    if ev_df.empty:
        return StsOffendersResponse(as_of=as_of, days=d, total_vessels=0, rows=[])

    init_counts = ev_df.groupby("mmsi").size().rename("as_initiator")
    # mmsi2 may be null
    ev_df_m2 = ev_df.dropna(subset=["mmsi2"]).copy()
    ev_df_m2["mmsi2"] = ev_df_m2["mmsi2"].astype(int)
    cp_counts = ev_df_m2.groupby("mmsi2").size().rename("as_counterpart")

    all_mmsis = (
        pd.concat(
            [
                init_counts.rename("n"),
                cp_counts.rename("n"),
            ]
        )
        .groupby(level=0)
        .sum()
    )
    initiator_map: dict[int, int] = init_counts.to_dict()
    counterpart_map: dict[int, int] = cp_counts.to_dict()

    all_mmsis = all_mmsis.sort_values(ascending=False).head(lim * 2)
    mmsi_list = [int(m) for m in all_mmsis.index.tolist()]

    lp_df = db.query(
        "SELECT mmsi, name, imo, kind, segment, region, lat, lon, sog "
        "FROM live_positions WHERE mmsi = ANY(?)",
        [mmsi_list],
    )
    lp_map: dict[int, dict] = {}
    if not lp_df.empty:
        for _, row in lp_df.iterrows():
            lp_map[int(row["mmsi"])] = row.to_dict()

    reg_df = db.pg_query(
        "SELECT imo, risk_score, ofac_sanctioned FROM vessels "
        "WHERE fetch_ok = TRUE AND imo = ANY(%s)",
        [list({lp_map[m]["imo"] for m in mmsi_list if m in lp_map and lp_map[m].get("imo")})],
    )
    reg_map: dict[int, dict] = {}
    if not reg_df.empty:
        for _, row in reg_df.iterrows():
            reg_map[int(row["imo"])] = row.to_dict()

    rows: list[StsOffenderRow] = []
    for mmsi_int, total in all_mmsis.items():
        live = lp_map.get(int(mmsi_int), {})
        if live.get("segment") == "Small":
            continue
        imo_val = valid_imo(live.get("imo"))
        reg = reg_map.get(imo_val) if imo_val else None
        rows.append(
            StsOffenderRow(
                mmsi=int(mmsi_int),
                name=str_or_none(live.get("name")),
                imo=imo_val,
                kind=str_or_none(live.get("kind")),
                segment=str_or_none(live.get("segment")),
                region=str_or_none(live.get("region")),
                lat=round(float(live["lat"]), 4) if live.get("lat") is not None else None,
                lon=round(float(live["lon"]), 4) if live.get("lon") is not None else None,
                sog=round(float(live["sog"]), 1) if live.get("sog") is not None else None,
                sts_events=int(total),
                as_initiator=initiator_map.get(int(mmsi_int), 0),
                as_counterpart=counterpart_map.get(int(mmsi_int), 0),
                registry_risk=int(reg["risk_score"])
                if reg and reg.get("risk_score") is not None
                else None,
                ofac=bool(reg["ofac_sanctioned"])
                if reg and reg.get("ofac_sanctioned") is not None
                else False,
            )
        )
        if len(rows) >= lim:
            break
    rows.sort(key=lambda r: r.sts_events, reverse=True)
    return StsOffendersResponse(as_of=as_of, days=d, total_vessels=len(all_mmsis), rows=rows)


@router.get("/api/analytics/reroutes", response_model=RerouteRiskResponse)
def analytics_reroutes(
    days: int = 7, min_risk: int = 0, segment: str | None = None, ocean_only: bool = True
):
    """Recent destination-change events enriched with vessel risk scores.

    Sorted by risk_score descending (reroutes by high-risk vessels first).
    Use min_risk=25 to filter to intelligence-relevant changes.
    ocean_only=true (default) excludes Small segment to filter inland waterway barges.
    """
    import json as _json

    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    clauses = []
    params: list = [cutoff]
    if segment:
        clauses.append("segment = ?")
        params.append(segment)
    if ocean_only:
        clauses.append("segment != 'Small'")
    where_extra = (" AND " + " AND ".join(clauses)) if clauses else ""

    events_df = db.query(
        "SELECT event_id, mmsi, start_ts, region, kind, segment, details "
        f"FROM ais_events WHERE type = 'reroute' AND start_ts >= ?{where_extra} "
        "ORDER BY start_ts DESC",
        params,
        db=db.analytics_db_path(),
    )
    if events_df.empty:
        return RerouteRiskResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            days=days,
            total_events=0,
            rows=[],
        )

    # MMSI -> name + risk from live_positions then vessels
    all_mmsis = list({int(m) for m in events_df["mmsi"].dropna().tolist()})
    mmsi_info: dict[int, dict] = {}
    if all_mmsis:
        ph = ",".join("?" * len(all_mmsis))
        lp_df = db.query(
            f"SELECT mmsi, name, imo FROM live_positions WHERE mmsi IN ({ph})",
            all_mmsis,
        )
        for _, r in lp_df.iterrows():
            mmsi_info[int(r["mmsi"])] = {"name": r.get("name"), "imo": r.get("imo")}
        missing = [m for m in all_mmsis if m not in mmsi_info]
        if missing:
            ph2 = ",".join("?" * len(missing))
            v_df = db.query(f"SELECT mmsi, name, imo FROM vessels WHERE mmsi IN ({ph2})", missing)
            for _, r in v_df.iterrows():
                mmsi_info[int(r["mmsi"])] = {"name": r.get("name"), "imo": r.get("imo")}

    imo_risk: dict[int, dict] = {}
    _rr_imos = [valid_imo(v.get("imo")) for v in mmsi_info.values()]
    known_imos = list({i for i in _rr_imos if i is not None})
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
    for _, ev in events_df.iterrows():
        det = {}
        if ev.get("details"):
            with contextlib.suppress(Exception):
                det = _json.loads(ev["details"])

        mmsi_val = int(ev["mmsi"])
        info = mmsi_info.get(mmsi_val, {})
        imo_val = valid_imo(info.get("imo"))
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}

        rs = risk_info.get("risk_score")
        if (rs or 0) < min_risk:
            continue

        rows.append(
            RerouteRiskEvent(
                event_id=str(ev["event_id"]),
                start_ts=iso(ev["start_ts"]) or "",
                region=str_or_none(ev.get("region")),
                kind=str_or_none(ev.get("kind")),
                segment=str_or_none(ev.get("segment")),
                mmsi=mmsi_val,
                name=str_or_none(info.get("name")),
                old_destination=str_or_none(det.get("old_destination")),
                new_destination=str_or_none(det.get("new_destination")),
                fixes_at_old=det.get("fixes_at_old"),
                risk_score=rs,
                ofac=bool(risk_info.get("ofac", False)),
            )
        )

    rows.sort(key=lambda r: r.risk_score or 0, reverse=True)
    return RerouteRiskResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        days=days,
        total_events=len(events_df),
        rows=rows,
    )


@router.get("/api/analytics/flag-mismatches", response_model=FlagMismatchResponse)
def flag_mismatches():
    """Live vessels whose MMSI-MID flag disagrees with their Equasis registry flag.

    A disagreement can indicate a recent reflag or identity obfuscation. Joins live
    positions (mmsi -> imo) to vessel_registry (imo -> flag_code). Empty when the
    registry has no usable rows.
    """
    as_of = iso(datetime.now(UTC).replace(tzinfo=None)) or ""
    df = live_all()
    if df.empty or "imo" not in df.columns:
        return FlagMismatchResponse(as_of=as_of, rows=[])
    live = df[df["imo"].notna() & (df["segment"] != "Small")].copy()
    if live.empty:
        return FlagMismatchResponse(as_of=as_of, rows=[])

    reg = db.pg_query(
        "SELECT imo, flag AS registry_flag, flag_code AS registry_flag_code "
        "FROM vessels WHERE fetch_ok = true AND flag_code IS NOT NULL",
    )
    if reg.empty:
        return FlagMismatchResponse(as_of=as_of, rows=[])
    reg_by_imo = {int(r.imo): r for r in reg.itertuples()}

    rows: list[FlagMismatchRow] = []
    for r in live.itertuples():
        imo = int(r.imo)
        rr = reg_by_imo.get(imo)
        if rr is None:
            continue
        f = flag_from_mmsi(int(r.mmsi)) if pd.notna(r.mmsi) else None
        if f is None or not f.code:
            continue
        reg_iso2 = to_iso2(str(rr.registry_flag_code))
        # Skip when the registry code can't be normalized (Equasis special codes)
        # or genuinely matches the MMSI-derived flag.
        if reg_iso2 is None or f.code == reg_iso2:
            continue
        rows.append(
            FlagMismatchRow(
                mmsi=int(r.mmsi),
                imo=imo,
                name=str_or_none(getattr(r, "name", None)),
                segment=str_or_none(getattr(r, "segment", None)),
                mmsi_flag=f.country,
                mmsi_flag_code=f.code,
                registry_flag=str(rr.registry_flag),
                registry_flag_code=str(rr.registry_flag_code),
            )
        )

    rows.sort(key=lambda r: (r.segment or "", r.name or ""))
    return FlagMismatchResponse(as_of=as_of, rows=rows)


@router.get("/api/analytics/transit-risk", response_model=TransitRiskResponse)
def analytics_transit_risk(chokepoint: str = "hormuz", days: int = 30, min_risk: int = 0):
    """Chokepoint transit events enriched with vessel risk scores.

    Two-database merge: analytics (transit_events) + AIS (MMSI->IMO) + registry (risk).
    Returns transits sorted by risk_score descending. Use min_risk=25 to filter noise.
    """
    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    df = db.query(
        "SELECT mmsi, chokepoint, entered_ts, exited_ts, direction, kind, segment, laden "
        "FROM transit_events WHERE chokepoint = ? AND entered_ts >= ? AND segment != 'Small' "
        "ORDER BY entered_ts DESC",
        [chokepoint, cutoff],
        db=db.analytics_db_path(),
    )
    if df.empty:
        return TransitRiskResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            days=days,
            chokepoint=chokepoint,
            total_transits=0,
            enriched=0,
            rows=[],
        )

    all_mmsis = list({int(m) for m in df["mmsi"].dropna().tolist()})
    mmsi_info: dict[int, dict] = {}
    if all_mmsis:
        ph = ",".join("?" * len(all_mmsis))
        lp_df = db.query(
            f"SELECT mmsi, name, imo FROM live_positions WHERE mmsi IN ({ph})", all_mmsis
        )
        for _, r in lp_df.iterrows():
            mmsi_info[int(r["mmsi"])] = {"name": str_or_none(r.get("name")), "imo": r.get("imo")}
        missing = [m for m in all_mmsis if m not in mmsi_info]
        if missing:
            ph2 = ",".join("?" * len(missing))
            v_df = db.query(f"SELECT mmsi, name, imo FROM vessels WHERE mmsi IN ({ph2})", missing)
            for _, r in v_df.iterrows():
                mmsi_info[int(r["mmsi"])] = {
                    "name": str_or_none(r.get("name")),
                    "imo": r.get("imo"),
                }

    imo_risk: dict[int, dict] = {}
    known_imos = list({i for i in (valid_imo(v.get("imo")) for v in mmsi_info.values()) if i})
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
    enriched = 0
    for _, ev in df.iterrows():
        mmsi_val = int(ev["mmsi"])
        info = mmsi_info.get(mmsi_val, {})
        imo_val = valid_imo(info.get("imo"))
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}
        rs = risk_info.get("risk_score")

        if (rs or 0) < min_risk:
            continue
        if rs is not None:
            enriched += 1

        laden_val = ev.get("laden")
        laden_bool = (
            None
            if laden_val is None or (isinstance(laden_val, float) and pd.isna(laden_val))
            else bool(laden_val)
        )

        rows.append(
            TransitRiskEvent(
                mmsi=mmsi_val,
                name=str_or_none(info.get("name")),
                imo=imo_val,
                chokepoint=str(ev["chokepoint"]),
                entered_ts=iso(ev["entered_ts"]) or "",
                exited_ts=iso(ev.get("exited_ts")) if ev.get("exited_ts") is not None else None,
                direction=str_or_none(ev.get("direction")),
                kind=str_or_none(ev.get("kind")),
                segment=str_or_none(ev.get("segment")),
                laden=laden_bool,
                risk_score=rs,
                ofac=bool(risk_info.get("ofac", False)),
            )
        )

    rows.sort(key=lambda r: r.risk_score or 0, reverse=True)
    return TransitRiskResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        days=days,
        chokepoint=chokepoint,
        total_transits=len(df),
        enriched=enriched,
        rows=rows,
    )


@router.get("/api/analytics/shadow-fleet", response_model=ShadowFleetResponse)
def analytics_shadow_fleet(days: int = 7, limit: int = 50):
    """Shadow fleet precursor monitor: vessels with STS events AND gap/spoof events.

    Identifies vessels matching the dark-transfer pattern (STS candidate + signal lost
    or position jump within the same window). Ranked by risk score then event count.
    Three-DB join: analytics (events) + AIS (MMSI->IMO+name) + registry (risk scores).
    """
    days = max(1, min(30, days))
    limit = max(1, min(200, limit))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    _adb = db.analytics_db_path()

    # STS participants in window
    sts_df = db.query(
        "SELECT mmsi, mmsi2, start_ts FROM ais_events "
        "WHERE type = 'sts' AND start_ts >= ? AND segment != 'Small'",
        [cutoff],
        db=_adb,
    )
    if sts_df.empty:
        return ShadowFleetResponse(as_of=iso(now_dt) or "", days=days, total=0, rows=[])

    sts_mmsis: set[int] = set(sts_df["mmsi"].dropna().astype(int).tolist())
    sts_mmsis.update(sts_df["mmsi2"].dropna().astype(int).tolist())

    # Gap + spoof events in window - filter to STS participants
    ph = ",".join("?" * len(sts_mmsis))
    covert_df = db.query(
        f"SELECT mmsi, type, start_ts FROM ais_events "
        f"WHERE type IN ('gap','spoof') AND start_ts >= ? AND mmsi IN ({ph})",
        [cutoff] + list(sts_mmsis),
        db=_adb,
    )
    if covert_df.empty:
        return ShadowFleetResponse(as_of=iso(now_dt) or "", days=days, total=0, rows=[])

    covert_mmsis: set[int] = set(covert_df["mmsi"].dropna().astype(int).tolist())

    # Build per-vessel event counts
    sts_count_map: dict[int, int] = {}
    for mmsi_val in sts_df["mmsi"].dropna().astype(int):
        sts_count_map[mmsi_val] = sts_count_map.get(mmsi_val, 0) + 1
    for mmsi_val in sts_df["mmsi2"].dropna().astype(int):
        sts_count_map[mmsi_val] = sts_count_map.get(mmsi_val, 0) + 1

    gap_count_map: dict[int, int] = {}
    spoof_count_map: dict[int, int] = {}
    last_event_map: dict[int, str] = {}
    for _, r in covert_df.iterrows():
        m = int(r["mmsi"])
        if r["type"] == "gap":
            gap_count_map[m] = gap_count_map.get(m, 0) + 1
        else:
            spoof_count_map[m] = spoof_count_map.get(m, 0) + 1
        ts_str = iso(r["start_ts"]) or ""
        if m not in last_event_map or ts_str > last_event_map[m]:
            last_event_map[m] = ts_str

    # MMSI -> kind/segment from ais_events (shadow fleet vessels are often not in
    # live_positions since they've gone dark; the event record always has this)
    all_mmsis = list(covert_mmsis)
    events_kind_df = db.query(
        f"SELECT mmsi, any_value(kind) AS kind, any_value(segment) AS segment "
        f"FROM ais_events WHERE mmsi IN ({','.join('?' * len(all_mmsis))}) GROUP BY mmsi",
        all_mmsis,
        db=_adb,
    )
    event_kind_map: dict[int, tuple[str | None, str | None]] = {}
    for _, r in events_kind_df.iterrows():
        event_kind_map[int(r["mmsi"])] = (
            str_or_none(r.get("kind")),
            str_or_none(r.get("segment")),
        )

    # MMSI -> name + IMO from live_positions
    lp_df = db.query(
        f"SELECT mmsi, name, imo FROM live_positions WHERE mmsi IN ({','.join('?' * len(all_mmsis))})",
        all_mmsis,
    )
    mmsi_info: dict[int, dict] = {}
    for _, r in lp_df.iterrows():
        mmsi_info[int(r["mmsi"])] = {"name": str_or_none(r.get("name")), "imo": r.get("imo")}

    # IMO -> risk from registry
    known_imos = [valid_imo(v.get("imo")) for v in mmsi_info.values() if valid_imo(v.get("imo"))]
    imo_risk: dict[int, dict] = {}
    if known_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac, flag "
            "FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [known_imos],
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac"]),
                "flag": str_or_none(r.get("flag")),
            }

    # Live region from cache
    live_df = live_all()
    mmsi_region: dict[int, str | None] = {}
    mmsi_kind: dict[int, str | None] = {}
    mmsi_segment: dict[int, str | None] = {}
    if not live_df.empty:
        for _, _row in live_df[live_df["mmsi"].isin(all_mmsis)].iterrows():
            _m = int(_row["mmsi"])
            mmsi_region[_m] = str_or_none(_row.get("region"))
            mmsi_kind[_m] = str_or_none(_row.get("kind"))
            mmsi_segment[_m] = str_or_none(_row.get("segment"))

    rows: list[ShadowFleetRow] = []
    for mmsi_val in covert_mmsis:
        info = mmsi_info.get(mmsi_val, {})
        imo_val = valid_imo(info.get("imo"))
        reg = imo_risk.get(imo_val, {}) if imo_val else {}
        ev_kind, ev_seg = event_kind_map.get(mmsi_val, (None, None))
        rows.append(
            ShadowFleetRow(
                mmsi=mmsi_val,
                imo=imo_val,
                name=str_or_none(info.get("name")),
                kind=mmsi_kind.get(mmsi_val) or ev_kind,
                segment=mmsi_segment.get(mmsi_val) or ev_seg,
                region=mmsi_region.get(mmsi_val),
                sts_count=sts_count_map.get(mmsi_val, 0),
                gap_count=gap_count_map.get(mmsi_val, 0),
                spoof_count=spoof_count_map.get(mmsi_val, 0),
                risk_score=reg.get("risk_score"),
                ofac=bool(reg.get("ofac", False)),
                flags=[reg["flag"]] if reg.get("flag") else [],
                last_event_ts=last_event_map.get(mmsi_val),
            )
        )

    rows.sort(
        key=lambda r: (
            -(r.risk_score or 0),
            -(r.gap_count + r.spoof_count),
            -(r.sts_count),
        )
    )
    rows = rows[:limit]

    return ShadowFleetResponse(
        as_of=iso(now_dt) or "",
        days=days,
        total=len(covert_mmsis),
        rows=rows,
    )


@router.get("/api/analytics/risk-events", response_model=RiskEventsResponse)
def analytics_risk_events(min_risk: int = 25, days: int = 2, limit: int = 50):
    """High-risk vessel intelligence feed: STS + reroute events where at least one party
    carries a registry risk score >= min_risk. Three-DB join: registry (IMO->score),
    AIS (IMO->MMSI), analytics (events). Sorted by max_risk descending then most recent.

    Use min_risk=50 for critical-only alerts (dark fleet / shadow tanker monitoring).
    """
    min_risk = max(0, min(100, min_risk))
    days = max(1, min(30, days))
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    now_ts = datetime.now(UTC).replace(tzinfo=None)

    # Step 1: high-risk IMOs from registry
    reg_df = db.pg_query(
        "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac "
        "FROM vessels WHERE risk_score >= %s AND fetch_ok = true",
        [min_risk],
    )
    if reg_df.empty:
        return RiskEventsResponse(
            as_of=iso(now_ts) or "",
            min_risk=min_risk,
            days=days,
            total_high_risk_vessels=0,
            rows=[],
        )

    imo_risk: dict[int, dict] = {}
    for _, r in reg_df.iterrows():
        imo_risk[int(r["imo"])] = {"risk_score": int(r["risk_score"]), "ofac": bool(r["ofac"])}

    known_imos = [int(i) for i in imo_risk]
    ph_imos = ",".join("?" * len(known_imos))

    # Step 2: IMO -> MMSI + name via live_positions
    lp_df = db.query(
        f"SELECT mmsi, imo, name FROM live_positions WHERE imo IN ({ph_imos})",
        known_imos,
    )
    mmsi_imo: dict[int, int] = {}
    mmsi_name: dict[int, str | None] = {}
    for _, r in lp_df.iterrows():
        imo_val = valid_imo(r.get("imo"))
        if imo_val:
            m = int(r["mmsi"])
            mmsi_imo[m] = imo_val
            mmsi_name[m] = str_or_none(r.get("name"))

    all_mmsis = list(mmsi_imo.keys())
    if not all_mmsis:
        return RiskEventsResponse(
            as_of=iso(now_ts) or "",
            min_risk=min_risk,
            days=days,
            total_high_risk_vessels=len(known_imos),
            rows=[],
        )

    # Step 3: recent STS + reroute events for these MMSIs (either party)
    ph_m = ",".join("?" * len(all_mmsis))
    events_df = db.query(
        f"SELECT event_id, type, mmsi, mmsi2, start_ts, lat, lon, "
        f"       region, kind, segment, details "
        f"FROM ais_events "
        f"WHERE (mmsi IN ({ph_m}) OR mmsi2 IN ({ph_m})) "
        f"  AND type IN ('sts', 'reroute') "
        f"  AND start_ts >= ? "
        f"ORDER BY start_ts DESC",
        [int(m) for m in all_mmsis] + [int(m) for m in all_mmsis] + [since],
        db=db.analytics_db_path(),
    )
    if events_df.empty:
        return RiskEventsResponse(
            as_of=iso(now_ts) or "",
            min_risk=min_risk,
            days=days,
            total_high_risk_vessels=len(known_imos),
            rows=[],
        )

    # Gather all MMSIs from events to look up names for non-high-risk counterparties
    extra_mmsis = []
    for _, r in events_df.iterrows():
        m2 = r.get("mmsi2")
        if m2 is not None and not pd.isna(m2) and int(m2) not in mmsi_name:
            extra_mmsis.append(int(m2))
    if extra_mmsis:
        ph_ex = ",".join("?" * len(extra_mmsis))
        ex_df = db.query(
            f"SELECT mmsi, imo, name FROM live_positions WHERE mmsi IN ({ph_ex})",
            extra_mmsis,
        )
        for _, r in ex_df.iterrows():
            m_val = int(r["mmsi"])
            mmsi_name.setdefault(m_val, str_or_none(r.get("name")))
            if m_val not in mmsi_imo:
                imo_val = valid_imo(r.get("imo"))
                if imo_val:
                    mmsi_imo[m_val] = imo_val

    risk_rows: list[RiskEventItem] = []
    for _, ev in events_df.iterrows():
        mmsi_val = int(ev["mmsi"])
        mmsi2_val: int | None = None
        if ev.get("mmsi2") is not None and not pd.isna(ev.get("mmsi2")):
            mmsi2_val = int(ev["mmsi2"])

        imo_val = mmsi_imo.get(mmsi_val)
        imo2_val = mmsi_imo.get(mmsi2_val) if mmsi2_val is not None else None

        ri = imo_risk.get(imo_val, {}) if imo_val else {}
        ri2 = imo_risk.get(imo2_val, {}) if imo2_val else {}

        rs = ri.get("risk_score")
        rs2 = ri2.get("risk_score")
        max_risk = max(rs or 0, rs2 or 0)
        if max_risk < min_risk:
            continue  # neither party qualifies - can happen if mmsi2 was the trigger

        det: dict = {}
        if ev.get("details"):
            with contextlib.suppress(Exception):
                det = _json.loads(ev["details"])

        lat_val: float | None = None
        lon_val: float | None = None
        if ev.get("lat") is not None and not pd.isna(ev.get("lat")):
            lat_val = round(float(ev["lat"]), 5)
        if ev.get("lon") is not None and not pd.isna(ev.get("lon")):
            lon_val = round(float(ev["lon"]), 5)

        risk_rows.append(
            RiskEventItem(
                event_id=str(ev["event_id"]),
                event_type=str(ev["type"]),
                event_ts=iso(ev["start_ts"]) or "",
                mmsi=mmsi_val,
                name=mmsi_name.get(mmsi_val),
                imo=imo_val,
                risk_score=rs,
                ofac=bool(ri.get("ofac", False)),
                mmsi2=mmsi2_val,
                name2=mmsi_name.get(mmsi2_val) if mmsi2_val is not None else None,
                imo2=imo2_val,
                risk_score2=rs2,
                ofac2=bool(ri2.get("ofac", False)),
                max_risk=max_risk,
                region=str_or_none(ev.get("region")),
                kind=str_or_none(ev.get("kind")),
                segment=str_or_none(ev.get("segment")),
                lat=lat_val,
                lon=lon_val,
                old_destination=str_or_none(det.get("old_destination"))
                if isinstance(det, dict)
                else None,
                new_destination=str_or_none(det.get("new_destination"))
                if isinstance(det, dict)
                else None,
            )
        )

    risk_rows.sort(key=lambda r: (-r.max_risk, r.event_ts))
    return RiskEventsResponse(
        as_of=iso(now_ts) or "",
        min_risk=min_risk,
        days=days,
        total_high_risk_vessels=len(known_imos),
        rows=risk_rows[:limit],
    )


_HIGH_RISK_REGIONS = frozenset(
    {
        "hormuz",
        "persian_gulf",
        "west_africa",
        "somalia",
        "red_sea",
        "bab_el_mandeb",
        "gulf_of_aden",
    }
)


@router.get("/api/analytics/sts-proximity", response_model=StsProximityResponse)
def analytics_sts_proximity(max_dist_m: float = 2000, max_sog: float = 3.0):
    """Live pairs of vessels within max_dist_m metres of each other at sog <= max_sog.

    Excludes anchored (nav_status=1) and moored (nav_status=5) vessels. Uses
    vectorized haversine over live_positions; returns up to 100 closest pairs.
    Pairs in high-risk regions (Hormuz, Red Sea, W Africa, etc.) are flagged.
    """
    import numpy as np

    d_m = max(200.0, min(max_dist_m, 10000.0))
    sog_cap = max(0.5, min(max_sog, 8.0))

    df = live_all()
    if not df.empty:
        sog_num = pd.to_numeric(df["sog"], errors="coerce")
        ns_num = pd.to_numeric(df.get("nav_status", pd.Series()), errors="coerce")
        ns_ok = ns_num.isna() | ~ns_num.isin([1, 5])
        df = df[
            (sog_num.notna())
            & (sog_num <= sog_cap)
            & ns_ok
            & df["lat"].notna()
            & df["lon"].notna()
            & (df["segment"] != "Small")
        ].copy()
        needed = [
            "mmsi",
            "name",
            "imo",
            "lat",
            "lon",
            "sog",
            "kind",
            "segment",
            "region",
            "nav_status",
        ]
        df = df[[c for c in needed if c in df.columns]]
    now = datetime.now(UTC)
    if df.empty or len(df) < 2:
        return StsProximityResponse(
            as_of=iso(now) or "",
            max_dist_m=d_m,
            max_sog=sog_cap,
            total_pairs=0,
            pairs=[],
        )

    df = df.reset_index(drop=True)
    R = 6_371_000.0
    lats = np.radians(df["lat"].values.astype(float))
    lons = np.radians(df["lon"].values.astype(float))
    dlat = lats[:, None] - lats[None, :]
    dlon = lons[:, None] - lons[None, :]
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lats[:, None]) * np.cos(lats[None, :]) * np.sin(dlon / 2) ** 2
    )
    dists = 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    mask = np.triu(dists < d_m, k=1)
    ii, jj = np.nonzero(mask)

    pairs: list[StsProximityPair] = []
    for i_idx, j_idx in zip(ii.tolist(), jj.tolist(), strict=False):
        a_row = df.iloc[i_idx]
        b_row = df.iloc[j_idx]
        region = str_or_none(a_row["region"]) or str_or_none(b_row["region"])
        pairs.append(
            StsProximityPair(
                mmsi_a=int(a_row["mmsi"]),
                name_a=str_or_none(a_row["name"]),
                imo_a=valid_imo(a_row["imo"]),
                kind_a=str_or_none(a_row["kind"]),
                segment_a=str_or_none(a_row["segment"]),
                sog_a=round(float(a_row["sog"]), 1),
                mmsi_b=int(b_row["mmsi"]),
                name_b=str_or_none(b_row["name"]),
                imo_b=valid_imo(b_row["imo"]),
                kind_b=str_or_none(b_row["kind"]),
                segment_b=str_or_none(b_row["segment"]),
                sog_b=round(float(b_row["sog"]), 1),
                dist_m=round(float(dists[i_idx, j_idx]), 0),
                lat=round((float(a_row["lat"]) + float(b_row["lat"])) / 2, 4),
                lon=round((float(a_row["lon"]) + float(b_row["lon"])) / 2, 4),
                region=region,
                risk_region=region in _HIGH_RISK_REGIONS if region else False,
            )
        )
    pairs.sort(key=lambda p: (not p.risk_region, p.dist_m))
    return StsProximityResponse(
        as_of=iso(now) or "",
        max_dist_m=d_m,
        max_sog=sog_cap,
        total_pairs=len(pairs),
        pairs=pairs[:100],
    )


@router.get("/api/analytics/anomaly-watchlist", response_model=AnomalyWatchlistResponse)
def analytics_anomaly_watchlist(
    min_score: int = 50,
    limit: int = 30,
):
    """Multi-signal anomaly watchlist: vessels with elevated composite risk scores.

    Combines behavioral events (STS + reroutes in 7d), Equasis registry risk,
    OFAC status, and geographic location. Each row includes human-readable signal
    descriptions explaining why the vessel is flagged.
    """
    min_score = max(0, min(100, min_score))
    limit = max(1, min(100, limit))
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    cutoff_7d = now_ts - timedelta(days=7)
    cutoff_30d = now_ts - timedelta(days=30)

    # Step 1: event counts (30d for scoring, 7d for recency signals)
    adb = db.analytics_db_path()
    ev_30d = db.query(
        "SELECT mmsi, type, COUNT(*) AS cnt FROM ais_events "
        "WHERE start_ts >= ? AND segment != 'Small' GROUP BY mmsi, type",
        [cutoff_30d],
        db=adb,
    )
    ev_7d = db.query(
        "SELECT mmsi, type, COUNT(*) AS cnt FROM ais_events "
        "WHERE start_ts >= ? AND segment != 'Small' GROUP BY mmsi, type",
        [cutoff_7d],
        db=adb,
    )

    # sts can be either party
    sts_30d_df = db.query(
        "SELECT mmsi2 AS mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND mmsi2 IS NOT NULL AND start_ts >= ? "
        "AND segment != 'Small' GROUP BY mmsi2",
        [cutoff_30d],
        db=adb,
    )
    sts_7d_df = db.query(
        "SELECT mmsi2 AS mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND mmsi2 IS NOT NULL AND start_ts >= ? "
        "AND segment != 'Small' GROUP BY mmsi2",
        [cutoff_7d],
        db=adb,
    )

    def _build_counts(df, mmsi2_df) -> tuple[dict[int, int], dict[int, int]]:
        sts: dict[int, int] = {}
        rr: dict[int, int] = {}
        if not df.empty:
            for _, r in df.iterrows():
                m = int(r["mmsi"])
                t = str(r["type"])
                c = int(r["cnt"])
                if t == "sts":
                    sts[m] = sts.get(m, 0) + c
                elif t == "reroute":
                    rr[m] = rr.get(m, 0) + c
        if not mmsi2_df.empty:
            for _, r in mmsi2_df.iterrows():
                m = int(r["mmsi"])
                c = int(r["cnt"])
                sts[m] = sts.get(m, 0) + c
        return sts, rr

    sts_30d, rr_30d = _build_counts(ev_30d, sts_30d_df)
    sts_7d, rr_7d = _build_counts(ev_7d, sts_7d_df)

    # Step 2: live positions
    lp_df = db.query(
        "SELECT mmsi, imo, name, kind, segment, region, lat, lon, sog, destination "
        "FROM live_positions WHERE segment != 'Small'"
    )
    if lp_df.empty:
        return AnomalyWatchlistResponse(
            as_of=iso(now_ts) or "",
            min_score=min_score,
            total_flagged=0,
            rows=[],
        )

    # Step 3: vessel state (laden/ballast)
    vs_df = db.query("SELECT mmsi, laden FROM vessel_state", db=adb)
    laden_map: dict[int, str] = {}
    if not vs_df.empty:
        for _, r in vs_df.iterrows():
            laden_map[int(r["mmsi"])] = str(r["laden"]) if r.get("laden") else "unknown"

    # Step 4: registry risk for all live IMOs
    live_imos = [
        valid_imo(r.get("imo")) for _, r in lp_df.iterrows() if valid_imo(r.get("imo")) is not None
    ]
    reg_map: dict[int, dict] = {}
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

    # Step 5: score and filter
    rows_out: list[AnomalyWatchlistItem] = []
    for _, row in lp_df.iterrows():
        mmsi = int(row["mmsi"])
        imo = valid_imo(row.get("imo"))
        reg = reg_map.get(imo) if imo else None

        sts_c = sts_30d.get(mmsi, 0)
        rr_c = rr_30d.get(mmsi, 0)
        behavioral = min(sts_c * 20 + rr_c * 5, 100)

        reg_risk = reg["risk_score"] if reg else None
        ofac = reg["ofac"] if reg else False
        if reg_risk is not None:
            base = round(behavioral * 0.4 + reg_risk * 0.6)
        else:
            base = behavioral
        total = min(base + (25 if ofac else 0), 100)

        if total < min_score:
            continue

        # Build signal descriptions
        signals: list[str] = []
        region = str_or_none(row.get("region"))
        if ofac:
            signals.append("OFAC SDN sanctioned")
        if reg_risk is not None and reg_risk >= 75:
            signals.append(f"Critical registry risk ({reg_risk}/100)")
        elif reg_risk is not None and reg_risk >= 50:
            signals.append(f"High registry risk ({reg_risk}/100)")
        elif reg_risk is not None and reg_risk >= 25:
            signals.append(f"Elevated registry risk ({reg_risk}/100)")
        sts_7d_c = sts_7d.get(mmsi, 0)
        rr_7d_c = rr_7d.get(mmsi, 0)
        if sts_7d_c > 0:
            signals.append(f"{sts_7d_c} STS event(s) in 7d")
        if rr_7d_c > 0:
            signals.append(f"{rr_7d_c} destination change(s) in 7d")
        if region in _HIGH_RISK_REGIONS:
            signals.append(f"In high-risk region ({region.replace('_', ' ')})")

        if total >= 75:
            risk_level = "Critical"
        elif total >= 50:
            risk_level = "High"
        elif total >= 25:
            risk_level = "Elevated"
        else:
            risk_level = "Low"

        laden_val = laden_map.get(mmsi, "unknown")
        sog_val = row.get("sog")
        sog_f = float(sog_val) if sog_val is not None and not pd.isna(sog_val) else None
        lat_f = (
            float(row["lat"]) if row.get("lat") is not None and not pd.isna(row["lat"]) else None
        )
        lon_f = (
            float(row["lon"]) if row.get("lon") is not None and not pd.isna(row["lon"]) else None
        )

        rows_out.append(
            AnomalyWatchlistItem(
                mmsi=mmsi,
                imo=imo,
                name=str_or_none(row.get("name")),
                kind=str_or_none(row.get("kind")),
                segment=str_or_none(row.get("segment")),
                region=region,
                lat=lat_f,
                lon=lon_f,
                sog=sog_f,
                destination=canonical_destination(str_or_none(row.get("destination"))),
                laden=laden_val,
                total_score=total,
                behavioral_score=behavioral,
                registry_risk=reg_risk,
                ofac=ofac,
                risk_level=risk_level,
                sts_count_7d=sts_7d_c,
                reroute_count_7d=rr_7d_c,
                signals=signals,
            )
        )

    rows_out.sort(key=lambda r: (-r.total_score, -r.behavioral_score))
    total_flagged = len(rows_out)

    return AnomalyWatchlistResponse(
        as_of=iso(now_ts) or "",
        min_score=min_score,
        total_flagged=total_flagged,
        rows=rows_out[:limit],
    )


@router.get("/api/analytics/vessel-risk-scores", response_model=VesselRiskResponse)
def analytics_vessel_risk_scores(
    top_n: int = 50,
    days: int = 30,
    segment: str | None = None,
    kind: str | None = None,
    min_score: int = 5,
):
    """Composite behavioral + registry risk leaderboard per live vessel.

    Scoring (0-100):
      behavioral_score = min(sts_count * 20 + reroute_count * 5, 100)
      registry_component = registry_risk if present else 0
      total_score = min(round((behavioral_score + registry_component) / 2)
                        + (25 if ofac else 0), 100)
    Vessels with no events and no registry data are excluded.
    """
    top_n = max(1, min(200, top_n))
    days = max(1, min(90, days))

    now_ts = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_ts - timedelta(days=days)

    # Step 1: event counts from analytics DB
    sts_df = db.query(
        "SELECT mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND start_ts >= ? GROUP BY mmsi "
        "UNION ALL "
        "SELECT mmsi2 AS mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND mmsi2 IS NOT NULL AND start_ts >= ? GROUP BY mmsi2",
        [cutoff, cutoff],
        db=db.analytics_db_path(),
    )
    sts_counts: dict[int, int] = {}
    if not sts_df.empty:
        for _, r in sts_df.iterrows():
            m = int(r["mmsi"])
            sts_counts[m] = sts_counts.get(m, 0) + int(r["cnt"])

    rr_df = db.query(
        "SELECT mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'reroute' AND start_ts >= ? GROUP BY mmsi",
        [cutoff],
        db=db.analytics_db_path(),
    )
    reroute_counts: dict[int, int] = {}
    if not rr_df.empty:
        for _, r in rr_df.iterrows():
            reroute_counts[int(r["mmsi"])] = int(r["cnt"])

    # Step 2: live positions (segment/kind filter applied here) - use cache
    lp_df = live_all()
    if not lp_df.empty:
        lp_df = lp_df[lp_df["segment"].notna() & (lp_df["segment"] != "Small")].copy()
        if segment:
            lp_df = lp_df[lp_df["segment"] == segment]
        if kind:
            lp_df = lp_df[lp_df["kind"] == kind]
        needed = ["mmsi", "imo", "name", "kind", "segment", "region", "lat", "lon"]
        lp_df = lp_df[[c for c in needed if c in lp_df.columns]]
    if lp_df.empty:
        return VesselRiskResponse(
            as_of=iso(now_ts) or "",
            days=days,
            top_n=top_n,
            total_candidates=0,
            rows=[],
        )

    # mmsi -> live fields map
    lp_map: dict[int, dict] = {}
    for _, r in lp_df.iterrows():
        lp_map[int(r["mmsi"])] = {
            "imo": valid_imo(r.get("imo")),
            "name": str_or_none(r.get("name")),
            "kind": str_or_none(r.get("kind")),
            "segment": str_or_none(r.get("segment")),
            "region": str_or_none(r.get("region")),
            "lat": float(r["lat"]) if r.get("lat") is not None and not pd.isna(r["lat"]) else None,
            "lon": float(r["lon"]) if r.get("lon") is not None and not pd.isna(r["lon"]) else None,
        }

    # Step 3: registry risk data via IMO
    live_imos = [v["imo"] for v in lp_map.values() if v["imo"] is not None]
    reg_map: dict[int, dict] = {}  # keyed by imo
    if live_imos:
        reg_df = db.pg_query(
            "SELECT imo, risk_score, ofac_sanctioned FROM vessels "
            "WHERE imo = ANY(%s) AND fetch_ok = true",
            [live_imos],
        )
        if not reg_df.empty:
            for _, r in reg_df.iterrows():
                imo_val = valid_imo(r.get("imo"))
                if imo_val is not None:
                    reg_map[imo_val] = {
                        "risk_score": int(r["risk_score"])
                        if r.get("risk_score") is not None and not pd.isna(r["risk_score"])
                        else None,
                        "ofac": bool(r["ofac_sanctioned"])
                        if r.get("ofac_sanctioned") is not None
                        and not pd.isna(r["ofac_sanctioned"])
                        else False,
                    }

    # Step 4: candidate MMSIs = vessels with behavioral events OR registry risk > 0
    behavioral_mmsis = set(sts_counts) | set(reroute_counts)
    reg_imo_to_mmsi: dict[int, int] = {
        v["imo"]: k for k, v in lp_map.items() if v["imo"] is not None
    }
    reg_risk_mmsis: set[int] = set()
    for imo, r in reg_map.items():
        if (r.get("risk_score") or 0) > 0 or r.get("ofac"):
            mmsi_for_imo = reg_imo_to_mmsi.get(imo)
            if mmsi_for_imo is not None:
                reg_risk_mmsis.add(mmsi_for_imo)

    fleet_mmsis = set(lp_map)
    candidate_mmsis = (behavioral_mmsis | reg_risk_mmsis) & fleet_mmsis

    # Step 5: score and filter
    rows_out: list[VesselRiskRow] = []
    for mmsi in candidate_mmsis:
        live = lp_map[mmsi]
        imo = live["imo"]
        reg = reg_map.get(imo) if imo else None

        sts_c = sts_counts.get(mmsi, 0)
        rr_c = reroute_counts.get(mmsi, 0)
        behavioral = min(sts_c * 20 + rr_c * 5, 100)

        reg_risk = reg["risk_score"] if reg else None
        ofac = reg["ofac"] if reg else False
        if reg_risk is not None:
            base = round(behavioral * 0.4 + reg_risk * 0.6)
        else:
            base = behavioral
        total = min(base + (25 if ofac else 0), 100)

        if total < min_score:
            continue

        rows_out.append(
            VesselRiskRow(
                mmsi=mmsi,
                imo=imo,
                name=live["name"],
                kind=live["kind"],
                segment=live["segment"],
                region=live["region"],
                lat=live["lat"],
                lon=live["lon"],
                sts_count=sts_c,
                reroute_count=rr_c,
                registry_risk=reg_risk,
                ofac=ofac,
                behavioral_score=behavioral,
                total_score=total,
            )
        )

    rows_out.sort(key=lambda r: (-r.total_score, -r.behavioral_score))
    total_candidates = len(rows_out)

    return VesselRiskResponse(
        as_of=iso(now_ts) or "",
        days=days,
        top_n=top_n,
        total_candidates=total_candidates,
        rows=rows_out[:top_n],
    )


@router.get("/api/analytics/destination-changes", response_model=DestinationChangesResponse)
def analytics_destination_changes(hours: int = 72, kind: str = "", min_confidence: int = 0):
    """Detect vessel destination changes by diffing consecutive ais_snapshots.

    Returns vessels whose reported destination changed within the window.
    Filters out trivially noisy changes (empty<->non-empty).
    hours: lookback window, clamped to [1, 336].
    kind: optional filter by vessel kind (tanker/bulk).
    """
    hours = max(1, min(hours, 336))
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_dt - timedelta(hours=hours)

    kind_clause = ""
    params: list = [cutoff]
    if kind:
        kind_clause = "AND kind = ? "
        params.append(kind)

    df = db.query(
        f"WITH ranked AS ("
        f"  SELECT mmsi, kind, segment, region, snapshot_ts, destination, "
        f"    LAG(destination) OVER (PARTITION BY mmsi ORDER BY snapshot_ts) AS prev_dest, "
        f"    LAG(snapshot_ts) OVER (PARTITION BY mmsi ORDER BY snapshot_ts) AS prev_ts "
        f"  FROM ais_snapshots "
        f"  WHERE snapshot_ts >= ? AND segment != 'Small' {kind_clause}"
        f") "
        f"SELECT mmsi, kind, segment, region, snapshot_ts AS changed_ts, "
        f"  prev_dest AS from_dest, destination AS to_dest "
        f"FROM ranked "
        f"WHERE prev_dest IS NOT NULL "
        f"  AND destination IS NOT NULL "
        f"  AND prev_dest != '' "
        f"  AND destination != '' "
        f"  AND prev_dest != destination "
        f"ORDER BY changed_ts DESC "
        f"LIMIT 500",
        params,
    )

    if df.empty:
        return DestinationChangesResponse(
            as_of=now_dt.isoformat(),
            hours=hours,
            total_changes=0,
            rows=[],
        )

    # Filter to meaningful destination changes: normalize and require first 5 chars differ
    # (LOCODE is 5 chars: country-code + location-code; same prefix = same port)
    def _dest_prefix(s: str) -> str:
        n = norm_dest(s)
        return n[:5] if len(n) >= 4 else ""

    df = df[
        df.apply(
            lambda r: (
                bool(_dest_prefix(str(r["from_dest"])))
                and bool(_dest_prefix(str(r["to_dest"])))
                and _dest_prefix(str(r["from_dest"])) != _dest_prefix(str(r["to_dest"]))
            ),
            axis=1,
        )
    ].copy()

    # Dedupe to most-recent change per vessel
    df = df.drop_duplicates(subset=["mmsi"], keep="first").copy()
    df["changed_ts"] = pd.to_datetime(df["changed_ts"])

    # Enrich with current position from live_positions (separate DB query, no cross-file JOIN)
    mmsi_list = df["mmsi"].tolist()
    if mmsi_list:
        pos_df = db.query(
            "SELECT mmsi, name, lat, lon FROM live_positions WHERE mmsi IN ("
            + ",".join("?" * len(mmsi_list))
            + ")",
            mmsi_list,
        )
    else:
        pos_df = pd.DataFrame(columns=["mmsi", "name", "lat", "lon"])

    pos_map: dict[int, dict] = {int(r["mmsi"]): r.to_dict() for _, r in pos_df.iterrows()}

    rows = []
    for _, r in df.iterrows():
        mmsi_int = int(r["mmsi"])
        pos = pos_map.get(mmsi_int)
        changed_ts = r["changed_ts"]
        hours_ago = round((now_dt - changed_ts.replace(tzinfo=None)).total_seconds() / 3600, 1)
        rows.append(
            DestinationChangeRow(
                mmsi=mmsi_int,
                name=str_or_none(pos.get("name")) if pos is not None else None,
                kind=str_or_none(r["kind"]),
                segment=str_or_none(r["segment"]),
                region=str_or_none(r["region"]),
                lat=float(pos["lat"]) if pos is not None and pos.get("lat") is not None else None,
                lon=float(pos["lon"]) if pos is not None and pos.get("lon") is not None else None,
                changed_ts=changed_ts.isoformat(),
                from_dest=norm_dest(str(r["from_dest"])),
                to_dest=norm_dest(str(r["to_dest"])),
                hours_ago=hours_ago,
            )
        )

    return DestinationChangesResponse(
        as_of=now_dt.isoformat(),
        hours=hours,
        total_changes=len(rows),
        rows=rows,
    )


@router.get("/api/analytics/owner-intelligence", response_model=OwnerIntelResponse)
def analytics_owner_intelligence(min_vessels: int = 2, min_risk: int = 0, limit: int = 50):
    """Aggregate fleet risk by beneficial owner from vessel_registry.

    min_vessels: minimum fleet size to include an owner.
    min_risk: minimum average risk score.
    limit: max rows, clamped to [1, 200].
    """
    limit = max(1, min(limit, 200))
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    reg_df = db.pg_query(
        "SELECT imo, owner, flag, risk_score, ship_type "
        "FROM vessels "
        "WHERE fetch_ok = true AND owner IS NOT NULL AND owner != '' ",
    )
    if reg_df.empty:
        return OwnerIntelResponse(as_of=now_dt.isoformat(), total_owners=0, rows=[])

    # Enrich with live fleet kind/segment via MMSI->IMO join on live_positions
    live_df = db.query(
        "SELECT imo, kind, segment FROM live_positions WHERE imo IS NOT NULL",
        [],
    )
    live_map: dict = {}
    if not live_df.empty:
        for _, r in live_df.iterrows():
            imo_val = r.get("imo")
            if imo_val and not pd.isna(imo_val):
                live_map[int(imo_val)] = {
                    "kind": str_or_none(r.get("kind")),
                    "segment": str_or_none(r.get("segment")),
                }

    owners: dict[str, dict] = {}
    for _, r in reg_df.iterrows():
        owner = str(r["owner"]).strip()
        imo_val = r.get("imo")
        live_info = (live_map.get(int(imo_val)) or {}) if imo_val and not pd.isna(imo_val) else {}
        kind_val = live_info.get("kind") or ""
        risk_val = r.get("risk_score")
        risk_int = int(risk_val) if risk_val is not None and not pd.isna(risk_val) else 0
        flag_val = str_or_none(r.get("flag")) or ""

        if owner not in owners:
            owners[owner] = {
                "vessel_count": 0,
                "risks": [],
                "flags": set(),
                "tanker_count": 0,
                "bulk_count": 0,
                "segments": [],
            }
        o = owners[owner]
        o["vessel_count"] += 1
        o["risks"].append(risk_int)
        if flag_val:
            o["flags"].add(flag_val)
        if kind_val == "tanker":
            o["tanker_count"] += 1
        elif kind_val == "bulk":
            o["bulk_count"] += 1
        seg_val = str_or_none(live_info.get("segment"))
        if seg_val:
            o["segments"].append(seg_val)

    rows = []
    for owner, o in owners.items():
        if o["vessel_count"] < min_vessels:
            continue
        risks = o["risks"]
        avg_risk = round(sum(risks) / len(risks), 1) if risks else None
        if avg_risk is not None and avg_risk < min_risk:
            continue
        max_risk = max(risks) if risks else None
        high_risk = sum(1 for s in risks if s >= 50)
        risk_weighted = sum(risks)
        segs = o["segments"]
        top_seg = max(set(segs), key=segs.count) if segs else None
        rows.append(
            OwnerIntelRow(
                owner=owner,
                vessel_count=o["vessel_count"],
                risk_weighted=risk_weighted,
                avg_risk=avg_risk,
                max_risk=max_risk,
                high_risk_count=high_risk,
                tanker_count=o["tanker_count"],
                bulk_count=o["bulk_count"],
                flags=sorted(o["flags"])[:5],
                top_segment=top_seg,
            )
        )

    rows.sort(key=lambda r: r.risk_weighted, reverse=True)
    rows = rows[:limit]

    return OwnerIntelResponse(
        as_of=now_dt.isoformat(),
        total_owners=len(owners),
        rows=rows,
    )


@router.get("/api/analytics/owner-fleet-status", response_model=OwnerFleetStatusResponse)
def analytics_owner_fleet_status(
    kind: str | None = None,
    min_vessels: int = 1,
    limit: int = 30,
):
    """Live laden/ballast status per beneficial owner.

    Joins live_positions (imo) -> vessel_registry (owner) -> vessel_state (laden).
    kind: filter to 'tanker' or 'bulk' (omit for all).
    min_vessels: minimum number of live vessels the owner must have (clamped 1-20).
    limit: max rows (clamped 1-100).
    """
    min_vessels = max(1, min(min_vessels, 20))
    limit = max(1, min(limit, 100))
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    # Live positions: keep only vessels with an IMO (registry join requires it)
    live_q = "SELECT mmsi, imo, kind, segment, region FROM live_positions WHERE imo IS NOT NULL"
    live_params: list = []
    if kind:
        live_q += " AND kind = ?"
        live_params.append(kind)
    live_df = db.query(live_q, live_params)

    if live_df.empty:
        return OwnerFleetStatusResponse(
            as_of=now_dt.isoformat(), kind=kind, total_owners=0, rows=[]
        )

    # Vessel state (laden/ballast) keyed by mmsi
    state_df = db.query(
        "SELECT mmsi, laden FROM vessel_state",
        [],
        db=db.analytics_db_path(),
    )
    laden_map: dict[int, str] = {}
    if not state_df.empty:
        for _, r in state_df.iterrows():
            laden_val = r.get("laden")
            if laden_val and not (isinstance(laden_val, float) and pd.isna(laden_val)):
                laden_map[int(r["mmsi"])] = str(laden_val)

    # Registry: imo -> owner, risk_score, flag
    imos = live_df["imo"].dropna().astype(int).unique().tolist()
    if not imos:
        return OwnerFleetStatusResponse(
            as_of=now_dt.isoformat(), kind=kind, total_owners=0, rows=[]
        )
    reg_df = db.pg_query(
        "SELECT imo, owner, risk_score, flag FROM vessels "
        "WHERE imo = ANY(%s) AND fetch_ok = true AND owner IS NOT NULL AND owner != ''",
        [imos],
    )
    imo_to_reg: dict[int, dict] = {}
    if not reg_df.empty:
        for _, r in reg_df.iterrows():
            imo_val = r.get("imo")
            if imo_val is not None and not (isinstance(imo_val, float) and pd.isna(imo_val)):
                imo_to_reg[int(imo_val)] = {
                    "owner": str(r["owner"]).strip(),
                    "risk_score": r.get("risk_score"),
                    "flag": str_or_none(r.get("flag")),
                }

    # Aggregate by owner
    owners_agg: dict[str, dict] = {}
    for _, r in live_df.iterrows():
        imo_val = r.get("imo")
        if imo_val is None or (isinstance(imo_val, float) and pd.isna(imo_val)):
            continue
        reg = imo_to_reg.get(int(imo_val))
        if not reg:
            continue
        owner = reg["owner"]
        mmsi_int = int(r["mmsi"])
        laden_str = laden_map.get(mmsi_int)
        seg = str_or_none(r.get("segment"))
        region = str_or_none(r.get("region"))
        risk = reg.get("risk_score")
        flag = reg.get("flag") or ""

        if owner not in owners_agg:
            owners_agg[owner] = {
                "laden": 0,
                "ballast": 0,
                "unknown": 0,
                "segments": [],
                "risks": [],
                "flags": set(),
                "regions": set(),
            }
        o = owners_agg[owner]
        if laden_str == "laden":
            o["laden"] += 1
        elif laden_str == "ballast":
            o["ballast"] += 1
        else:
            o["unknown"] += 1
        if seg:
            o["segments"].append(seg)
        if risk is not None and not (isinstance(risk, float) and pd.isna(risk)):
            o["risks"].append(int(risk))
        if flag:
            o["flags"].add(flag)
        if region:
            o["regions"].add(region.replace("_", " "))

    rows: list[OwnerFleetStatusRow] = []
    for owner, o in owners_agg.items():
        live_count = o["laden"] + o["ballast"] + o["unknown"]
        if live_count < min_vessels:
            continue
        segs = o["segments"]
        top_seg = max(set(segs), key=segs.count) if segs else None
        risks = o["risks"]
        avg_risk = round(sum(risks) / len(risks), 1) if risks else None
        rows.append(
            OwnerFleetStatusRow(
                owner=owner,
                live_count=live_count,
                laden=o["laden"],
                ballast=o["ballast"],
                unknown=o["unknown"],
                top_segment=top_seg,
                avg_risk=avg_risk,
                flags=sorted(o["flags"])[:4],
                regions=sorted(o["regions"])[:5],
            )
        )

    rows.sort(key=lambda r: r.live_count, reverse=True)
    rows = rows[:limit]

    return OwnerFleetStatusResponse(
        as_of=now_dt.isoformat(),
        kind=kind,
        total_owners=len(owners_agg),
        rows=rows,
    )


@router.get("/api/analytics/speed-anomalies", response_model=SpeedAnomalyResponse)
def analytics_speed_anomalies(
    kind: str = "tanker", min_z: float = 2.5, min_sog: float = 1.0, limit: int = 50
):
    """Detect vessels moving significantly faster or slower than their segment peers.

    Uses live_positions; computes per-segment median and IQR-based Z-score.
    min_z: minimum |z-score| to include (default 2.5).
    min_sog: minimum SOG to include (filters anchored/drifting vessels, default 1.0 kn).
    kind: vessel type filter.
    limit: max rows returned.
    """
    limit = max(1, min(limit, 200))
    now_dt = datetime.now(UTC).replace(tzinfo=None)

    # Use the in-process live cache to avoid DB lock contention
    fleet_df = live_all()
    if not fleet_df.empty:
        fleet_df = fleet_df[fleet_df["sog"].fillna(0) >= min_sog]
        if kind:
            fleet_df = fleet_df[fleet_df["kind"] == kind]
        fleet_df = fleet_df[fleet_df["segment"].notna() & (fleet_df["segment"] != "Small")]
        needed = [
            "mmsi",
            "name",
            "kind",
            "segment",
            "region",
            "lat",
            "lon",
            "sog",
            "destination",
            "nav_status",
            "imo",
        ]
        fleet_df = fleet_df[[c for c in needed if c in fleet_df.columns]]

    if fleet_df.empty:
        return SpeedAnomalyResponse(
            as_of=now_dt.isoformat(),
            total_vessels_checked=0,
            anomaly_count=0,
            rows=[],
        )

    fleet_df["sog"] = pd.to_numeric(fleet_df["sog"], errors="coerce")
    fleet_df = fleet_df.dropna(subset=["sog"])

    # Compute per-segment median and MAD (median absolute deviation)
    seg_stats: dict[str, tuple[float, float]] = {}
    for seg, grp in fleet_df.groupby("segment"):
        sogs = grp["sog"].sort_values().values
        n = len(sogs)
        if n < 5:
            continue
        median_sog = float(sogs[n // 2])
        mad = float(sorted(abs(s - median_sog) for s in sogs)[n // 2])
        if mad < 0.1:
            mad = 0.5  # floor MAD to avoid division by zero / hyper-sensitivity
        seg_stats[str(seg)] = (median_sog, mad)

    # Build intermediate dicts so we can enrich before constructing Pydantic objects
    # (Pydantic v2 models are immutable - cannot set attributes after construction)
    raw_rows: list[dict] = []
    for _, r in fleet_df.iterrows():
        seg = str_or_none(r.get("segment"))
        if not seg or seg not in seg_stats:
            continue
        median_sog, mad = seg_stats[seg]
        sog_val = float(r["sog"])
        z = (sog_val - median_sog) / (1.4826 * mad)  # 1.4826 = conversion factor MAD -> sigma

        if abs(z) < min_z:
            continue

        raw_rows.append(
            {
                "mmsi": int(r["mmsi"]),
                "imo": valid_imo(r.get("imo")),
                "name": str_or_none(r.get("name")),
                "kind": str_or_none(r.get("kind")),
                "segment": seg,
                "region": str_or_none(r.get("region")),
                "lat": float(r["lat"]) if not pd.isna(r.get("lat")) else None,
                "lon": float(r["lon"]) if not pd.isna(r.get("lon")) else None,
                "sog": round(sog_val, 1),
                "segment_median_sog": round(median_sog, 1),
                "z_score": round(z, 2),
                "anomaly_type": "fast" if z > 0 else "slow",
                "destination": str_or_none(r.get("destination")),
                "nav_status": int(r["nav_status"])
                if r.get("nav_status") is not None and not pd.isna(r.get("nav_status"))
                else None,
                "registry_risk": None,
            }
        )

    # Sort by |z_score| descending, take top limit
    raw_rows.sort(key=lambda d: abs(d["z_score"]), reverse=True)
    raw_rows = raw_rows[:limit]

    # Enrich with registry risk before constructing Pydantic objects
    imo_list = [d["imo"] for d in raw_rows if d["imo"] is not None]
    if imo_list:
        reg_df = db.pg_query(
            "SELECT imo, risk_score FROM vessels WHERE imo = ANY(%s) AND fetch_ok = true",
            [imo_list],
        )
        risk_m: dict[int, int] = {}
        if not reg_df.empty:
            for _, rr in reg_df.iterrows():
                imo_v = rr.get("imo")
                risk_v = rr.get("risk_score")
                if imo_v and not pd.isna(imo_v) and risk_v and not pd.isna(risk_v):
                    risk_m[int(imo_v)] = int(risk_v)
        for d in raw_rows:
            if d["imo"] is not None:
                d["registry_risk"] = risk_m.get(d["imo"])

    rows = [SpeedAnomalyRow(**d) for d in raw_rows]

    return SpeedAnomalyResponse(
        as_of=now_dt.isoformat(),
        total_vessels_checked=len(fleet_df),
        anomaly_count=len(rows),
        rows=rows,
    )
