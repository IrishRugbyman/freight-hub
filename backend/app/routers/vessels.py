"""Per-vessel detail: track, state, voyages, behavioural risk, Equasis and MyShipTracking."""

from __future__ import annotations

import json as _json
import math
from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter

from .. import db
from ..common import iso, str_or_none, valid_imo
from ..schemas import (
    MstPortCall,
    MstVesselData,
    MstVoyage,
    TrackPoint,
    VesselBehavioralRisk,
    VesselStateData,
    VoyageEvent,
    VoyagesResponse,
)

router = APIRouter()


@router.get("/api/vessels/{mmsi}/track", response_model=list[TrackPoint])
def vessel_track(mmsi: int, hours: int = 24):
    """Historical trail for a vessel from ais_snapshots. hours clamped to [1, 336].

    Spatial cleanup so anchored vessels don't draw scribble-circles. An anchored
    vessel swings around its chain inside a ~500m circle while its SOG blips
    0.1<->0.9 kn from tidal current, so speed-based detection fractures the run.
    Instead we cluster spatially: any maximal run of points staying within ~400m
    of their running centroid is "stationary" and collapses to a single fix (the
    last one). Genuinely moving points are distance-thinned to ~200m for size.
    The most recent fix is always included.
    """

    h = max(1, min(hours, 336))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=h)
    df = db.query(
        "SELECT snapshot_ts, lat, lon, sog FROM ais_snapshots "
        "WHERE mmsi = ? AND snapshot_ts >= ? ORDER BY snapshot_ts",
        [mmsi, cutoff],
    )
    if df.empty:
        return []
    df = df.astype(object).where(df.notna(), None)
    rows = list(df.itertuples())

    _CLUSTER_M = 400.0  # within this of the run centroid => stationary swing
    _MOVE_M = 200.0  # distance-thinning for moving points
    _M_PER_DEG = 111_000.0
    _coslat = math.cos(math.radians(rows[0].lat))

    def _dist_m(a_lat, a_lon, b_lat, b_lon) -> float:
        dlat = (a_lat - b_lat) * _M_PER_DEG
        dlon = (a_lon - b_lon) * _M_PER_DEG * _coslat
        return math.hypot(dlat, dlon)

    kept: list = []
    i, n = 0, len(rows)
    while i < n:
        # Grow a stationary cluster from i: extend while the next fix stays within
        # _CLUSTER_M of the running centroid (robust to SOG noise and swing).
        sum_lat, sum_lon, cnt, j = rows[i].lat, rows[i].lon, 1, i
        while j + 1 < n:
            c_lat, c_lon = sum_lat / cnt, sum_lon / cnt
            nxt = rows[j + 1]
            if _dist_m(nxt.lat, nxt.lon, c_lat, c_lon) <= _CLUSTER_M:
                sum_lat += nxt.lat
                sum_lon += nxt.lon
                cnt += 1
                j += 1
            else:
                break
        if cnt >= 3:
            kept.append(rows[j])  # collapse the swing to its departure fix
            i = j + 1
        else:
            r = rows[i]
            if not kept or _dist_m(r.lat, r.lon, kept[-1].lat, kept[-1].lon) >= _MOVE_M:
                kept.append(r)
            i += 1

    if not kept:
        kept.append(rows[-1])
    elif kept[-1] is not rows[-1]:
        kept.append(rows[-1])  # always include the most recent fix

    return [TrackPoint(ts=iso(r.snapshot_ts), lat=r.lat, lon=r.lon, sog=r.sog) for r in kept]


