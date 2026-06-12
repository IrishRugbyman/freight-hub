"""freight-api — live vessel tracker + transport-arb routes + dispersion analytics.

Live endpoints (AIS) read ais_positions.duckdb via db.py.
Static-backed endpoints (routes, dispersion backtest) serve precomputed JSON from
app/static/, with a live-compute fallback if the static file is absent.
The live dispersion series reads ais_vessel_dispersion from commo.duckdb via loaders.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from ais.regions import REGIONS
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse
from loaders.freight import load_ais_dispersion
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import db, equasis, fleet as _fleet
from .runner_dispersion import run_dispersion_default
from .runner_routes import run_routes_default
from .schemas import (
    AisDispersionRow,
    AisEvent,
    AnalyticsZone,
    ChokepointCount,
    CongestionResponse,
    DensityResponse,
    DispersionResponse,
    EventsResponse,
    FlagRiskResponse,
    FlagRiskRow,
    FleetFacets,
    FleetKPIs,
    FleetResponse,
    HighRiskPosition,
    HighRiskPositionsResponse,
    LadenResponse,
    LadenSegment,
    Meta,
    OwnerRiskItem,
    OwnerRiskResponse,
    PortFlowResponse,
    PortDestItem,
    RegionUtilResponse,
    RegionUtilRow,
    AnchorageDwellResponse,
    AnchoredVessel,
    CargoTransitionEvent,
    CargoTransitionsResponse,
    FleetAgeBand,
    FleetAgeResponse,
    RerouteRiskEvent,
    RerouteRiskResponse,
    DestinationFlowRow,
    DestinationFlowsResponse,
    MarketSegmentSummary,
    MarketSummaryResponse,
    RiskEventItem,
    RiskEventsResponse,
    ChokepointHeatmapCell,
    ChokepointHeatmapResponse,
    AnomalyWatchlistItem,
    AnomalyWatchlistResponse,
    TradeLaneCell,
    TradeLaneMatrixResponse,
    VesselBehavioralRisk,
    PortCongestionRow,
    PortCongestionResponse,
    VesselRiskRow,
    VesselRiskResponse,
    RoutesResponse,
    SlowSteamerEvent,
    SlowSteamersResponse,
    FleetUtilizationRow,
    FleetUtilizationResponse,
    TransitRiskEvent,
    TransitRiskResponse,
    SpeedAnalyticsResponse,
    RegionMomentumRow,
    RegionMomentumResponse,
    StsProximityPair,
    StsProximityResponse,
    StsRiskEvent,
    StsRiskResponse,
    SpeedSegmentRow,
    SpeedTrendPoint,
    SpeedTrendResponse,
    TrackPoint,
    TransitsResponse,
    Vessel,
    VesselStateData,
    VoyageEvent,
    VoyagesResponse,
)

_STATIC = Path(__file__).parent / "static"
_STATIC_ROUTES = _STATIC / "routes_default.json"
_STATIC_DISPERSION = _STATIC / "dispersion_default.json"

def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _serve_cached(static_path: Path, compute_fn, schema_class):
    if static_path.exists():
        return schema_class.model_validate_json(static_path.read_text())
    return compute_fn()


limiter = Limiter(key_func=get_remote_address, default_limits=["240/minute"])
app = FastAPI(title="freight-api", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda r, e: _rate_limited())
# Gzip large JSON responses (vessels payload is 1.5MB+ raw with 5000+ vessels)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://freight.lbzgiu.xyz", "http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _rate_limited():
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})


def _fresh_cutoff() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=db.STALE_HOURS)


def _valid_imo(v) -> int | None:
    """Return int IMO if valid, else None. Handles pandas NA/NaN/None."""
    if v is None:
        return None
    s = str(v)
    if s in ("nan", "None", "NA", "<NA>", ""):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _str_or_none(v) -> str | None:
    """Return string value or None, treating NaN/NA/None as None."""
    if v is None:
        return None
    s = str(v)
    return None if s in ("nan", "None", "NA", "<NA>", "") else s


def _live(where: str = "", params: list | None = None):
    """Fresh live_positions rows with an optional extra WHERE clause."""
    clause = f" AND {where}" if where else ""
    return db.query(
        f"SELECT * FROM live_positions WHERE updated_ts > ?{clause}",
        [_fresh_cutoff(), *(params or [])],
    )


@app.get("/api/health")
def health() -> dict:
    """DB reachability + count and timestamp of currently-tracked vessels."""
    df = _live()
    return {
        "ok": True,
        "tracked": int(len(df)),
        "last_update": _iso(df["updated_ts"].max()) if not df.empty else None,
    }


@app.get("/api/vessels", response_model=list[Vessel])
def vessels(kind: str | None = None, segment: str | None = None, region: str | None = None):
    """Fresh vessel positions, optionally filtered by kind / segment / region."""
    conds, params = [], []
    for col, val in (("kind", kind), ("segment", segment), ("region", region)):
        if val:
            conds.append(f"{col} = ?")
            params.append(val)
    df = _live(" AND ".join(conds), params)
    if df.empty:
        return []
    # Round to AIS-native resolution: cuts JSON size ~35% before gzip, lossless for display
    df["lat"] = df["lat"].round(5)   # ~1.1 m precision
    df["lon"] = df["lon"].round(5)
    for col, nd in (("sog", 1), ("cog", 1), ("draught", 1)):
        if col in df.columns:
            df[col] = df[col].round(nd)
    if "heading" in df.columns:
        df["heading"] = df["heading"].round(0)
    df = df.astype(object).where(df.notna(), None)  # NaN -> None for pydantic
    cols = set(df.columns)
    return [
        Vessel(
            mmsi=int(r.mmsi),
            name=r.name,
            lat=r.lat,
            lon=r.lon,
            sog=r.sog,
            cog=r.cog,
            heading=r.heading,
            destination=r.destination,
            kind=r.kind,
            segment=r.segment,
            region=r.region,
            updated_ts=_iso(r.updated_ts),
            imo=getattr(r, "imo", None) if "imo" in cols else None,
            draught=getattr(r, "draught", None) if "draught" in cols else None,
            nav_status=getattr(r, "nav_status", None) if "nav_status" in cols else None,
            eta=getattr(r, "eta", None) if "eta" in cols else None,
        )
        for r in df.itertuples()
    ]


@app.get("/api/vessels/{mmsi}/track", response_model=list[TrackPoint])
def vessel_track(mmsi: int, hours: int = 24):
    """Historical trail for a vessel from ais_snapshots. hours clamped to [1, 336]."""
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
    return [
        TrackPoint(ts=_iso(r.snapshot_ts), lat=r.lat, lon=r.lon, sog=r.sog)
        for r in df.itertuples()
    ]


@app.get("/api/vessels/{mmsi}/state", response_model=VesselStateData | None)
def vessel_state_endpoint(mmsi: int):
    """Laden/ballast state for a vessel inferred from accumulated draught history."""
    df = db.query(
        "SELECT laden, last_draught, max_draught_seen, updated_ts "
        "FROM vessel_state WHERE mmsi = ?",
        [mmsi],
        db=db.analytics_db_path(),
    )
    if df.empty:
        return None
    r = df.iloc[0]

    def _fv(v):
        return None if (v is None or (isinstance(v, float) and pd.isna(v))) else float(v)

    return VesselStateData(
        mmsi=mmsi,
        laden=str(r["laden"]) if r["laden"] else None,
        last_draught=_fv(r["last_draught"]),
        max_draught_seen=_fv(r["max_draught_seen"]),
        updated_ts=_iso(r["updated_ts"]),
    )


@app.get("/api/vessels/{mmsi}/voyages", response_model=VoyagesResponse)
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
                ts=_iso(r["start_ts"]) or "",
                end_ts=_iso(r["end_ts"]),
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
        laden_bool = None if (laden_val is None or (isinstance(laden_val, float) and pd.isna(laden_val))) else bool(laden_val)
        events.append(
            VoyageEvent(
                type="transit",
                ts=_iso(r["entered_ts"]) or "",
                end_ts=_iso(r["exited_ts"]),
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
                ts=_iso(r["start_ts"]) or "",
                end_ts=_iso(r["start_ts"]),
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
        sts_mmsis2 = [int(m) for m in sts_df["mmsi2"].dropna().unique() if _valid_imo(m)]
        name2_map: dict[int, str | None] = {}
        if sts_mmsis2:
            ph_sts = ",".join("?" * len(sts_mmsis2))
            n2 = db.query(f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({ph_sts})", sts_mmsis2)
            for _, r in n2.iterrows():
                name2_map[int(r["mmsi"])] = _str_or_none(r.get("name"))
        for _, r in sts_df.iterrows():
            try:
                d = _json.loads(r["details"]) if r["details"] else {}
            except (ValueError, TypeError):
                d = {}
            mmsi2_val = int(r["mmsi2"]) if r["mmsi2"] is not None and not pd.isna(r["mmsi2"]) else None
            events.append(VoyageEvent(
                type="sts",
                ts=_iso(r["start_ts"]) or "",
                end_ts=_iso(r["end_ts"]) if r["end_ts"] is not None else None,
                lat=float(r["lat"]) if r["lat"] is not None and not pd.isna(r["lat"]) else None,
                lon=float(r["lon"]) if r["lon"] is not None and not pd.isna(r["lon"]) else None,
                kind=str(r["kind"]) if r["kind"] else None,
                segment=str(r["segment"]) if r["segment"] else None,
                mmsi2=mmsi2_val,
                name2=name2_map.get(mmsi2_val) if mmsi2_val else None,
            ))

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
            .agg(d_median=("draught", "median"), lat=("lat", "median"), lon=("lon", "median"), fix_cnt=("draught", "count"))
            .reset_index()
        )
        bucket_agg = bucket_agg[bucket_agg["fix_cnt"] >= 2].sort_values("bucket").reset_index(drop=True)
        if len(bucket_agg) >= 2:
            bucket_agg["prev_d"] = bucket_agg["d_median"].shift(1)
            bucket_agg["change"] = bucket_agg["d_median"] - bucket_agg["prev_d"]
            bucket_agg = bucket_agg.dropna(subset=["change"])
            for _, row in bucket_agg[bucket_agg["change"].abs() >= 2.0].iterrows():
                ch = float(row["change"])
                bkt = row["bucket"]
                trans_ts = bkt.to_pydatetime().replace(tzinfo=None) if hasattr(bkt, "to_pydatetime") else bkt
                events.append(VoyageEvent(
                    type="cargo_load" if ch > 0 else "cargo_discharge",
                    ts=_iso(trans_ts) or "",
                    lat=round(float(row["lat"]), 4),
                    lon=round(float(row["lon"]), 4),
                    draught_before=round(float(row["prev_d"]), 1),
                    draught_after=round(float(row["d_median"]), 1),
                    change_m=round(abs(ch), 1),
                ))

    # Sort all events chronologically
    events.sort(key=lambda e: e.ts)
    return VoyagesResponse(mmsi=mmsi, events=events)


@app.get("/api/vessels/{mmsi}/behavioral-risk", response_model=VesselBehavioralRisk)
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
            recent_events.append({
                "type": str(r["type"]),
                "ts": _iso(r["start_ts"]) or "",
                "lat": round(float(r["lat"]), 4) if r["lat"] is not None and not pd.isna(r["lat"]) else None,
                "lon": round(float(r["lon"]), 4) if r["lon"] is not None and not pd.isna(r["lon"]) else None,
                **{k: v for k, v in d.items() if k in ("old_destination", "new_destination")},
            })

    # Look up IMO from live_positions to query registry
    lp_df = db.query("SELECT imo FROM live_positions WHERE mmsi = ?", [mmsi])
    imo = _valid_imo(lp_df.iloc[0].get("imo")) if not lp_df.empty else None

    reg_risk: int | None = None
    ofac = False
    if imo:
        reg_df = db.query(
            "SELECT risk_score, ofac_sanctioned FROM vessel_registry "
            "WHERE imo = ? AND fetch_ok = true",
            [imo],
            db=db.registry_db_path(),
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


@app.get("/api/analytics/ports", response_model=PortFlowResponse)
def analytics_ports(kind: str | None = None, top_n: int = 20):
    """Current destination distribution across the live fleet.

    Groups live_positions by normalized destination and returns counts by vessel kind.
    top_n clamped 5-50.
    """
    top_n = max(5, min(50, top_n))
    cutoff = _fresh_cutoff()
    params: list = [cutoff]
    kind_clause = ""
    if kind:
        kind_clause = "AND kind = ?"
        params.append(kind)

    df = db.query(
        f"SELECT "
        f"  UPPER(TRIM(destination)) AS dest, "
        f"  COUNT(*) AS cnt, "
        f"  COUNT(CASE WHEN kind='tanker' THEN 1 END) AS tankers, "
        f"  COUNT(CASE WHEN kind='bulk' THEN 1 END) AS bulkers "
        f"FROM live_positions "
        f"WHERE updated_ts > ? {kind_clause} "
        f"  AND destination IS NOT NULL AND TRIM(destination) != '' "
        f"GROUP BY dest ORDER BY cnt DESC LIMIT ?",
        params + [top_n],
    )

    total_df = db.query(
        f"SELECT COUNT(*) AS n FROM live_positions "
        f"WHERE updated_ts > ? {kind_clause} "
        f"  AND destination IS NOT NULL AND TRIM(destination) != ''",
        params,
    )
    total_with_dest = int(total_df.iloc[0]["n"]) if not total_df.empty else 0

    ports = []
    if not df.empty:
        for _, r in df.iterrows():
            ports.append(
                PortDestItem(
                    destination=str(r["dest"]),
                    count=int(r["cnt"]),
                    tankers=int(r["tankers"]),
                    bulkers=int(r["bulkers"]),
                )
            )

    return PortFlowResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        total_with_dest=total_with_dest,
        ports=ports,
    )


@app.get("/api/analytics/high-risk-positions", response_model=HighRiskPositionsResponse)
def analytics_high_risk_positions(min_risk: int = 60):
    """Live positions of vessels with risk_score >= min_risk from the vessel registry.

    Two-query approach: fetch scored vessels from registry, then look up their
    current live positions by IMO. min_risk clamped 0-100.
    """
    min_risk = max(0, min(100, min_risk))
    cutoff = _fresh_cutoff()

    reg_df = db.query(
        "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
        "FROM vessel_registry "
        "WHERE risk_score >= ? AND fetch_ok = true AND imo IS NOT NULL",
        [min_risk],
        db=db.registry_db_path(),
    )
    if reg_df.empty:
        return HighRiskPositionsResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            min_risk=min_risk,
            rows=[],
        )

    ifo_list = reg_df["imo"].tolist()
    placeholders = ",".join(["?" for _ in ifo_list])
    live_df = db.query(
        f"SELECT mmsi, imo, lat, lon, name, segment, kind "
        f"FROM live_positions "
        f"WHERE updated_ts > ? AND imo IN ({placeholders})",
        [cutoff, *ifo_list],
    )

    if live_df.empty:
        return HighRiskPositionsResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            min_risk=min_risk,
            rows=[],
        )

    merged = live_df.merge(reg_df, on="imo", how="inner")
    rows = []
    for _, r in merged.iterrows():
        rows.append(HighRiskPosition(
            mmsi=int(r["mmsi"]),
            imo=int(r["imo"]),
            lat=float(r["lat"]),
            lon=float(r["lon"]),
            name=str(r["name"]) if r["name"] else None,
            segment=str(r["segment"]) if r["segment"] else None,
            kind=str(r["kind"]) if r["kind"] else None,
            risk_score=int(r["risk_score"]),
            ofac_sanctioned=bool(r["ofac_sanctioned"]),
        ))

    rows.sort(key=lambda x: -x.risk_score)
    return HighRiskPositionsResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        min_risk=min_risk,
        rows=rows,
    )


@app.get("/api/analytics/speed", response_model=SpeedAnalyticsResponse)
def analytics_speed():
    """Fleet speed and utilization by segment, computed from live positions.

    Nav status codes: 0=under way engine, 1=at anchor, 5=moored.
    avg_sog_underway is the mean SOG of nav_status=0 vessels with SOG > 0.2 kn.
    Useful as a demand signal: rising average speed = tighter freight market.
    """
    cutoff = _fresh_cutoff()
    df = db.query(
        "SELECT kind, segment, nav_status, sog "
        "FROM live_positions "
        "WHERE updated_ts > ? AND kind IS NOT NULL AND segment IS NOT NULL",
        [cutoff],
    )
    if df.empty:
        return SpeedAnalyticsResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
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
        rows.append(SpeedSegmentRow(
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
        ))

    rows.sort(key=lambda r: r.total, reverse=True)
    return SpeedAnalyticsResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        total_vessels=len(df),
        rows=rows,
    )


@app.get("/api/analytics/speed-trend", response_model=SpeedTrendResponse)
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
        series.append(SpeedTrendPoint(
            date=str(day),
            avg_sog=avg_sog,
            underway_count=len(underway),
            total_count=total,
        ))

    series.sort(key=lambda p: p.date)
    return SpeedTrendResponse(kind=kind, segment=segment, days=days, series=series)


@app.get("/api/analytics/region-util", response_model=RegionUtilResponse)
def analytics_region_util():
    """Fleet utilization (underway/anchored/moored) per maritime region.

    Aggregates from live positions. High anchor ratios = congestion signal.
    """
    cutoff = _fresh_cutoff()
    df = db.query(
        "SELECT region, nav_status, sog "
        "FROM live_positions "
        "WHERE updated_ts > ? AND region IS NOT NULL",
        [cutoff],
    )
    if df.empty:
        return RegionUtilResponse(as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "", rows=[])

    rows = []
    for region, grp in df.groupby("region"):
        total = len(grp)
        underway = int((grp["nav_status"] == 0).sum())
        anchored = int((grp["nav_status"] == 1).sum())
        moored = int((grp["nav_status"] == 5).sum())
        sog_vals = grp["sog"].dropna()
        avg_sog = round(float(sog_vals.mean()), 1) if len(sog_vals) > 0 else None
        rows.append(RegionUtilRow(
            region=str(region),
            total=total,
            underway=underway,
            anchored=anchored,
            moored=moored,
            pct_underway=round(underway / total * 100, 1) if total > 0 else 0.0,
            avg_sog=avg_sog,
        ))

    rows.sort(key=lambda r: r.total, reverse=True)
    return RegionUtilResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        rows=rows,
    )


@app.get("/api/analytics/sts-risk", response_model=StsRiskResponse)
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
        "WHERE type = 'sts' AND start_ts >= ?",
        [cutoff],
        db=db.analytics_db_path(),
    )
    if events_df.empty:
        return StsRiskResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            days=days, total_events=0, enriched_events=0, rows=[],
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
    known_imos = [_valid_imo(v.get("imo")) for v in mmsi_info.values()]
    known_imos = [i for i in known_imos if i is not None]
    if known_imos:
        imo_list = list(set(known_imos))
        ph3 = ",".join("?" * len(imo_list))
        reg_df = db.query(
            f"SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            f"FROM vessel_registry WHERE imo IN ({ph3}) AND fetch_ok = true",
            imo_list,
            db=db.registry_db_path(),
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac_sanctioned"]),
            }

    def _vessel_risk(mmsi_val):
        info = mmsi_info.get(int(mmsi_val), {})
        imo_val = _valid_imo(info.get("imo"))
        if imo_val:
            return imo_risk.get(imo_val, {})
        return {}

    rows = []
    enriched = 0
    for _, ev in events_df.iterrows():
        det = {}
        if ev.get("details"):
            try:
                det = _json.loads(ev["details"])
            except Exception:
                pass

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

        rows.append(StsRiskEvent(
            event_id=str(ev["event_id"]),
            start_ts=_iso(ev["start_ts"]) or "",
            region=_str_or_none(ev.get("region")),
            kind=_str_or_none(ev.get("kind")),
            segment=_str_or_none(ev.get("segment")),
            mmsi=mmsi_val,
            mmsi2=mmsi2_val,
            name=_str_or_none(mmsi_info.get(mmsi_val, {}).get("name")),
            name2=_str_or_none(mmsi_info.get(mmsi2_val, {}).get("name")) if mmsi2_val else None,
            duration_hours=det.get("duration_hours"),
            co_location_fixes=det.get("co_location_fixes"),
            risk_score=rs1,
            risk_score2=rs2,
            ofac=bool(r1.get("ofac", False)),
            ofac2=bool(r2.get("ofac", False)),
            max_risk=max_risk,
        ))

    rows.sort(key=lambda r: r.max_risk, reverse=True)
    return StsRiskResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        days=days,
        total_events=len(events_df),
        enriched_events=enriched,
        rows=rows,
    )


@app.get("/api/analytics/reroutes", response_model=RerouteRiskResponse)
def analytics_reroutes(days: int = 7, min_risk: int = 0, segment: str | None = None):
    """Recent destination-change events enriched with vessel risk scores.

    Sorted by risk_score descending (reroutes by high-risk vessels first).
    Use min_risk=25 to filter to intelligence-relevant changes.
    """
    import json as _json

    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    seg_clause = " AND segment = ?" if segment else ""
    seg_params = [segment] if segment else []

    events_df = db.query(
        "SELECT event_id, mmsi, start_ts, region, kind, segment, details "
        f"FROM ais_events WHERE type = 'reroute' AND start_ts >= ?{seg_clause} "
        "ORDER BY start_ts DESC",
        [cutoff, *seg_params],
        db=db.analytics_db_path(),
    )
    if events_df.empty:
        return RerouteRiskResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            days=days, total_events=0, rows=[],
        )

    # MMSI -> name + risk from live_positions then vessels
    all_mmsis = list(set(int(m) for m in events_df["mmsi"].dropna().tolist()))
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
            v_df = db.query(
                f"SELECT mmsi, name, imo FROM vessels WHERE mmsi IN ({ph2})", missing
            )
            for _, r in v_df.iterrows():
                mmsi_info[int(r["mmsi"])] = {"name": r.get("name"), "imo": r.get("imo")}

    imo_risk: dict[int, dict] = {}
    _rr_imos = [_valid_imo(v.get("imo")) for v in mmsi_info.values()]
    known_imos = list(set(i for i in _rr_imos if i is not None))
    if known_imos:
        ph3 = ",".join("?" * len(known_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            f"FROM vessel_registry WHERE imo IN ({ph3}) AND fetch_ok = true",
            known_imos,
            db=db.registry_db_path(),
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
            try:
                det = _json.loads(ev["details"])
            except Exception:
                pass

        mmsi_val = int(ev["mmsi"])
        info = mmsi_info.get(mmsi_val, {})
        imo_val = _valid_imo(info.get("imo"))
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}

        rs = risk_info.get("risk_score")
        if (rs or 0) < min_risk:
            continue

        rows.append(RerouteRiskEvent(
            event_id=str(ev["event_id"]),
            start_ts=_iso(ev["start_ts"]) or "",
            region=_str_or_none(ev.get("region")),
            kind=_str_or_none(ev.get("kind")),
            segment=_str_or_none(ev.get("segment")),
            mmsi=mmsi_val,
            name=_str_or_none(info.get("name")),
            old_destination=_str_or_none(det.get("old_destination")),
            new_destination=_str_or_none(det.get("new_destination")),
            fixes_at_old=det.get("fixes_at_old"),
            risk_score=rs,
            ofac=bool(risk_info.get("ofac", False)),
        ))

    rows.sort(key=lambda r: (r.risk_score or 0), reverse=True)
    return RerouteRiskResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        days=days,
        total_events=len(events_df),
        rows=rows,
    )


@app.get("/api/vessels/{imo}/equasis")
def vessel_equasis(imo: int):
    """Equasis registry data for a vessel by IMO number.

    Reads vessel_registry.duckdb first (populated by the crawl job). Falls back to
    a live Equasis scrape when the registry has no entry or the previous fetch failed.
    The API never writes to the registry; the crawler is the sole writer.
    """
    from fastapi import HTTPException

    # Try registry first
    reg_df = db.query(
        "SELECT * FROM vessel_registry WHERE imo = ? AND fetch_ok = true",
        [imo],
        db=db.registry_db_path(),
    )
    if not reg_df.empty:
        row = reg_df.iloc[0]
        # Build response dict matching the equasis scraper output shape.
        # gross_tonnage / dwt / year_built stored as INT in the registry; return as str
        # so the frontend EquasisData interface (string fields) needs no changes.
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

    # Fall back to live scrape (result cached in-process 12h)
    data = equasis.get_ship_info(imo)
    if data is None:
        raise HTTPException(status_code=503, detail="Equasis unavailable")
    return data


@app.get("/api/chokepoints", response_model=list[ChokepointCount])
def chokepoints():
    """Per-region live vessel counts (with bbox + per-segment breakdown)."""
    df = _live("region IS NOT NULL")
    out = []
    for name, bbox in REGIONS.items():
        sub = df[df["region"] == name] if not df.empty else df
        by_seg = (
            {str(k): int(v) for k, v in sub["segment"].value_counts().items()}
            if not sub.empty
            else {}
        )
        out.append(ChokepointCount(region=name, bbox=bbox, total=len(sub), by_segment=by_seg))
    return out


@app.get("/api/meta", response_model=Meta)
def meta():
    """Distinct kinds/segments/regions, total tracked, and last update time."""
    df = _live()
    if df.empty:
        return Meta(kinds=[], segments=[], regions=[], total_tracked=0, last_update=None)
    return Meta(
        kinds=sorted(df["kind"].dropna().unique().tolist()),
        segments=sorted(df["segment"].dropna().unique().tolist()),
        regions=sorted(df["region"].dropna().unique().tolist()),
        total_tracked=int(len(df)),
        last_update=_iso(df["updated_ts"].max()),
    )


@app.get("/api/routes", response_model=RoutesResponse)
def routes():
    """Transport-arb route matrix (precomputed, static JSON fallback to live compute)."""
    return _serve_cached(_STATIC_ROUTES, run_routes_default, RoutesResponse)


@app.get("/api/dispersion", response_model=DispersionResponse)
def dispersion():
    """Freight-dispersion backtest results (precomputed, static JSON fallback)."""
    return _serve_cached(_STATIC_DISPERSION, run_dispersion_default, DispersionResponse)


@app.get("/api/dispersion/live", response_model=list[AisDispersionRow])
def dispersion_live(segment: str | None = None):
    """Live AIS fleet-dispersion series from commo.duckdb (last 2 years, long format)."""
    end = date.today()
    start = end - timedelta(days=730)
    df = load_ais_dispersion(start, end, segment=segment)
    if df.empty:
        return []
    rows = []
    for idx, row in df.iterrows():
        rows.append(
            AisDispersionRow(
                date=str(idx.date()),
                kind=str(row.get("kind", "")),
                segment=str(row.get("segment", "")),
                vessel_count=int(row.get("vessel_count", 0)),
                dispersion_nm=round(float(row.get("dispersion_nm", 0)), 2),
            )
        )
    return rows


@app.get("/api/analytics/transits", response_model=TransitsResponse)
def analytics_transits(chokepoint: str = "suez", days: int = 30):
    """Daily chokepoint transit counts grouped by direction and vessel kind."""
    d = max(1, min(days, 365))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)
    df = db.query(
        "SELECT entered_ts, direction, kind FROM transit_events "
        "WHERE chokepoint = ? AND entered_ts >= ?",
        [chokepoint, cutoff],
        db=db.analytics_db_path(),
    )
    series = []
    if not df.empty:
        df["date"] = pd.to_datetime(df["entered_ts"]).dt.date.astype(str)
        for (date_s, direction, kind), grp in df.groupby(["date", "direction", "kind"]):
            from .schemas import TransitDay
            series.append(TransitDay(date=date_s, direction=direction, kind=kind, count=len(grp)))
        series.sort(key=lambda r: r.date)
    return TransitsResponse(chokepoint=chokepoint, days=d, series=series)


@app.get("/api/analytics/transit-risk", response_model=TransitRiskResponse)
def analytics_transit_risk(chokepoint: str = "hormuz", days: int = 30, min_risk: int = 0):
    """Chokepoint transit events enriched with vessel risk scores.

    Two-database merge: analytics (transit_events) + AIS (MMSI->IMO) + registry (risk).
    Returns transits sorted by risk_score descending. Use min_risk=25 to filter noise.
    """
    days = max(1, min(90, days))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    df = db.query(
        "SELECT mmsi, chokepoint, entered_ts, exited_ts, direction, kind, segment, laden "
        "FROM transit_events WHERE chokepoint = ? AND entered_ts >= ? ORDER BY entered_ts DESC",
        [chokepoint, cutoff],
        db=db.analytics_db_path(),
    )
    if df.empty:
        return TransitRiskResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            days=days, chokepoint=chokepoint, total_transits=0, enriched=0, rows=[],
        )

    all_mmsis = list(set(int(m) for m in df["mmsi"].dropna().tolist()))
    mmsi_info: dict[int, dict] = {}
    if all_mmsis:
        ph = ",".join("?" * len(all_mmsis))
        lp_df = db.query(
            f"SELECT mmsi, name, imo FROM live_positions WHERE mmsi IN ({ph})", all_mmsis
        )
        for _, r in lp_df.iterrows():
            mmsi_info[int(r["mmsi"])] = {"name": _str_or_none(r.get("name")), "imo": r.get("imo")}
        missing = [m for m in all_mmsis if m not in mmsi_info]
        if missing:
            ph2 = ",".join("?" * len(missing))
            v_df = db.query(
                f"SELECT mmsi, name, imo FROM vessels WHERE mmsi IN ({ph2})", missing
            )
            for _, r in v_df.iterrows():
                mmsi_info[int(r["mmsi"])] = {"name": _str_or_none(r.get("name")), "imo": r.get("imo")}

    imo_risk: dict[int, dict] = {}
    known_imos = list(set(i for i in (_valid_imo(v.get("imo")) for v in mmsi_info.values()) if i))
    if known_imos:
        ph3 = ",".join("?" * len(known_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            f"FROM vessel_registry WHERE imo IN ({ph3}) AND fetch_ok = true",
            known_imos, db=db.registry_db_path(),
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
        imo_val = _valid_imo(info.get("imo"))
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}
        rs = risk_info.get("risk_score")

        if (rs or 0) < min_risk:
            continue
        if rs is not None:
            enriched += 1

        laden_val = ev.get("laden")
        laden_bool = None if laden_val is None or (isinstance(laden_val, float) and pd.isna(laden_val)) else bool(laden_val)

        rows.append(TransitRiskEvent(
            mmsi=mmsi_val,
            name=info.get("name"),
            imo=imo_val,
            chokepoint=str(ev["chokepoint"]),
            entered_ts=_iso(ev["entered_ts"]) or "",
            exited_ts=_iso(ev.get("exited_ts")) if ev.get("exited_ts") is not None else None,
            direction=_str_or_none(ev.get("direction")),
            kind=_str_or_none(ev.get("kind")),
            segment=_str_or_none(ev.get("segment")),
            laden=laden_bool,
            risk_score=rs,
            ofac=bool(risk_info.get("ofac", False)),
        ))

    rows.sort(key=lambda r: (r.risk_score or 0), reverse=True)
    return TransitRiskResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        days=days,
        chokepoint=chokepoint,
        total_transits=len(df),
        enriched=enriched,
        rows=rows,
    )


@app.get("/api/analytics/congestion", response_model=CongestionResponse)
def analytics_congestion(zone: str = "singapore_west", days: int = 30):
    """Daily anchored vessel counts and median dwell hours per anchorage zone."""
    d = max(1, min(days, 365))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)
    df = db.query(
        "SELECT start_ts, end_ts FROM anchored_episodes WHERE zone = ? AND start_ts >= ?",
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
            from .schemas import CongestionDay
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


@app.get("/api/analytics/anchorage-dwell", response_model=AnchorageDwellResponse)
def analytics_anchorage_dwell(zone: str = "singapore_west", limit: int = 50):
    """Vessels currently anchored at a zone, ranked by dwell time (longest first).

    Joins anchored_episodes (open end_ts) + vessel_state + live_positions + registry.
    Long dwell vessels are likely ready to depart - a freight market timing signal.
    """
    limit = max(5, min(200, limit))
    now = datetime.now(UTC).replace(tzinfo=None)

    # Open episodes (end_ts IS NULL) at the requested zone
    ep_df = db.query(
        "SELECT mmsi, zone, start_ts, kind, segment "
        "FROM anchored_episodes WHERE zone = ? AND end_ts IS NULL "
        "ORDER BY start_ts ASC LIMIT ?",
        [zone, limit],
        db=db.analytics_db_path(),
    )
    if ep_df.empty:
        return AnchorageDwellResponse(
            as_of=_iso(now) or "", zone=zone, rows=[]
        )

    all_mmsis = list(set(int(m) for m in ep_df["mmsi"].dropna().tolist()))
    ph = ",".join("?" * len(all_mmsis))

    # Vessel names
    mmsi_name: dict[int, str | None] = {}
    lp_df = db.query(f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({ph})", all_mmsis)
    for _, r in lp_df.iterrows():
        mmsi_name[int(r["mmsi"])] = _str_or_none(r.get("name"))
    missing = [m for m in all_mmsis if m not in mmsi_name]
    if missing:
        ph2 = ",".join("?" * len(missing))
        v_df = db.query(f"SELECT mmsi, name FROM vessels WHERE mmsi IN ({ph2})", missing)
        for _, r in v_df.iterrows():
            mmsi_name[int(r["mmsi"])] = _str_or_none(r.get("name"))

    # Laden state from vessel_state
    mmsi_laden: dict[int, str | None] = {}
    vs_df = db.query(
        f"SELECT mmsi, laden FROM vessel_state WHERE mmsi IN ({ph})", all_mmsis,
        db=db.analytics_db_path(),
    )
    for _, r in vs_df.iterrows():
        mmsi_laden[int(r["mmsi"])] = _str_or_none(r.get("laden"))

    # MMSI -> IMO from live_positions / vessels
    mmsi_imo: dict[int, int | None] = {}
    lp2 = db.query(f"SELECT mmsi, imo FROM live_positions WHERE mmsi IN ({ph})", all_mmsis)
    for _, r in lp2.iterrows():
        mmsi_imo[int(r["mmsi"])] = _valid_imo(r.get("imo"))
    for m in all_mmsis:
        if m not in mmsi_imo:
            mmsi_imo[m] = None

    known_imos = list(set(i for i in mmsi_imo.values() if i))
    imo_risk: dict[int, dict] = {}
    if known_imos:
        ph3 = ",".join("?" * len(known_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            f"FROM vessel_registry WHERE imo IN ({ph3}) AND fetch_ok = true",
            known_imos, db=db.registry_db_path(),
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac_sanctioned"]),
            }

    rows = []
    for _, ep in ep_df.iterrows():
        mmsi_val = int(ep["mmsi"])
        start = pd.Timestamp(ep["start_ts"])
        dwell_h = round((pd.Timestamp(now) - start).total_seconds() / 3600, 1)

        imo_val = mmsi_imo.get(mmsi_val)
        risk_info = imo_risk.get(imo_val, {}) if imo_val else {}
        rs = risk_info.get("risk_score")

        rows.append(AnchoredVessel(
            mmsi=mmsi_val,
            name=mmsi_name.get(mmsi_val),
            zone=str(ep["zone"]),
            kind=_str_or_none(ep.get("kind")),
            segment=_str_or_none(ep.get("segment")),
            start_ts=_iso(ep["start_ts"]) or "",
            dwell_hours=dwell_h,
            laden=mmsi_laden.get(mmsi_val),
            risk_score=rs,
            ofac=bool(risk_info.get("ofac", False)),
        ))

    rows.sort(key=lambda r: r.dwell_hours, reverse=True)
    return AnchorageDwellResponse(as_of=_iso(now) or "", zone=zone, rows=rows)


@app.get("/api/analytics/cargo-transitions", response_model=CargoTransitionsResponse)
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
        f"WHERE snapshot_ts >= ? AND draught > 0 {seg_clause} "
        "GROUP BY mmsi "
        "HAVING COUNT(*) >= 6 AND MAX(draught) - MIN(draught) >= ? "
        "ORDER BY MAX(draught) - MIN(draught) DESC "
        "LIMIT 500",
        [since] + seg_params + [min_change * 0.7],
    )
    if cand_df.empty:
        return CargoTransitionsResponse(as_of=_iso(now) or "", days=days, min_change=min_change, rows=[])

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
        return CargoTransitionsResponse(as_of=_iso(now) or "", days=days, min_change=min_change, rows=[])

    snap_df["snapshot_ts"] = pd.to_datetime(snap_df["snapshot_ts"])

    # Floor timestamps to 6h buckets (dt.floor handles datetime64[us] and datetime64[ns])
    snap_df["bucket"] = snap_df["snapshot_ts"].dt.floor("6h")

    transitions: list[dict] = []
    for mmsi_val, grp in snap_df.groupby("mmsi"):
        bucket_agg = (
            grp.groupby("bucket")
            .agg(d_median=("draught", "median"), lat=("lat", "median"), lon=("lon", "median"), fix_cnt=("draught", "count"))
            .reset_index()
        )
        bucket_agg = bucket_agg[bucket_agg["fix_cnt"] >= 2].sort_values("bucket").reset_index(drop=True)
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
        kind_val = _str_or_none(grp["kind"].mode().iloc[0]) if not grp["kind"].dropna().empty else None
        seg_val = _str_or_none(grp["segment"].mode().iloc[0]) if not grp["segment"].dropna().empty else None
        region_slice = grp[
            (grp["snapshot_ts"] >= pd.Timestamp(trans_ts))
            & (grp["snapshot_ts"] < pd.Timestamp(trans_ts) + pd.Timedelta(hours=6))
        ]
        region_val = _str_or_none(
            region_slice["region"].mode().iloc[0]
            if not region_slice.empty and not region_slice["region"].dropna().empty
            else (grp["region"].mode().iloc[0] if not grp["region"].dropna().empty else None)
        )

        transitions.append({
            "mmsi": int(mmsi_val),
            "kind": kind_val,
            "segment": seg_val,
            "region": region_val,
            "direction": "loading" if change_val > 0 else "discharging",
            "draught_before": round(float(best["prev_d"]), 1),
            "draught_after": round(float(best["d_median"]), 1),
            "change_m": round(abs(change_val), 1),
            "transition_ts": _iso(trans_ts) or "",
            "lat": round(float(best["lat"]), 4),
            "lon": round(float(best["lon"]), 4),
        })

    if not transitions:
        return CargoTransitionsResponse(as_of=_iso(now) or "", days=days, min_change=min_change, rows=[])

    transitions.sort(key=lambda t: t["change_m"], reverse=True)

    # Enrich with vessel names
    all_mmsis = [t["mmsi"] for t in transitions[:100]]
    ph2 = ",".join("?" * len(all_mmsis))

    mmsi_name: dict[int, str | None] = {}
    lp_df = db.query(f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({ph2})", all_mmsis)
    for _, r in lp_df.iterrows():
        mmsi_name[int(r["mmsi"])] = _str_or_none(r.get("name"))
    missing = [m for m in all_mmsis if m not in mmsi_name]
    if missing:
        ph3 = ",".join("?" * len(missing))
        v_df = db.query(f"SELECT mmsi, name FROM vessels WHERE mmsi IN ({ph3})", missing)
        for _, r in v_df.iterrows():
            mmsi_name[int(r["mmsi"])] = _str_or_none(r.get("name"))

    # MMSI -> IMO -> risk score
    mmsi_imo: dict[int, int | None] = {}
    lp2 = db.query(f"SELECT mmsi, imo FROM live_positions WHERE mmsi IN ({ph2})", all_mmsis)
    for _, r in lp2.iterrows():
        mmsi_imo[int(r["mmsi"])] = _valid_imo(r.get("imo"))
    for m in all_mmsis:
        if m not in mmsi_imo:
            mmsi_imo[m] = None

    known_imos = list(set(i for i in mmsi_imo.values() if i))
    imo_risk: dict[int, dict] = {}
    if known_imos:
        ph4 = ",".join("?" * len(known_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            f"FROM vessel_registry WHERE imo IN ({ph4}) AND fetch_ok = true",
            known_imos,
            db=db.registry_db_path(),
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
        rows.append(CargoTransitionEvent(
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
        ))

    return CargoTransitionsResponse(as_of=_iso(now) or "", days=days, min_change=min_change, rows=rows)


@app.get("/api/analytics/density", response_model=DensityResponse)
def analytics_density(region: str = "singapore_malacca", days: int = 30):
    """Fleet density series per region: daily laden/ballast/unknown counts by segment."""
    d = max(1, min(days, 365))
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=d)
    df = db.query(
        "SELECT ts, kind, segment, laden_count, ballast_count, unknown_count "
        "FROM fleet_density WHERE region = ? AND ts >= ? ORDER BY ts",
        [region, cutoff],
        db=db.analytics_db_path(),
    )
    series = []
    if not df.empty:
        df["date"] = pd.to_datetime(df["ts"]).dt.date.astype(str)
        agg = (
            df.groupby(["date", "kind", "segment"])[["laden_count", "ballast_count", "unknown_count"]]
            .sum()
            .reset_index()
        )
        from .schemas import DensityDay
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


@app.get("/api/analytics/region-momentum", response_model=RegionMomentumResponse)
def analytics_region_momentum(hours_back: int = 24):
    """Net change in vessel count per region vs hours_back hours ago.

    Reads fleet_density for the latest snapshot and the closest snapshot to
    hours_back hours prior. Returns per-region deltas sorted by absolute delta.
    """
    h = max(1, min(hours_back, 168))
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now_dt - timedelta(hours=h + 1)

    df = db.query(
        "SELECT ts, region, laden_count, ballast_count, unknown_count "
        "FROM fleet_density WHERE ts >= ? ORDER BY ts DESC",
        [cutoff],
        db=db.analytics_db_path(),
    )
    as_of = _iso(now_dt) or ""
    if df.empty:
        return RegionMomentumResponse(as_of=as_of, hours_back=h, rows=[])

    df["ts"] = pd.to_datetime(df["ts"])
    df["total"] = df["laden_count"] + df["ballast_count"] + df["unknown_count"]

    latest_ts = df["ts"].max()
    target_prev_ts = latest_ts - timedelta(hours=h)
    # Pick closest snapshot to target_prev_ts
    unique_ts = df["ts"].unique()
    prev_ts = unique_ts[abs(unique_ts - target_prev_ts).argmin()]

    curr = df[df["ts"] == latest_ts].groupby("region")[["laden_count", "ballast_count", "unknown_count", "total"]].sum()
    prev = df[df["ts"] == prev_ts].groupby("region")["total"].sum().rename("prev_total")

    merged = curr.join(prev, how="outer").fillna(0)
    merged.columns = ["laden_count", "ballast_count", "unknown_count", "current_total", "prev_total"]
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
    return RegionMomentumResponse(as_of=as_of, hours_back=h, rows=rows)


@app.get("/api/analytics/laden", response_model=LadenResponse)
def analytics_laden(kind: str = "tanker"):
    """Current fleet laden/ballast/unknown split by segment from vessel_state."""
    df = db.query(
        "SELECT vs.mmsi, vs.laden, lp.segment "
        "FROM vessel_state vs "
        "LEFT JOIN ( "
        "   SELECT mmsi, segment FROM live_positions "
        ") lp ON vs.mmsi = lp.mmsi "
        "WHERE lp.kind = ? OR lp.kind IS NULL",
        [kind],
        db=db.analytics_db_path(),
    )
    # vessel_state has no kind column; join with live_positions for kind filter
    # Fallback: query vessel_state alone if analytics DB has no join candidates
    if df.empty or "segment" not in df.columns or df["segment"].isna().all():
        df = db.query(
            "SELECT mmsi, laden, CAST(NULL AS VARCHAR) as segment FROM vessel_state",
            db=db.analytics_db_path(),
        )

    if df.empty:
        return LadenResponse(kind=kind, segments=[])

    df["laden"] = df["laden"].fillna("unknown").astype(str)
    df["segment"] = df["segment"].fillna("Unknown").astype(str)
    result: dict[str, dict[str, int]] = {}
    for _, row in df.iterrows():
        seg = str(row["segment"]) if row["segment"] else "Unknown"
        status = str(row["laden"])
        entry = result.setdefault(seg, {"laden": 0, "ballast": 0, "unknown": 0})
        entry[status] = entry.get(status, 0) + 1

    segments = [
        LadenSegment(segment=seg, laden=v["laden"], ballast=v["ballast"], unknown=v["unknown"])
        for seg, v in sorted(result.items())
    ]
    return LadenResponse(kind=kind, segments=segments)


@app.get("/api/analytics/zones", response_model=list[AnalyticsZone])
def analytics_zones():
    """All anchorage bboxes and chokepoint region bboxes for frontend overlay."""
    from analytics.zones import ANCHORAGE_ZONES
    from ais.regions import REGIONS

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


def _iso(ts) -> str | None:
    if ts is None:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


@app.get("/api/events", response_model=EventsResponse)
def events(
    type: str | None = None,
    days: int = 7,
    limit: int = 200,
):
    """Intelligence event feed: AIS gaps, loitering, STS candidates.

    ?type=gap|loiter|sts  - filter by event type (omit for all)
    ?days=7               - lookback window (clamped 1..30)
    ?limit=200            - max events returned (clamped 1..500)
    """
    days = max(1, min(30, days))
    limit = max(1, min(500, limit))
    _db = db.analytics_db_path()

    from datetime import UTC, datetime, timedelta as _td
    cutoff = datetime.now(UTC).replace(tzinfo=None) - _td(days=days)

    where_clauses = ["start_ts >= ?"]
    params: list = [cutoff]
    if type:
        where_clauses.append("type = ?")
        params.append(type)

    events_sql = (
        "SELECT event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon, "
        "       region, kind, segment, details "
        "FROM ais_events "
        "WHERE " + " AND ".join(where_clauses) +
        " ORDER BY start_ts DESC LIMIT ?"
    )
    params.append(limit)

    rows_df = db.query(events_sql, params, db=_db)
    if rows_df.empty:
        return EventsResponse(events=[], total=0)

    # Enrich with vessel names from live_positions (separate AIS DB query)
    all_mmsis = list(set(rows_df["mmsi"].dropna().astype(int).tolist()) |
                     set(rows_df["mmsi2"].dropna().astype(int).tolist()))
    name_map: dict[int, str] = {}
    if all_mmsis:
        placeholders = ",".join(["?"] * len(all_mmsis))
        name_df = db.query(
            f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({placeholders})",
            all_mmsis,
        )
        if not name_df.empty:
            name_map = dict(zip(name_df["mmsi"].astype(int), name_df["name"].fillna("")))

    import json as _json

    result_events = []
    for _, row in rows_df.iterrows():
        mmsi_int = int(row["mmsi"])
        import pandas as _pd
        mmsi2_val = row["mmsi2"]
        mmsi2_int = int(mmsi2_val) if mmsi2_val is not None and not _pd.isna(mmsi2_val) else None
        try:
            details_dict = _json.loads(row["details"]) if row["details"] else {}
        except (ValueError, TypeError):
            details_dict = {}
        result_events.append(
            AisEvent(
                event_id=str(row["event_id"]),
                type=str(row["type"]),
                mmsi=mmsi_int,
                mmsi2=mmsi2_int,
                start_ts=_iso(row["start_ts"]) or "",
                end_ts=_iso(row["end_ts"]) or "",
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                region=str(row["region"]) if row["region"] else None,
                kind=str(row["kind"]) if row["kind"] else None,
                segment=str(row["segment"]) if row["segment"] else None,
                details=details_dict,
                vessel_name=name_map.get(mmsi_int),
                vessel2_name=name_map.get(mmsi2_int) if mmsi2_int else None,
            )
        )

    return EventsResponse(events=result_events, total=len(result_events))


@app.get("/api/fleet", response_model=FleetResponse)
def fleet(
    q: str | None = None,
    flag: str | None = None,
    owner: str | None = None,
    class_society: str | None = None,
    pi_club: str | None = None,
    paris_mou: str | None = None,
    tokyo_mou: str | None = None,
    kind: str | None = None,
    segment: str | None = None,
    built_min: int | None = None,
    built_max: int | None = None,
    dwt_min: int | None = None,
    dwt_max: int | None = None,
    detention_min: float | None = None,
    risk_min: int | None = None,
    live_only: bool = False,
    sort: str = "ship_name",
    order: str = "asc",
    page: int = 1,
):
    """Filterable, sortable, paginated fleet registry (registry + live AIS join)."""
    return _fleet.query_fleet(
        q=q, flag=flag, owner=owner, class_society=class_society,
        pi_club=pi_club, paris_mou=paris_mou, tokyo_mou=tokyo_mou,
        kind=kind, segment=segment,
        built_min=built_min, built_max=built_max,
        dwt_min=dwt_min, dwt_max=dwt_max, detention_min=detention_min,
        risk_min=risk_min, live_only=live_only, sort=sort, order=order, page=page,
    )


@app.get("/api/fleet/facets", response_model=FleetFacets)
def fleet_facets():
    """Distinct filter values with counts for the Fleet Explorer dropdowns."""
    return _fleet.query_facets()


@app.get("/api/fleet/owner-risk", response_model=OwnerRiskResponse)
def fleet_owner_risk(min_vessels: int = 2, top_n: int = 30):
    """Owner concentration analysis: which owners control the most high-risk tonnage.

    Only includes owners with >= min_vessels (clamped 1-10) vessels in the registry.
    Returns top_n owners by avg risk score (clamped 1-100).
    """
    min_vessels = max(1, min(10, min_vessels))
    top_n = max(1, min(100, top_n))

    df = db.query(
        "SELECT owner, risk_score, flag, ofac_sanctioned "
        "FROM vessel_registry "
        "WHERE fetch_ok = true AND owner IS NOT NULL AND risk_score IS NOT NULL",
        db=db.registry_db_path(),
    )
    if df.empty:
        return OwnerRiskResponse(as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "", rows=[])

    rows = []
    for owner, grp in df.groupby("owner"):
        if len(grp) < min_vessels:
            continue
        avg_risk = float(grp["risk_score"].mean())
        max_risk = int(grp["risk_score"].max())
        high_risk = int((grp["risk_score"] >= 50).sum())
        ofac_count = int(grp["ofac_sanctioned"].fillna(False).astype(bool).sum()) if "ofac_sanctioned" in grp.columns else 0
        flags = sorted(set(grp["flag"].dropna().tolist()))[:5]
        rows.append(OwnerRiskItem(
            owner=str(owner),
            vessel_count=len(grp),
            avg_risk_score=round(avg_risk, 1),
            max_risk_score=max_risk,
            high_risk_count=high_risk,
            ofac_count=ofac_count,
            flags=flags,
        ))

    rows.sort(key=lambda r: r.avg_risk_score, reverse=True)
    return OwnerRiskResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        rows=rows[:top_n],
    )


@app.get("/api/fleet/flag-risk", response_model=FlagRiskResponse)
def fleet_flag_risk(top_n: int = 30):
    """Flag-state risk analysis: which flags concentrate the most risk tonnage.

    Groups vessel_registry by flag (fetch_ok=true, risk_score not null).
    Sorts by avg_risk_score descending. top_n clamped 5-100.
    """
    top_n = max(5, min(100, top_n))
    df = db.query(
        "SELECT flag, flag_code, risk_score, "
        "       COALESCE(ofac_sanctioned, false) AS ofac_sanctioned, "
        "       paris_mou, tokyo_mou "
        "FROM vessel_registry "
        "WHERE fetch_ok = true AND flag IS NOT NULL AND risk_score IS NOT NULL",
        db=db.registry_db_path(),
    )
    if df.empty:
        return FlagRiskResponse(as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "", rows=[])

    rows = []
    for flag, grp in df.groupby("flag"):
        avg_risk = float(grp["risk_score"].mean())
        max_risk = int(grp["risk_score"].max())
        high_risk = int((grp["risk_score"] >= 50).sum())
        ofac_count = int(grp["ofac_sanctioned"].fillna(False).astype(bool).sum()) if "ofac_sanctioned" in grp.columns else 0
        flag_code_vals = grp["flag_code"].dropna().tolist()
        flag_code = flag_code_vals[0] if flag_code_vals else None
        # Most common paris/tokyo MOU status for this flag
        paris_counts = grp["paris_mou"].dropna().value_counts()
        tokyo_counts = grp["tokyo_mou"].dropna().value_counts()
        paris_mou = paris_counts.index[0] if len(paris_counts) > 0 else None
        tokyo_mou = tokyo_counts.index[0] if len(tokyo_counts) > 0 else None
        rows.append(FlagRiskRow(
            flag=str(flag),
            flag_code=flag_code,
            vessel_count=len(grp),
            avg_risk_score=round(avg_risk, 1),
            max_risk_score=max_risk,
            high_risk_count=high_risk,
            ofac_count=ofac_count,
            paris_mou=paris_mou,
            tokyo_mou=tokyo_mou,
        ))

    rows.sort(key=lambda r: r.avg_risk_score, reverse=True)
    return FlagRiskResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        rows=rows[:top_n],
    )


@app.get("/api/fleet/kpis", response_model=FleetKPIs)
def fleet_kpis():
    """Aggregate risk intelligence KPIs for the fleet registry.

    Single-query summary: total vessels, risk coverage, OFAC count,
    high/critical risk counts, avg score among scored vessels.
    """
    df = db.query(
        "SELECT risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
        "FROM vessel_registry WHERE fetch_ok = true",
        db=db.registry_db_path(),
    )
    if df.empty:
        return FleetKPIs(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            total_registry=0, scored=0, elevated=0, high_risk=0,
            critical=0, ofac_count=0, avg_risk_score=None, pct_scored=0.0,
        )

    total = len(df)
    scored_df = df[df["risk_score"].notna()]
    scored = len(scored_df)
    elevated = int((scored_df["risk_score"] >= 25).sum()) if scored else 0
    high_risk = int((scored_df["risk_score"] >= 50).sum()) if scored else 0
    critical = int((scored_df["risk_score"] >= 75).sum()) if scored else 0
    ofac_count = int(df["ofac_sanctioned"].fillna(False).astype(bool).sum())
    avg_risk = round(float(scored_df["risk_score"].mean()), 1) if scored else None
    pct_scored = round(scored / total * 100, 1) if total else 0.0

    return FleetKPIs(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        total_registry=total,
        scored=scored,
        elevated=elevated,
        high_risk=high_risk,
        critical=critical,
        ofac_count=ofac_count,
        avg_risk_score=avg_risk,
        pct_scored=pct_scored,
    )


@app.get("/api/fleet/age", response_model=FleetAgeResponse)
def fleet_age():
    """Fleet age distribution by 5-year bands from vessel registry.

    Includes avg risk score and high-risk count per band. Shows how vessel age
    correlates with risk profile across the registry.
    """
    ref_year = datetime.now(UTC).year
    df = db.query(
        "SELECT year_built, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned, dwt "
        "FROM vessel_registry WHERE fetch_ok = true AND year_built IS NOT NULL",
        db=db.registry_db_path(),
    )
    if df.empty:
        return FleetAgeResponse(
            as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            reference_year=ref_year,
            bands=[],
        )

    df["age"] = ref_year - df["year_built"].astype(int)
    df["band"] = pd.cut(
        df["age"],
        bins=[0, 5, 10, 15, 20, 25, 200],
        labels=["0-4", "5-9", "10-14", "15-19", "20-24", "25+"],
        right=False,
    )

    bands = []
    for band_label in ["0-4", "5-9", "10-14", "15-19", "20-24", "25+"]:
        grp = df[df["band"] == band_label]
        if grp.empty:
            continue
        scored = grp[grp["risk_score"].notna()]
        avg_risk = round(float(scored["risk_score"].mean()), 1) if not scored.empty else None
        high_risk = int((scored["risk_score"] >= 50).sum()) if not scored.empty else 0
        dwt_vals = grp["dwt"].dropna()
        avg_dwt = round(float(dwt_vals.mean()), 0) if not dwt_vals.empty else None
        bands.append(FleetAgeBand(
            age_band=band_label,
            vessel_count=len(grp),
            avg_risk_score=avg_risk,
            high_risk_count=high_risk,
            avg_dwt=avg_dwt,
        ))

    return FleetAgeResponse(
        as_of=_iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        reference_year=ref_year,
        bands=bands,
    )


@app.get("/api/analytics/slow-steamers", response_model=SlowSteamersResponse)
def analytics_slow_steamers(kind: str = "", limit: int = 50):
    """Vessels currently underway at less than 60% of their segment's median SOG.

    Slow steaming is a freight market signal: vessels reduce speed when cargo demand
    falls (fuel savings > waiting-for-cargo cost). Anchored and moored vessels are
    excluded. Segment medians are computed from the live fleet.
    """
    limit = max(5, min(200, limit))
    now = datetime.now(UTC).replace(tzinfo=None)

    # All underway vessels with reliable SOG (nav_status 0 = underway engine; exclude anchored=1, moored=5)
    lp_df = db.query(
        "SELECT mmsi, name, kind, segment, region, sog, imo "
        "FROM live_positions "
        "WHERE sog IS NOT NULL AND sog > 0.5 AND sog < 25.0 "
        "  AND (nav_status IS NULL OR nav_status NOT IN (1, 5))"
    )

    if kind:
        lp_df = lp_df[lp_df["kind"] == kind]

    if lp_df.empty:
        return SlowSteamersResponse(as_of=_iso(now) or "", total_fleet_underway=0, rows=[])

    total_underway = len(lp_df)

    # Segment medians from vessels actually underway (sog >= 2 kn)
    underway_mask = lp_df["sog"] >= 2.0
    seg_medians: dict[str, float] = {}
    for seg, grp in lp_df[underway_mask].groupby("segment"):
        if len(grp) >= 5:  # require at least 5 vessels for a reliable median
            seg_medians[str(seg)] = float(grp["sog"].median())

    if not seg_medians:
        return SlowSteamersResponse(as_of=_iso(now) or "", total_fleet_underway=total_underway, rows=[])

    # Find vessels at < 60% of their segment median
    candidates: list[dict] = []
    for _, v in lp_df.iterrows():
        seg = _str_or_none(v.get("segment"))
        if not seg:
            continue
        median_sog = seg_medians.get(seg)
        if not median_sog or median_sog < 1.0:
            continue
        ratio = float(v["sog"]) / median_sog
        if ratio >= 0.6:
            continue
        candidates.append({
            "mmsi": int(v["mmsi"]),
            "name": _str_or_none(v.get("name")),
            "kind": _str_or_none(v.get("kind")),
            "segment": seg,
            "region": _str_or_none(v.get("region")),
            "sog": round(float(v["sog"]), 1),
            "segment_median_sog": round(median_sog, 1),
            "pct_of_median": round(ratio * 100, 1),
            "imo": _valid_imo(v.get("imo")),
        })

    candidates.sort(key=lambda c: c["pct_of_median"])
    candidates = candidates[:limit]

    if not candidates:
        return SlowSteamersResponse(as_of=_iso(now) or "", total_fleet_underway=total_underway, rows=[])

    # Enrich with risk scores
    all_imos = list(set(c["imo"] for c in candidates if c["imo"]))
    imo_risk: dict[int, dict] = {}
    if all_imos:
        ph = ",".join("?" * len(all_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
            f"FROM vessel_registry WHERE imo IN ({ph}) AND fetch_ok = true",
            all_imos,
            db=db.registry_db_path(),
        )
        for _, r in reg_df.iterrows():
            imo_risk[int(r["imo"])] = {
                "risk_score": int(r["risk_score"]) if r["risk_score"] is not None else None,
                "ofac": bool(r["ofac_sanctioned"]),
            }

    rows = []
    for c in candidates:
        risk_info = imo_risk.get(c["imo"], {}) if c["imo"] else {}
        rows.append(SlowSteamerEvent(
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
        ))

    return SlowSteamersResponse(as_of=_iso(now) or "", total_fleet_underway=total_underway, rows=rows)


@app.get("/api/analytics/market-summary", response_model=MarketSummaryResponse)
def analytics_market_summary():
    """Current market state: fleet laden/ballast split, 24h event counts, per-segment breakdown.

    Three-DB fan-out: analytics (vessel_state + event counts + transits),
    AIS (live_positions for segment/underway classification).
    """
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    since_24h = now_ts - timedelta(hours=24)

    # Event counts from analytics DB
    ev_df = db.query(
        "SELECT type, COUNT(*) AS cnt FROM ais_events WHERE start_ts >= ? GROUP BY type",
        [since_24h],
        db=db.analytics_db_path(),
    )
    ev_counts: dict[str, int] = {}
    if not ev_df.empty:
        for _, r in ev_df.iterrows():
            ev_counts[str(r["type"])] = int(r["cnt"])

    tr_df = db.query(
        "SELECT COUNT(*) AS cnt FROM transit_events WHERE entered_ts >= ?",
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

    # Live fleet from AIS DB (segment/kind/nav_status)
    lp_df = db.query(
        "SELECT mmsi, segment, kind, nav_status, sog FROM live_positions WHERE segment != 'Small'",
    )

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
            seg_mmsis = set(int(m) for m in grp["mmsi"])
            seg_laden = len(seg_mmsis & laden_mmsi)
            seg_ballast = len(seg_mmsis & ballast_mmsi)
            seg_unknown = seg_total - seg_laden - seg_ballast

            nav_grp = grp["nav_status"]
            sog_grp = grp["sog"]
            underway = int(((nav_grp == 0) | (pd.to_numeric(sog_grp, errors="coerce") > 2.0)).sum())

            seg_rows.append(MarketSegmentSummary(
                segment=str(segment),
                kind=str(kind),
                total=seg_total,
                laden=seg_laden,
                ballast=seg_ballast,
                unknown=seg_unknown,
                laden_pct=round(seg_laden / max(seg_laden + seg_ballast, 1) * 100, 1),
                underway_pct=round(underway / max(seg_total, 1) * 100, 1),
            ))

        seg_rows.sort(key=lambda r: r.total, reverse=True)

    return MarketSummaryResponse(
        as_of=_iso(now_ts) or "",
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


@app.get("/api/analytics/fleet-utilization", response_model=FleetUtilizationResponse)
def analytics_fleet_utilization():
    """Fleet utilization by segment: % underway vs idle across the live fleet.

    Underway = nav_status 0 (or unknown) AND sog > 2 kn.
    Idle = sog < 0.5 kn OR nav_status in (1=anchored, 5=moored).
    Unknown = everything else (slow but not confirmed idle).
    Excludes 'Small' segment (too noisy for freight signals).
    Sorted by underway_pct ascending (most-idle segments first).
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    lp_df = db.query(
        "SELECT segment, kind, sog, nav_status "
        "FROM live_positions "
        "WHERE segment IS NOT NULL AND segment != 'Small'"
    )

    if lp_df.empty:
        return FleetUtilizationResponse(as_of=_iso(now) or "", total_fleet=0, rows=[])

    def classify(row) -> str:
        sog = row.get("sog")
        ns = row.get("nav_status")
        # Anchored or moored: explicit nav status wins
        if (ns is not None and not pd.isna(ns) and int(ns) in (1, 5)):
            return "idle"
        sog_f = float(sog) if sog is not None and not pd.isna(sog) else None
        if sog_f is None:
            return "unknown"
        if sog_f > 2.0:
            return "underway"
        if sog_f < 0.5:
            return "idle"
        return "unknown"

    lp_df["status"] = lp_df.apply(classify, axis=1)
    lp_df["sog_num"] = pd.to_numeric(lp_df["sog"], errors="coerce")

    rows_out: list[FleetUtilizationRow] = []
    for (segment, kind), grp in lp_df.groupby(["segment", "kind"]):
        total = len(grp)
        underway = int((grp["status"] == "underway").sum())
        idle = int((grp["status"] == "idle").sum())
        unknown = int((grp["status"] == "unknown").sum())
        underway_sog = grp[grp["status"] == "underway"]["sog_num"]
        avg_sog = round(float(underway_sog.mean()), 1) if not underway_sog.empty else None
        rows_out.append(FleetUtilizationRow(
            segment=str(segment),
            kind=str(kind),
            total=total,
            underway_count=underway,
            idle_count=idle,
            unknown_count=unknown,
            underway_pct=round(100.0 * underway / total, 1) if total > 0 else 0.0,
            idle_pct=round(100.0 * idle / total, 1) if total > 0 else 0.0,
            avg_sog_underway=avg_sog,
        ))

    rows_out.sort(key=lambda r: r.underway_pct)  # most idle first
    total_fleet = len(lp_df)
    return FleetUtilizationResponse(as_of=_iso(now) or "", total_fleet=total_fleet, rows=rows_out)


