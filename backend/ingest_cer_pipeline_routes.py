"""Ingest Canada Energy Regulator (CER) pipeline routes into freight_analytics.duckdb.

Downloads all 28 federally regulated pipeline systems from the CER ArcGIS Online
FeatureServer (public, no auth required). Each pipeline is a GeoJSON MultiLineString.
Converts to the WM multi-segment format [[lat,lon],...] and stores in
global_pipeline_routes.

The CER service returns full-detail geometry (NGTL alone has 4319 pts across 1483 paths).
RDP simplification (epsilon=0.05 deg ~5 km) reduces this to a renderable count.

Usage:
    cd backend
    .venv/bin/python ingest_cer_pipeline_routes.py [--db <path>] [--dry-run] [--force]

Options:
    --force    Overwrite existing routes (default: skip already-routed WM IDs)
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

DB_DEFAULT = Path(__file__).parent / "data" / "freight_analytics.duckdb"

CER_URL = (
    "https://services5.arcgis.com/vNzamREXvX2WcX6d/arcgis/rest/services"
    "/CER_Pipeline_Systems_WGS84_view/FeatureServer/3/query"
)

# Default RDP tolerance - 0.05 deg (~5 km)
RDP_EPSILON = 0.05

# Per-pipeline overrides for sprawling networks that would otherwise produce
# thousands of segments (each rendered as a separate Leaflet polyline).
_EPSILON_OVERRIDE: dict[str, float] = {
    "NGTL": 0.10,
    "TCPL": 0.08,
}

# Drop paths shorter than this threshold (km) - filters out short gathering
# laterals from network pipelines like NGTL, keeping only main trunk segments.
_MIN_PATH_KM: dict[str, float] = {
    "NGTL": 40.0,  # exclude laterals < 40 km; keeps major Alberta transmission grid
    "TCPL": 10.0,
}


# ---------------------------------------------------------------------------
# CER PipelineID -> WM pipeline IDs
# One CER entry may cover multiple WM IDs (aliases, phases, same corridor).
# Empty list means this CER pipeline is already well-routed from EIA/OSM; skip.
# ---------------------------------------------------------------------------
_CER_TO_WM: dict[str, list[str]] = {
    # --- Gas pipelines ---
    "NGTL":      ["nova-gas-transmission-ngtl-pipeline-alberta-gas-pipeline-sys-ca"],
    "Westcoast": ["bc-gas-pipeline-westcoast-pipeline-ca"],
    "Foothills": ["foothills-system-gas-pipeline-ca"],
    "TCPL":      ["canadian-mainline-gas-pipeline-ca"],
    "Alliance":  [],  # already routed from OSM
    "MNP":       [],  # maritimes-and-northeast-gas-pipeline-ca already routed
    "TQM":       [],  # already routed
    "Vector":    [],  # great-lakes-gas-transmission-pipeline-ca already routed
    "ManyIslands": [],
    "Brunswick": [],
    # --- Liquid pipelines ---
    "Cochin":         ["cochin-pipeline-system-ca"],
    "EnbridgeBakken": ["enbridge-line-65-oil-pipeline-ca"],
    "Wascana":        ["saskatchewan-oil-pipeline-ca"],
    "TransMountain":  [],  # trans-mountain already routed from EIA/OSM
    "Keystone":       [],  # keystone already routed from EIA
    "EnbridgeMainline": [],  # enbridge-mainline already routed
    "EnbridgeLine9":  [],  # already routed
    "Express":        [],  # express-oil-pipeline-system-ca already routed
    "NormanWells":    [],  # norman-wells-oil-pipeline-ca already routed
    "SouthernLights": [],  # already routed (enbridge-mainline shares corridor)
    "EnbridgeLine7":  [],
    "EnbridgeLine11": [],
    "TransNorthern":  [],
    "Montreal":       [],
    "Genesis":        [],
    "Westspur":       [],  # westpur-oil-pipeline-ca already routed
    "Aurora":         [],
    "MilkRiver":      [],
    "EnbridgeLine65": [],  # not a real PipelineID - covered by EnbridgeBakken above
}


# ---------------------------------------------------------------------------
# Geometry utilities
# ---------------------------------------------------------------------------

def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _rdp(pts: list, eps: float) -> list:
    if len(pts) <= 2:
        return pts
    x1, y1 = pts[0]
    x2, y2 = pts[-1]
    dx, dy = x2 - x1, y2 - y1
    d = (dx * dx + dy * dy) ** 0.5
    max_dist, max_idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        if d:
            dist = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / d
        else:
            dist = _hav(*pts[i], *pts[0])
        if dist > max_dist:
            max_dist, max_idx = dist, i
    if max_dist > eps:
        return _rdp(pts[: max_idx + 1], eps)[:-1] + _rdp(pts[max_idx:], eps)
    return [pts[0], pts[-1]]


def _simplify_paths(
    paths: list[list[list[float]]],
    eps: float = RDP_EPSILON,
    min_km: float = 0.0,
) -> list:
    """RDP-simplify each path; drop sub-2-point results and paths shorter than min_km."""
    out = []
    for path in paths:
        if min_km > 0:
            raw_km = sum(
                _hav(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1])
                for i in range(len(path) - 1)
            )
            if raw_km < min_km:
                continue
        simplified = _rdp(path, eps)
        if len(simplified) >= 2:
            out.append(simplified)
    return out


def _path_km(segments: list) -> float:
    total = 0.0
    for seg in segments:
        for i in range(len(seg) - 1):
            total += _hav(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
    return total


# ---------------------------------------------------------------------------
# CER fetch
# ---------------------------------------------------------------------------

def fetch_cer_pipelines() -> list[dict]:
    """Return list of {pipeline_id, pipeline_name, company, commodity, paths} dicts.

    Each path is a list of [lat, lon] pairs (coordinates swapped from GeoJSON [lng,lat]).
    """
    params = urllib.parse.urlencode({
        "where": "1=1",
        "outFields": "PipelineID,Pipeline_Name,Company,Commodity",
        "returnGeometry": "true",
        "f": "geojson",
        "resultRecordCount": 2000,
    })
    url = f"{CER_URL}?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read())

    results = []
    for feat in data.get("features", []):
        props = feat.get("properties") or feat.get("attributes") or {}
        geom = feat.get("geometry", {})
        if not geom or geom.get("type") not in ("MultiLineString", "LineString"):
            continue

        pid = (props.get("PipelineID") or "").strip()
        if not pid:
            continue

        # GeoJSON uses [lng, lat]; swap to [lat, lon] for storage
        raw_paths: list[list] = []
        if geom["type"] == "MultiLineString":
            raw_paths = geom.get("coordinates", [])
        else:  # LineString
            raw_paths = [geom.get("coordinates", [])]

        paths = [[[pt[1], pt[0]] for pt in path if len(pt) >= 2] for path in raw_paths]
        paths = [p for p in paths if len(p) >= 2]

        if not paths:
            continue

        results.append({
            "pipeline_id": pid,
            "pipeline_name": (props.get("Pipeline_Name") or "").strip(),
            "company": (props.get("Company") or "").strip(),
            "commodity": (props.get("Commodity") or "").strip(),
            "paths": paths,
        })

    return results


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _already_routed(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Return set of WM IDs that already have routes in global_pipeline_routes."""
    rows = con.execute("SELECT wm_id FROM global_pipeline_routes").fetchall()
    return {r[0] for r in rows}


