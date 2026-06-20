"""Ingest global pipeline route geometry from OpenStreetMap via Overpass API.

Queries oil and gas pipeline ways from OSM for world regions not covered by
IGGIELGN (EU) or EIA (US gas). Builds a routing graph per region and runs
Dijkstra shortest-path routing for each unrouted World Monitor pipeline using
its WM start/end coordinates as snap points.

Also handles the 39 RexTag-only US pipelines without EIA routes by querying OSM
by name directly.

Results stored in global_pipeline_routes table in freight_analytics.duckdb.

Usage:
    cd backend
    .venv/bin/python ingest_global_pipeline_routes.py
"""

from __future__ import annotations

import heapq
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ANALYTICS_DB = Path(__file__).parent / "data" / "freight_analytics.duckdb"

# World regions to query via Overpass (south, west, north, east)
# Excludes Europe/Russia-west/N.Africa which are already covered by IGGIELGN.
# Split large regions to stay within Overpass response limits.
REGIONS = [
    # Middle East
    ("gulf_states",     10.0,  24.0,  35.0,  60.0),
    ("iran_east",       25.0,  44.0,  42.0,  65.0),
    # Russia east (IGGIELGN only covers to ~60E)
    ("russia_east",     45.0,  55.0,  75.0, 100.0),
    ("russia_far_east", 40.0,  90.0,  75.0, 180.0),
    # Central + South Asia
    ("central_asia_e",  30.0,  55.0,  50.0,  80.0),
    ("south_asia",       5.0,  60.0,  37.0, 100.0),
    # Southeast + East Asia
    ("southeast_asia", -15.0,  90.0,  25.0, 145.0),
    ("east_asia",       15.0,  95.0,  55.0, 145.0),
    # Africa
    ("africa_north",    10.0, -18.0,  38.0,  25.0),
    ("africa_east",    -35.0,  24.0,  15.0,  55.0),
    ("africa_west",    -10.0, -18.0,  20.0,  25.0),
    # Americas - North
    ("alaska_canada_w",  48.0, -170.0, 73.0, -100.0),
    ("canada_east",      42.0, -100.0, 65.0,  -52.0),
    ("us_west",          24.0, -130.0, 50.0,  -100.0),
    ("us_central",       24.0, -100.0, 50.0,   -86.0),
    ("us_east",          24.0,  -86.0, 50.0,   -55.0),
    ("mexico_central_am", 5.0,  -95.0, 34.0,   -75.0),
    # South America
    ("south_america_n",   -5.0, -85.0, 15.0,  -50.0),
    ("south_america_s",  -60.0, -80.0,  -5.0,  -30.0),
    # Oceania
    ("australia",        -50.0, 105.0,   0.0, 180.0),
]

# OSM substance/pipeline-type filters that indicate energy pipelines
_OIL_GAS_FILTER = "~\"gas|oil|petroleum|crude|fuel|lpg|ngl|condensate\""


# ---- Haversine ----

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


# ---- RDP simplification ----

def _perp_dist(p: tuple, a: tuple, b: tuple) -> float:
    if a == b:
        return _haversine_km(p[0], p[1], a[0], a[1])
    ax, ay = a[1], a[0]
    bx, by = b[1], b[0]
    px, py = p[1], p[0]
    ab2 = (bx - ax) ** 2 + (by - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / ab2))
    qx = ax + t * (bx - ax)
    qy = ay + t * (by - ay)
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


def simplify(coords: list[tuple], epsilon: float = 0.02) -> list[tuple]:
    return _rdp(coords, epsilon) if len(coords) > 2 else coords


# ---- Overpass query ----

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
_ep_idx = 0


def _wait_for_slot(max_wait: int = 120) -> None:
    """Poll the Overpass status endpoint until a slot is available."""
    waited = 0
    while waited < max_wait:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "10", "https://overpass-api.de/api/status"],
            capture_output=True, text=True,
        )
        if "Slot available after:" in r.stdout:
            # Extract seconds to wait
            for line in r.stdout.splitlines():
                if "in " in line and " seconds" in line:
                    try:
                        secs = int(line.split("in ")[1].split(" ")[0])
                        wait = min(secs + 2, 60)
                        print(f"  [rate-limit] waiting {wait}s for Overpass slot...")
                        time.sleep(wait)
                        waited += wait
                    except (ValueError, IndexError):
                        time.sleep(15)
                        waited += 15
                    break
        else:
            return  # slot available
    print("  [rate-limit] gave up waiting, proceeding anyway")


