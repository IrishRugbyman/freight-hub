"""freight-api — read-only live vessel data for the freight hub tracker.

Thin layer over the AIS collector's live_positions table. No heavy compute; just
freshness-filtered reads + per-region aggregation. Vessels older than STALE_HOURS
are excluded everywhere (the map shows only currently-tracked ships).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ais.regions import REGIONS
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import db
from .schemas import ChokepointCount, Meta, Vessel

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


def _iso(ts) -> str | None:
    if ts is None:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