@app.get("/api/analytics/destination-flows", response_model=DestinationFlowsResponse)
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
                as_of=_iso(now_ts) or "", laden_only=laden_only, total_laden=0, rows=[]
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

    params.append(top_n)
    flow_df = db.query(
        "SELECT region AS origin_region, destination, segment, kind, COUNT(*) AS vessel_count "
        "FROM live_positions "
        f"WHERE {' AND '.join(conds)} "
        "GROUP BY region, destination, segment, kind "
        "ORDER BY vessel_count DESC LIMIT ?",
        params,
    )

    if flow_df.empty:
        return DestinationFlowsResponse(
            as_of=_iso(now_ts) or "", laden_only=laden_only, total_laden=total_laden, rows=[]
        )

    rows_out = [
        DestinationFlowRow(
            origin_region=_str_or_none(r.get("origin_region")) or "unknown",
            destination=str(r["destination"]).strip(),
            segment=_str_or_none(r.get("segment")),
            kind=_str_or_none(r.get("kind")),
            vessel_count=int(r["vessel_count"]),
        )
        for _, r in flow_df.iterrows()
    ]

    return DestinationFlowsResponse(
        as_of=_iso(now_ts) or "",
        laden_only=laden_only,
        total_laden=total_laden,
        rows=rows_out,
    )


