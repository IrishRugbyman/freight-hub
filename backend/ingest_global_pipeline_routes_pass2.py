"""Second-pass global pipeline routing using merged super-region graphs.

The first pass (ingest_global_pipeline_routes.py) failed on trans-regional
pipelines (e.g. Enbridge Mainline, TAPS, Trans-Mountain) because each OSM
query covers only one sub-region while those pipelines span two or more.

This script merges all sub-region OSM downloads into super-region graphs and
re-routes only the pipelines that still lack a route. Results are written to
the existing global_pipeline_routes table (INSERT OR REPLACE).

Usage:
    cd backend
    .venv/bin/python ingest_global_pipeline_routes_pass2.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

# Import shared utilities from the first-pass script
sys.path.insert(0, str(Path(__file__).parent))
from ingest_global_pipeline_routes import (
    ANALYTICS_DB,
    MAX_SNAP_KM,
    _wait_for_slot,
    _overpass_query,
    _OIL_GAS_FILTER,
    fetch_pipeline_ways,
    build_graph,
    route_pipelines_on_graph,
    simplify,
    _haversine_km,
    load_unrouted_wm,
)

import duckdb

# Super-regions: list of (name, list of (south,west,north,east) bbox tuples)
# Each super-region's ways are merged before routing.
SUPER_REGIONS = [
    (
        "north_america",
        [
            (48.0, -170.0, 73.0, -100.0),   # alaska_canada_w
            (42.0, -100.0,  65.0,  -52.0),   # canada_east
            (24.0, -130.0,  50.0, -100.0),   # us_west
            (24.0, -100.0,  50.0,  -86.0),   # us_central
            (24.0,  -86.0,  50.0,  -55.0),   # us_east
            (5.0,   -95.0,  34.0,  -75.0),   # mexico_ca (only the Canadian/US part)
        ],
    ),
    (
        "south_america",
        [
            (-5.0, -85.0,  15.0, -50.0),   # north
            (-60.0, -80.0,  -5.0, -30.0),   # south
        ],
    ),
    (
        "middle_east",
        [
            (10.0, 24.0, 35.0, 60.0),   # gulf_states
            (25.0, 44.0, 42.0, 65.0),   # iran_east
        ],
    ),
    (
        "asia",
        [
            (45.0,  55.0, 75.0, 100.0),   # russia_east
            (40.0,  90.0, 75.0, 180.0),   # russia_far_east
            (30.0,  55.0, 50.0,  80.0),   # central_asia_e
            ( 5.0,  60.0, 37.0, 100.0),   # south_asia
            (-15.0, 90.0, 25.0, 145.0),   # southeast_asia
            (15.0,  95.0, 55.0, 145.0),   # east_asia
        ],
    ),
    (
        "africa",
        [
            (10.0, -18.0, 38.0, 25.0),   # north
            (-35.0,  24.0, 15.0, 55.0),  # east
            (-10.0, -18.0, 20.0, 25.0),  # west
        ],
    ),
    (
        "oceania",
        [
            (-50.0, 105.0, 0.0, 180.0),  # australia
        ],
    ),
]


def fetch_all_ways_for_super_region(bboxes: list[tuple]) -> list[dict]:
    """Download and merge pipeline ways for all bboxes in a super-region."""
    all_ways: list[dict] = []
    for i, (s, w, n, e) in enumerate(bboxes):
        print(f"  [{i+1}/{len(bboxes)}] bbox ({s},{w},{n},{e}) ...", flush=True)
        time.sleep(5)
        ways = fetch_pipeline_ways(s, w, n, e)
        print(f"    Got {len(ways)} ways", flush=True)
        all_ways.extend(ways)
    return all_ways


def main():
    print("=== Global Pipeline Routes Pass 2 (super-region merged graphs) ===\n", flush=True)

    # Load all unrouted WM pipelines
    all_unrouted = load_unrouted_wm()
    print(f"Unrouted WM pipelines: {len(all_unrouted)}", flush=True)

    # Filter to only those still missing from global_pipeline_routes
    con = duckdb.connect(str(ANALYTICS_DB))
    already = set(r[0] for r in con.execute("SELECT wm_id FROM global_pipeline_routes").fetchall())
    con.close()

    # Also exclude those with EU routes
    con2 = duckdb.connect(str(ANALYTICS_DB), read_only=True)
    eu_done = set(r[0] for r in con2.execute("SELECT wm_id FROM eu_pipeline_routes").fetchall())
    con2.close()

    pending = [p for p in all_unrouted if p["wm_id"] not in already and p["wm_id"] not in eu_done]
    print(f"Still pending after pass 1 + EU: {len(pending)}", flush=True)

    total_stored = 0

    for super_name, bboxes in SUPER_REGIONS:
        # Determine which pending pipelines fall into this super-region
        # (union of all bboxes with 3-degree pad)
        min_lat = min(b[0] for b in bboxes) - 3
        max_lat = max(b[2] for b in bboxes) + 3
        min_lon = min(b[1] for b in bboxes) - 3
        max_lon = max(b[3] for b in bboxes) + 3

        regional = [
            p for p in pending
            if (
                (min_lat <= p["start_lat"] <= max_lat and min_lon <= p["start_lon"] <= max_lon)
                or (min_lat <= p["end_lat"] <= max_lat and min_lon <= p["end_lon"] <= max_lon)
            )
        ]

        if not regional:
            print(f"\n[{super_name}] no pending pipelines", flush=True)
            continue

        print(f"\n[{super_name}] {len(regional)} pipelines to route", flush=True)

        ways = fetch_all_ways_for_super_region(bboxes)
        if len(ways) < 10:
            print(f"  Too sparse ({len(ways)} ways) - skip", flush=True)
            continue

        graph = build_graph(ways)
        print(f"  Combined graph: {len(graph['endpoints'])} nodes, {len(graph['ways'])} ways", flush=True)

        results = route_pipelines_on_graph(graph, regional)
        print(f"  Routed: {len(results)}/{len(regional)}", flush=True)

        if not results:
            continue

        con_w = duckdb.connect(str(ANALYTICS_DB))
        for r in results:
            print(
                f"    OK {r['wm_id']}: {r['n_points']} pts, {r['path_km']:.0f} km"
                f" (snap {r['snap_km_start']:.0f}/{r['snap_km_end']:.0f} km)",
                flush=True,
            )
            try:
                con_w.execute(
                    "INSERT OR REPLACE INTO global_pipeline_routes VALUES (?,?,?,?,?,?)",
                    [r["wm_id"], r["n_points"], r["path_km"], r["snap_km_start"], r["snap_km_end"], r["route_json"]],
                )
                total_stored += 1
            except Exception as exc:
                print(f"  WARN: {r['wm_id']}: {exc}")
        con_w.close()

        routed_ids = {r["wm_id"] for r in results}
        pending = [p for p in pending if p["wm_id"] not in routed_ids]

    print(f"\nPass 2 complete: {total_stored} new routes stored.", flush=True)
    print(f"Remaining unrouted: {len(pending)}", flush=True)


if __name__ == "__main__":
    main()
