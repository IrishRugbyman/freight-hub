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
    ChokepointCount,
    DispersionResponse,
    Meta,
    RoutesResponse,
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


def _iso(ts) -> str | None:
    if ts is None:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