@app.get("/api/analytics/risk-events", response_model=RiskEventsResponse)
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
    reg_df = db.query(
        "SELECT imo, risk_score, COALESCE(ofac_sanctioned, false) AS ofac "
        "FROM vessel_registry WHERE risk_score >= ? AND fetch_ok = true",
        [min_risk],
        db=db.registry_db_path(),
    )
    if reg_df.empty:
        return RiskEventsResponse(
            as_of=_iso(now_ts) or "", min_risk=min_risk, days=days,
            total_high_risk_vessels=0, rows=[],
        )

    imo_risk: dict[int, dict] = {}
    for _, r in reg_df.iterrows():
        imo_risk[int(r["imo"])] = {"risk_score": int(r["risk_score"]), "ofac": bool(r["ofac"])}

    known_imos = [int(i) for i in imo_risk.keys()]
    ph_imos = ",".join("?" * len(known_imos))

    # Step 2: IMO -> MMSI + name via live_positions
    lp_df = db.query(
        f"SELECT mmsi, imo, name FROM live_positions WHERE imo IN ({ph_imos})",
        known_imos,
    )
    mmsi_imo: dict[int, int] = {}
    mmsi_name: dict[int, str | None] = {}
    for _, r in lp_df.iterrows():
        imo_val = _valid_imo(r.get("imo"))
        if imo_val:
            m = int(r["mmsi"])
            mmsi_imo[m] = imo_val
            mmsi_name[m] = _str_or_none(r.get("name"))

    all_mmsis = list(mmsi_imo.keys())
    if not all_mmsis:
        return RiskEventsResponse(
            as_of=_iso(now_ts) or "", min_risk=min_risk, days=days,
            total_high_risk_vessels=len(known_imos), rows=[],
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
            as_of=_iso(now_ts) or "", min_risk=min_risk, days=days,
            total_high_risk_vessels=len(known_imos), rows=[],
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
            mmsi_name.setdefault(m_val, _str_or_none(r.get("name")))
            if m_val not in mmsi_imo:
                imo_val = _valid_imo(r.get("imo"))
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
            try:
                det = _json.loads(ev["details"])
            except Exception:
                pass

        lat_val: float | None = None
        lon_val: float | None = None
        if ev.get("lat") is not None and not pd.isna(ev.get("lat")):
            lat_val = round(float(ev["lat"]), 5)
        if ev.get("lon") is not None and not pd.isna(ev.get("lon")):
            lon_val = round(float(ev["lon"]), 5)

        risk_rows.append(RiskEventItem(
            event_id=str(ev["event_id"]),
            event_type=str(ev["type"]),
            event_ts=_iso(ev["start_ts"]) or "",
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
            region=_str_or_none(ev.get("region")),
            kind=_str_or_none(ev.get("kind")),
            segment=_str_or_none(ev.get("segment")),
            lat=lat_val,
            lon=lon_val,
            old_destination=_str_or_none(det.get("old_destination")) if isinstance(det, dict) else None,
            new_destination=_str_or_none(det.get("new_destination")) if isinstance(det, dict) else None,
        ))

    risk_rows.sort(key=lambda r: (-r.max_risk, r.event_ts))
    return RiskEventsResponse(
        as_of=_iso(now_ts) or "",
        min_risk=min_risk,
        days=days,
        total_high_risk_vessels=len(known_imos),
        rows=risk_rows[:limit],
    )


@app.get("/api/analytics/port-congestion", response_model=PortCongestionResponse)
def analytics_port_congestion(kind: str = "", days: int = 14):
    """Port and anchorage congestion monitor.

    Compares current anchored vessel counts against a historical baseline derived
    from completed episodes in the last `days` days. Returns a congestion_factor
    (current / baseline) per zone, sorted most congested first.

    Zones with no historical baseline still appear if vessels are currently anchored:
    they get congestion_factor=1.0 (no comparison available).
    """
    days = max(3, min(90, days))
    now_ts = datetime.now(UTC).replace(tzinfo=None)
    since = now_ts - timedelta(days=days)

    # Step 1: Current open anchored episodes (end_ts IS NULL)
    kind_cond = " AND kind = ?" if kind else ""
    kind_params = [kind] if kind else []

    # Cannot JOIN across DuckDB files - fetch open episodes then look up region separately
    open_df = db.query(
        f"SELECT mmsi, zone, start_ts, kind, segment "
        f"FROM anchored_episodes "
        f"WHERE end_ts IS NULL{kind_cond}",
        kind_params,
        db=db.analytics_db_path(),
    )

    # Enrich with region from live_positions (separate DB)
    open_df["region"] = None
    if not open_df.empty:
        open_mmsis = [int(m) for m in open_df["mmsi"].unique()]
        ph_open = ",".join("?" * len(open_mmsis))
        region_df = db.query(
            f"SELECT mmsi, region FROM live_positions WHERE mmsi IN ({ph_open})",
            open_mmsis,
        )
        if not region_df.empty:
            region_map = {int(r["mmsi"]): _str_or_none(r.get("region")) for _, r in region_df.iterrows()}
            open_df["region"] = open_df["mmsi"].apply(lambda m: region_map.get(int(m)))

    # Step 2: Completed historical episodes for baseline
    hist_df = db.query(
        f"SELECT zone, kind, start_ts, end_ts, "
        f"       DATEDIFF('hour', start_ts, end_ts) AS dwell_hours "
        f"FROM anchored_episodes "
        f"WHERE end_ts IS NOT NULL AND start_ts >= ?{kind_cond}",
        [since] + kind_params,
        db=db.analytics_db_path(),
    )

    now_pd = pd.Timestamp(now_ts)

    # Build current state: zone -> {vessels, avg_dwell}
    zone_current: dict[str, dict] = {}
    if not open_df.empty:
        open_df["dwell_hours_so_far"] = (
            (now_pd - pd.to_datetime(open_df["start_ts"])).dt.total_seconds() / 3600
        )
        for zone_key, grp in open_df.groupby("zone"):
            zone_current[str(zone_key)] = {
                "current_vessels": len(grp),
                "avg_current_dwell_hours": round(float(grp["dwell_hours_so_far"].mean()), 1),
                "region": _str_or_none(grp["region"].iloc[0]) if not grp["region"].isnull().all() else None,
                "kind": _str_or_none(grp["kind"].iloc[0]),
            }

    # Build historical baseline: zone -> avg_count per snapshot, avg_dwell
    zone_baseline: dict[str, dict] = {}
    if not hist_df.empty:
        hist_df["dwell_hours"] = pd.to_numeric(hist_df["dwell_hours"], errors="coerce")
        for zone_key, grp in hist_df.groupby("zone"):
            valid_dwell = grp["dwell_hours"].dropna()
            # Estimate concurrent vessel count: sum(dwell_hours) / observation_window_hours
            obs_hours = max((now_ts - since).total_seconds() / 3600, 1)
            avg_concurrent = float(valid_dwell.sum()) / obs_hours
            zone_baseline[str(zone_key)] = {
                "baseline_avg_vessels": round(avg_concurrent, 2),
                "baseline_avg_dwell_hours": round(float(valid_dwell.mean()), 1) if len(valid_dwell) else None,
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

        rows_out.append(PortCongestionRow(
            zone=z,
            region=cur.get("region"),
            kind=cur.get("kind"),
            current_vessels=cv,
            avg_current_dwell_hours=cur.get("avg_current_dwell_hours"),
            baseline_avg_vessels=bav,
            baseline_avg_dwell_hours=bas.get("baseline_avg_dwell_hours"),
            congestion_factor=factor,
        ))

    rows_out.sort(key=lambda r: (-r.congestion_factor, -r.current_vessels))
    return PortCongestionResponse(
        as_of=_iso(now_ts) or "",
        days_baseline=days,
        rows=rows_out,
    )


_DEST_REGION_MAP: dict[str, str] = {
    # Far East
    "CN": "Far East", "HK": "Far East", "TW": "Far East",
    "KR": "Far East", "JP": "Far East",
    # Southeast Asia
    "SG": "SE Asia", "MY": "SE Asia", "TH": "SE Asia",
    "ID": "SE Asia", "PH": "SE Asia", "VN": "SE Asia",
    # South Asia
    "IN": "South Asia", "PK": "South Asia", "LK": "South Asia", "BD": "South Asia",
    # Middle East
    "AE": "Middle East", "SA": "Middle East", "KW": "Middle East",
    "IQ": "Middle East", "IR": "Middle East", "QA": "Middle East",
    "OM": "Middle East", "BH": "Middle East", "YE": "Middle East",
    # Europe (NW)
    "NL": "NW Europe", "BE": "NW Europe", "GB": "NW Europe",
    "FR": "NW Europe", "DE": "NW Europe", "DK": "NW Europe",
    "NO": "NW Europe", "SE": "NW Europe", "FI": "NW Europe",
    "PL": "NW Europe", "LV": "NW Europe", "LT": "NW Europe",
    "EE": "NW Europe", "IE": "NW Europe",
    # Mediterranean
    "ES": "Med", "IT": "Med", "PT": "Med", "GR": "Med",
    "TR": "Med", "EG": "Med", "LY": "Med", "TN": "Med",
    "MA": "Med", "DZ": "Med", "MT": "Med", "HR": "Med",
    # Americas
    "US": "Americas", "MX": "Americas", "PA": "Americas",
    "CA": "Americas", "CO": "Americas", "VE": "Americas",
    "BR": "Americas", "AR": "Americas", "CL": "Americas",
    "PE": "Americas", "EC": "Americas", "TT": "Americas",
    # West Africa
    "NG": "W Africa", "AO": "W Africa", "CI": "W Africa",
    "GH": "W Africa", "CM": "W Africa", "SN": "W Africa",
    "TG": "W Africa", "CD": "W Africa", "GA": "W Africa",
    # East Africa / Indian Ocean
    "TZ": "E Africa", "KE": "E Africa", "MZ": "E Africa",
    "MU": "E Africa", "ZA": "S Africa",
    # Australia / Pacific
    "AU": "Oceania", "NZ": "Oceania",
    # Baltic / Black Sea
    "RU": "Russia/CIS", "UA": "Russia/CIS", "KZ": "Russia/CIS",
    "BY": "Russia/CIS",
}


def _dest_to_region(dest: str | None) -> str:
    """Map a 5-char UNLOCODE destination to a macro-region using the first 2 chars."""
    if not dest or len(dest) < 2:
        return "Unknown"
    return _DEST_REGION_MAP.get(dest[:2].upper(), "Unknown")


_HIGH_RISK_REGIONS = frozenset({
    "hormuz", "persian_gulf", "west_africa", "somalia",
    "red_sea", "bab_el_mandeb", "gulf_of_aden",
})


@app.get("/api/analytics/sts-proximity", response_model=StsProximityResponse)
def analytics_sts_proximity(max_dist_m: float = 2000, max_sog: float = 3.0):
    """Live pairs of vessels within max_dist_m metres of each other at sog <= max_sog.

    Excludes anchored (nav_status=1) and moored (nav_status=5) vessels. Uses
    vectorized haversine over live_positions; returns up to 100 closest pairs.
    Pairs in high-risk regions (Hormuz, Red Sea, W Africa, etc.) are flagged.
    """
    import numpy as np

    d_m = max(200.0, min(max_dist_m, 10000.0))
    sog_cap = max(0.5, min(max_sog, 8.0))

    df = db.query(
        "SELECT mmsi, name, imo, lat, lon, sog, kind, segment, region, nav_status "
        "FROM live_positions "
        "WHERE sog IS NOT NULL AND sog <= ? "
        "  AND (nav_status IS NULL OR nav_status NOT IN (1, 5)) "
        "  AND lat IS NOT NULL AND lon IS NOT NULL",
        [sog_cap],
    )
    now = datetime.now(UTC)
    if df.empty or len(df) < 2:
        return StsProximityResponse(
            as_of=_iso(now) or "",
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
    a = np.sin(dlat / 2) ** 2 + np.cos(lats[:, None]) * np.cos(lats[None, :]) * np.sin(dlon / 2) ** 2
    dists = 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    mask = np.triu(dists < d_m, k=1)
    ii, jj = np.where(mask)

    pairs: list[StsProximityPair] = []
    for i_idx, j_idx in zip(ii.tolist(), jj.tolist()):
        a_row = df.iloc[i_idx]
        b_row = df.iloc[j_idx]
        region = _str_or_none(a_row["region"]) or _str_or_none(b_row["region"])
        pairs.append(
            StsProximityPair(
                mmsi_a=int(a_row["mmsi"]),
                name_a=_str_or_none(a_row["name"]),
                imo_a=_valid_imo(a_row["imo"]),
                kind_a=_str_or_none(a_row["kind"]),
                segment_a=_str_or_none(a_row["segment"]),
                sog_a=round(float(a_row["sog"]), 1),
                mmsi_b=int(b_row["mmsi"]),
                name_b=_str_or_none(b_row["name"]),
                imo_b=_valid_imo(b_row["imo"]),
                kind_b=_str_or_none(b_row["kind"]),
                segment_b=_str_or_none(b_row["segment"]),
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
        as_of=_iso(now) or "",
        max_dist_m=d_m,
        max_sog=sog_cap,
        total_pairs=len(pairs),
        pairs=pairs[:100],
    )


@app.get("/api/analytics/anomaly-watchlist", response_model=AnomalyWatchlistResponse)
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
        "SELECT mmsi, type, COUNT(*) AS cnt FROM ais_events WHERE start_ts >= ? GROUP BY mmsi, type",
        [cutoff_30d],
        db=adb,
    )
    ev_7d = db.query(
        "SELECT mmsi, type, COUNT(*) AS cnt FROM ais_events WHERE start_ts >= ? GROUP BY mmsi, type",
        [cutoff_7d],
        db=adb,
    )

    # sts can be either party
    sts_30d_df = db.query(
        "SELECT mmsi2 AS mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND mmsi2 IS NOT NULL AND start_ts >= ? GROUP BY mmsi2",
        [cutoff_30d],
        db=adb,
    )
    sts_7d_df = db.query(
        "SELECT mmsi2 AS mmsi, COUNT(*) AS cnt FROM ais_events "
        "WHERE type = 'sts' AND mmsi2 IS NOT NULL AND start_ts >= ? GROUP BY mmsi2",
        [cutoff_7d],
        db=adb,
    )

    def _build_counts(df, mmsi2_df) -> tuple[dict[int, int], dict[int, int]]:
        sts: dict[int, int] = {}
        rr: dict[int, int] = {}
        if not df.empty:
            for _, r in df.iterrows():
                m = int(r["mmsi"]); t = str(r["type"]); c = int(r["cnt"])
                if t == "sts": sts[m] = sts.get(m, 0) + c
                elif t == "reroute": rr[m] = rr.get(m, 0) + c
        if not mmsi2_df.empty:
            for _, r in mmsi2_df.iterrows():
                m = int(r["mmsi"]); c = int(r["cnt"])
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
            as_of=_iso(now_ts) or "",
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
    live_imos = [_valid_imo(r.get("imo")) for _, r in lp_df.iterrows() if _valid_imo(r.get("imo")) is not None]
    reg_map: dict[int, dict] = {}
    if live_imos:
        ph = ",".join(["?"] * len(live_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, ofac_sanctioned FROM vessel_registry "
            f"WHERE imo IN ({ph}) AND fetch_ok = true",
            live_imos,
            db=db.registry_db_path(),
        )
        if not reg_df.empty:
            for _, r in reg_df.iterrows():
                imo_v = _valid_imo(r.get("imo"))
                if imo_v:
                    reg_map[imo_v] = {
                        "risk_score": int(r["risk_score"]) if r.get("risk_score") is not None and not pd.isna(r["risk_score"]) else None,
                        "ofac": bool(r["ofac_sanctioned"]) if r.get("ofac_sanctioned") is not None and not pd.isna(r["ofac_sanctioned"]) else False,
                    }

    # Step 5: score and filter
    rows_out: list[AnomalyWatchlistItem] = []
    for _, row in lp_df.iterrows():
        mmsi = int(row["mmsi"])
        imo = _valid_imo(row.get("imo"))
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
        region = _str_or_none(row.get("region"))
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
        lat_f = float(row["lat"]) if row.get("lat") is not None and not pd.isna(row["lat"]) else None
        lon_f = float(row["lon"]) if row.get("lon") is not None and not pd.isna(row["lon"]) else None

        rows_out.append(AnomalyWatchlistItem(
            mmsi=mmsi,
            imo=imo,
            name=_str_or_none(row.get("name")),
            kind=_str_or_none(row.get("kind")),
            segment=_str_or_none(row.get("segment")),
            region=region,
            lat=lat_f,
            lon=lon_f,
            sog=sog_f,
            destination=_str_or_none(row.get("destination")),
            laden=laden_val,
            total_score=total,
            behavioral_score=behavioral,
            registry_risk=reg_risk,
            ofac=ofac,
            risk_level=risk_level,
            sts_count_7d=sts_7d_c,
            reroute_count_7d=rr_7d_c,
            signals=signals,
        ))

    rows_out.sort(key=lambda r: (-r.total_score, -r.behavioral_score))
    total_flagged = len(rows_out)

    return AnomalyWatchlistResponse(
        as_of=_iso(now_ts) or "",
        min_score=min_score,
        total_flagged=total_flagged,
        rows=rows_out[:limit],
    )


@app.get("/api/analytics/trade-lane-matrix", response_model=TradeLaneMatrixResponse)
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

    lp_df = db.query(
        "SELECT mmsi, imo, region, destination FROM live_positions WHERE " +
        " AND ".join(lp_conds),
        lp_params or None,
    )
    if lp_df.empty:
        return TradeLaneMatrixResponse(
            as_of=_iso(now_ts) or "",
            kind=kind or "",
            laden_only=laden_only,
            origin_regions=[],
            dest_regions=[],
            cells=[],
        )

    fleet_mmsis = {int(r["mmsi"]) for _, r in lp_df.iterrows()}

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
    live_imos = [_valid_imo(r.get("imo")) for _, r in lp_df.iterrows() if _valid_imo(r.get("imo")) is not None]
    reg_map: dict[int, dict] = {}  # imo -> {risk_score, ofac}
    if live_imos:
        placeholders = ",".join(["?"] * len(live_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, ofac_sanctioned FROM vessel_registry "
            f"WHERE imo IN ({placeholders}) AND fetch_ok = true",
            live_imos,
            db=db.registry_db_path(),
        )
        if not reg_df.empty:
            for _, r in reg_df.iterrows():
                imo_v = _valid_imo(r.get("imo"))
                if imo_v:
                    reg_map[imo_v] = {
                        "risk_score": int(r["risk_score"]) if r.get("risk_score") is not None and not pd.isna(r["risk_score"]) else None,
                        "ofac": bool(r["ofac_sanctioned"]) if r.get("ofac_sanctioned") is not None and not pd.isna(r["ofac_sanctioned"]) else False,
                    }

    # Step 4: aggregate cells
    from collections import defaultdict
    cell_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"total": 0, "high_risk": 0, "laden": 0})
    origin_totals: dict[str, int] = defaultdict(int)
    dest_totals: dict[str, int] = defaultdict(int)

    for _, row in lp_df.iterrows():
        mmsi = int(row["mmsi"])
        origin = str(row["region"]) if row.get("region") else "Unknown"
        dest_region = _dest_to_region(_str_or_none(row.get("destination")))

        if laden_only and mmsi not in laden_mmsi:
            continue

        # Is this a high-risk vessel?
        imo = _valid_imo(row.get("imo"))
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
            as_of=_iso(now_ts) or "",
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
        cells.append(TradeLaneCell(
            origin_region=orig,
            dest_region=dest,
            vessel_count=counts["total"],
            high_risk_count=counts["high_risk"],
            laden_count=counts["laden"],
        ))

    return TradeLaneMatrixResponse(
        as_of=_iso(now_ts) or "",
        kind=kind or "",
        laden_only=laden_only,
        origin_regions=origin_regions,
        dest_regions=dest_regions,
        cells=cells,
    )


@app.get("/api/analytics/chokepoint-heatmap", response_model=ChokepointHeatmapResponse)
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

    where_clauses = ["entered_ts >= ?"]
    params: list = [cutoff]
    if kind:
        where_clauses.append("kind = ?")
        params.append(kind)

    sql = (
        "SELECT strftime(entered_ts, '%Y-%m-%d') AS dt, chokepoint, kind, COUNT(*) AS cnt "
        "FROM transit_events "
        "WHERE " + " AND ".join(where_clauses) +
        " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
    )
    df = db.query(sql, params, db=db.analytics_db_path())

    if df.empty:
        return ChokepointHeatmapResponse(
            as_of=_iso(now_ts) or "",
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
        cells.append(ChokepointHeatmapCell(
            date=dt,
            chokepoint=cp,
            total=counts["tanker"] + counts["bulk"],
            tanker=counts["tanker"],
            bulk=counts["bulk"],
        ))

    return ChokepointHeatmapResponse(
        as_of=_iso(now_ts) or "",
        days=days,
        kind=kind or "",
        chokepoints=chokepoints_ordered,
        cells=cells,
    )


@app.get("/api/analytics/vessel-risk-scores", response_model=VesselRiskResponse)
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

    # Step 2: live positions (segment/kind filter applied here)
    lp_conds = ["segment != 'Small'"]
    lp_params: list = []
    if segment:
        lp_conds.append("segment = ?")
        lp_params.append(segment)
    if kind:
        lp_conds.append("kind = ?")
        lp_params.append(kind)
    lp_df = db.query(
        "SELECT mmsi, imo, name, kind, segment, region, lat, lon "
        "FROM live_positions WHERE " + " AND ".join(lp_conds),
        lp_params or None,
    )
    if lp_df.empty:
        return VesselRiskResponse(
            as_of=_iso(now_ts) or "",
            days=days,
            top_n=top_n,
            total_candidates=0,
            rows=[],
        )

    # mmsi -> live fields map
    lp_map: dict[int, dict] = {}
    for _, r in lp_df.iterrows():
        lp_map[int(r["mmsi"])] = {
            "imo": _valid_imo(r.get("imo")),
            "name": _str_or_none(r.get("name")),
            "kind": _str_or_none(r.get("kind")),
            "segment": _str_or_none(r.get("segment")),
            "region": _str_or_none(r.get("region")),
            "lat": float(r["lat"]) if r.get("lat") is not None and not pd.isna(r["lat"]) else None,
            "lon": float(r["lon"]) if r.get("lon") is not None and not pd.isna(r["lon"]) else None,
        }

    # Step 3: registry risk data via IMO
    live_imos = [v["imo"] for v in lp_map.values() if v["imo"] is not None]
    reg_map: dict[int, dict] = {}  # keyed by imo
    if live_imos:
        placeholders = ",".join(["?"] * len(live_imos))
        reg_df = db.query(
            f"SELECT imo, risk_score, ofac_sanctioned FROM vessel_registry "
            f"WHERE imo IN ({placeholders}) AND fetch_ok = true",
            live_imos,
            db=db.registry_db_path(),
        )
        if not reg_df.empty:
            for _, r in reg_df.iterrows():
                imo_val = _valid_imo(r.get("imo"))
                if imo_val is not None:
                    reg_map[imo_val] = {
                        "risk_score": int(r["risk_score"]) if r.get("risk_score") is not None and not pd.isna(r["risk_score"]) else None,
                        "ofac": bool(r["ofac_sanctioned"]) if r.get("ofac_sanctioned") is not None and not pd.isna(r["ofac_sanctioned"]) else False,
                    }

    # Step 4: candidate MMSIs = vessels with behavioral events OR registry risk > 0
    behavioral_mmsis = set(sts_counts) | set(reroute_counts)
    reg_imo_to_mmsi: dict[int, int] = {v["imo"]: k for k, v in lp_map.items() if v["imo"] is not None}
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

        rows_out.append(VesselRiskRow(
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
        ))

    rows_out.sort(key=lambda r: (-r.total_score, -r.behavioral_score))
    total_candidates = len(rows_out)

    return VesselRiskResponse(
        as_of=_iso(now_ts) or "",
        days=days,
        top_n=top_n,
        total_candidates=total_candidates,
        rows=rows_out[:top_n],
    )


@app.get("/api/fleet/export")
def fleet_export(
    q: str | None = None,
    flag: str | None = None,
    owner: str | None = None,
    class_society: str | None = None,
    pi_club: str | None = None,
    paris_mou: str | None = None,
    tokyo_mou: str | None = None,
    kind: str | None = None,
    segment: str | None = None,
    built_min: int | None = None,
    built_max: int | None = None,
    dwt_min: int | None = None,
    dwt_max: int | None = None,
    detention_min: float | None = None,
    risk_min: int | None = None,
    live_only: bool = False,
):
    """Download current filtered fleet as CSV."""
    csv_text = _fleet.export_csv(
        q=q, flag=flag, owner=owner, class_society=class_society,
        pi_club=pi_club, paris_mou=paris_mou, tokyo_mou=tokyo_mou,
        kind=kind, segment=segment,
        built_min=built_min, built_max=built_max,
        dwt_min=dwt_min, dwt_max=dwt_max, detention_min=detention_min,
        risk_min=risk_min, live_only=live_only,
    )
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fleet.csv"},
    )


@app.get("/api/stream")
async def stream_vessels(request: Request):
    """SSE endpoint: emits all live vessels every 15 seconds.

    Clients connect once and receive updates without re-polling. Falls back to the
    normal /api/vessels polling if EventSource is not supported or the connection drops.
    The X-Accel-Buffering: no header disables nginx proxy buffering for this response.
    """

    async def generate():
        while True:
            if await request.is_disconnected():
                break

            try:
                df = await asyncio.to_thread(
                    db.query,
                    "SELECT mmsi, name, lat, lon, sog, cog, heading, destination, "
                    "       ship_type, length_m, kind, segment, region, updated_ts, "
                    "       imo, draught, nav_status, eta "
                    "FROM live_positions "
                    "WHERE updated_ts >= now() - INTERVAL 30 MINUTE",
                )
                if not df.empty:
                    # Coerce timestamps to ISO strings for JSON serialisation
                    df["updated_ts"] = df["updated_ts"].astype(str)
                    records = df.where(pd.notna(df), None).to_dict("records")
                    yield f"data: {_json.dumps(records)}\n\n"
            except Exception:
                pass

            await asyncio.sleep(15)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