def _ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS global_pipeline_routes (
            wm_id          TEXT PRIMARY KEY,
            n_points       INTEGER,
            path_km        REAL,
            snap_km_start  REAL,
            snap_km_end    REAL,
            route_json     TEXT
        )
    """)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB_DEFAULT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite routes for already-routed WM IDs")
    args = ap.parse_args()

    print("=== CER Pipeline Routes Ingest ===\n")
    print("Fetching CER pipeline features...", flush=True)
    cer_features = fetch_cer_pipelines()
    print(f"  {len(cer_features)} CER pipelines fetched\n", flush=True)

    # Index by PipelineID
    cer_by_id: dict[str, dict] = {f["pipeline_id"]: f for f in cer_features}

    con = duckdb.connect(args.db)
    _ensure_table(con)
    already_routed = _already_routed(con)

    total_stored = 0

    for pipeline_id, wm_ids in _CER_TO_WM.items():
        if not wm_ids:
            continue

        feat = cer_by_id.get(pipeline_id)
        if feat is None:
            print(f"[{pipeline_id}] NOT FOUND in CER data - skip", flush=True)
            continue

        eps = _EPSILON_OVERRIDE.get(pipeline_id, RDP_EPSILON)
        min_km = _MIN_PATH_KM.get(pipeline_id, 0.0)
        simplified = _simplify_paths(feat["paths"], eps, min_km)
        if not simplified:
            print(f"[{pipeline_id}] geometry empty after simplification - skip", flush=True)
            continue

        n_pts = sum(len(s) for s in simplified)
        km = _path_km(simplified)

        print(
            f"[{pipeline_id}] {feat['pipeline_name']} | "
            f"{feat['company']} | "
            f"{len(simplified)} segments {n_pts} pts {km:.0f} km",
            flush=True,
        )

        for wm_id in wm_ids:
            if wm_id in already_routed and not args.force:
                print(f"  {wm_id!r:60} SKIP (already routed, use --force to overwrite)")
                continue

            print(f"  {wm_id!r:60} -> storing {n_pts} pts {km:.0f} km")
            if args.dry_run:
                total_stored += 1
                continue

            try:
                con.execute(
                    "INSERT OR REPLACE INTO global_pipeline_routes VALUES (?,?,?,?,?,?)",
                    [wm_id, n_pts, km, 0.0, 0.0, json.dumps(simplified)],
                )
                total_stored += 1
                already_routed.add(wm_id)
            except Exception as exc:
                print(f"  WARN: {wm_id}: {exc}", flush=True)

        print()

    con.close()

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"\n{prefix}Done. {total_stored} WM routes stored from CER data.")

    if not args.dry_run:
        con_r = duckdb.connect(args.db, read_only=True)
        total_global = con_r.execute("SELECT COUNT(*) FROM global_pipeline_routes").fetchone()[0]
        con_r.close()
        print(f"Total global_pipeline_routes: {total_global}")


if __name__ == "__main__":
    main()
