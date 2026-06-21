"""Ingest pipeline routes by OSM name-tag matching + greedy way chaining.

Unlike the Dijkstra approach (ingest_global_pipeline_routes.py), this script
does NOT require a connected network graph. It:
  1. Downloads all named pipeline ways per region from Overpass (out geom)
  2. Groups ways by their OSM name tag
  3. Fuzzy-matches OSM name groups to unrouted WM pipeline names
  4. Greedily chains the disconnected way segments by nearest-endpoint
  5. Applies RDP simplification and stores in global_pipeline_routes

Handles international pipelines where OSM ways share the same name but
have no shared endpoints (common for China, India, Iran, Saudi Arabia).

Usage:
    .venv/bin/python ingest_osm_named_pipeline_routes.py [--region <name>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
import subprocess
import urllib.parse
from collections import defaultdict
from pathlib import Path

import duckdb

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANALYTICS_DB = Path(__file__).parent / "data" / "freight_analytics.duckdb"
RDP_EPSILON = 0.02       # degrees (~2 km), same as Dijkstra routes
MAX_CHAIN_GAP_KM = 300   # treat gap > 300 km as a separate segment
MIN_WAY_POINTS = 2       # discard sub-2-point ways
JACCARD_THRESHOLD = 0.38  # need at least ~2 distinctive words in common
MAX_SNAP_KM_THRESHOLD = 600  # skip match if best-scoring name group centroid is >600km from WM endpoints

# Generic OSM name fragments that produce false-positives - skip any OSM group
# whose entire name normalises to only these words.
_GENERIC_NAMES = frozenset({
    "pipeline", "gas pipeline", "oil pipeline", "crude oil pipeline",
    "petroleum pipeline", "natural gas pipeline", "gas", "oil", "pipe",
    "main gas pipeline", "main oil pipeline",
})
MIN_OSM_DISTINCTIVE_WORDS = 2  # need at least 2 non-generic non-stop words

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

# Region definitions: (name, south, west, north, east)
REGIONS = [
    ("middle_east_west",    10.0,  24.0,  40.0,  50.0),  # Egypt, Sudan, Syria, Lebanon, Israel, Jordan
    ("middle_east_gulf",    15.0,  44.0,  40.0,  65.0),  # Saudi Arabia, UAE, Qatar, Kuwait, Iraq, Iran W
    ("iran_east",           25.0,  56.0,  40.0,  67.0),  # Iran East, Turkmenistan S
    ("central_asia_n",      38.0,  50.0,  56.0,  80.0),  # Kazakhstan, Uzbekistan, Kyrgyzstan
    ("central_asia_s",      30.0,  55.0,  42.0,  75.0),  # Turkmenistan, Tajikistan, Afghanistan
    ("russia_w",            47.0,  28.0,  70.0,  65.0),  # Western Russia, Urals
    ("russia_c",            47.0,  60.0,  70.0,  100.0), # Siberia Central
    ("russia_e",            47.0,  95.0,  72.0,  145.0), # East Siberia, Far East
    ("south_asia",           5.0,  60.0,  38.0,  98.0),  # India, Pakistan, Bangladesh, Nepal
    ("southeast_asia",     -15.0,  92.0,  25.0,  142.0), # Myanmar, Thailand, Indonesia, Malaysia, Vietnam
    ("china_w",             25.0,  73.0,  55.0,  108.0), # Xinjiang, Tibet, Sichuan, Gansu
    ("china_e",             18.0, 103.0,  45.0,  135.0), # Eastern China, NE China
    ("africa_n",            10.0, -18.0,  38.0,  38.0),  # North Africa, Horn, Sudan
    ("africa_w",            -5.0, -18.0,  18.0,  20.0),  # West Africa, Niger Delta
    ("africa_e",           -35.0,  20.0,  15.0,  55.0),  # East Africa, Southern Africa
    ("latam_n",             -5.0, -85.0,  18.0, -50.0),  # Colombia, Venezuela, Peru, Ecuador, Brazil N
    ("latam_s",            -60.0, -80.0,  -5.0, -30.0),  # Argentina, Chile, Brazil S, Bolivia
    ("mexico_ca",           14.0,-120.0,  34.0, -82.0),  # Mexico, Central America
    ("canada",              42.0,-145.0,  72.0, -52.0),  # Canada
    ("oceania",            -50.0, 105.0,   5.0, 180.0),  # Australia + Pacific
    ("us_lower48",          24.0,-125.0,  50.0, -65.0),  # Remaining US pipelines
]


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------
_STOP = {"the", "of", "and", "for", "in", "to", "a", "an", "at", "by", "on",
         "de", "del", "la", "el", "los", "las", "en", "da", "do", "dos"}
_EXPAND = {
    r"\bpl\b": "pipeline", r"\bpipe\b": "pipeline", r"\bngl\b": "ngl",
    r"\bgas\b": "gas", r"\boil\b": "oil", r"\bcrude\b": "crude",
    r"\bsys(tem)?\b": "system", r"\btrans\b": "trans", r"\bco\b": "company",
    r"\bllc\b": "", r"\blp\b": "", r"\binc\b": "",
    # Spanish/Portuguese pipeline words (LATAM)
    r"\bgasoducto\b": "gas pipeline",
    r"\boleoducto\b": "oil pipeline",
    r"\bpoliducto\b": "products pipeline",
    r"\bgasoduto\b": "gas pipeline",     # Portuguese
    r"\boleoduto\b": "oil pipeline",     # Portuguese
    r"\bducto\b": "pipeline",
    r"\bsistema\b": "system",
    r"\bnorte\b": "north",
    r"\bsur\b": "south",
    r"\bcentro\b": "central",
    r"\beste\b": "east",
    r"\boeste\b": "west",
    r"\bandino\b": "andean",
}


def _norm(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    for pat, repl in _EXPAND.items():
        s = re.sub(pat, repl, s)
    return {w for w in s.split() if len(w) > 1 and w not in _STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------
def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# RDP simplification
# ---------------------------------------------------------------------------
def _rdp(pts: list, eps: float) -> list:
    if len(pts) <= 2:
        return pts
    x1, y1 = pts[0]
    x2, y2 = pts[-1]
    dx, dy = x2 - x1, y2 - y1
    d = (dx * dx + dy * dy) ** 0.5
    max_dist, max_idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        dist = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / d if d else _hav(*pts[i], *pts[0])
        if dist > max_dist:
            max_dist, max_idx = dist, i
    if max_dist > eps:
        return _rdp(pts[: max_idx + 1], eps)[:-1] + _rdp(pts[max_idx:], eps)
    return [pts[0], pts[-1]]


def _simplify_segs(segs: list, eps: float = RDP_EPSILON) -> list:
    out = []
    for seg in segs:
        s = _rdp(seg, eps)
        if len(s) >= 2:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Overpass helpers (curl-based to avoid urllib 406 rejections)
# ---------------------------------------------------------------------------
_ep_idx = 0


def _wait_for_slot(ep: str) -> None:
    """Poll Overpass status until a query slot is free."""
    status_url = ep.replace("/api/interpreter", "/api/status")
    for _ in range(20):
        r = subprocess.run(
            ["curl", "-s", "--max-time", "15", status_url],
            capture_output=True, text=True,
        )
        txt = r.stdout
        if "Slot available after" not in txt:
            return  # slot available -> proceed
        m = re.search(r"in (\d+) seconds", txt)
        wait = int(m.group(1)) + 2 if m else 15
        print(f"    [rate limit] waiting {wait}s ...", flush=True)
        time.sleep(wait)


def _overpass_query(ql: str) -> dict | None:
    """Run an Overpass QL query via curl subprocess with retry/backoff.

    Uses curl to avoid Python urllib 406 rejections from Overpass servers.
    On HTML response (rate limit or server rejection): wait 90s before retry.
    On empty response: rotate endpoint, wait 15s.
    """
    global _ep_idx
    for attempt in range(8):
        ep = OVERPASS_ENDPOINTS[_ep_idx % len(OVERPASS_ENDPOINTS)]
        _wait_for_slot(ep)
        r = subprocess.run(
            [
                "curl", "-s", "--max-time", "180",
                "-X", "POST", ep,
                "--data", f"data={urllib.parse.quote(ql)}",
            ],
            capture_output=True, text=True,
        )
        if not r.stdout.strip():
            print(f"    [attempt {attempt+1}] empty response from {ep}", flush=True)
            _ep_idx += 1
            time.sleep(15)
            continue
        if r.stdout.lstrip().startswith("<"):
            # HTML error page - rate limit or query rejected by server
            wait = 90 + attempt * 30
            print(f"    [attempt {attempt+1}] server returned HTML (rate limit/reject) - waiting {wait}s", flush=True)
            time.sleep(wait)
            # Don't rotate - same endpoint, rate limit is IP-based
            continue
        try:
            d = json.loads(r.stdout)
            if "elements" in d:
                return d
            remark = d.get("remark", "")
            print(f"    [attempt {attempt+1}] no elements key, remark={remark!r}", flush=True)
        except json.JSONDecodeError:
            snippet = r.stdout[:200]
            print(f"    [attempt {attempt+1}] JSON error, response: {snippet!r}", flush=True)
        _ep_idx += 1
        time.sleep(10)
    return None



# ---------------------------------------------------------------------------
# Download all named pipeline ways in a bbox
# Returns {osm_name: [[lat,lon], ...] list-of-ways}
# ---------------------------------------------------------------------------
def _is_generic_osm_name(name: str) -> bool:
    """True if the OSM name is too generic to safely match (e.g. 'Gas Pipeline')."""
    normalised = name.lower().strip()
    if normalised in _GENERIC_NAMES:
        return True
    words = _norm(name) - {"pipeline", "gas", "oil", "crude", "petroleum", "natural", "pipe", "main"}
    return len(words) < MIN_OSM_DISTINCTIVE_WORDS


def _pick_osm_name(tags: dict) -> str:
    """Pick the best matchable name from OSM tags.

    Preference: name:en > int_name > alt_name > name
    For non-Latin script regions (Russia, China, Iran, Arab), name:en is the
    only matchable form since our Jaccard scoring uses ASCII tokens.
    """
    for key in ("name:en", "int_name", "alt_name", "name"):
        val = (tags.get(key) or "").strip()
        if val and not _is_generic_osm_name(val):
            return val
    return ""


def fetch_named_pipeline_ways(s: float, w: float, n: float, e: float) -> dict[str, list[list[list[float]]]]:
    ql = f"""
