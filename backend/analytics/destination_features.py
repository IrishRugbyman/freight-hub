"""Candidate generation for the destination predictor.

Per live vessel, builds the shortlist of plausible destination ports scored by
`destination_predict`: the union of

  * geometric candidates - targets ahead of the vessel's course and within range,
    reusing the exact gate `eta_serving._candidate_pairs` already applies for True
    ETA (bearing within 75deg of COG/heading, <=1500nm), and
  * the resolved AIS-reported destination (via `destination_resolver.resolve`),
    which is *not* bearing-gated - a vessel is presumed to be heading toward
    whatever the crew actually reported, geometry aside. This is the candidate the
    predictor votes against: when the model's top choice disagrees with it, that
    disagreement is the reroute signal.

A resolved destination that lands within `_SAME_PORT_NM` of an already-included
geometric candidate is not duplicated - it flags that candidate's `reported_match`
instead, so "the model agrees with the crew" is a property of one row, not two.

This module is pure (no DB/IO): callers pass in the already-loaded `live` and
`targets` frames (`eta_serving._load_live` / `_load_targets` are the production
sources) so it stays independently testable.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.destination_resolver import resolve as resolve_destination
from analytics.eta_labels import haversine_nm
from analytics.eta_serving import _angle_diff, _candidate_pairs
from quant_lib.freight.eta import initial_bearing

# Two targets within this of each other are "the same place" - a resolved
# reported destination this close to a geometric candidate marks that candidate
# as reported-matching rather than adding a duplicate row.
_SAME_PORT_NM = 20.0

_CANDIDATE_COLS = [
    "mmsi",
    "lat",
    "lon",
    "sog",
    "cog",
    "segment",
    "draught",
    "target_id",
    "target_type",
    "target_name",
    "target_lat",
    "target_lon",
    "gc_dist_nm",
    "bearing_align",
    "reported_match",
    "resolver_score",
]


def bearing_alignment(
    lat: float, lon: float, course: float | None, t_lat: float, t_lon: float
) -> float:
    """How well a vessel's course points at a target, in [0, 1] (1 = dead-on).

    0.5 (neutral) when no course is known - absence of signal, not disagreement.
    """
    if course is None:
        return 0.5
    bearing = initial_bearing(lat, lon, t_lat, t_lon)
    return 1.0 - (_angle_diff(bearing, course) / 180.0)


def _bearing_to_many(lat: float, lon: float, t_lats: np.ndarray, t_lons: np.ndarray) -> np.ndarray:
    """Vectorized initial bearing (deg, 0-360) from one point to many targets."""
    phi1 = math.radians(lat)
    phi2 = np.radians(np.asarray(t_lats, dtype=float))
    dlon = np.radians(np.asarray(t_lons, dtype=float) - lon)
    y = np.sin(dlon) * np.cos(phi2)
    x = math.cos(phi1) * np.sin(phi2) - math.sin(phi1) * np.cos(phi2) * np.cos(dlon)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def bearing_alignment_vec(
    lat: float, lon: float, course: float | None, t_lats: np.ndarray, t_lons: np.ndarray
) -> np.ndarray:
    """Vectorized twin of `bearing_alignment`: one point vs many candidate targets.

    Used by the training-set builder, which must score a vessel's course against
    every target in the universe (not just the one geometric candidate a live pass
    would gate on).
    """
    n = len(t_lats)
    if course is None:
        return np.full(n, 0.5)
    bearings = _bearing_to_many(lat, lon, t_lats, t_lons)
    d = np.abs((bearings - course) % 360.0)
    diff = np.minimum(d, 360.0 - d)
    return 1.0 - diff / 180.0


def candidate_frame(live: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """One row per (vessel, candidate target). Empty frame if no live/targets.

    `live` must already be underway-filtered (mirrors `eta_serving._load_live`);
    this module applies no speed/segment filtering of its own.
    """
    if live.empty or targets.empty:
        return pd.DataFrame(columns=_CANDIDATE_COLS)

    pairs = _candidate_pairs(live, targets)
    by_vessel: dict[int, list[tuple[float, float, float, int]]] = {}
    for vi, lat, lon, gc, ti in pairs:
        by_vessel.setdefault(vi, []).append((lat, lon, gc, ti))

    rows: list[dict] = []
    for vi, v in enumerate(live.itertuples()):
        mmsi = int(v.mmsi)
        lat, lon = float(v.lat), float(v.lon)
        sog = float(v.sog) if pd.notna(getattr(v, "sog", None)) else None
        segment = str(v.segment) if pd.notna(getattr(v, "segment", None)) else None
        draught = float(v.draught) if pd.notna(getattr(v, "draught", None)) else None
        course = None
        if pd.notna(getattr(v, "cog", None)):
            course = float(v.cog)
        elif pd.notna(getattr(v, "heading", None)):
            course = float(v.heading)

        dest_str = getattr(v, "destination", None)
        rp = None
        if isinstance(dest_str, str) and dest_str.strip():
            rp = resolve_destination(dest_str, lat, lon)

        matched_reported = False
        for _lat0, _lon0, gc, ti in by_vessel.get(vi, []):
            t = targets.iloc[ti]
            t_lat, t_lon = float(t["lat"]), float(t["lon"])
            reported_match = (
                rp is not None and haversine_nm(t_lat, t_lon, rp.lat, rp.lon) <= _SAME_PORT_NM
            )
            if reported_match:
                matched_reported = True
            rows.append(
                {
                    "mmsi": mmsi,
                    "lat": lat,
                    "lon": lon,
                    "sog": sog,
                    "cog": course,
                    "segment": segment,
                    "draught": draught,
                    "target_id": str(t["target_id"]),
                    "target_type": str(t["target_type"]),
                    "target_name": str(t["name"]),
                    "target_lat": t_lat,
                    "target_lon": t_lon,
                    "gc_dist_nm": float(gc),
                    "bearing_align": bearing_alignment(lat, lon, course, t_lat, t_lon),
                    "reported_match": reported_match,
                    "resolver_score": float(rp.score) if reported_match else None,
                }
            )

        if rp is not None and not matched_reported:
            gc_r = haversine_nm(lat, lon, rp.lat, rp.lon)
            rows.append(
                {
                    "mmsi": mmsi,
                    "lat": lat,
                    "lon": lon,
                    "sog": sog,
                    "cog": course,
                    "segment": segment,
                    "draught": draught,
                    "target_id": f"dest:{rp.locode}",
                    "target_type": "destination",
                    "target_name": rp.name,
                    "target_lat": rp.lat,
                    "target_lon": rp.lon,
                    "gc_dist_nm": gc_r,
                    "bearing_align": bearing_alignment(lat, lon, course, rp.lat, rp.lon),
                    "reported_match": True,
                    "resolver_score": float(rp.score),
                }
            )

    return pd.DataFrame(rows, columns=_CANDIDATE_COLS)