def _overpass_query(ql: str, timeout_s: int = 180) -> list[dict] | None:
    """Run an Overpass QL query via curl; return elements list or None on failure."""
    global _ep_idx
    for attempt in range(3):
        endpoint = _OVERPASS_ENDPOINTS[_ep_idx % len(_OVERPASS_ENDPOINTS)]
        result = subprocess.run(
            ["curl", "-s", f"--max-time", str(timeout_s + 15), "-X", "POST", endpoint, "--data", ql],
            capture_output=True,
            text=True,
        )
        raw = result.stdout.strip()
        if not raw:
            _ep_idx += 1
            time.sleep(5)
            continue
        if "rate_limited" in raw or "<!DOCTYPE" in raw:
            _wait_for_slot()
            _ep_idx += 1  # rotate endpoint
            continue
        try:
            data = json.loads(raw)
            return data.get("elements", [])
        except json.JSONDecodeError:
            _ep_idx += 1
            time.sleep(5)
    return None


def fetch_pipeline_ways(south: float, west: float, north: float, east: float) -> list[dict]:
    """Fetch gas/oil pipeline ways from OSM for a bounding box.

    Returns list of dicts: {coords: [(lat,lon),...], km: float, name: str|None}.
    """
    bbox = f"{south},{west},{north},{east}"
    ql = (
        f"[out:json][timeout:180];"
        f"("
        f"way[\"man_made\"=\"pipeline\"][\"substance\"{_OIL_GAS_FILTER}]({bbox});"
        f"way[\"man_made\"=\"pipeline\"][\"pipeline\"{_OIL_GAS_FILTER}]({bbox});"
        f");"
        f"out geom;"
    )
    elements = _overpass_query(ql, timeout_s=180)
    if elements is None:
        return []

    ways = []
    for e in elements:
        if e.get("type") != "way":
            continue
        geom = e.get("geometry", [])
        if len(geom) < 2:
            continue
        coords = [(g["lat"], g["lon"]) for g in geom]
        km = sum(
            _haversine_km(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
            for i in range(len(coords) - 1)
        )
        name = e.get("tags", {}).get("name")
        ways.append({"coords": coords, "km": km, "name": name})
    return ways


# ---- Build routing graph ----

def build_graph(ways: list[dict]) -> dict:
    """Build adjacency graph from OSM ways.

    Returns: {endpoints: [(lat,lon)], adj: {idx: [(nb_idx, way_idx, km)]},
              ways: [way_dict]}
    """
    ep_to_idx: dict[tuple, int] = {}
    endpoints: list[tuple] = []

    def get_ep(lat: float, lon: float) -> int:
        key = (round(lat, 5), round(lon, 5))
        if key not in ep_to_idx:
            ep_to_idx[key] = len(endpoints)
            endpoints.append(key)
        return ep_to_idx[key]

    adj: dict[int, list] = {}

    for wi, way in enumerate(ways):
        coords = way["coords"]
        si = get_ep(coords[0][0], coords[0][1])
        ei = get_ep(coords[-1][0], coords[-1][1])
        if si == ei:
            continue
        km = way["km"] or 1.0
        adj.setdefault(si, []).append((ei, wi, km))
        adj.setdefault(ei, []).append((si, wi, km))

    return {"endpoints": endpoints, "adj": adj, "ways": ways}


# ---- KD-tree ----

def build_kdtree(pts: list[tuple]) -> list:
    def build(idxs: list[int], depth: int):
        if not idxs:
            return None
        axis = depth % 2
        idxs.sort(key=lambda i: pts[i][axis])
        mid = len(idxs) // 2
        return [idxs[mid], build(idxs[:mid], depth + 1), build(idxs[mid + 1 :], depth + 1)]
    return build(list(range(len(pts))), 0)


def kd_nearest(tree, pts: list[tuple], query: tuple) -> int | None:
    if tree is None:
        return None
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
    dist = {start: 0.0}
    prev: dict[int, tuple | None] = {start: None}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == end:
            path = []
            cur = end
            while prev[cur] is not None:
                pnode, wi = prev[cur]
                path.append(wi)
                cur = pnode
            return list(reversed(path))
        if d > dist.get(u, float("inf")):
            continue
        for v, wi, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, wi)
                heapq.heappush(heap, (nd, v))
    return None


def path_to_coords(path: list[int], ways: list[dict], start_node_idx: int, endpoints: list[tuple]) -> list[tuple]:
    coords: list[tuple] = []
    cur_ep = endpoints[start_node_idx]

    for wi in path:
        way = ways[wi]
        wc = way["coords"]
        first_ep = (round(wc[0][0], 5), round(wc[0][1], 5))
        cur_ep_r = (round(cur_ep[0], 5), round(cur_ep[1], 5))
        if first_ep == cur_ep_r:
            seg = wc
            cur_ep = wc[-1]
        else:
            seg = list(reversed(wc))
            cur_ep = wc[0]
        if coords:
            coords.extend(seg[1:])
        else:
            coords.extend(seg)

    return coords


