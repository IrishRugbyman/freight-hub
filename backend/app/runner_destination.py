"""Read layer for destination predictions.

Thin, read-only access to the `destination_predictions` snapshot the analytics
job rewrites each run (`analytics.destination_serving`). Mirrors `runner_eta`:
the API never scores a prediction, it only serves the persisted ranked
candidates.
"""

from __future__ import annotations

import pandas as pd

from . import db

_SELECT = (
    "SELECT mmsi, rank, target_id, target_name, target_type, target_lat, target_lon, "
    "       prob, method, reported_match, gc_dist_nm, as_of "
    "FROM destination_predictions"
)


def _f(v) -> float | None:
    return float(v) if v is not None and not pd.isna(v) else None


def _s(v) -> str | None:
    return str(v) if v is not None and not pd.isna(v) else None


def _row_to_dict(r) -> dict:
    reported = r.get("reported_match")
    return {
        "target_id": str(r["target_id"]),
        "target_name": _s(r.get("target_name")),
        "target_type": _s(r.get("target_type")),
        "target_lat": _f(r.get("target_lat")),
        "target_lon": _f(r.get("target_lon")),
        "prob": _f(r.get("prob")),
        "method": _s(r.get("method")),
        "reported_match": bool(reported)
        if reported is not None and not pd.isna(reported)
        else False,
        "gc_dist_nm": _f(r.get("gc_dist_nm")),
    }


def vessel_destination(mmsi: int) -> list[dict]:
    """Ranked candidate destinations for one vessel, most likely first."""
    df = db.query(
        _SELECT + " WHERE mmsi = ? ORDER BY rank",
        [int(mmsi)],
        db=db.analytics_db_path(),
    )
    if df.empty:
        return []
    return [_row_to_dict(r) for _, r in df.iterrows()]
