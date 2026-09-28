"""Pipeline network map with disruption status."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import APIRouter

from ..schemas import (
    PipelineSegment,
    PipelinesResponse,
)

router = APIRouter()


# Cache avoids re-querying PostgreSQL on every map render (data changes ~quarterly).
_pipelines_cache: dict[bool, tuple[float, PipelinesResponse]] = {}
_PIPELINES_TTL = 3600.0  # 1 hour


@router.get("/api/pipelines", response_model=PipelinesResponse)
def get_pipelines(disrupted_only: bool = True):
    """Pipeline segments for the map layer and table.

    By default returns only disrupted pipelines (offline + reduced, ~37 rows).
    Pass disrupted_only=false for all pipelines: 618 World Monitor globals + 104
    RexTag US domestic FERC gas pipelines (no GPS coords, table-only).
    World Monitor records that match RexTag are enriched with owner/length/states.
    Data sources: World Monitor (CC-BY 4.0) + RexTag.com (public informational pages).
    """
    import time

    import pandas as pd
    from loaders.worldmonitor import load_pipelines_for_map, load_rextag_us_only_pipelines

    now = time.monotonic()
    if disrupted_only in _pipelines_cache:
        cached_ts, cached_result = _pipelines_cache[disrupted_only]
        if now - cached_ts < _PIPELINES_TTL:
            return cached_result

    try:
        df = load_pipelines_for_map(disrupted_only=disrupted_only)
    except Exception:
        df = pd.DataFrame()

    def _float(v) -> float | None:
        try:
            f = float(v)
            return None if math.isnan(f) else round(f, 3)
        except (TypeError, ValueError):
            return None

    def _str(v) -> str | None:
        return str(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None

    rows: list[PipelineSegment] = []
    for _, r in df.iterrows():
        route_json = r.get("route_json")
        route_coords = None
        if route_json and isinstance(route_json, str):
            try:
                import json as _json

                route_coords = _json.loads(route_json)
            except Exception:
                pass

        rows.append(
            PipelineSegment(
                id=str(r["id"]),
                name=str(r["name"]),
                commodity=str(r["commodity"]),
                physical_state=str(r["physical_state"]),
                capacity_mbd=_float(r.get("capacity_mbd")),
                capacity_bcm_yr=_float(r.get("capacity_bcm_yr")),
                from_country=str(r["from_country"]),
                to_country=str(r["to_country"]),
                start_lat=_float(r.get("start_lat")),
                start_lon=_float(r.get("start_lon")),
                end_lat=_float(r.get("end_lat")),
                end_lon=_float(r.get("end_lon")),
                disruption_description=_str(r.get("disruption_description")),
                disruption_event_type=_str(r.get("disruption_event_type")),
                disruption_since=str(r["disruption_since"]) if r.get("disruption_since") else None,
                owner=_str(r.get("owner")),
                length_miles=_float(r.get("length_miles")),
                states_served=_str(r.get("states_served")),
                data_source="worldmonitor",
                route_coords=route_coords,
            )
        )

    # Append RexTag-only US domestic pipelines (table-only, no GPS)
    if not disrupted_only:
        try:
            rt_df = load_rextag_us_only_pipelines()
        except Exception:
            rt_df = pd.DataFrame()

        for _, r in rt_df.iterrows():
            rt_route_json = r.get("route_json")
            rt_route_coords = None
            if rt_route_json and isinstance(rt_route_json, str):
                try:
                    import json as _json

                    rt_route_coords = _json.loads(rt_route_json)
                except Exception:
                    pass

            rows.append(
                PipelineSegment(
                    id=str(r["id"]),
                    name=str(r["name"]),
                    commodity="gas",
                    physical_state="flowing",
                    capacity_bcm_yr=None,
                    capacity_mbd=None,
                    capacity_bcfd=_float(r.get("capacity_bcfd")),
                    from_country="US",
                    to_country="US",
                    start_lat=None,
                    start_lon=None,
                    end_lat=None,
                    end_lon=None,
                    owner=_str(r.get("owner")),
                    length_miles=_float(r.get("length_miles")),
                    states_served=_str(r.get("states_served")),
                    data_source="rextag",
                    route_coords=rt_route_coords,
                )
            )

    total_offline_mbd = sum((p.capacity_mbd or 0.0) for p in rows if p.physical_state == "offline")
    total_offline_bcm = sum(
        (p.capacity_bcm_yr or 0.0) for p in rows if p.physical_state == "offline"
    )

    result = PipelinesResponse(
        as_of=datetime.now(UTC).isoformat(),
        disrupted_only=disrupted_only,
        total_offline=sum(1 for p in rows if p.physical_state == "offline"),
        total_reduced=sum(1 for p in rows if p.physical_state == "reduced"),
        total_offline_mbd=round(total_offline_mbd, 2),
        total_offline_bcm=round(total_offline_bcm, 1),
        pipelines=rows,
    )
    _pipelines_cache[disrupted_only] = (now, result)
    return result
