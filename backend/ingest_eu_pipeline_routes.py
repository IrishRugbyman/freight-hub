"""Ingest European pipeline route geometry from SciGRID_gas IGGIELGN dataset.

Downloads the IGGIELGN.zip from Zenodo, builds a graph from pipeline segments,
then runs Dijkstra shortest-path routing for each World Monitor EU pipeline using
its start/end coordinates. Stores full polyline routes in eu_pipeline_routes
table in freight_analytics.duckdb.

Usage:
    cd backend
    uv run python ingest_eu_pipeline_routes.py
    # or re-run to refresh (idempotent, overwrites table)
"""

from __future__ import annotations

import heapq
import json
import math
import sys
import tempfile
import zipfile
from pathlib import Path

ANALYTICS_DB = Path(__file__).parent / "data" / "freight_analytics.duckdb"
IGGIELGN_URL = "https://zenodo.org/api/records/4767098/files/IGGIELGN.zip/content"
TMP_DIR = Path(__file__).parent / "data" / "eu_pipelines_tmp"

# ---- Haversine ----

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---- RDP simplification (same as EIA ingest) ----

def _perp_dist(p: tuple, a: tuple, b: tuple) -> float:
    if a == b:
        return _haversine_km(p[0], p[1], a[0], a[1])
    ax, ay = a[1], a[0]
    bx, by = b[1], b[0]
    px, py = p[1], p[0]
    ab2 = (bx - ax) ** 2 + (by - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / ab2))
    qx, qy = ax + t * (bx - ax), ay + t * (by - ay)
    return math.sqrt((px - qx) ** 2 + (py - qy) ** 2)


