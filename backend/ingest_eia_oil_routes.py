"""Ingest EIA crude oil and petroleum product pipeline routes into freight_analytics.duckdb.

Downloads from two EIA ArcGIS FeatureServer endpoints:
  - Crude Oil Pipelines (231 records, opername/pipename fields, LineString paths)
  - Petroleum Product Pipelines (329 records, Opername/Pipename fields)

Fuzzy-matches to WM pipeline IDs (pipeline_registry) and stores route geometry
in a new eia_oil_pipeline_routes table (keyed by wm_id) with multi-segment format
matching the EIA gas routes convention: route_json = JSON array of segments,
each segment a [[lat,lon], ...] array.

Usage:
    cd backend
    .venv/bin/python ingest_eia_oil_routes.py [--db <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb

DB_DEFAULT = Path(__file__).parent / "data" / "freight_analytics.duckdb"

# EIA FeatureServer endpoints
CRUDE_OIL_URL = (
    "https://services5.arcgis.com/vNzamREXvX2WcX6d/arcgis/rest/services"
    "/EIA_Crude_Oil_Pipeline/FeatureServer/0"
)
PETRO_PROD_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
    "/Petroleum_Products_Pipelines_1/FeatureServer/0"
)

RDP_EPSILON = 0.01  # ~1 km - tighter than gas since oil routes are shorter


# ---------------------------------------------------------------------------
# Manual overrides: (opername, pipename) -> WM pipeline id
# ---------------------------------------------------------------------------
_MANUAL: dict[tuple[str, str], str] = {
    # Crude oil
    ("ALYESKA PIPELINE", "Trans Alaska Pipeline System (TAPS)"): "taps",
    ("ENBRIDGE", "Mainline"): "enbridge-mainline",
    ("ENBRIDGE", "Alberta Clipper"): "enbridge-line-93-oil-pipeline-ca",
    ("ENBRIDGE", "Lakehead"): "enbridge-mainline",
    ("ENBRIDGE", "Southern Access"): "enbridge-mainline",
    ("ENTERPRISE PRODUCTS PARTNERS", "Seaway"): "seaway",
    ("KINDER MORGAN", "TransMountain"): "trans-mountain",
    # Keystone: Phase 1 = Hardisty->Patoka mainline, Gulf Coast = Phase 2
    ("TRANSCANADA", "Keystone"): "keystone-oil-pipeline-mainline-phase-1-ca",
    ("TRANSCANADA", "Gulf Coast Project"): "keystone-oil-pipeline-phase-2-us",
    ("ENERGY TRANSFER", "Dakota Access Pipeline (DAPL)"): "dakota-access-oil-pipeline-dapl-us",
    ("PHILLIPS 66 PIPELINE", "Gray Oak Pipeline"): "gray-oak-oil-pipeline-us",
    ("PHILLIPS  66 PIPELINE", "Gray Oak Pipeline"): "gray-oak-oil-pipeline-us",
    ("MAGELLAN MIDSTREAM PARTNERS", "BridgeTex"): "bridgetex-oil-pipeline-us",
    ("MAGELLAN MIDSTREAM PARTNERS", "Longhorn"): "longhorn-oil-pipeline-crude-oil-system-us",
    ("SHELL PIPELINE COMPANY", "Capline"): "capline-oil-pipeline-us",
    ("PLAINS ALL AMERICAN PIPELINE", "Basin"): "basin-oil-pipeline-us",
    ("PLAINS ALL AMERICAN PIPELINE", "Cactus Pipeline"): "cactus-oil-pipeline-us",
    ("SPECTRA ENERGY", "Express System"): "express-oil-pipeline-system-ca",
    ("SPECTRA ENERGY", "Platte Pipeline"): "platte-crude-oil-pipeline-us",
    ("TALLGRASS ENERGY", "Pony Express Pipeline"): "pony-express-oil-pipeline-us",
    ("MAGELLAN MIDSTREAM PARTNERS", "Saddlehorn Pipeline"): "saddlehorn-oil-pipeline-expansion-us",
    # Note: Bayou Bridge, LOOP, Portland Montreal not in WM pipeline_registry
}

# Operator keyword overrides for fuzzy matching - only include WM-registered IDs
_OP_SLUG_MAP: dict[str, str] = {
    # Petroleum products companies are generally not tracked by WM (US domestic only)
    # Leave empty to rely on fuzzy matching alone
}


# ---------------------------------------------------------------------------
# Text normalisation for fuzzy matching
# ---------------------------------------------------------------------------
_EXPAND = {
    r'\bpl\b': 'pipeline',
    r'\bpipe\b': 'pipeline',
    r'\bcorp\b': 'corporation',
    r'\binc\b': 'incorporated',
    r'\bllc\b': '',
    r'\blp\b': '',
    r'\bcrud[eo]?\b': 'crude',
    r'\boil\b': 'oil',
    r'\btrans\b': 'trans',
    r'\bsys\b': 'system',
    r'\bpetroleum\b': 'petroleum',
    r'\bproducts?\b': 'product',
    r'\brefined\b': 'refined',
}


def _norm(s: str) -> set[str]:
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    for pat, repl in _EXPAND.items():
        s = re.sub(pat, repl, s)
    return {w for w in s.split() if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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


def simplify_segments(
    segments: list[list[list[float]]], eps: float = RDP_EPSILON
) -> list[list[list[float]]]:
    result = []
    for seg in segments:
        s = _rdp(seg, eps)
        if len(s) >= 2:
            result.append(s)
    return result


# ---------------------------------------------------------------------------
# FeatureServer download
# ---------------------------------------------------------------------------
def _fetch_features(base_url: str, op_field: str, name_field: str) -> list[dict]:
    """Download all features from a FeatureServer layer, return list of dicts
    with keys: opername, pipename, paths (list of [[lat,lon],...] segments).
    Geometry returned in WGS84 (outSR=4326).
    """
    features = []
    offset = 0
    batch = 500
    while True:
        url = (
            f"{base_url}/query?where=1%3D1"
            f"&outFields={op_field},{name_field}"
            f"&geometryType=esriGeometryPolyline"
            f"&outSR=4326"
            f"&f=json"
            f"&resultRecordCount={batch}"
            f"&resultOffset={offset}"
        )
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read())
        batch_feats = d.get('features', [])
        for feat in batch_feats:
            a = feat.get('attributes', {})
            geom = feat.get('geometry', {})
            paths = geom.get('paths', [])
            # paths: [[[lon, lat], ...], ...]  -> flip to [[lat, lon], ...]
            converted: list[list[list[float]]] = []
            for path in paths:
                seg = [[pt[1], pt[0]] for pt in path if len(pt) >= 2]
                if len(seg) >= 2:
                    converted.append(seg)
            if converted:
                features.append({
                    'opername': (a.get(op_field) or '').strip(),
                    'pipename': (a.get(name_field) or '').strip(),
                    'paths': converted,
                })
        print(f"  offset={offset}: {len(batch_feats)} records", flush=True)
        if len(batch_feats) < batch:
            break
        offset += batch
    return features


# ---------------------------------------------------------------------------
# Group features by composite key
# ---------------------------------------------------------------------------
def _group_features(features: list[dict]) -> dict[tuple[str, str], list[list[list[float]]]]:
    """Group by (opername, pipename), merging all path segments."""
    by_key: dict[tuple[str, str], list[list[list[float]]]] = defaultdict(list)
    for feat in features:
        key = (feat['opername'], feat['pipename'])
        by_key[key].extend(feat['paths'])
    return dict(by_key)


# ---------------------------------------------------------------------------
# Match EIA (opername, pipename) -> WM pipeline id
# ---------------------------------------------------------------------------
def match_to_wm(
    eia_groups: dict[tuple[str, str], list[list[list[float]]]],
    wm_all: list[dict],
    wm_us_ca: list[dict],
) -> dict[str, list[list[list[float]]]]:
    """Return {wm_id: [segments]} for matched pipelines.

    Manual overrides are checked against all WM oil pipelines.
    Fuzzy matching is restricted to US/CA WM pipelines to avoid false positives
    matching international WM IDs to US EIA pipeline names.
    """
    wm_by_id = {p['id']: p for p in wm_all}
    # Fuzzy matching only over US/CA pipelines
    wm_norm = {p['id']: _norm(p['id'].replace('-', ' ') + ' ' + p['name']) for p in wm_us_ca}

    results: dict[str, list[list[list[float]]]] = defaultdict(list)

    for (opername, pipename), segs in eia_groups.items():
        wm_id = None

        # 1. Manual override by exact (opername, pipename)
        wm_id = _MANUAL.get((opername, pipename))

        # 2. Operator keyword override
        if not wm_id:
            op_lower = opername.lower()
            for kw, slug in _OP_SLUG_MAP.items():
                if kw in op_lower:
                    wm_id = slug
                    break

        # 3. Fuzzy match composite string against WM ids + names
        if not wm_id:
            composite_words = _norm(opername + ' ' + pipename)
            best_score, best_id = 0.0, None
            for pid, pwords in wm_norm.items():
                score = _jaccard(composite_words, pwords)
                if score > best_score:
                    best_score, best_id = score, pid
            if best_score >= 0.35:
                wm_id = best_id

        if wm_id and wm_id in wm_by_id:
            results[wm_id].extend(segs)
        elif wm_id:
            print(f"  SKIP: ({opername!r}, {pipename!r}) -> {wm_id!r} (not in WM registry)")

    return dict(results)


# ---------------------------------------------------------------------------
# Load WM oil pipelines
# ---------------------------------------------------------------------------
def _load_wm_oil(db_path: str | None = None) -> tuple[list[dict], list[dict]]:
    """Return (all_wm_oil, us_eligible_wm_oil) from WM oil pipeline registry.

    Fuzzy matching is restricted to WM pipelines where at least one endpoint
    is in the US (from_country == 'US' OR to_country == 'US'). This prevents
    false-positive matches between US-only EIA data and Canada-only WM entries.
    Cross-border CA->US and US->CA pipelines are included since EIA may have
    their US segments.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'shared' / 'market-data' / 'src'))
    from loaders.worldmonitor import load_pipelines_for_map
    df = load_pipelines_for_map(disrupted_only=False)
    oil = df[df['commodity'] == 'oil'].copy()
    all_oil = oil[['id', 'name']].to_dict('records')
    # Fuzzy scope: must have US as from OR to country (not Canada-only, not international)
    us_eligible = oil[
        (oil['from_country'] == 'US') | (oil['to_country'] == 'US')
    ][['id', 'name']]
    return all_oil, us_eligible.to_dict('records')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(DB_DEFAULT))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--epsilon', type=float, default=RDP_EPSILON)
    args = parser.parse_args()

    print("=== EIA Oil Pipeline Routes Ingest ===\n", flush=True)

    # Download crude oil features
    print("Downloading crude oil pipelines...", flush=True)
    crude_feats = _fetch_features(CRUDE_OIL_URL, 'opername', 'pipename')
    print(f"  {len(crude_feats)} crude oil segments\n", flush=True)

    # Download petroleum product features
    print("Downloading petroleum product pipelines...", flush=True)
    petro_feats = _fetch_features(PETRO_PROD_URL, 'Opername', 'Pipename')
    print(f"  {len(petro_feats)} petroleum product segments\n", flush=True)

    # Combine and group
    all_feats = crude_feats + petro_feats
    groups = _group_features(all_feats)
    print(f"Total groups (operator+name combos): {len(groups)}", flush=True)

    # Load WM oil pipelines for matching
    print("\nLoading WM oil pipeline registry...", flush=True)
    wm_all, wm_us_ca = _load_wm_oil()
    print(f"  {len(wm_all)} WM oil pipelines ({len(wm_us_ca)} US/CA)", flush=True)

    # Match to WM IDs
    print("\nMatching EIA segments to WM pipeline IDs...", flush=True)
    matched = match_to_wm(groups, wm_all, wm_us_ca)
    print(f"  Matched: {len(matched)} WM pipelines", flush=True)

    # Simplify geometry
    print("\nSimplifying geometry (RDP epsilon={:.3f} deg)...".format(args.epsilon), flush=True)
    simplified: dict[str, list[list[list[float]]]] = {}
    for wm_id, segs in matched.items():
        s = simplify_segments(segs, args.epsilon)
        if s:
            simplified[wm_id] = s
            n_before = sum(len(seg) for seg in segs)
            n_after = sum(len(seg) for seg in s)
            print(f"  {wm_id}: {len(segs)} segs, {n_before} pts -> {n_after} pts", flush=True)

    if args.dry_run:
        print(f"\n[dry-run] Would store {len(simplified)} routes.")
        return

    # Store in DuckDB
    print(f"\nStoring {len(simplified)} routes in DuckDB...", flush=True)
    con = duckdb.connect(args.db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS eia_oil_pipeline_routes (
            wm_id      VARCHAR PRIMARY KEY,
            n_segments INTEGER,
            n_points   INTEGER,
            route_json VARCHAR
        )
    """)

    stored = 0
    for wm_id, segs in simplified.items():
        n_pts = sum(len(s) for s in segs)
        try:
            con.execute(
                "INSERT OR REPLACE INTO eia_oil_pipeline_routes VALUES (?, ?, ?, ?)",
                [wm_id, len(segs), n_pts, json.dumps(segs)],
            )
            stored += 1
        except Exception as e:
            print(f"  WARN: {wm_id}: {e}")

    total = con.execute("SELECT COUNT(*) FROM eia_oil_pipeline_routes").fetchone()[0]
    con.close()

    print(f"\nStored {stored} new routes.")
    print(f"Total eia_oil_pipeline_routes: {total}")
    print("\nDone. Restart freight-api to pick up changes.")


if __name__ == '__main__':
    main()
