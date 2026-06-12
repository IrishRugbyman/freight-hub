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
    RoutesResponse,
    SpeedAnalyticsResponse,
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

    # Sort all events chronologically
    events.sort(key=lambda e: e.ts)
    return VoyagesResponse(mmsi=mmsi, events=events)


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