# ---- Load unrouted WM pipelines ----

def load_unrouted_wm() -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "market-data"))
    from loaders.worldmonitor import load_pipelines_for_map

    df = load_pipelines_for_map(disrupted_only=False)
    unrouted = df[df["route_json"].isna()].copy()

    result = []
    for _, row in unrouted.iterrows():
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


def load_unrouted_rextag() -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared" / "market-data"))
    from loaders.worldmonitor import load_rextag_us_only_pipelines

    df = load_rextag_us_only_pipelines()
    unrouted = df[df["route_json"].isna()]
    result = []
    for _, row in unrouted.iterrows():
        # id column holds the rextag slug; name is the pipeline name
        result.append({"slug": str(row["id"]), "name": str(row["name"])})
    return result


# ---- Main routing loop ----

MAX_SNAP_KM = 200


def route_pipelines_on_graph(graph: dict, pipes: list[dict]) -> list[dict]:
    """Route a list of pipelines on a pre-built graph. Returns matched routes."""
    endpoints = graph["endpoints"]
    adj = graph["adj"]
    ways = graph["ways"]

    if not endpoints:
        return []

    tree = build_kdtree(endpoints)
    results = []

    for pipe in pipes:
        start = (pipe["start_lat"], pipe["start_lon"])
        end = (pipe["end_lat"], pipe["end_lon"])

        si = kd_nearest(tree, endpoints, start)
        ei = kd_nearest(tree, endpoints, end)

        if si is None or ei is None or si == ei:
            continue

        ds = _haversine_km(start[0], start[1], endpoints[si][0], endpoints[si][1])
        de = _haversine_km(end[0], end[1], endpoints[ei][0], endpoints[ei][1])

        if ds > MAX_SNAP_KM and de > MAX_SNAP_KM:
            continue

        path = dijkstra(adj, si, ei)
        if path is None:
            continue

        coords = path_to_coords(path, ways, si, endpoints)
        if len(coords) < 2:
            continue

        coords = simplify(coords)
        path_km = sum(ways[wi]["km"] for wi in path)
        results.append(
            {
                "wm_id": pipe["wm_id"],
                "n_points": len(coords),
                "path_km": round(path_km, 1),
                "snap_km_start": round(ds, 1),
                "snap_km_end": round(de, 1),
                "route_json": json.dumps([coords]),
            }
        )
    return results


# ---- RexTag-only OSM name match ----

def route_rextag_by_name(slugs: list[dict]) -> list[dict]:
    """Try to find each RexTag pipeline in OSM by name and return its geometry."""
    results = []
    for pipe in slugs:
        name = pipe["name"]
        # Query OSM for pipeline ways with this name in the continental US
        safe_name = name.replace('"', '\\"').replace("&", "and")
        ql = (
            f'[out:json][timeout:30];'
            f'way["man_made"="pipeline"]["name"~"{safe_name}",i](20,-130,55,-65);'
            f'out geom;'
        )
        elements = _overpass_query(ql, timeout_s=30)
        if not elements:
            time.sleep(1)
            continue

        # Collect all matching way coords
        all_coords: list[tuple] = []
        for e in elements:
            geom = e.get("geometry", [])
            for g in geom:
                all_coords.append((g["lat"], g["lon"]))

        if len(all_coords) < 2:
            time.sleep(1)
            continue

        # Sort by longitude (west to east) as a crude ordering
        all_coords.sort(key=lambda p: p[1])
        coords = simplify(list(dict.fromkeys(all_coords)))  # dedup preserving order

        results.append(
            {
                "slug": pipe["slug"],
                "n_points": len(coords),
                "route_json": json.dumps([coords]),
            }
        )
        print(f"  (name match) {pipe['slug']}: {len(coords)} pts")
        time.sleep(2)

    return results


# ---- Main ----

