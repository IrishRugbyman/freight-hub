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
MIN_OSM_DISTINCTIVE_WORDS = 1  # 1 distinctive word is enough; acronyms (HVJ, JHBDPL) are valid

# Translation map for non-Latin OSM name tags (Chinese, etc.) that would otherwise
# produce empty ASCII token sets after normalisation.  Keys are the exact OSM `name`
# strings as returned by Overpass; values are WM-matchable English equivalents.
_FOREIGN_NAME_MAP: dict[str, str] = {
    # West-East Gas Pipeline trunk lines (西气东输)
    "西气东输":     "West-East Gas Pipeline",
    "西气东输一线": "West-East Gas Pipeline 1",
    "西气东输二线": "West-East Gas Pipeline 2",
    "西气东输三线": "West-East Gas Pipeline 3",
    "西气东输四线": "West-East Gas Pipeline 4",
    # China-Russia East Gas Pipeline (中俄东线, called Power of Siberia on Russian side)
    "中俄东线天然气管道":              "China Russia East Gas Pipeline",
    "中俄东线天然气管道长岭-长春支线": "China Russia East Gas Pipeline Changling Changchun Branch",
    # China-Central Asia Gas Pipeline (中国-中亚)
    "中国—中亚天然气管道": "China Central Asia Gas Pipeline",
    "中国-中亚天然气管道": "China Central Asia Gas Pipeline",
    # Sino-Myanmar (Chinese name tag on some OSM ways in Yunnan)
    "中缅油气管道":    "Sino Myanmar oil gas pipeline",
    "中缅天然气管道":  "Sino Myanmar gas pipeline",
    "中缅原油管道":    "Sino Myanmar crude oil pipeline",
    # Shaan-Jing (Shaanxi-Beijing) gas pipeline lines 1-4
    "陕京一线": "Shaan-Jing Pipeline 1",
    "陕京二线": "Shaan-Jing Pipeline 2",
    "陕京三线": "Shaan-Jing Pipeline 3",
    "陕京四线": "Shaan-Jing Pipeline 4",
    # Sichuan-Shanghai / Sichuan-to-East gas pipelines
    "川气东送":     "Sichuan East Gas Pipeline",
    "川气东送管道": "Sichuan East Gas Pipeline",
    # Eastern Siberia - Pacific Ocean (ESPO): Russian side already has name:en;
    # add the Chinese-character tags for Chinese territory ways
    "中俄原油管道": "Eastern Siberia Pacific Ocean China crude oil pipeline",
    # Kazakhstan-China oil pipeline (Atasu-Alashankou) - Chinese segment
    "哈中管道":         "Kazakhstan China oil pipeline",
    "中哈原油管道":     "Kazakhstan China crude oil pipeline",
    # Russia ESPO spur to China (ВСТО - Китай) - Cyrillic tag on some cross-border ways
    'Отвод "ВСТО - Китай"': "Eastern Siberia Pacific Ocean China spur crude oil pipeline",
    # Trans-Sakhalin (Sakhalin-2 crude export pipeline, Gazprom/Shell JV)
    "Транссахалинская трубопроводная система": "Trans-Sakhalin pipeline system Sakhalin 2 crude oil export",
    # Sakhalin-Khabarovsk-Vladivostok gas pipeline (main line)
    "Магистральный газопровод Сахалин-Хабаровск-Владивосток": "Sakhalin Khabarovsk Vladivostok gas pipeline",
    # SHV gas pipeline - abbreviated name used on Amur Oblast / Khabarovsk ways (Belogorsk section)
    "Газопровод \"СХВ\"": "Belogorsk Khabarovsk Amur gas pipeline SHV",
    # Okha-Komsomolsk crude pipeline (Soviet-era, Sakhalin to mainland)
    "Оха — Комсомольск-на-Амуре": "Okha Komsomolsk crude oil pipeline Sakhalin",
    # Kuyumba-Taishet oil pipeline (Krasnoyarsk, Transneft)
    "Магистральный нефтепровод Куюмба-Тайшет": "Kuyumba Taishet oil pipeline",
    # SRTO-Ural gas pipelines (Northern Tyumen Oblast to Ural, Gazprom)
    "СРТО — Урал":   "SRTO Ural gas pipeline",
    "СРТО — Урал 2": "SRTO Ural gas pipeline 2",
    # Igrim-Serov-Nizhny Tagil gas pipeline (older Soviet-era trunk)
    "Игрим — Серов — Нижний Тагил":           "Igrim Serov Nizhny Tagil gas pipeline",
    "Игрим — Серов — Нижний Тагил (лупинг)":  "Igrim Serov Nizhny Tagil gas pipeline looping",
    # Bukhara-Ural gas pipeline (Uzbekistan to Russian Urals, Gazprom/Transneft era)
    "Бухара - Урал":            "Bukhara Ural gas pipeline",
    "Бухара — Урал 1":          "Bukhara Ural gas pipeline 1",
    "Бухара — Урал 1 (лупинг)": "Bukhara Ural gas pipeline 1 looping",
    # Beineu-Shymkent gas pipeline (Kazakhstan, connects Caspian to south)
    "Газопровод бейнеу - шымкент": "Beineu Shymkent gas pipeline Kazakhstan",
    # Vuktyl-Ukhta gas pipeline (Komi Republic / Timan-Pechora)
    "Вуктыл — Ухта 1": "Vuktyl Ukhta gas pipeline 1",
    "Вуктыл — Ухта 2": "Vuktyl Ukhta gas pipeline 2",
    # India - KKBMPL GAIL pipeline (Kochi-Koottanad-Bangalore-Mangalore Pipeline)
    # OSM uses the acronym; expand so it matches the WM full-name entry
    "KKBMPL GAIL Pipeline": "Kochi Koottanad Bangalore Mangalore gas pipeline phase 2 India",
    # Assaluyeh-Iranshahr gas pipeline (Iran, southern IGAT leg to Baluchestan)
    "خط لوله گاز عسلویه - ایرانشهر": "Assaluyeh Iranshahr gas pipeline Iran",
    # Iran First National Gas Pipeline (IGAT-1) - may be already routed; included for completeness
    "خط لوله اول سراسری گاز ایران": "Iran gas trunk line 1 IGAT first national",
    # TAPI - Turkmenistan-Afghanistan-Pakistan-India pipeline (abbreviated in OSM as "TAPI")
    "TAPI": "Turkmenistan Afghanistan Pakistan India gas pipeline",
    # Central Asia-Center gas pipeline (Turkmenistan/Uzbekistan to Russia, Russian name)
    "Средняя Азия - Центр": "Central Asia Center gas pipeline",
    # Bukhara-Tashkent-Bishkek-Almaty gas pipeline (Uzbek/Russian OSM names)
    "Бухара - Ташкент - Бишкек - Алматы": "Bukhara Tashkent Bishkek Almaty gas pipeline",
    "Газопровод Бухара - Ташкент - Бишкек - Алматы": "Bukhara Tashkent Bishkek Almaty gas pipeline",
    # SRTO-Surgut-Omsk (SAC) - if OSM has a different Cyrillic name
    "СРТО — Центр 1": "SRTO Center gas pipeline 1",
    "СРТО — Центр 2": "SRTO Center gas pipeline 2",
    # Russia western Siberia oil/gas pipelines (Cyrillic OSM names)
    "Усть-Балык-Омск": "Ust Balyk Omsk oil pipeline",
    "Уренгой — Челябинск 1": "Urengoy Chelyabinsk gas pipeline 1",
    "Уренгой — Челябинск 2": "Urengoy Chelyabinsk gas pipeline 2",
    "Нижневартовский ГПЗ — Парабель — Кузбасс": "Nizhnevartovsk Parabel Kuzbass gas pipeline",
    "Газопровод «Нижневартовский ГПЗ — Парабель — Кузбасс»": "Nizhnevartovsk Parabel Kuzbass gas pipeline",
    # Bolivia: OSM uses endpoint-pair name; WM uses GIJA/Yacuiba formulation
    "Gasoducto Yacuiba Río Grande": "Bolivia Argentina Yacuiba GIJA gas pipeline",
    # Argentina Cordillerano: OSM short name has only 1 distinctive token vs WM's 3
    "Gasoducto Cordillerano": "Cordillerano Patagónico north Argentina gas pipeline",
    # China WEP Lundu branch (additional line, no name:en on OSM)
    "西气东输二线轮吐支干线": "West East Gas Pipeline 2 Lundu branch Xinjiang",
    # Kazakhstan-China Oil Pipeline (Atasu-Alashankou section -> connects to Shanshan-Lanzhou)
    "Kazakhstan - China Oil Pipeline (Atasu - Alashankou section)": "western crude oil pipeline shanshan lanzhou Kazakhstan Alashankou",
    # Myanmar-China pipeline (the two WM entries are Myanmar-side; OSM tags the full route)
    "Myanmar - China pipeline": "Sino Myanmar oil gas pipeline Myanmar",
}

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
    ("china_ne",            42.0, 118.0,  55.0,  140.0), # Manchuria / NE China - China-Russia East Pipeline
    ("africa_n",            10.0, -18.0,  38.0,  38.0),  # North Africa, Horn, Sudan
    ("africa_w",            -5.0, -18.0,  18.0,  20.0),  # West Africa, Niger Delta
    ("africa_e",           -35.0,  20.0,  15.0,  55.0),  # East Africa, Southern Africa
    ("latam_n",             -5.0, -85.0,  18.0, -50.0),  # Colombia, Venezuela, Peru, Ecuador, Brazil N
    ("latam_s",            -60.0, -80.0,  -5.0, -30.0),  # Argentina, Chile, Brazil S, Bolivia
    ("mexico_ca",           14.0,-120.0,  34.0, -82.0),  # Mexico, Central America
    # Canada split into west/east to avoid Overpass timeout on the full-country bbox
    ("canada_west",         48.0,-145.0,  65.0,-105.0),  # BC, Alberta, NWT west (NGTL, Westcoast, Cold Lake)
    ("canada_east",         42.0,-110.0,  56.0, -52.0),  # Prairies, Ontario, Quebec, Maritimes
    ("north_sea",           54.0,  -5.0,  68.0,  12.0),  # Norwegian shelf, UK shelf, Langeled, Åsgard
    ("oceania",            -50.0, 105.0,   5.0, 180.0),  # Australia + Pacific
    # US split into sub-regions to avoid Overpass timeout on the full continental bbox
    ("us_northeast",        37.0, -83.0,  48.0, -66.0),  # PA, OH, NY, NE - Mariner, Utopia, Dominion
    ("us_southeast",        25.0, -92.0,  37.0, -75.0),  # VA, KY, TN, NC, SC, GA, FL
    ("us_gulf",             24.0,-100.0,  33.0, -87.0),  # TX/LA/MS/AL Gulf Coast - NGL, offshore
    ("us_permian",          28.0,-107.0,  34.0, -88.0),  # West TX/NM Permian Basin gap coverage
    ("us_midcontinent",     33.0,-104.0,  40.0, -90.0),  # OK, KS, AR, MO, IL - intrastate
    ("us_rockies_north",    40.0,-116.0,  50.0, -96.0),  # WY, MT, ND, SD, CO north, NE - Overland Pass, Bakken
    ("us_west",             32.0,-125.0,  49.0,-109.0),  # CA, OR, WA, NV, AZ, NM, UT, ID
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
    r"\bbrasil\b": "brazil",             # Portuguese/Spanish -> English for Bolivia-Brazil match
    r"\bneuba\b": "neuquen buenos aires",  # NEUBA = Neuquen-Buenos Aires gas pipeline
    # Compound Spanish pipeline names - expand to final form directly (ordering: these run after
    # \bandino\b already fired, so must include the andean substitution inline)
    r"\bnorandino\b": "nor andean",       # NorAndino -> "nor andean" (pre-expanded)
    r"\btransandino\b": "trans andean",   # Transandino -> "trans andean"
    r"\btransecuatoriano\b": "trans ecuadorian",  # SOTE Transecuatoriano
    r"\becuatoriano\b": "ecuadorian",             # standalone (after hyphen split: "Trans-Ecuatoriano")
    r"\bnorperuano\b": "north peruvian",           # Oleoducto NorPeruano compound form
    r"\bnororiental\b": "northeastern",            # nororiental in Venezuelan gas pipeline names
    # SOTE abbreviation: expand to final form matching OSM's full Spanish name expanded tokens
    r"\bsote\b": "system trans ecuadorian oil pipeline",
    # India pipeline abbreviations: expand so OSM short-form names match WM full names
    r"\bjhbdpl\b": "jagdishpur haldia bokaro dhamra",
    r"\bphbpl\b": "paradip haldia barauni",
    r"\bdvpl\b": "dahej vijaipur",
    r"\bhvj\b": "hazira vijaipur jagdishpur",
}


