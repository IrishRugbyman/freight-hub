"""freight-api — live vessel tracker + transport-arb routes + dispersion analytics.

Live endpoints (AIS) read ais_positions.duckdb via db.py.
Static-backed endpoints (routes, dispersion backtest) serve precomputed JSON from
app/static/, with a live-compute fallback if the static file is absent.
The live dispersion series reads ais_vessel_dispersion from commo.duckdb via loaders.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from ais.regions import REGIONS
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loaders.freight import load_ais_dispersion
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import db
from .runner_dispersion import run_dispersion_default
from .runner_routes import run_routes_default
from .schemas import (
    AisDispersionRow,
    AnalyticsZone,
    ChokepointCount,
    CongestionResponse,
    DensityResponse,
    DispersionResponse,
    LadenResponse,
    LadenSegment,
    Meta,
    RoutesResponse,
    TrackPoint,
    TransitsResponse,
    Vessel,
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
