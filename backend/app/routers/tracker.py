"""Tracker page: the live vessel list, chokepoint counts, meta/health and the SSE stream."""

from __future__ import annotations

import asyncio
import json as _json
from datetime import UTC, datetime

import pandas as pd
from ais.regions import REGIONS
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from quant_lib.freight import flag_from_mmsi

from .. import db
from ..common import iso
from ..live import covered_regions, feed_status, live_all, live_visible, query_live
from ..ports import canonical_destination, canonical_origin
from ..schemas import (
    ChokepointCount,
    Meta,
    Vessel,
)

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    """DB reachability + count and timestamp of currently-tracked vessels.

    `ok` stays a statement about *this service*, not about the upstream feed:
    uptime monitoring watches it, and flipping it during an aisstream outage
    would page for something no deploy of ours can fix. The feed's own state is
    reported separately, and honestly.
    """
    df = query_live()
    return {
        "ok": True,
        "tracked": int(len(df)),
        "last_update": iso(df["updated_ts"].max()) if not df.empty else None,
        "feed": feed_status().model_dump(),
    }


@router.get("/api/vessels", response_model=list[Vessel])
def vessels(
    kind: str | None = None,
    segment: str | None = None,
    region: str | None = None,
    flag: str | None = None,
    foc: bool | None = None,
    shadow: bool | None = None,
):
    """Live + last-known vessel positions, filtered by kind / segment / region / flag.

    Returns vessels seen within VISIBLE_HOURS (default 24h). Vessels not seen within
    STALE_HOURS (default 3h) are tagged stale=True and rendered as grey markers on the
    map; they are excluded from all analytics endpoints which still use the 3h window.

    `flag` matches either the ISO2 code or the country name. `foc` / `shadow` filter
    to flags of convenience / high-shadow-activity flags (derived from the MMSI MID).
    """
    now_dt = datetime.now(UTC).replace(tzinfo=None)
    stale_threshold_min = db.STALE_HOURS * 60

    df = live_visible()
    if not df.empty:
        for col, val in (("kind", kind), ("segment", segment), ("region", region)):
            if val:
                df = df[df[col] == val]
    if df.empty:
        return []
    # Derive flag state from the MMSI MID (free, ~100% coverage).
    flags = {int(m): flag_from_mmsi(int(m)) for m in df["mmsi"].dropna().unique()}
    df["flag"] = df["mmsi"].map(lambda m: (f := flags.get(int(m))) and f.country or None)
    df["flag_code"] = df["mmsi"].map(lambda m: (f := flags.get(int(m))) and f.code or None)
    df["flag_foc"] = df["mmsi"].map(lambda m: bool((f := flags.get(int(m))) and f.is_foc))
    df["flag_shadow"] = df["mmsi"].map(lambda m: bool((f := flags.get(int(m))) and f.is_shadow))
    if flag:
        df = df[(df["flag_code"] == flag.upper()) | (df["flag"].str.lower() == flag.lower())]
    if foc:
        df = df[df["flag_foc"]]
    if shadow:
        df = df[df["flag_shadow"]]
    if df.empty:
        return []
    # Round to AIS-native resolution: cuts JSON size ~35% before gzip, lossless for display.
    # Use pd.to_numeric first: ghost rows (from ais_snapshots fallback) may have Python None
    # in numeric columns which causes .round() to raise TypeError.
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce").round(5)
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce").round(5)
    for col, nd in (("sog", 1), ("cog", 1), ("draught", 1)):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(nd)
    if "heading" in df.columns:
        df["heading"] = pd.to_numeric(df["heading"], errors="coerce").round(0)
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
            destination=canonical_destination(r.destination),
            origin=canonical_origin(r.destination),
            kind=r.kind,
            segment=r.segment,
            region=r.region,
            updated_ts=iso(r.updated_ts),
            imo=getattr(r, "imo", None) if "imo" in cols else None,
            draught=getattr(r, "draught", None) if "draught" in cols else None,
            nav_status=getattr(r, "nav_status", None) if "nav_status" in cols else None,
            eta=getattr(r, "eta", None) if "eta" in cols else None,
            flag=getattr(r, "flag", None),
            flag_code=getattr(r, "flag_code", None),
            flag_foc=bool(getattr(r, "flag_foc", False)),
            flag_shadow=bool(getattr(r, "flag_shadow", False)),
            stale=((now_dt - r.updated_ts).total_seconds() / 60) > stale_threshold_min
            if r.updated_ts is not None
            else False,
            age_minutes=round((now_dt - r.updated_ts).total_seconds() / 60)
            if r.updated_ts is not None
            else None,
        )
        for r in df.itertuples()
    ]


@router.get("/api/chokepoints", response_model=list[ChokepointCount])
def chokepoints():
    """Per-region live vessel counts (with bbox + per-segment breakdown)."""
    df = live_all()
    if not df.empty:
        df = df[df["region"].notna()]
    covered = covered_regions()
    out = []
    for name, bbox in REGIONS.items():
        sub = df[df["region"] == name] if not df.empty else df
        by_seg = (
            {str(k): int(v) for k, v in sub["segment"].value_counts().items()}
            if not sub.empty
            else {}
        )
        out.append(
            ChokepointCount(
                region=name,
                bbox=bbox,
                total=len(sub),
                by_segment=by_seg,
                has_coverage=name in covered,
            )
        )
    return out


@router.get("/api/meta", response_model=Meta)
def meta():
    """Distinct kinds/segments/regions, total tracked, last update, and feed state.

    The feed block rides along here rather than getting its own endpoint because
    the frontend already polls /api/meta on the 60s tier, so the banner costs no
    extra request.
    """
    feed = feed_status()
    df = query_live()
    if df.empty:
        return Meta(kinds=[], segments=[], regions=[], total_tracked=0, last_update=None, feed=feed)
    return Meta(
        kinds=sorted(df["kind"].dropna().unique().tolist()),
        segments=sorted(df["segment"].dropna().unique().tolist()),
        regions=sorted(df["region"].dropna().unique().tolist()),
        total_tracked=int(len(df)),
        last_update=iso(df["updated_ts"].max()),
        feed=feed,
    )


@router.get("/api/stream")
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
