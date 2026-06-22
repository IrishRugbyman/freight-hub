"""Straight-line (2-point) fallback routes for WM pipelines that have no
georeferenced path data from any external source.

Reads start_lat/start_lon/end_lat/end_lon from pipeline_registry and stores a
2-segment route (just the two terminal coordinates) in eia_oil_pipeline_routes.
These are clearly low-fidelity approximations but enable proximity analysis for
pipelines that are otherwise unmapped.

Pipelines excluded:
  - Identical start/end coords (placeholder data): Cameron Highway, Zydeco
  - Very long systems (>3000 km) where a straight line would be highly misleading:
    HPL System (6116 km), Tejas Gas (5221 km)
  - Already fully routed via another source
  - Keystone XL (cancelled 2021, never built)

Usage:
    cd backend
    .venv/bin/python ingest_wm_straightline_routes.py [--db <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import duckdb
import psycopg2

DB_DEFAULT = Path(__file__).parent / "data" / "freight_analytics.duckdb"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "market-data" / "src"))

# WM pipeline IDs to generate straight-line routes for.
# Exclusion criteria documented in module docstring.
_TARGETS = [
    "bangl-pipeline-us",
    "capline-oil-pipeline-patoka-to-catlettsburg-expansion-us",
    "eaglebine-express-crude-oil-pipeline-us",
    "heavy-louisiana-sweet-crude-oil-pipeline-system-us",
    "hobbs-east-gathering-system-rio-grande-pipeline-us",
    "kpc-gas-pipeline-us",
    "lone-star-express-y-grade-pipeline-us",
    "lone-star-express-y-grade-pipeline-expansion-us",
    "matterhorn-express-gas-pipeline-us",
    "poseidon-oil-pipeline-us",
    "sunrise-pipeline-system-us",
]


def _haversine(a: list[float], b: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(h ** 0.5)


def _load_wm_coords() -> dict[str, dict]:
    """Fetch start/end coords from pipeline_registry for target IDs."""
    conn = psycopg2.connect("postgresql:///market_data")
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, start_lat, start_lon, end_lat, end_lon, length_km"
        " FROM pipeline_registry WHERE id = ANY(%s)",
        [_TARGETS],
    )
    rows = {
        row[0]: {
            "name": row[1],
            "start_lat": row[2],
            "start_lon": row[3],
            "end_lat": row[4],
            "end_lon": row[5],
            "length_km": row[6],
        }
        for row in cur.fetchall()
    }
    conn.close()
    return rows


def _skip_already_routed(targets: list[str], db_path: str) -> list[str]:
    """Return subset of targets not yet in eia_oil_pipeline_routes."""
    try:
        con = duckdb.connect(db_path, read_only=True)
        existing = {
            row[0]
            for row in con.execute("SELECT wm_id FROM eia_oil_pipeline_routes").fetchall()
        }
        con.close()
    except Exception:
        existing = set()
    return [t for t in targets if t not in existing]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== WM Straight-Line Fallback Route Ingest ===\n")

    coords = _load_wm_coords()
    unrouted = _skip_already_routed(_TARGETS, args.db)
    print(f"Targets: {len(_TARGETS)}  |  already routed: {len(_TARGETS) - len(unrouted)}  |  to ingest: {len(unrouted)}\n")

    routes: list[tuple[str, list[list[list[float]]]]] = []
    for wm_id in unrouted:
        info = coords.get(wm_id)
        if not info:
            print(f"  SKIP {wm_id}: not found in pipeline_registry")
            continue
        s_lat, s_lon = info["start_lat"], info["start_lon"]
        e_lat, e_lon = info["end_lat"], info["end_lon"]
        if None in (s_lat, s_lon, e_lat, e_lon):
            print(f"  SKIP {wm_id}: missing coordinates")
            continue
        dist = _haversine([s_lat, s_lon], [e_lat, e_lon])
        if dist < 1.0:
            print(f"  SKIP {wm_id}: start==end (bad data, {dist:.1f} km)")
            continue
        seg = [[s_lat, s_lon], [e_lat, e_lon]]
        routes.append((wm_id, [seg]))
        print(f"  {wm_id}")
        print(f"    {info['name']} | straight-line {dist:.0f} km (WM length: {info['length_km']} km)")

    print(f"\nRoutes to store: {len(routes)}")
    if args.dry_run:
        print("[dry-run] No data written.")
        return

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
    for wm_id, segs in routes:
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
    print(f"\nStored {stored} straight-line routes.")
    print(f"Total eia_oil_pipeline_routes: {total}")
    print("\nDone. Restart freight-api to pick up changes.")


if __name__ == "__main__":
    main()