@router.get("/api/vessels/{mmsi}/state", response_model=VesselStateData | None)
def vessel_state_endpoint(mmsi: int):
    """Laden/ballast state for a vessel inferred from accumulated draught history.

    Also computes days_at_anchor: length of the contiguous at-anchor streak
    (nav_status 1 or 5) ending at the most recent snapshot, or None if underway.
    """
    df = db.query(
        "SELECT laden, last_draught, max_draught_seen, updated_ts FROM vessel_state WHERE mmsi = ?",
        [mmsi],
        db=db.analytics_db_path(),
    )

    laden_val = last_draught_val = max_draught_val = updated_ts_val = None
    if not df.empty:
        r = df.iloc[0]

        def _fv(v):
            return None if (v is None or (isinstance(v, float) and pd.isna(v))) else float(v)

        laden_val = str(r["laden"]) if r["laden"] else None
        last_draught_val = _fv(r["last_draught"])
        max_draught_val = _fv(r["max_draught_seen"])
        updated_ts_val = iso(r["updated_ts"])

    # Days at anchor: walk back through snapshots looking for a contiguous streak
    # of nav_status IN (1=anchor, 5=moored). Look back up to 60 days.
    days_at_anchor: float | None = None
    snap_df = db.query(
        "SELECT snapshot_ts, nav_status FROM ais_snapshots "
        "WHERE mmsi = ? AND snapshot_ts >= ? "
        "ORDER BY snapshot_ts DESC",
        [mmsi, datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)],
    )
    if not snap_df.empty:
        streak_start = None
        for row in snap_df.itertuples():
            ns = row.nav_status
            is_null = ns is None or pd.isna(ns)
            if not is_null and int(ns) in (1, 5):
                streak_start = row.snapshot_ts
            elif is_null and streak_start is not None:
                streak_start = row.snapshot_ts  # bridge through reporting gaps
            else:
                break
        if streak_start is not None:
            now_ts = datetime.now(UTC).replace(tzinfo=None)
            days_at_anchor = round((now_ts - streak_start).total_seconds() / 86400, 1)

    if df.empty and days_at_anchor is None:
        return None

    return VesselStateData(
        mmsi=mmsi,
        laden=laden_val,
        last_draught=last_draught_val,
        max_draught_seen=max_draught_val,
        updated_ts=updated_ts_val,
        days_at_anchor=days_at_anchor,
    )