[out:json][timeout:120][maxsize:536870912];
way[man_made=pipeline][name]({s},{w},{n},{e});
out geom;
"""
    result = _overpass_query(ql)
    if not result:
        return {}

    by_name: dict[str, list[list[list[float]]]] = defaultdict(list)
    for elem in result.get("elements", []):
        tags = elem.get("tags", {})
        name = _pick_osm_name(tags)
        if not name:
            continue
        geom = elem.get("geometry", [])
        pts = [[g["lat"], g["lon"]] for g in geom if "lat" in g and "lon" in g]
        if len(pts) >= MIN_WAY_POINTS:
            by_name[name].append(pts)
    return dict(by_name)


# ---------------------------------------------------------------------------
# Greedy chaining of disconnected ways into segments
# Returns list of segments (each segment = [[lat,lon], ...])
# Segments are split when gap between consecutive ways > MAX_CHAIN_GAP_KM
# ---------------------------------------------------------------------------
def greedy_chain(ways: list[list[list[float]]]) -> list[list[list[float]]]:
    if not ways:
        return []
    remaining = [list(w) for w in ways]  # copy
    chain_segs: list[list[list[float]]] = []
    current_seg = remaining.pop(0)

    while remaining:
        tail = current_seg[-1]
        best_dist = float("inf")
        best_idx = 0
        best_flip = False
        for i, way in enumerate(remaining):
            d_s = _hav(tail[0], tail[1], way[0][0], way[0][1])
            d_e = _hav(tail[0], tail[1], way[-1][0], way[-1][1])
            if d_s < best_dist:
                best_dist, best_idx, best_flip = d_s, i, False
            if d_e < best_dist:
                best_dist, best_idx, best_flip = d_e, i, True

        way = remaining.pop(best_idx)
        if best_flip:
            way = way[::-1]

        if best_dist > MAX_CHAIN_GAP_KM:
            # Gap too large -> new separate segment
            chain_segs.append(current_seg)
            current_seg = way
        else:
            current_seg = current_seg + way

    chain_segs.append(current_seg)
    return chain_segs


# ---------------------------------------------------------------------------
# Load unrouted WM pipelines from loader
# ---------------------------------------------------------------------------
def load_unrouted_wm() -> list[dict]:
    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "shared" / "market-data" / "src")
    )
    from loaders.worldmonitor import load_pipelines_for_map  # noqa: PLC0415

    df = load_pipelines_for_map(disrupted_only=False)
    unrouted = df[df["route_json"].isna()].copy()
    # Exclude pipelines without coordinates
    unrouted = unrouted[unrouted["start_lat"].notna() & unrouted["end_lat"].notna()]
    records = []
    for _, row in unrouted.iterrows():
        records.append({
            "wm_id": row["id"],
            "name": row["name"],
            "commodity": row["commodity"],
            "from_country": row["from_country"],
            "to_country": row["to_country"],
            "start_lat": float(row["start_lat"]),
            "start_lon": float(row["start_lon"]),
            "end_lat": float(row["end_lat"]),
            "end_lon": float(row["end_lon"]),
        })
    return records


# ---------------------------------------------------------------------------
# Match OSM name groups to WM pipelines
# Returns list of (wm_pipeline_dict, best_osm_name, score, chained_ways)
# ---------------------------------------------------------------------------
def match_osm_to_wm(
    osm_groups: dict[str, list[list[list[float]]]],
    wm_pipes: list[dict],
) -> list[tuple[dict, str, float, list[list[list[float]]]]]:
    osm_norm = {name: _norm(name) for name in osm_groups}
    results = []

    for pipe in wm_pipes:
        wm_words = _norm(pipe["wm_id"].replace("-", " ") + " " + pipe["name"])
        best_score, best_name = 0.0, None
        for osm_name, osm_words in osm_norm.items():
            score = _jaccard(wm_words, osm_words)
            if score > best_score:
                best_score, best_name = score, osm_name

        if best_score < JACCARD_THRESHOLD or best_name is None:
            continue

        # Sanity check: centroid of matched ways should be near WM endpoints
        ways = osm_groups[best_name]
        all_pts = [pt for w in ways for pt in w]
        if not all_pts:
            continue
        c_lat = sum(p[0] for p in all_pts) / len(all_pts)
        c_lon = sum(p[1] for p in all_pts) / len(all_pts)
        d_start = _hav(pipe["start_lat"], pipe["start_lon"], c_lat, c_lon)
        d_end = _hav(pipe["end_lat"], pipe["end_lon"], c_lat, c_lon)
        if min(d_start, d_end) > MAX_SNAP_KM_THRESHOLD:
            continue

        chained = greedy_chain(ways)
        total_pts = sum(len(s) for s in chained)
        if chained and total_pts >= 4:  # require at least 4 pts - rejects stub ways
            results.append((pipe, best_name, best_score, chained))

    return results


# ---------------------------------------------------------------------------
# Already-routed set (to skip)
# ---------------------------------------------------------------------------
def _already_routed() -> set[str]:
    con = duckdb.connect(str(ANALYTICS_DB), read_only=True)
    existing = set()
    for tbl in ("global_pipeline_routes", "eu_pipeline_routes", "eia_oil_pipeline_routes"):
        try:
            rows = con.execute(f"SELECT wm_id FROM {tbl}").fetchall()
            existing.update(r[0] for r in rows)
        except Exception:
            pass
    con.close()
    return existing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", action="append", dest="regions",
                        help="Run only this region (may be repeated)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== OSM Named Pipeline Routes Ingest ===\n", flush=True)

    # Load unrouted pipelines
    all_unrouted = load_unrouted_wm()
    already = _already_routed()
    unrouted = [p for p in all_unrouted if p["wm_id"] not in already]
    print(f"Unrouted WM pipelines: {len(unrouted)} (of {len(all_unrouted)} total unrouted)\n", flush=True)

    # Create table if needed (reuses global_pipeline_routes)
    if not args.dry_run:
        con = duckdb.connect(str(ANALYTICS_DB))
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
        con.close()

    total_stored = 0
    region_filter = set(args.regions) if args.regions else None
    regions = REGIONS if not region_filter else [r for r in REGIONS if r[0] in region_filter]

    for region_name, s, w, n, e in regions:
        # Filter pipelines in this region
        regional = [
            p for p in unrouted
            if (s - 5 <= p["start_lat"] <= n + 5 and w - 5 <= p["start_lon"] <= e + 5)
            or (s - 5 <= p["end_lat"] <= n + 5 and w - 5 <= p["end_lon"] <= e + 5)
        ]
        if not regional:
            print(f"[{region_name}] no unrouted pipelines -> skip\n", flush=True)
            continue

        print(f"[{region_name}] {len(regional)} unrouted pipelines in bbox ({s},{w},{n},{e})", flush=True)
        time.sleep(3)

        osm_groups = fetch_named_pipeline_ways(s, w, n, e)
        print(f"  OSM named pipeline groups: {len(osm_groups)}", flush=True)
        if not osm_groups:
            print("  No OSM data -> skip\n", flush=True)
            continue

        matches = match_osm_to_wm(osm_groups, regional)
        print(f"  Matched: {len(matches)}", flush=True)

        stored = 0
        for pipe, osm_name, score, chained in matches:
            simplified = _simplify_segs(chained)
            if not simplified:
                continue
            n_pts = sum(len(s) for s in simplified)
            # Estimate path length
            path_km = sum(
                _hav(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1])
                for seg in simplified
                for i in range(len(seg) - 1)
            )
            osm_short = osm_name[:35]
            print(
                f"    {pipe['wm_id']!r:50} <- {osm_short!r} score={score:.2f} "
                f"{len(simplified)} segs {n_pts} pts {path_km:.0f} km",
                flush=True,
            )
            if args.dry_run:
                stored += 1
                continue
            try:
                con_w = duckdb.connect(str(ANALYTICS_DB))
                con_w.execute(
                    "INSERT OR REPLACE INTO global_pipeline_routes VALUES (?,?,?,?,?,?)",
                    [pipe["wm_id"], n_pts, path_km, 0.0, 0.0, json.dumps(simplified)],
                )
                con_w.close()
                stored += 1
                total_stored += 1
                # Remove from unrouted so subsequent regions don't re-process
                unrouted = [p for p in unrouted if p["wm_id"] != pipe["wm_id"]]
            except Exception as exc:
                print(f"    WARN: {pipe['wm_id']}: {exc}", flush=True)

        print(f"  Stored: {stored}\n", flush=True)

    if args.dry_run:
        print("[dry-run] No data written.")
    else:
        con_r = duckdb.connect(str(ANALYTICS_DB), read_only=True)
        total_global = con_r.execute("SELECT COUNT(*) FROM global_pipeline_routes").fetchone()[0]
        con_r.close()
        print(f"\nDone. {total_stored} new routes stored in global_pipeline_routes.")
        print(f"Total global_pipeline_routes: {total_global}")


if __name__ == "__main__":
    main()
