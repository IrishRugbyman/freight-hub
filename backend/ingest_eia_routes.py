"""
Ingest EIA natural gas pipeline route geometry into freight_analytics.duckdb.

Downloads NaturalGas_InterIntrastate_Pipelines_US_EIA.zip (Jan 2020, EIA / public domain),
extracts per-operator polyline segments, fuzzy-matches to RexTag slugs, and stores
the full route geometry as JSON arrays of [[lat, lon], ...] coordinate lists.

Usage:
    .venv/bin/python ingest_eia_routes.py [--shp <path>] [--db <path>]

The shapefile is already at data/eia_pipelines_tmp/shp/ if previously downloaded.
"""

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import duckdb
import fiona

# ---------------------------------------------------------------------------
# Abbreviation expansions for fuzzy matching
# ---------------------------------------------------------------------------
_EXPAND = {
    r'\bpl\b': 'pipeline',
    r'\bpipe\b': 'pipeline',
    r'\btrans\b': 'transmission',
    r'\bco\b': 'company',
    r'\bcorp\b': 'corporation',
    r'\binc\b': 'incorporated',
    r'\bllc\b': '',
    r'\blp\b': '',
    r'\blllp\b': '',
    r'\bgas\b': 'gas',
    r'\bng\b': 'natural gas',
    r'\bnat gas\b': 'natural gas',
    r'\bsys\b': 'system',
}


def normalize(s: str) -> set[str]:
    """Lowercase, expand abbreviations, return word set."""
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    s = s.lower()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    for pat, repl in _EXPAND.items():
        s = re.sub(pat, repl, s)
    return {w for w in s.split() if len(w) > 1}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Manual overrides: EIA operator -> list of RexTag slugs it belongs to
# Some systems are split across multiple EIA operator names.
# ---------------------------------------------------------------------------
_MANUAL: dict[str, list[str]] = {
    'transcontinental gas pl':           ['transcontinental-gas-pipeline'],
    'texas eastern trans co':            ['texas-eastern-transmission'],
    'natural gas pl co of am':           ['natural-gas-pipeline-company-of-america-ngpl'],
    'northern natural gas co':           ['northern-natural-gas'],
    'northwestern energy co':            ['northwestern-pipeline'],
    'columbia gas trans co':             ['columbia-gas-transmission'],
    'columbia gulf pipeline':            ['columbia-gulf-transmission'],
    'algonquin gas trans co':            ['algonquin-gas-transmission'],
    'tennessee gas pipeline':            ['tennessee-gas-pipeline'],
    'southern natural gas co':           ['southern-natural-gas'],
    'anr pipeline co':                   ['anr-pipeline'],
    'enable gas transmission':           ['enable-gas-transmission'],
    'iroquois gas trans co':             ['iroquois-gas-transmission-system'],
    'gulf south pipeline co':            ['gulf-south-pipeline'],
    'el paso natural gas co':            ['el-paso-natural-gas'],
    'el paso texas pipeline co':         ['el-paso-natural-gas'],
    'southern union gas co':             ['panhandle-eastern-pipeline'],
    'panhandle eastern pipe line co':    ['panhandle-eastern-pipeline'],
    'texas intrastate pipeline co':      ['texas-eastern-transmission'],  # intrastate section
    'kern river gas trans co':           ['kern-river-gas-transmission'],
    'questar pipeline co':               ['questar-pipeline'],
    'pacific gas trans co':              ['pacific-gas-transmission'],
    'portland natural gas pl':           ['portland-natural-gas-transmission-system'],
    'colorado interstate gas co':        ['colorado-interstate-gas'],
    'southern star central gas pl co':   ['southern-star-central-gas-pipeline'],
    'trailblazer pipeline co':           ['trailblazer-pipeline'],
    'rockies express pipeline':          ['rockies-express-pipeline'],
    'boardwalk gulf pipeline':           ['gulf-south-pipeline'],
    'florida gas transmission co':       ['florida-gas-transmission'],
}

# Slug prefix -> set of EIA operators (populated from _MANUAL by inversion)
_SLUG_TO_OPS: dict[str, list[str]] = defaultdict(list)
for op, slugs in _MANUAL.items():
    for slug in slugs:
        _SLUG_TO_OPS[slug].append(op)


def load_eia_segments(shp_path: str) -> dict[str, list[list[list[float]]]]:
    """Load all LineString segments grouped by normalised operator name.

    Returns {normalised_operator: [[[lat, lon], ...], ...]}
    Note: shapefile coords are (lon, lat); we flip to (lat, lon) for Leaflet.
    """
    by_op: dict[str, list[list[list[float]]]] = defaultdict(list)
    with fiona.open(shp_path) as src:
        for feat in src:
            op = feat['properties'].get('Operator', '') or ''
            geom = feat['geometry']
            if not geom:
                continue
            if geom['type'] == 'LineString':
                coords = [[pt[1], pt[0]] for pt in geom['coordinates']]
                by_op[op.lower().strip()].append(coords)
            elif geom['type'] == 'MultiLineString':
                for ring in geom['coordinates']:
                    coords = [[pt[1], pt[0]] for pt in ring]
                    by_op[op.lower().strip()].append(coords)
    return by_op