def main():
    import duckdb

    print("=== Global Pipeline Route Ingest (OSM Overpass) ===\n", flush=True)

    # Check existing routes
    print("Loading unrouted WM pipelines...")
    unrouted_wm = load_unrouted_wm()
    print(f"  {len(unrouted_wm)} WM pipelines without routes")

    print("Loading unrouted RexTag-only pipelines...")
    unrouted_rt = load_unrouted_rextag()
    print(f"  {len(unrouted_rt)} RexTag-only pipelines without routes")

    # Check what already exists in global_pipeline_routes (for idempotency)
    con = duckdb.connect(str(ANALYTICS_DB))
    con.execute("""
        CREATE TABLE IF NOT EXISTS global_pipeline_routes (
            wm_id        TEXT PRIMARY KEY,
            n_points     INTEGER,
            path_km      REAL,
            snap_km_start REAL,
            snap_km_end  REAL,
            route_json   TEXT
        )
    """)
    already_done = set(
        r[0] for r in con.execute("SELECT wm_id FROM global_pipeline_routes").fetchall()
    )
    con.close()

    if already_done:
        print(f"  Skipping {len(already_done)} already-routed pipelines")
        unrouted_wm = [p for p in unrouted_wm if p["wm_id"] not in already_done]

    all_results: list[dict] = []

    # Route by region
    for region_name, south, west, north, east in REGIONS:
        # Filter pipelines whose start OR end falls in or near this region (2-degree padding)
        pad = 2.0
        regional_pipes = [
            p for p in unrouted_wm
            if (
                (south - pad <= p["start_lat"] <= north + pad and west - pad <= p["start_lon"] <= east + pad)
                or (south - pad <= p["end_lat"] <= north + pad and west - pad <= p["end_lon"] <= east + pad)
            )
        ]
        if not regional_pipes:
            print(f"  [{region_name}] no unrouted pipelines - skip")
            continue

        print(f"\n[{region_name}] {len(regional_pipes)} pipelines, querying OSM...")
        sys.stdout.flush()
        time.sleep(5)  # respect Overpass rate limit

        ways = fetch_pipeline_ways(south, west, north, east)
        print(f"  Got {len(ways)} OSM pipeline ways")

        if len(ways) < 5:
            print(f"  Too sparse - skip")
            continue

        graph = build_graph(ways)
        print(f"  Graph: {len(graph['endpoints'])} nodes, {len(graph['ways'])} edges")

        region_results = route_pipelines_on_graph(graph, regional_pipes)
        print(f"  Routed: {len(region_results)}/{len(regional_pipes)}", flush=True)

        for r in region_results:
            print(
                f"    OK {r['wm_id']}: {r['n_points']} pts, {r['path_km']:.0f} km"
                f" (snap {r['snap_km_start']:.0f}/{r['snap_km_end']:.0f} km)",
                flush=True,
            )
            all_results.append(r)

        # Flush to DuckDB immediately (idempotent; avoids losing work on interrupt)
        if region_results:
            con_tmp = duckdb.connect(str(ANALYTICS_DB))
            for r in region_results:
                try:
                    con_tmp.execute(
                        "INSERT OR REPLACE INTO global_pipeline_routes VALUES (?,?,?,?,?,?)",
                        [r["wm_id"], r["n_points"], r["path_km"], r["snap_km_start"], r["snap_km_end"], r["route_json"]],
                    )
                except Exception as exc:
                    print(f"  WARN: {r['wm_id']}: {exc}")
            con_tmp.close()

        # Remove routed pipes from the pending list
        routed_ids = {r["wm_id"] for r in region_results}
        unrouted_wm = [p for p in unrouted_wm if p["wm_id"] not in routed_ids]

    print(f"\nDijkstra phase complete: {len(all_results)} routes stored.", flush=True)
    print(f"Remaining unrouted WM: {len(unrouted_wm)}", flush=True)

    # RexTag-only by OSM name match (run separately; each query needs its own Overpass slot)
    # Only attempt if --rextag flag is passed (avoids rate limit interference with main ingest)
    if "--rextag" in sys.argv and unrouted_rt:
        print(f"\n[rextag-name] Trying OSM name match for {len(unrouted_rt)} pipelines...")
        rt_results = route_rextag_by_name(unrouted_rt)
        for r in rt_results:
            r["wm_id"] = r.pop("slug")
            r.setdefault("path_km", 0.0)
            r.setdefault("snap_km_start", 0.0)
            r.setdefault("snap_km_end", 0.0)
        if rt_results:
            con_rt = duckdb.connect(str(ANALYTICS_DB))
            for r in rt_results:
                try:
                    con_rt.execute(
                        "INSERT OR REPLACE INTO global_pipeline_routes VALUES (?,?,?,?,?,?)",
                        [r["wm_id"], r["n_points"], r["path_km"], r["snap_km_start"], r["snap_km_end"], r["route_json"]],
                    )
                except Exception as exc:
                    print(f"  WARN: {r['wm_id']}: {exc}")
            con_rt.close()
            print(f"Stored {len(rt_results)} RexTag name-match routes.")


if __name__ == "__main__":
    main()