@router.get("/api/vessels/{mmsi}/voyages", response_model=VoyagesResponse)
def vessel_voyages(mmsi: int, days: int = 14):
    """Voyage timeline for a vessel: port-call history, chokepoint transits, destination changes.

    Events are sorted chronologically and cover the last `days` days (clamped 1-90).
    """
    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    _db = db.analytics_db_path()

    events: list[VoyageEvent] = []

    # Port calls (anchored episodes)
    anch_df = db.query(
        "SELECT zone, start_ts, end_ts, kind, segment "
        "FROM anchored_episodes WHERE mmsi = ? AND end_ts >= ? ORDER BY start_ts",
        [mmsi, cutoff],
        db=_db,
    )
    for _, r in anch_df.iterrows():
        try:
            dwell_h = round(
                (pd.Timestamp(r["end_ts"]) - pd.Timestamp(r["start_ts"])).total_seconds() / 3600,
                1,
            )
        except Exception:
            dwell_h = None
        events.append(
            VoyageEvent(
                type="port_call",
                ts=iso(r["start_ts"]) or "",
                end_ts=iso(r["end_ts"]),
                zone=str(r["zone"]) if r["zone"] else None,
                dwell_hours=dwell_h,
                kind=str(r["kind"]) if r["kind"] else None,
                segment=str(r["segment"]) if r["segment"] else None,
            )
        )

    # Chokepoint transits
    transit_df = db.query(
        "SELECT chokepoint, entered_ts, exited_ts, direction, kind, segment, laden "
        "FROM transit_events WHERE mmsi = ? AND entered_ts >= ? ORDER BY entered_ts",
        [mmsi, cutoff],
        db=_db,
    )
    for _, r in transit_df.iterrows():
        laden_val = r["laden"]
        laden_bool = (
            None
            if (laden_val is None or (isinstance(laden_val, float) and pd.isna(laden_val)))
            else bool(laden_val)
        )
        events.append(
            VoyageEvent(
                type="transit",
                ts=iso(r["entered_ts"]) or "",
                end_ts=iso(r["exited_ts"]),
                zone=str(r["chokepoint"]) if r["chokepoint"] else None,
                direction=str(r["direction"]) if r["direction"] else None,
                laden=laden_bool,
                kind=str(r["kind"]) if r["kind"] else None,
                segment=str(r["segment"]) if r["segment"] else None,
            )
        )

    # Destination changes (reroute events)
    reroute_df = db.query(
        "SELECT start_ts, lat, lon, kind, segment, details "
        "FROM ais_events WHERE mmsi = ? AND type = 'reroute' AND start_ts >= ? ORDER BY start_ts",
        [mmsi, cutoff],
        db=_db,
    )
    for _, r in reroute_df.iterrows():
        try:
            d = _json.loads(r["details"]) if r["details"] else {}
        except (ValueError, TypeError):
            d = {}
        events.append(
            VoyageEvent(
                type="reroute",
                ts=iso(r["start_ts"]) or "",
                end_ts=iso(r["start_ts"]),
                lat=float(r["lat"]) if r["lat"] is not None else None,
                lon=float(r["lon"]) if r["lon"] is not None else None,
                old_destination=d.get("old_destination"),
                new_destination=d.get("new_destination"),
                kind=str(r["kind"]) if r["kind"] else None,
                segment=str(r["segment"]) if r["segment"] else None,
            )
        )

    # STS events (ship-to-ship transfers involving this vessel)
    sts_df = db.query(
        "SELECT start_ts, end_ts, mmsi2, lat, lon, kind, segment, details "
        "FROM ais_events WHERE type = 'sts' AND (mmsi = ? OR mmsi2 = ?) AND start_ts >= ? "
        "ORDER BY start_ts",
        [mmsi, mmsi, cutoff],
        db=_db,
    )
    if not sts_df.empty:
        sts_mmsis2 = [int(m) for m in sts_df["mmsi2"].dropna().unique() if valid_imo(m)]
        name2_map: dict[int, str | None] = {}
        if sts_mmsis2:
            ph_sts = ",".join("?" * len(sts_mmsis2))
            n2 = db.query(
                f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({ph_sts})", sts_mmsis2
            )
            for _, r in n2.iterrows():
                name2_map[int(r["mmsi"])] = str_or_none(r.get("name"))
        for _, r in sts_df.iterrows():
            try:
                d = _json.loads(r["details"]) if r["details"] else {}
            except (ValueError, TypeError):
                d = {}
            mmsi2_val = (
                int(r["mmsi2"]) if r["mmsi2"] is not None and not pd.isna(r["mmsi2"]) else None
            )
            events.append(
                VoyageEvent(
                    type="sts",
                    ts=iso(r["start_ts"]) or "",
                    end_ts=iso(r["end_ts"]) if r["end_ts"] is not None else None,
                    lat=float(r["lat"]) if r["lat"] is not None and not pd.isna(r["lat"]) else None,
                    lon=float(r["lon"]) if r["lon"] is not None and not pd.isna(r["lon"]) else None,
                    kind=str(r["kind"]) if r["kind"] else None,
                    segment=str(r["segment"]) if r["segment"] else None,
                    mmsi2=mmsi2_val,
                    name2=name2_map.get(mmsi2_val) if mmsi2_val else None,
                )
            )

    # Cargo transitions for this vessel from AIS snapshots
    snap_df = db.query(
        "SELECT snapshot_ts, draught, lat, lon, region "
        "FROM ais_snapshots "
        "WHERE mmsi = ? AND snapshot_ts >= ? AND draught > 0 "
        "ORDER BY snapshot_ts",
        [mmsi, cutoff],
    )
    if not snap_df.empty and len(snap_df) >= 4:
        snap_df["snapshot_ts"] = pd.to_datetime(snap_df["snapshot_ts"])
        snap_df["bucket"] = snap_df["snapshot_ts"].dt.floor("6h")
        bucket_agg = (
            snap_df.groupby("bucket")
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
        if len(bucket_agg) >= 2:
            bucket_agg["prev_d"] = bucket_agg["d_median"].shift(1)
            bucket_agg["change"] = bucket_agg["d_median"] - bucket_agg["prev_d"]
            bucket_agg = bucket_agg.dropna(subset=["change"])
            for _, row in bucket_agg[bucket_agg["change"].abs() >= 2.0].iterrows():
                ch = float(row["change"])
                bkt = row["bucket"]
                trans_ts = (
                    bkt.to_pydatetime().replace(tzinfo=None)
                    if hasattr(bkt, "to_pydatetime")
                    else bkt
                )
                events.append(
                    VoyageEvent(
                        type="cargo_load" if ch > 0 else "cargo_discharge",
                        ts=iso(trans_ts) or "",
                        lat=round(float(row["lat"]), 4),
                        lon=round(float(row["lon"]), 4),
                        draught_before=round(float(row["prev_d"]), 1),
                        draught_after=round(float(row["d_median"]), 1),
                        change_m=round(abs(ch), 1),
                    )
                )

    # Sort all events chronologically
    events.sort(key=lambda e: e.ts)
    return VoyagesResponse(mmsi=mmsi, events=events)


@router.get("/api/vessels/{mmsi}/behavioral-risk", response_model=VesselBehavioralRisk)
def vessel_behavioral_risk(mmsi: int, days: int = 30):
    """Behavioral risk assessment for a single vessel.

    Counts STS and reroute events over the last N days, combines with Equasis registry
    risk score (if the vessel has an IMO in the registry), and returns a composite score.

    Scoring mirrors the fleet leaderboard in /api/analytics/vessel-risk-scores:
      behavioral_score = min(sts_count * 20 + reroute_count * 5, 100)
      total_score = round(behavioral * 0.4 + registry * 0.6) [if registry present]
                  = behavioral                               [if no registry data]
      + 25 if OFAC sanctioned, capped at 100
    """
    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    _adb = db.analytics_db_path()

    # STS count (as either party)
    sts_df = db.query(
        "SELECT COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND (mmsi = ? OR mmsi2 = ?) AND start_ts >= ?",
        [mmsi, mmsi, cutoff],
        db=_adb,
    )
    sts_count = int(sts_df.iloc[0]["cnt"]) if not sts_df.empty else 0

    # Reroute count
    rr_df = db.query(
        "SELECT COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'reroute' AND mmsi = ? AND start_ts >= ?",
        [mmsi, cutoff],
        db=_adb,
    )
    reroute_count = int(rr_df.iloc[0]["cnt"]) if not rr_df.empty else 0

    # Recent events (last 5 STS + reroute)
    ev_df = db.query(
        "SELECT type, start_ts, lat, lon, details FROM ais_events "
        "WHERE (mmsi = ? OR mmsi2 = ?) AND type IN ('sts', 'reroute') AND start_ts >= ? "
        "ORDER BY start_ts DESC LIMIT 5",
        [mmsi, mmsi, cutoff],
        db=_adb,
    )
    recent_events: list[dict] = []
    if not ev_df.empty:
        for _, r in ev_df.iterrows():
            try:
                d = _json.loads(r["details"]) if r["details"] else {}
            except (ValueError, TypeError):
                d = {}
            recent_events.append(
                {
                    "type": str(r["type"]),
                    "ts": iso(r["start_ts"]) or "",
                    "lat": round(float(r["lat"]), 4)
                    if r["lat"] is not None and not pd.isna(r["lat"])
                    else None,
                    "lon": round(float(r["lon"]), 4)
                    if r["lon"] is not None and not pd.isna(r["lon"])
                    else None,
                    **{k: v for k, v in d.items() if k in ("old_destination", "new_destination")},
                }
            )

    # Look up IMO from live_positions to query registry
    lp_df = db.query("SELECT imo FROM live_positions WHERE mmsi = ?", [mmsi])
    imo = valid_imo(lp_df.iloc[0].get("imo")) if not lp_df.empty else None

    reg_risk: int | None = None
    ofac = False
    if imo:
        reg_df = db.pg_query(
            "SELECT risk_score, ofac_sanctioned FROM vessels WHERE imo = %s AND fetch_ok = true",
            [imo],
        )
        if not reg_df.empty:
            rs = reg_df.iloc[0].get("risk_score")
            of = reg_df.iloc[0].get("ofac_sanctioned")
            reg_risk = int(rs) if rs is not None and not pd.isna(rs) else None
            ofac = bool(of) if of is not None and not pd.isna(of) else False

    behavioral = min(sts_count * 20 + reroute_count * 5, 100)
    if reg_risk is not None:
        base = round(behavioral * 0.4 + reg_risk * 0.6)
    else:
        base = behavioral
    total = min(base + (25 if ofac else 0), 100)

    if total >= 75:
        risk_level = "Critical"
    elif total >= 50:
        risk_level = "High"
    elif total >= 25:
        risk_level = "Elevated"
    else:
        risk_level = "Low"

    return VesselBehavioralRisk(
        mmsi=mmsi,
        imo=imo,
        sts_count=sts_count,
        reroute_count=reroute_count,
        days=days,
        behavioral_score=behavioral,
        registry_risk=reg_risk,
        ofac=ofac,
        total_score=total,
        risk_level=risk_level,
        recent_events=recent_events,
    )


@router.get("/api/vessels/{imo}/equasis")
def vessel_equasis(imo: int):
    """Equasis registry data for a vessel by IMO, served from vessels PG table.

    Read-only: the crawler (registry/crawl.py via freight-registry.service) is the
    sole writer. No live Equasis requests are made here - those consume the monthly
    consultation quota and must only happen in the scheduled crawler.
    """
    from fastapi import HTTPException

    reg_df = db.pg_query(
        "SELECT * FROM vessels WHERE imo = %s AND fetch_ok = true",
        [imo],
    )
    if reg_df.empty:
        raise HTTPException(status_code=404, detail="Not in registry yet")

    row = reg_df.iloc[0]
    result: dict = {"imo": imo}
    for col in reg_df.columns:
        if col in ("imo", "fetched_ts", "fetch_ok"):
            continue
        val = row[col]
        if val is None:
            continue
        import pandas as _pd

        if _pd.isna(val):
            continue
        if col in ("gross_tonnage", "dwt", "year_built"):
            result[col] = str(int(val))
        elif col == "risk_indicators":
            import json as _json_mod

            try:
                result[col] = _json_mod.loads(val) if isinstance(val, str) else val
            except (ValueError, TypeError):
                result[col] = []
        elif col == "risk_score":
            result[col] = int(val)
        elif col == "ofac_sanctioned":
            result[col] = bool(val)
        else:
            result[col] = val
    return result


@router.get("/api/vessels/{mmsi}/myshiptracking", response_model=MstVesselData)
def vessel_myshiptracking(mmsi: int):
    """MyShipTracking enrichment for a vessel by MMSI, served from mst.duckdb.

    Read-only: the crawler (registry/crawl_mst.py via freight-mst.timer) is the sole
    writer. No live scrape happens here - voyage/port-call history is immutable and
    persisted, so once a vessel has been crawled it is served instantly from DuckDB.
    """
    from fastapi import HTTPException

    _db = db.mst_db_path()
    state = db.query("SELECT * FROM mst_vessel_state WHERE mmsi = ?", [mmsi], db=_db)
    if state.empty:
        raise HTTPException(status_code=404, detail="Not crawled by MyShipTracking yet")

    import pandas as _pd

    def _v(val):
        return None if (val is None or _pd.isna(val)) else val

    def _i(val):
        v = _v(val)
        return int(v) if v is not None else None

    r = state.iloc[0]

    voy_df = db.query(
        "SELECT origin, departure, destination, arrival, distance_nm, duration, "
        "draught_m, avg_speed_kn, max_speed_kn, stops FROM mst_voyages "
        "WHERE mmsi = ? ORDER BY departure DESC",
        [mmsi],
        db=_db,
    )
    voyages = [
        MstVoyage(
            origin=_v(x["origin"]),
            departure=_v(x["departure"]),
            destination=_v(x["destination"]),
            arrival=_v(x["arrival"]),
            distance_nm=_v(x["distance_nm"]),
            duration=_v(x["duration"]),
            draught_m=_v(x["draught_m"]),
            avg_speed_kn=_v(x["avg_speed_kn"]),
            max_speed_kn=_v(x["max_speed_kn"]),
            stops=_i(x["stops"]),
        )
        for _, x in voy_df.iterrows()
    ]

    pc_df = db.query(
        "SELECT port, arrival, departure FROM mst_port_calls WHERE mmsi = ? ORDER BY arrival DESC",
        [mmsi],
        db=_db,
    )
    port_calls = [
        MstPortCall(port=_v(x["port"]), arrival=_v(x["arrival"]), departure=_v(x["departure"]))
        for _, x in pc_df.iterrows()
    ]

    return MstVesselData(
        mmsi=mmsi,
        imo=_i(r["imo"]),
        name=_v(r["name"]),
        flag=_v(r["flag"]),
        call_sign=_v(r["call_sign"]),
        ship_type=_v(r["ship_type"]),
        length_m=_v(r["length_m"]),
        beam_m=_v(r["beam_m"]),
        gross_tonnage=_i(r["gross_tonnage"]),
        dwt=_i(r["dwt"]),
        year_built=_i(r["year_built"]),
        status=_v(r["status"]),
        destination=_v(r["destination"]),
        eta=_v(r["eta"]),
        draught_m=_v(r["draught_m"]),
        station=_v(r["station"]),
        position_received_utc=_v(r["position_received_utc"]),
        fetched_ts=iso(r["fetched_ts"]),
        voyages=voyages,
        port_calls=port_calls,
    )