def _norm(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", str(s))
    # Non-ASCII chars: keep combining marks (they fuse harmlessly with base letters
    # under ASCII encoding); replace everything else (en-dashes, foreign punctuation,
    # etc.) with spaces so "Habshan–Fujairah" -> "Habshan Fujairah", not "habshanfujairah".
    buf = []
    for c in s:
        if c.isascii():
            buf.append(c)
        elif unicodedata.category(c).startswith("M"):
            buf.append(c)
        else:
            buf.append(" ")
    s = "".join(buf).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    for pat, repl in _EXPAND.items():
        s = re.sub(pat, repl, s)
    # Keep single-digit tokens (line numbers like "1", "2", "3") - they distinguish
    # numbered pipeline variants (e.g. West-East Line 2 vs Line 3).
    return {w for w in s.split() if (len(w) > 1 or w.isdigit()) and w not in _STOP}


_GENERIC_TOKENS = {
    "pipeline", "gas", "oil", "crude", "natural", "petroleum",
    "system", "main", "line", "pipe", "ngl", "products", "refined",
}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    # Score only on distinctive tokens - generic terms (gas, oil, pipeline...) inflate
    # scores for pipelines that share only commodity/type words, causing false positives.
    # Example: "SRTO Surgut Omsk Gas Pipeline" vs "SRTO Ural Gas Pipeline" would score
    # 0.43 if gas+pipeline count, but 0.20 on filtered tokens (correctly rejected).
    af = a - _GENERIC_TOKENS
    bf = b - _GENERIC_TOKENS
    if not af or not bf:
        return 0.0
    inter = af & bf
    if not inter:
        return 0.0
    return len(inter) / len(af | bf)


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

    Preference: name:en > int_name > alt_name > translated foreign name > name
    For non-Latin script regions (China, Russia, Iran, Arab), name:en is checked
    first; if absent, the raw `name` is looked up in _FOREIGN_NAME_MAP to get a
    matchable English equivalent before falling back to the raw string.
    """
    for key in ("name:en", "int_name", "alt_name"):
        val = (tags.get(key) or "").strip()
        if val and not _is_generic_osm_name(val):
            return val
    raw_name = (tags.get("name") or "").strip()
    if raw_name:
        translated = _FOREIGN_NAME_MAP.get(raw_name)
        if translated and not _is_generic_osm_name(translated):
            return translated
    if raw_name and not _is_generic_osm_name(raw_name):
        return raw_name
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

        # Sanity check: at least one WM endpoint must be within MAX_SNAP_KM_THRESHOLD
        # of the NEAREST OSM way point (not the centroid, which fails for pipelines
        # that are 3000+ km long where sub-section WM entries sit far from the centroid).
        ways = osm_groups[best_name]
        all_pts = [pt for w in ways for pt in w]
        if not all_pts:
            continue
        # Sample points evenly - full scan is too slow for 10k+ point pipelines
        step = max(1, len(all_pts) // 600)
        sample = all_pts[::step]
        d_start_min = min(_hav(pipe["start_lat"], pipe["start_lon"], p[0], p[1]) for p in sample)
        d_end_min = min(_hav(pipe["end_lat"], pipe["end_lon"], p[0], p[1]) for p in sample)
        if min(d_start_min, d_end_min) > MAX_SNAP_KM_THRESHOLD:
            continue

        chained = greedy_chain(ways)
        total_pts = sum(len(s) for s in chained)
        if chained and total_pts >= 4:  # require at least 4 pts - rejects stub ways
            results.append((pipe, best_name, best_score, chained, d_start_min, d_end_min))

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
        for pipe, osm_name, score, chained, d_start_min, d_end_min in matches:
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
            # Reject stub matches: a named pipeline should span at least 30km
            if path_km < 30:
                print(
                    f"    [stub<30km] {pipe['wm_id']!r:46} <- {osm_short!r} score={score:.2f} {path_km:.0f} km",
                    flush=True,
                )
                continue
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
                    [pipe["wm_id"], n_pts, path_km, d_start_min, d_end_min, json.dumps(simplified)],
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
