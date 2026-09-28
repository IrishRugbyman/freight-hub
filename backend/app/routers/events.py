"""The event log and its Atom / JSON Feed syndication."""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import Response

from .. import db
from .. import feed as _feed
from ..common import iso
from ..schemas import (
    AisEvent,
    EventsResponse,
)

router = APIRouter()


@router.get("/api/events", response_model=EventsResponse)
def events(
    type: str | None = None,
    days: int = 7,
    limit: int = 200,
    ocean_only: bool = True,
):
    """Intelligence event feed: AIS gaps, loitering, STS candidates.

    ?type=gap|loiter|sts  - filter by event type (omit for all)
    ?days=7               - lookback window (clamped 1..30)
    ?limit=200            - max events returned (clamped 1..500)
    ?ocean_only=true      - exclude Small segment (inland barges, default true)
    """
    days = max(1, min(30, days))
    limit = max(1, min(500, limit))
    _db = db.analytics_db_path()

    from datetime import UTC, datetime
    from datetime import timedelta as _td

    cutoff = datetime.now(UTC).replace(tzinfo=None) - _td(days=days)

    where_clauses = ["start_ts >= ?"]
    params: list = [cutoff]
    if type:
        where_clauses.append("type = ?")
        params.append(type)
    if ocean_only:
        where_clauses.append("segment != 'Small'")

    events_sql = (
        "SELECT event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon, "
        "       region, kind, segment, details "
        "FROM ais_events "
        "WHERE " + " AND ".join(where_clauses) + " ORDER BY start_ts DESC LIMIT ?"
    )
    params.append(limit)

    rows_df = db.query(events_sql, params, db=_db)
    if rows_df.empty:
        return EventsResponse(events=[], total=0)

    # Enrich with vessel names from live_positions (separate AIS DB query)
    all_mmsis = list(
        set(rows_df["mmsi"].dropna().astype(int).tolist())
        | set(rows_df["mmsi2"].dropna().astype(int).tolist())
    )
    name_map: dict[int, str] = {}
    if all_mmsis:
        placeholders = ",".join(["?"] * len(all_mmsis))
        name_df = db.query(
            f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({placeholders})",
            all_mmsis,
        )
        if not name_df.empty:
            name_map = dict(
                zip(name_df["mmsi"].astype(int), name_df["name"].fillna(""), strict=False)
            )

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
                start_ts=iso(row["start_ts"]) or "",
                end_ts=iso(row["end_ts"]) or "",
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


def _fetch_events_raw(types: list[str], days: int, limit: int) -> list[dict]:
    """Fetch + name-enrich ``ais_events`` rows as plain dicts.

    Shared read path for the syndication feeds. ``types`` filters by event type
    (empty list = no type filter); rows are returned newest-first.
    """
    days = max(1, min(30, days))
    limit = max(1, min(500, limit))
    _db = db.analytics_db_path()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    where_clauses = ["start_ts >= ?", "segment != 'Small'"]
    params: list = [cutoff]
    if types:
        placeholders = ",".join(["?"] * len(types))
        where_clauses.append(f"type IN ({placeholders})")
        params.extend(types)

    sql = (
        "SELECT event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon, "
        "       region, kind, segment, details "
        "FROM ais_events WHERE " + " AND ".join(where_clauses) + " ORDER BY start_ts DESC LIMIT ?"
    )
    params.append(limit)

    rows_df = db.query(sql, params, db=_db)
    if rows_df.empty:
        return []

    all_mmsis = list(
        set(rows_df["mmsi"].dropna().astype(int).tolist())
        | set(rows_df["mmsi2"].dropna().astype(int).tolist())
    )
    name_map: dict[int, str] = {}
    if all_mmsis:
        placeholders = ",".join(["?"] * len(all_mmsis))
        name_df = db.query(
            f"SELECT mmsi, name FROM live_positions WHERE mmsi IN ({placeholders})",
            all_mmsis,
        )
        if not name_df.empty:
            name_map = dict(
                zip(name_df["mmsi"].astype(int), name_df["name"].fillna(""), strict=False)
            )

    out: list[dict] = []
    for _, row in rows_df.iterrows():
        mmsi_int = int(row["mmsi"])
        mmsi2_val = row["mmsi2"]
        mmsi2_int = int(mmsi2_val) if mmsi2_val is not None and not pd.isna(mmsi2_val) else None
        try:
            details_dict = _json.loads(row["details"]) if row["details"] else {}
        except (ValueError, TypeError):
            details_dict = {}
        out.append(
            {
                "event_id": str(row["event_id"]),
                "type": str(row["type"]),
                "mmsi": mmsi_int,
                "mmsi2": mmsi2_int,
                "start_ts": iso(row["start_ts"]) or "",
                "end_ts": iso(row["end_ts"]) or "",
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "region": str(row["region"]) if row["region"] else None,
                "kind": str(row["kind"]) if row["kind"] else None,
                "segment": str(row["segment"]) if row["segment"] else None,
                "details": details_dict,
                "vessel_name": name_map.get(mmsi_int),
                "vessel2_name": name_map.get(mmsi2_int) if mmsi2_int else None,
            }
        )
    return out


def _feed_types(types: str | None) -> list[str]:
    """Resolve the ?types= override to a validated list, defaulting to high-risk."""
    if not types:
        return list(_feed.HIGH_RISK_TYPES)
    requested = [t.strip() for t in types.split(",") if t.strip()]
    valid = set(_feed.HIGH_RISK_TYPES) | {"reroute"}
    return [t for t in requested if t in valid] or list(_feed.HIGH_RISK_TYPES)


@router.get("/api/feed.xml")
def feed_atom(days: int = 7, limit: int = 100, types: str | None = None):
    """Atom 1.0 feed of high-risk maritime events. ?days=7 ?types=dark_voyage,sts."""
    events = _fetch_events_raw(_feed_types(types), days, limit)
    xml = _feed.build_atom(events, f"{_feed.SITE_URL}/api/feed.xml")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8")


@router.get("/api/feed.json")
def feed_json(days: int = 7, limit: int = 100, types: str | None = None):
    """JSON Feed 1.1 of high-risk maritime events. ?days=7 ?types=dark_voyage,sts."""
    events = _fetch_events_raw(_feed_types(types), days, limit)
    doc = _feed.build_json_feed(events, f"{_feed.SITE_URL}/api/feed.json")
    return Response(content=doc, media_type="application/feed+json; charset=utf-8")
