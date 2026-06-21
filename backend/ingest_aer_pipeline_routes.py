"""Ingest Alberta intra-provincial pipeline routes via the AER GIS service.

Source: Alberta Energy Regulator (AER) via the Alberta Government ERCB GIS layer at
  https://gis.energy.gov.ab.ca/arcgis/rest/services/Geoview/ERCB_Ext_PROD/MapServer/10

The layer has 324,617 pipeline segments (every licensed pipeline in Alberta including
wellbore flowlines). We filter tightly by operator name + substance + status + optional
diameter threshold to extract only the specific trunk pipelines we need, then store in
global_pipeline_routes alongside the CER and OSM routes.

Target pipelines (all intra-provincial Alberta, not CER-regulated):
  - Athabasca Oil Pipeline      (Enbridge Pipelines (Athabasca) Inc., crude)
  - Grand Rapids Oil Pipeline   (Grand Rapids Pipeline GP Ltd., crude)
  - Cold Lake Pipeline System   (Cold Lake Pipeline Ltd., crude + LVP)
  - Corridor Oil Pipeline       (Inter Pipeline (Corridor) Inc., crude)
  - Horizon Crude Oil Pipeline  (Canadian Natural Resources Ltd., crude, diameter >= 508 mm)
  - Alberta Ethane Gathering    (NOVA Chemicals Corporation, ethane)

Usage:
    cd backend
    .venv/bin/python ingest_aer_pipeline_routes.py [--db <path>] [--dry-run] [--force]

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

AER_URL = (
    "https://gis.energy.gov.ab.ca/arcgis/rest/services"
    "/Geoview/ERCB_Ext_PROD/MapServer/10/query"
)

RDP_EPSILON = 0.05  # ~5 km default
MAX_RECORDS = 2000  # per paginated request


# ---------------------------------------------------------------------------
# AER source definitions
# Each entry: wm_id -> {where, min_km, epsilon, label}
# Paths are fetched via company name + substance + status filters.
# min_km drops short gathering laterals from network operators.
# ---------------------------------------------------------------------------
_SOURCES: dict[str, dict] = {
    "athabasca-oil-pipeline-ca": {
        "label": "Athabasca Oil Pipeline (Enbridge)",
        "where": (
            "CompanyName LIKE '%Enbridge Pipelines (Athabasca)%'"
            " AND SubstanceCode1 = 'Crude Oil'"
            " AND PipelineStatus = 'Operating'"
        ),
        "min_km": 15.0,   # 282 raw paths; drop short spurs to keep main trunk
        "epsilon": 0.05,
    },
    "grand-rapids-oil-pipeline-ca": {
        "label": "Grand Rapids Oil Pipeline",
        "where": (
            "CompanyName LIKE '%Grand Rapids Pipeline%'"
            " AND SubstanceCode1 = 'Crude Oil'"
            " AND PipelineStatus = 'Operating'"
        ),
        "min_km": 12.0,   # keeps ~15 main segments (top segment 63.5 km)
        "epsilon": 0.05,
    },
    "cold-lake-pipeline-system-ca": {
        # Cold Lake is a large gathering network (~1265 km total, 61 segs >5 km).
        # min_km=20 keeps the 23 main trunk/lateral segments.
        "label": "Cold Lake Pipeline System",
        "where": (
            "CompanyName LIKE '%Cold Lake Pipeline%'"
            " AND SubstanceCode1 IN ('Crude Oil', 'LVP Products')"
            " AND PipelineStatus = 'Operating'"
        ),
        "min_km": 20.0,
        "epsilon": 0.05,
    },
    "corridor-oil-pipeline-ca": {
        "label": "Corridor Oil Pipeline (Inter Pipeline)",
        "where": (
            "CompanyName LIKE '%Inter Pipeline (Corridor)%'"
            " AND SubstanceCode1 = 'Crude Oil'"
            " AND PipelineStatus = 'Operating'"
        ),
        "min_km": 0.0,
        "epsilon": 0.05,
    },
    # horizon-crude-oil-pipeline-ca: CNRL's Horizon mine has only pump station
    # piping segments in AER (all <10 km); no continuous trunk from 57.37°N to
    # Edmonton. The corridor is largely covered by the Enbridge Athabasca entry
    # above. Not mapped here.
    #
    # alberta-ethane-gathering-system-aegs-ca: NOVA Chemicals holds only ~1 km
    # of ethane pipe near their Joffre plant in AER. The AEGS gathering
    # infrastructure is part of the CER-regulated NGTL system (already stored).
    # Not mapped here.
    #
    # co-ed-system-ngl-pipeline-ca: AER NGL operators don't reach the WM start
    # point at Cochrane (51.19°N). Likely a historical name for pipeline now
    # operated as several segments under Pembina/Keyera. Not mapped here.
}


# ---------------------------------------------------------------------------
# Geometry utilities (same as ingest_cer_pipeline_routes.py)
# ---------------------------------------------------------------------------

def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
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
    return sum(
        _hav(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
        for seg in segments
        for i in range(len(seg) - 1)
    )


# ---------------------------------------------------------------------------
# AER fetch (paginated)
# ---------------------------------------------------------------------------

def fetch_aer_paths(
    where: str,
    bbox: dict | None = None,
) -> list[list[list[float]]]:
    """Fetch all matching polyline segments from the AER layer; return as list of paths.

    Each path is [[lat, lon], ...] (AER returns projected coords; we request outSR=4326).
    Paginates automatically using resultOffset.

    bbox: optional {"xmin": float, "ymin": float, "xmax": float, "ymax": float}
          in WGS84 degrees - restricts fetch to that envelope.
    """
    all_paths: list[list[list[float]]] = []
    offset = 0

    geo_params: dict = {}
    if bbox:
        geo_params = {
            "geometry": json.dumps({**bbox, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }

    while True:
        params = urllib.parse.urlencode({
            "where": where,
            "outFields": "OBJECTID",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultRecordCount": MAX_RECORDS,
            "resultOffset": offset,
            "f": "json",
            **geo_params,
        })
        with urllib.request.urlopen(f"{AER_URL}?{params}", timeout=60) as resp:
            data = json.loads(resp.read())

        features = data.get("features", [])
        for feat in features:
            for path in feat.get("geometry", {}).get("paths", []):
                # AER paths are [[lng, lat], ...] - swap to [lat, lon]
                pts = [[pt[1], pt[0]] for pt in path if len(pt) >= 2]
                if len(pts) >= 2:
                    all_paths.append(pts)

        if len(features) < MAX_RECORDS:
            break  # last page
        offset += MAX_RECORDS

    return all_paths


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _already_routed(con: duckdb.DuckDBPyConnection) -> set[str]:
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

    print("=== AER Pipeline Routes Ingest ===\n")

    con = duckdb.connect(args.db)
    _ensure_table(con)
    already_routed = _already_routed(con)

    total_stored = 0

    for wm_id, src in _SOURCES.items():
        if wm_id in already_routed and not args.force:
            print(f"[{wm_id}] SKIP (already routed, use --force to overwrite)")
            continue

        print(f"[{wm_id}]  {src['label']}", flush=True)
        print(f"  WHERE: {src['where'][:80]}...", flush=True)

        paths = fetch_aer_paths(src["where"], src.get("bbox"))
        print(f"  Raw: {len(paths)} paths fetched from AER", flush=True)

        if not paths:
            print("  No data returned - skip\n", flush=True)
            continue

        simplified = _simplify_paths(paths, src["epsilon"], src["min_km"])
        if not simplified:
            print("  Empty after simplification - skip\n", flush=True)
            continue

        n_pts = sum(len(s) for s in simplified)
        km = _path_km(simplified)
        print(f"  Result: {len(simplified)} segments {n_pts} pts {km:.0f} km", flush=True)

        if args.dry_run:
            total_stored += 1
            print()
            continue

        try:
            con.execute(
                "INSERT OR REPLACE INTO global_pipeline_routes VALUES (?,?,?,?,?,?)",
                [wm_id, n_pts, km, 0.0, 0.0, json.dumps(simplified)],
            )
            total_stored += 1
            already_routed.add(wm_id)
            print(f"  Stored.\n", flush=True)
        except Exception as exc:
            print(f"  WARN: {exc}\n", flush=True)

    con.close()

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}Done. {total_stored} WM routes stored from AER data.")

    if not args.dry_run:
        con_r = duckdb.connect(args.db, read_only=True)
        total_global = con_r.execute("SELECT COUNT(*) FROM global_pipeline_routes").fetchone()[0]
        con_r.close()
        print(f"Total global_pipeline_routes: {total_global}")


if __name__ == "__main__":
    main()