def simplify_segments(segments: list[list[list[float]]], epsilon: float = 0.02) -> list[list[list[float]]]:
    """Ramer-Douglas-Peucker simplification on each segment."""
    def rdp(pts: list, eps: float) -> list:
        if len(pts) <= 2:
            return pts
        # Find point farthest from line between first and last
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
            left = rdp(pts[:max_idx + 1], eps)
            right = rdp(pts[max_idx:], eps)
            return left[:-1] + right
        return [pts[0], pts[-1]]

    result = []
    for seg in segments:
        simplified = rdp(seg, epsilon)
        if len(simplified) >= 2:
            result.append(simplified)
    return result


def match_rextag_to_eia(
    rextag_slugs: list[str],
    eia_segments: dict[str, list[list[list[float]]]],
) -> dict[str, list[list[list[float]]]]:
    """Return {rextag_slug: [segments]} for each matched slug."""
    eia_ops = list(eia_segments.keys())
    eia_norm = {op: normalize(op) for op in eia_ops}
    results: dict[str, list[list[list[float]]]] = {}

    for slug in rextag_slugs:
        # 1. Manual override: check known operator names
        manual_ops = _SLUG_TO_OPS.get(slug, [])
        matched_ops = [op for op in eia_ops if op in manual_ops]

        # 2. Fuzzy fallback: Jaccard on slug words vs operator words
        if not matched_ops:
            slug_words = normalize(slug.replace('-', ' '))
            best_score, best_op = 0.0, None
            for op, op_words in eia_norm.items():
                score = jaccard(slug_words, op_words)
                if score > best_score:
                    best_score, best_op = score, op
            if best_score >= 0.35 and best_op:
                matched_ops = [best_op]

        if matched_ops:
            segs: list[list[list[float]]] = []
            for op in matched_ops:
                segs.extend(eia_segments.get(op, []))
            if segs:
                results[slug] = segs

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shp', default='data/eia_pipelines_tmp/shp/NaturalGas_Pipelines_US_202001.shp')
    parser.add_argument('--db', default='data/freight_analytics.duckdb')
    parser.add_argument('--epsilon', type=float, default=0.02,
                        help='RDP simplification tolerance in degrees (~2 km at 0.02)')
    args = parser.parse_args()

    print('Loading EIA shapefile...')
    eia_segments = load_eia_segments(args.shp)
    print(f'  {len(eia_segments)} distinct operators, '
          f'{sum(len(v) for v in eia_segments.values())} total segments')

    # Load RexTag slugs from DuckDB
    con = duckdb.connect(args.db)
    slugs = [r[0] for r in con.execute('SELECT slug FROM rextag_pipelines').fetchall()]
    print(f'  {len(slugs)} RexTag pipeline slugs to match')

    print('Matching operators...')
    matched = match_rextag_to_eia(slugs, eia_segments)
    print(f'  Matched: {len(matched)}/{len(slugs)} RexTag pipelines')

    print('Simplifying geometry (RDP epsilon={:.3f} deg)...'.format(args.epsilon))
    simplified: dict[str, list[list[list[float]]]] = {}
    for slug, segs in matched.items():
        s = simplify_segments(segs, args.epsilon)
        simplified[slug] = s
        n_pts_before = sum(len(seg) for seg in segs)
        n_pts_after = sum(len(seg) for seg in s)
        print(f'  {slug}: {len(segs)} segs, {n_pts_before} pts -> {n_pts_after} pts')

    print('Storing in DuckDB...')
    con.execute('DROP TABLE IF EXISTS eia_pipeline_routes')
    con.execute('''
        CREATE TABLE eia_pipeline_routes (
            rextag_slug VARCHAR PRIMARY KEY,
            n_segments  INTEGER,
            n_points    INTEGER,
            route_json  VARCHAR
        )
    ''')

    for slug, segs in simplified.items():
        n_pts = sum(len(s) for s in segs)
        con.execute(
            'INSERT INTO eia_pipeline_routes VALUES (?, ?, ?, ?)',
            [slug, len(segs), n_pts, json.dumps(segs)],
        )

    count = con.execute('SELECT COUNT(*) FROM eia_pipeline_routes').fetchone()[0]
    total_pts = con.execute('SELECT SUM(n_points) FROM eia_pipeline_routes').fetchone()[0]
    print(f'  Stored {count} pipeline routes, {total_pts:,} total coordinate points')
    con.close()

    # Cleanup temp files
    import shutil
    shutil.rmtree('data/eia_pipelines_tmp', ignore_errors=True)
    print('Done.')


if __name__ == '__main__':
    main()
