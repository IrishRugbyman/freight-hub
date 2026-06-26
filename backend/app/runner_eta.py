"""Read layer for True ETA predictions (Phase E).

Thin, read-only access to the `eta_predictions` snapshot the analytics job
rewrites each run (`analytics.eta_serving`). Mirrors `runner_routes` /
`runner_dispersion`: the API never computes ETAs, it only serves the persisted
physics estimates + calibrated intervals.

Two access shapes:
  * `vessel_predictions(mmsi)`  - all resolvable-target ETAs for one vessel,
    powering `GET /api/analytics/eta`.
  * `predictions_by_mmsi(mmsis)` + `nearest_prediction(...)` - bulk lookup used
    to enrich the inbound / LNG cards with a true ETA to the terminal a vessel is
    matched to (by nearest target centroid, so it is robust to the port/zone
    dedupe in target seeding).
"""

from __future__ import annotations

import math

import pandas as pd

from . import db

_R_NM = 3440.065  # Earth radius (nm)

# A persisted prediction's target must be within this of a card's resolved
# terminal to count as "the same place". 30 nm covers the gap between a curated
# point terminal and a nearby anchorage-zone target that won the seeding dedupe.
_MATCH_NM = 30.0

_SELECT = (
    "SELECT mmsi, target_id, target_name, target_type, target_lat, target_lon, "
    "       eta_p50_h, eta_low_h, eta_high_h, eta_naive_h, method, "
    "       eta_arrival_ts, route_dist_nm, gc_dist_nm, route_method, sog, "
    "       segment, laden "
    "FROM eta_predictions"
)


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R_NM * math.asin(min(1.0, math.sqrt(a)))


def _f(v) -> float | None:
    """Float or None - guards against pandas NA/NaN (DuckDB nulls)."""
    return float(v) if v is not None and not pd.isna(v) else None


def _s(v) -> str | None:
    return str(v) if v is not None and not pd.isna(v) else None


def _row_to_dict(r) -> dict:
    arr = r.get("eta_arrival_ts")
    laden = r.get("laden")
    return {
        "mmsi": int(r["mmsi"]),
        "target_id": str(r["target_id"]),
        "target_name": _s(r.get("target_name")),
        "target_type": _s(r.get("target_type")),
        "target_lat": _f(r.get("target_lat")),
        "target_lon": _f(r.get("target_lon")),
        "eta_p50_h": _f(r.get("eta_p50_h")),
        "eta_low_h": _f(r.get("eta_low_h")),
        "eta_high_h": _f(r.get("eta_high_h")),
        "eta_naive_h": _f(r.get("eta_naive_h")),
        "method": _s(r.get("method")),
        "eta_arrival_ts": arr.isoformat()
        if hasattr(arr, "isoformat")
        else (str(arr) if arr is not None and not pd.isna(arr) else None),
        "route_dist_nm": _f(r.get("route_dist_nm")),
        "gc_dist_nm": _f(r.get("gc_dist_nm")),
        "route_method": _s(r.get("route_method")),
        "sog": _f(r.get("sog")),
        "segment": _s(r.get("segment")),
        "laden": bool(laden) if laden is not None and not pd.isna(laden) else None,
    }


def vessel_predictions(mmsi: int) -> list[dict]:
    """All target ETAs for one vessel, soonest first. Empty if none/locked."""
    df = db.query(
        _SELECT + " WHERE mmsi = ? ORDER BY eta_p50_h",
        [int(mmsi)],
        db=db.analytics_db_path(),
    )
    if df.empty:
        return []
    return [_row_to_dict(r) for _, r in df.iterrows()]


def predictions_by_mmsi(mmsis: list[int]) -> dict[int, list[dict]]:
    """Bulk lookup: mmsi -> its list of target predictions (for card enrichment)."""
    uniq = sorted({int(m) for m in mmsis})
    if not uniq:
        return {}
    placeholders = ",".join("?" * len(uniq))
    df = db.query(
        _SELECT + f" WHERE mmsi IN ({placeholders})",
        uniq,
        db=db.analytics_db_path(),
    )
    out: dict[int, list[dict]] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        d = _row_to_dict(r)
        out.setdefault(d["mmsi"], []).append(d)
    return out


def nearest_prediction(
    preds: list[dict], lat: float, lon: float, max_nm: float = _MATCH_NM
) -> dict | None:
    """Pick the vessel's prediction whose target centroid is nearest (lat, lon).

    Returns None when no target sits within `max_nm` of the point - the caller then
    keeps the naive ETA. This decouples the cards from target-id slugs / dedupe.
    """
    best: dict | None = None
    best_d = max_nm
    for p in preds:
        if p.get("target_lat") is None or p.get("target_lon") is None:
            continue
        d = _haversine_nm(lat, lon, p["target_lat"], p["target_lon"])
        if d <= best_d:
            best_d = d
            best = p
    return best