def _rdp(pts: list[tuple], eps: float) -> list[tuple]:
    if len(pts) <= 2:
        return pts
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = _perp_dist(pts[i], pts[0], pts[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = _rdp(pts[: idx + 1], eps)
        right = _rdp(pts[idx:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def simplify_polyline(coords: list[tuple], epsilon: float = 0.02) -> list[tuple]:
    """RDP simplification; coords are (lat, lon) tuples."""
    if len(coords) <= 2:
        return coords
    return _rdp(coords, epsilon)


# ---- Build graph from IGGIELGN ----

def load_iggielgn(geojson_path: Path) -> dict:
    """Parse IGGIELGN_PipeSegments.geojson into a routing graph.

    Returns dict with:
      'endpoints': list of (lat, lon) tuples (all unique segment endpoints)
      'adj': adjacency list {endpoint_idx: [(neighbor_idx, seg_idx, dist_km)]}
      'segments': list of {start_idx, end_idx, coords [(lat,lon),...], km}
    """
    with open(geojson_path) as f:
        gj = json.load(f)

    feats = gj["features"]

    # Build endpoint registry
    ep_to_idx: dict[tuple, int] = {}
    endpoints: list[tuple] = []

    def get_ep(lon: float, lat: float) -> int:
        key = (round(lon, 6), round(lat, 6))
        if key not in ep_to_idx:
            ep_to_idx[key] = len(endpoints)
            endpoints.append((lat, lon))  # store as (lat, lon)
        return ep_to_idx[key]

    segments = []
    adj: dict[int, list] = {}

    for feat in feats:
        geom_coords = feat["geometry"]["coordinates"]  # [[lon,lat], [lon,lat]]
        param = feat["properties"].get("param", {})

        lon_s, lat_s = geom_coords[0]
        lon_e, lat_e = geom_coords[1]

        si = get_ep(lon_s, lat_s)
        ei = get_ep(lon_e, lat_e)

        if si == ei:
            continue  # degenerate zero-length segment

        # Build detailed coordinate list: start + intermediates + end
        path_lat = param.get("path_lat", [])
        path_lon = param.get("path_long", [])

        coords: list[tuple] = [(lat_s, lon_s)]
        if path_lat:
            for la, lo in zip(path_lat, path_lon):
                coords.append((la, lo))
        coords.append((lat_e, lon_e))

        dist_km = param.get("length_km") or _haversine_km(lat_s, lon_s, lat_e, lon_e)

        seg_idx = len(segments)
        segments.append({"start": si, "end": ei, "coords": coords, "km": dist_km})

        adj.setdefault(si, []).append((ei, seg_idx, dist_km))
        adj.setdefault(ei, []).append((si, seg_idx, dist_km))

    print(f"  Graph: {len(endpoints)} nodes, {len(segments)} edges")
    return {"endpoints": endpoints, "adj": adj, "segments": segments}


# ---- KD-tree for nearest-node lookup ----

def _build_kdtree(pts: list[tuple]) -> list:
    """Simple k-d tree on (lat, lon) pairs. Returns the root node."""
    # Each node: [point_idx, left, right]
    def build(idxs: list[int], depth: int):
        if not idxs:
            return None
        axis = depth % 2  # 0=lat, 1=lon
        idxs.sort(key=lambda i: pts[i][axis])
        mid = len(idxs) // 2
        return [idxs[mid], build(idxs[:mid], depth + 1), build(idxs[mid + 1:], depth + 1)]
    return build(list(range(len(pts))), 0)


def _kd_nearest(tree, pts: list[tuple], query: tuple):
    """Find index of nearest point in pts to query (lat, lon)."""
    best = [None, float("inf")]

    def search(node, depth):
        if node is None:
            return
        idx, left, right = node
        pt = pts[idx]
        d = (pt[0] - query[0]) ** 2 + (pt[1] - query[1]) ** 2
        if d < best[1]:
            best[0] = idx
            best[1] = d
        axis = depth % 2
        diff = query[axis] - pt[axis]
        first, second = (left, right) if diff <= 0 else (right, left)
        search(first, depth + 1)
        if diff ** 2 < best[1]:
            search(second, depth + 1)

    search(tree, 0)
    return best[0]


# ---- Dijkstra ----

def dijkstra(adj: dict, start: int, end: int) -> list[int] | None:
    """Return list of segment indices forming shortest path start->end, or None."""
    dist = {start: 0.0}
    prev: dict[int, tuple | None] = {start: None}  # node -> (prev_node, seg_idx)
    heap = [(0.0, start)]

    while heap:
        d, u = heapq.heappop(heap)
        if u == end:
            # Reconstruct path
            path = []
            cur = end
            while prev[cur] is not None:
                pnode, seg_idx = prev[cur]
                path.append(seg_idx)
                cur = pnode
            return list(reversed(path))
        if d > dist.get(u, float("inf")):
            continue
        for v, seg_idx, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, seg_idx)
                heapq.heappush(heap, (nd, v))
    return None


# ---- Reconstruct polyline from path ----

def path_to_coords(path: list[int], segments: list[dict], start_node: int) -> list[tuple]:
    """Build an ordered (lat, lon) polyline from a list of segment indices."""
    coords: list[tuple] = []
    cur_node = start_node

    for seg_idx in path:
        seg = segments[seg_idx]
        if seg["start"] == cur_node:
            seg_coords = seg["coords"]
            cur_node = seg["end"]
        else:
            seg_coords = list(reversed(seg["coords"]))
            cur_node = seg["start"]

        if coords:
            # Skip first point to avoid duplicate at junction
            coords.extend(seg_coords[1:])
        else:
            coords.extend(seg_coords)

    return coords


# ---- WM pipeline loading ----

def load_wm_eu_pipelines() -> list[dict]:
    """Load World Monitor EU pipelines with start/end coordinates."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "market-data"))
    from loaders.worldmonitor import load_pipeline_registry

    df = load_pipeline_registry()
    df = df.dropna(subset=["start_lat", "start_lon", "end_lat", "end_lon"])

    eu_countries = {
        "DE", "FR", "GB", "IT", "ES", "NL", "BE", "AT", "CH", "PL", "CZ", "SK",
        "HU", "RO", "BG", "GR", "RS", "HR", "TR", "NO", "SE", "DK", "FI", "PT",
        "UA", "BY", "MD", "AL", "MK", "BA", "ME", "SI", "LT", "LV", "EE", "LU",
        "IE", "MT", "CY", "AZ", "GE", "DZ", "LY", "RU",
    }
    mask = df["from_country"].isin(eu_countries) | df["to_country"].isin(eu_countries)
    eu = df[mask]

    result = []
    for _, row in eu.iterrows():
        result.append(
            {
                "wm_id": row["id"],
                "name": row["name"],
                "from_country": row["from_country"],
                "to_country": row["to_country"],
                "start_lat": float(row["start_lat"]),
                "start_lon": float(row["start_lon"]),
                "end_lat": float(row["end_lat"]),
                "end_lon": float(row["end_lon"]),
            }
        )
    return result


# ---- Download helper ----

def download_iggielgn(tmp_dir: Path) -> Path:
    """Download and extract IGGIELGN_PipeSegments.geojson; return path to file."""
    geojson_path = tmp_dir / "IGGIELGN_PipeSegments.geojson"
    if geojson_path.exists():
        print(f"Using cached {geojson_path}")
        return geojson_path

    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / "IGGIELGN.zip"

    import urllib.request
    print(f"Downloading IGGIELGN.zip from Zenodo (~21 MB)...")
    urllib.request.urlretrieve(IGGIELGN_URL, zip_path)
    print(f"  Downloaded {zip_path.stat().st_size // 1024 // 1024} MB")

    with zipfile.ZipFile(zip_path) as zf:
        # The file is at data/IGGIELGN_PipeSegments.geojson inside the zip
        zf.extract("data/IGGIELGN_PipeSegments.geojson", tmp_dir)

    extracted = tmp_dir / "data" / "IGGIELGN_PipeSegments.geojson"
    extracted.rename(geojson_path)
    zip_path.unlink(missing_ok=True)
    (tmp_dir / "data").rmdir()

    return geojson_path


# ---- Main ----

def main():
    import duckdb

    print("=== EU Pipeline Route Ingest (IGGIELGN Dijkstra routing) ===\n")

    # Download/load IGGIELGN
    geojson_path = download_iggielgn(TMP_DIR)

    # Load graph
    print("Building routing graph...")
    graph = load_iggielgn(geojson_path)
    endpoints = graph["endpoints"]
    adj = graph["adj"]
    segments = graph["segments"]

    # Build KD-tree for nearest-node lookup
    print("Building KD-tree...")
    kdtree = _build_kdtree(endpoints)

    # Load WM EU pipelines
    print("Loading WM EU pipelines...")
    wm_pipes = load_wm_eu_pipelines()
    print(f"  {len(wm_pipes)} EU pipelines with coordinates")

    # Route each pipeline
    results = []
    skipped = 0

    for pipe in wm_pipes:
        wm_id = pipe["wm_id"]
        start = (pipe["start_lat"], pipe["start_lon"])
        end = (pipe["end_lat"], pipe["end_lon"])

        # Find nearest graph nodes
        si = _kd_nearest(kdtree, endpoints, start)
        ei = _kd_nearest(kdtree, endpoints, end)

        if si is None or ei is None or si == ei:
            skipped += 1
            continue

        start_node_pt = endpoints[si]
        end_node_pt = endpoints[ei]
        dist_to_start = _haversine_km(start[0], start[1], start_node_pt[0], start_node_pt[1])
        dist_to_end = _haversine_km(end[0], end[1], end_node_pt[0], end_node_pt[1])

        # Skip if nearest nodes are very far from WM coords (data gap)
        MAX_SNAP_KM = 150
        if dist_to_start > MAX_SNAP_KM and dist_to_end > MAX_SNAP_KM:
            print(f"  SKIP {wm_id}: snap dist too large ({dist_to_start:.0f}km to start, {dist_to_end:.0f}km to end)")
            skipped += 1
            continue

        # Run Dijkstra
        path = dijkstra(adj, si, ei)

        if path is None:
            print(f"  SKIP {wm_id}: no path found in graph")
            skipped += 1
            continue

        # Build coordinate list
        coords = path_to_coords(path, segments, si)

        if len(coords) < 2:
            skipped += 1
            continue

        # Simplify
        coords = simplify_polyline(coords, epsilon=0.02)

        # Compute path length
        path_km = sum(segments[s]["km"] for s in path)

        # store as single-segment route (one polyline, not multiple segments)
        route_json = json.dumps([coords])

        results.append(
            {
                "wm_id": wm_id,
                "n_points": len(coords),
                "path_km": round(path_km, 1),
                "snap_km_start": round(dist_to_start, 1),
                "snap_km_end": round(dist_to_end, 1),
                "route_json": route_json,
            }
        )
        print(
            f"  OK  {wm_id}: {len(path)} segs -> {len(coords)} pts, {path_km:.0f} km"
            f" (snap {dist_to_start:.0f}/{dist_to_end:.0f} km)"
        )

    print(f"\nRouted: {len(results)}, skipped: {skipped}")

    # Write to DuckDB
    print(f"\nWriting to {ANALYTICS_DB}...")
    con = duckdb.connect(str(ANALYTICS_DB))
    con.execute("""
        CREATE OR REPLACE TABLE eu_pipeline_routes (
            wm_id        TEXT PRIMARY KEY,
            n_points     INTEGER,
            path_km      REAL,
            snap_km_start REAL,
            snap_km_end  REAL,
            route_json   TEXT
        )
    """)
    for r in results:
        con.execute(
            "INSERT INTO eu_pipeline_routes VALUES (?,?,?,?,?,?)",
            [r["wm_id"], r["n_points"], r["path_km"], r["snap_km_start"], r["snap_km_end"], r["route_json"]],
        )
    con.close()
    print(f"Stored {len(results)} EU pipeline routes.")

    # Cleanup tmp dir
    import shutil
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
        print(f"Cleaned up {TMP_DIR}")


if __name__ == "__main__":
    main()
