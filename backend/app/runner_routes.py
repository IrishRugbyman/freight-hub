from __future__ import annotations

import math
import sys
from functools import lru_cache

from .project_paths import project_dir

_TA_SRC = project_dir("transport-arb") / "src"
if str(_TA_SRC) not in sys.path:
    sys.path.insert(0, str(_TA_SRC))

from transport_arb.config import TransportArbConfig
from transport_arb.pipeline import run_once

from .schemas import ArbMatrixCell, BwetInfo, RouteResult, RoutesResponse

_CONFIG = project_dir("transport-arb") / "configs" / "crude_v1.json"


def _clean(v: float) -> float:
    return round(v, 3) if not math.isnan(v) else 0.0


@lru_cache(maxsize=1)
def run_routes_default() -> RoutesResponse:
    cfg = TransportArbConfig.from_json(_CONFIG)
    r = run_once(cfg)

    routes = []
    for rt in r["routes"]:
        routes.append(
            RouteResult(
                id=rt["id"],
                origin=rt["origin"],
                destination=rt["destination"],
                product_class=rt["product_class"],
                vessel_class=rt["vessel_class"],
                voyage_days=rt["voyage_days"],
                description=rt["description"],
                origin_spot=_clean(rt.get("origin_spot", 0)),
                origin_price=_clean(rt.get("origin_price", 0)),
                dest_spot=_clean(rt.get("dest_spot", 0)),
                dest_fwd=_clean(rt.get("dest_fwd", 0)),
                fwd_curve_effect=_clean(rt.get("fwd_curve_effect", 0)),
                freight=_clean(rt.get("freight", 0)),
                freight_base=_clean(rt.get("freight_base", rt.get("freight", 0))),
                freight_bwet_adjusted=bool(rt.get("freight_bwet_adjusted", False)),
                port_cost=_clean(rt.get("port_cost", 0)),
                finance_cost=_clean(rt.get("finance_cost", 0)),
                insurance_cost=_clean(rt.get("insurance_cost", 0)),
                total_cost=_clean(rt.get("total_cost", 0)),
                gross_margin=_clean(rt.get("gross_margin", 0)),
                net_margin=_clean(rt.get("net_margin", 0)),
                net_margin_baseline=_clean(rt.get("net_margin_baseline", 0)),
                breakeven_freight=_clean(rt.get("breakeven_freight", 0)),
                status=rt.get("status", "closed"),
                status_near=rt.get("status_near", "closed"),
            )
        )

    bw = r.get("bwet", {})
    bwet_info = BwetInfo(
        bwet_close=bw.get("bwet_close"),
        bwet_baseline=bw.get("bwet_baseline", 16.6),
        scale_factor=bw.get("scale_factor", 1.0),
        source=bw.get("source", "static"),
        bwet_date=bw.get("bwet_date"),
    )

    origins_seen: list[str] = []
    dests_seen: list[str] = []
    for rt in r["routes"]:
        if rt["origin"] not in origins_seen:
            origins_seen.append(rt["origin"])
        if rt["destination"] not in dests_seen:
            dests_seen.append(rt["destination"])

    route_lookup: dict[tuple[str, str], RouteResult] = {
        (rt.origin, rt.destination): rt for rt in routes
    }
    matrix = []
    for orig in origins_seen:
        for dest in dests_seen:
            if orig == dest:
                continue
            rt = route_lookup.get((orig, dest))
            if rt is not None:
                matrix.append(
                    ArbMatrixCell(
                        origin=orig,
                        destination=dest,
                        net_margin=rt.net_margin,
                        status=rt.status,
                        voyage_days=rt.voyage_days,
                    )
                )
            else:
                matrix.append(ArbMatrixCell(origin=orig, destination=dest))

    return RoutesResponse(
        name=r["name"],
        as_of=r["as_of"],
        spots=r["spots"],
        routes=routes,
        n_open=r["n_open"],
        n_closed=r["n_closed"],
        n_near=r["n_near"],
        hist_series=r["hist_series"],
        bwet=bwet_info,
        matrix=matrix,
        matrix_origins=origins_seen,
        matrix_destinations=dests_seen,
    )
