"""Ingest EIA Natural Gas intrastate pipeline routes.

Uses the EIA Natural Gas Interstate and Intrastate Pipelines FeatureServer,
filtering by operator name (no system name field exists in this dataset).
Only intrastate operators that map cleanly to a single WM pipeline entry are
included - operators running multiple distinct systems are excluded.

Routes stored in eia_oil_pipeline_routes (wm_id keyed, already in loader cascade).

Usage:
    cd backend
    .venv/bin/python ingest_eia_ng_intrastate_routes.py [--db <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb

DB_DEFAULT = Path(__file__).parent / "data" / "freight_analytics.duckdb"

NG_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
    "/Natural_Gas_Interstate_and_Intrastate_Pipelines_1/FeatureServer/0"
)

RDP_EPSILON = 0.02  # ~2 km - same as OSM ingest (dense intrastate networks)

# ---------------------------------------------------------------------------
# Manual overrides: EIA Operator value -> list of WM pipeline ids
# Only include operators that uniquely identify a single WM pipeline system.
# ---------------------------------------------------------------------------
_OPERATOR_MAP: dict[str, list[str]] = {
    # Acadian Gas Pipeline System (Louisiana intrastate)
    "Acadian Gas Pipeline Sys": ["acadian-gas-pipeline-system-us"],
    # Acadian gathering feeds the main pipeline - merge into same WM entry
    "Acadian Gas Gathering System": ["acadian-gas-pipeline-system-us"],
    # Bridgeline Gas Pipeline (Louisiana intrastate)
    "Bridgeline Holdings Pipeline LP": ["bridgeline-gas-pipeline-us"],
    # Louisiana Intrastate Gas (LIG) Pipeline
    "Louisiana Intrastate Gas Co": ["louisiana-intrastate-gas-lig-pipeline-us"],
    # Oasis Gas Pipeline (Louisiana intrastate)
    "Oasis Pipeline": ["oasis-gas-pipeline-us"],
    # SoCalGas Pipeline (Southern California intrastate transmission)
    "Southern California Gas Co": ["socalgas-pipeline-us"],
    # Houston Pipeline (HPL) System and Tejas Gas Pipeline are excluded:
    # "Houston Pipeline Co" (662 segs, 7058 km) and "Kinder Morgan Texas Pipeline Co"
    # (812 segs, 7023 km) each cover most of Texas's gas distribution network under one
    # operator name - they cannot be cleanly isolated to the specific WM pipeline entries.
}


# ---------------------------------------------------------------------------
# RDP simplification
# ---------------------------------------------------------------------------
def _rdp(pts: list[list[float]], eps: float) -> list[list[float]]:
    if len(pts) <= 2:
        return pts
    x1, y1 = pts[0]
    x2, y2 = pts[-1]
    dx, dy = x2 - x1, y2 - y1
    d = (dx * dx + dy * dy) ** 0.5
    max_dist, max_idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        if d == 0:
            dist = ((pts[i][0] - x1) ** 2 + (pts[i][1] - y1) ** 2) ** 0.5
        else:
            dist = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / d
        if dist > max_dist:
            max_dist, max_idx = dist, i
    if max_dist > eps:
        return _rdp(pts[:max_idx + 1], eps)[:-1] + _rdp(pts[max_idx:], eps)
    return [pts[0], pts[-1]]


def _simplify_segments(segs: list[list[list[float]]]) -> list[list[list[float]]]:
    result = []
    for seg in segs:
        s = _rdp(seg, RDP_EPSILON)
        if len(s) >= 2:
            result.append(s)
    return result


def _haversine(a: list[float], b: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(h ** 0.5)


def _path_km(segs: list[list[list[float]]]) -> float:
    return sum(
        _haversine(seg[i], seg[i + 1])
        for seg in segs
        for i in range(len(seg) - 1)
    )


# ---------------------------------------------------------------------------
# Download operator segments with geometry
# ---------------------------------------------------------------------------
def _fetch_operator(operator: str) -> list[list[list[float]]]:
    """Fetch all polyline segments for a given Operator value.
    Returns list of [[lat,lon],...] segments.
    """
    op_enc = urllib.parse.quote(f"Operator = '{operator}'")
    segments: list[list[list[float]]] = []
    offset = 0
    batch = 500
    while True:
        url = (
            f"{NG_URL}/query?where={op_enc}"
            "&outFields=Operator"
            "&geometryType=esriGeometryPolyline"
            "&outSR=4326"
            "&f=json"
            f"&resultRecordCount={batch}"
            f"&resultOffset={offset}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        feats = d.get("features", [])
        for feat in feats:
            for path in feat.get("geometry", {}).get("paths", []):
                seg = [[pt[1], pt[0]] for pt in path if len(pt) >= 2]
                if len(seg) >= 2:
                    segments.append(seg)
        if not d.get("exceededTransferLimit"):
            break
        offset += batch
    return segments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== EIA Natural Gas Intrastate Pipeline Routes Ingest ===\n")

    wm_routes: dict[str, list[list[list[float]]]] = defaultdict(list)

    for operator, wm_ids in _OPERATOR_MAP.items():
        print(f"Fetching: {operator!r} -> {wm_ids} ...", flush=True)
        segs = _fetch_operator(operator)
        print(f"  {len(segs)} segments", flush=True)
        for wm_id in wm_ids:
            wm_routes[wm_id].extend(segs)

    print(f"\nSimplifying {len(wm_routes)} routes...")
    simplified: dict[str, list[list[list[float]]]] = {}
    for wm_id, segs in wm_routes.items():
        simp = _simplify_segments(segs)
        if not simp:
            continue
        n_before = sum(len(s) for s in segs)
        n_after = sum(len(s) for s in simp)
        km = _path_km(simp)
        print(f"  {wm_id}: {len(segs)} segs, {n_before} pts -> {n_after} pts, {km:.0f} km")
        simplified[wm_id] = simp

    if args.dry_run:
        print(f"\n[dry-run] Would store {len(simplified)} routes.")
        return

    print(f"\nStoring {len(simplified)} routes in DuckDB...")
    con = duckdb.connect(args.db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS eia_oil_pipeline_routes (
            wm_id      TEXT PRIMARY KEY,
            n_segments INTEGER,
            n_points   INTEGER,
            route_json TEXT
        )
    """)
    stored = 0
    for wm_id, segs in simplified.items():
        n_pts = sum(len(s) for s in segs)
        con.execute(
            "INSERT OR REPLACE INTO eia_oil_pipeline_routes (wm_id, n_segments, n_points, route_json)"
            " VALUES (?, ?, ?, ?)",
            [wm_id, len(segs), n_pts, json.dumps(segs)],
        )
        stored += 1
    con.close()

    total = duckdb.connect(args.db, read_only=True).execute(
        "SELECT COUNT(*) FROM eia_oil_pipeline_routes"
    ).fetchone()[0]
    print(f"\nStored {stored} new/updated routes.")
    print(f"Total eia_oil_pipeline_routes: {total}")
    print("\nDone. Restart freight-api to pick up changes.")


if __name__ == "__main__":
    main()
