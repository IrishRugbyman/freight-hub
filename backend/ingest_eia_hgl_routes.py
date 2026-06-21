"""Ingest EIA Hydrocarbon Gas Liquids (HGL/NGL) pipeline routes.

Downloads from the EIA ArcGIS FeatureServer:
  - Hydrocarbon Gas Liquids Pipelines (133 records, Opername/Pipename fields)
    Covers NGL/Y-grade/ethane/LPG interstate pipelines in the US.

Routes are matched to WM pipeline IDs using manual overrides and stored in the
existing eia_oil_pipeline_routes table (already consumed by the loader cascade).

Usage:
    cd backend
    .venv/bin/python ingest_eia_hgl_routes.py [--db <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb

DB_DEFAULT = Path(__file__).parent / "data" / "freight_analytics.duckdb"

HGL_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
    "/Hydrocarbon_Gas_Liquids_Pipelines_1/FeatureServer/0"
)

RDP_EPSILON = 0.01  # ~1 km


# ---------------------------------------------------------------------------
# Manual overrides: (Opername, Pipename) -> list of WM pipeline ids
# ---------------------------------------------------------------------------
_MANUAL: dict[tuple[str, str], list[str]] = {
    # ONEOK NGL systems (Permian/Bakken/Mid-Continent)
    ("ONEOK", "Overland Pass"): ["overland-pass-ngl-pipeline-us"],
    ("ONEOK", "Elk Creek Pipeline"): ["elk-creek-ngl-pipeline-us"],
    ("ONEOK", "Bakken NGL Pipeline"): ["bakken-ngl-pipeline-us"],
    # Sterling NGL Pipelines covers Lines I, II, III (all under same WM entry)
    ("ONEOK", "Sterling III"): ["sterling-ngl-pipelines-lines-i-ii-and-iii-us"],
    # Grand Prix Y-Grade (Targa Resources, Permian Basin -> Mont Belvieu TX)
    ("TARGA RESOURCES", "Grand Prix Pipeline"): [
        "grand-prix-y-grade-pipeline-north-texas-mont-belvieu-us"
    ],
    # Skelly-Belvieu (Enterprise Products, Skelly OK -> Mont Belvieu TX)
    ("ENTERPRISE PRODUCTS", "Skelly-Belvieu"): ["skelly-belvieu-pipeline-us"],
    # Mariner West (Sunoco/MPLX, Appalachian ethane -> Sarnia Ontario)
    ("SUNOCO LOGISTICS", "Mariner West"): ["mariner-west-pipeline-us"],
    ("MPLX", "Mariner West to Keystone"): ["mariner-west-pipeline-us"],
    # Utopia East (Kinder Morgan, ethane from Harrison County OH to Windsor ON)
    # WM entry is "Utopia Ethane Pipeline" - same physical pipe, renamed post-construction
    ("KINDER MORGAN", "Utopia East"): ["utopia-ethane-pipeline-us"],
    # DCP Midstream NGL systems (Colorado/Rocky Mountain region)
    # Sand Hills: Permian to Corpus Christi TX (NGL export)
    ("DCP MIDSTREAM", "Sand Hills"): [],  # not in WM unrouted list
    ("DCP MIDSTREAM", "Southern Hills"): [],  # not in WM unrouted list
    # EPIC Y-Grade (Permian Basin NGL, Orla TX -> Corpus Christi TX)
    ("EPIC", "EPIC Y-Grade Pipeline"): [],  # not currently in WM unrouted list
    # Front Range NGL (DJ Basin CO -> Conway KS)
    ("NGL LOGISTICS", "Front Range Pipeline"): [],  # not in WM unrouted list
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


# ---------------------------------------------------------------------------
# Download HGL features from EIA FeatureServer
# ---------------------------------------------------------------------------
def _fetch_hgl() -> list[dict]:
    features = []
    offset = 0
    batch = 500
    while True:
        url = (
            f"{HGL_URL}/query?where=1%3D1"
            "&outFields=Opername,Pipename"
            "&geometryType=esriGeometryPolyline"
            "&outSR=4326"
            "&f=json"
            f"&resultRecordCount={batch}"
            f"&resultOffset={offset}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        batch_feats = d.get("features", [])
        for feat in batch_feats:
            a = feat.get("attributes", {})
            geom = feat.get("geometry", {})
            paths = geom.get("paths", [])
            converted: list[list[list[float]]] = []
            for path in paths:
                seg = [[pt[1], pt[0]] for pt in path if len(pt) >= 2]
                if len(seg) >= 2:
                    converted.append(seg)
            if converted:
                features.append({
                    "opername": (a.get("Opername") or "").strip(),
                    "pipename": (a.get("Pipename") or "").strip(),
                    "paths": converted,
                })
        print(f"  offset={offset}: {len(batch_feats)} records", flush=True)
        if len(batch_feats) < batch or not d.get("exceededTransferLimit"):
            break
        offset += batch
    return features


def _group_features(
    features: list[dict],
) -> dict[tuple[str, str], list[list[list[float]]]]:
    by_key: dict[tuple[str, str], list[list[list[float]]]] = defaultdict(list)
    for feat in features:
        by_key[(feat["opername"], feat["pipename"])].extend(feat["paths"])
    return dict(by_key)


# ---------------------------------------------------------------------------
# Haversine distance (km) between two [lat,lon] points
# ---------------------------------------------------------------------------
def _haversine(a: list[float], b: list[float]) -> float:
    import math
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(h ** 0.5)


def _path_km(segs: list[list[list[float]]]) -> float:
    total = 0.0
    for seg in segs:
        for i in range(len(seg) - 1):
            total += _haversine(seg[i], seg[i + 1])
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== EIA HGL (NGL/Y-grade) Pipeline Routes Ingest ===\n")

    print("Downloading HGL pipeline features from EIA ArcGIS...")
    features = _fetch_hgl()
    print(f"  {len(features)} HGL segments downloaded")

    groups = _group_features(features)
    print(f"  {len(groups)} unique operator+name groups\n")

    # Resolve manual overrides to WM IDs with segments
    wm_routes: dict[str, list[list[list[float]]]] = defaultdict(list)

    print("Matching to WM pipeline IDs via manual overrides...")
    matched_count = 0
    for (opername, pipename), segs in groups.items():
        wm_ids = _MANUAL.get((opername, pipename))
        if wm_ids is None:
            continue
        if not wm_ids:
            continue
        for wm_id in wm_ids:
            wm_routes[wm_id].extend(segs)
            matched_count += 1
            print(f"  {wm_id}  <-  ({opername!r}, {pipename!r})")

    print(f"\nMatched: {matched_count} WM routes\n")

    print("Simplifying geometry...")
    simplified: dict[str, list[list[list[float]]]] = {}
    for wm_id, segs in wm_routes.items():
        simp = _simplify_segments(segs)
        if not simp:
            continue
        n_pts_before = sum(len(s) for s in segs)
        n_pts_after = sum(len(s) for s in simp)
        km = _path_km(simp)
        print(f"  {wm_id}: {len(segs)} segs, {n_pts_before} pts -> {n_pts_after} pts, {km:.0f} km")
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
        route_json = json.dumps(segs)
        con.execute(
            """
            INSERT OR REPLACE INTO eia_oil_pipeline_routes (wm_id, n_segments, n_points, route_json)
            VALUES (?, ?, ?, ?)
            """,
            [wm_id, len(segs), n_pts, route_json],
        )
        stored += 1
    con.close()

    total = duckdb.connect(args.db, read_only=True).execute(
        "SELECT COUNT(*) FROM eia_oil_pipeline_routes"
    ).fetchone()[0]
    print(f"\nStored {stored} new routes.")
    print(f"Total eia_oil_pipeline_routes: {total}")
    print("\nDone. Restart freight-api to pick up changes.")


if __name__ == "__main__":
    main()
