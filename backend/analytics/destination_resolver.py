"""Resolve an AIS `destination` free-text string to a seaport with coordinates.

The AIS destination field is hand-typed by the crew and unreliable - abbreviations
("RTM"), UN/LOCODE codes ("NLRTM", "BE ANR"), full names ("ROTTERDAM"), stale junk
("FOR ORDERS"), and terminal descriptors ("ROTTERDAM 3E PETROHA"). We never use it
as an ETA *target* blindly, but a resolver that turns it into a real port with
coordinates lets us show a computed ETA to the *reported* destination alongside the
model's ETA to geometric targets (what the paid AIS products do).

Resolution is a cascade, most-confident first:
  1. **UN/LOCODE exact** - the 5-char code (with or without an internal space, e.g.
     "NLRTM" / "NL RTM") looked up directly. Highest confidence.
  2. **Exact normalized name** - "ROTTERDAM" -> Rotterdam. Prefers the primary port
     over same-name terminals ("Busan" over "Busan New Port").
  3. **Fuzzy name** (rapidfuzz WRatio) - handles aliases/typos/terminal suffixes
     ("ANTWERP" -> Antwerpen, "ROTTERDAM 3E PETROHA" -> Rotterdam) above a score
     cutoff; junk ("FOR ORDERS") scores below it and stays unresolved.

Same-name collisions (e.g. Rotterdam NL vs Rotterdam NY) are broken by proximity to
the vessel's current position when supplied. Gazetteer is the committed
`data/unlocode_seaports.csv` (built by `scripts`/`build_gazetteer` from the free
UN/LOCODE list); no runtime network dependency. Coordinate coverage is UN/LOCODE's
(coordless major ports were coord-filled from same-name ports at build time); a few
ports it lacks entirely stay unresolved - the honest outcome, not a fabricated point.
"""

from __future__ import annotations

import csv
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

_GAZETTEER = Path(__file__).resolve().parent / "data" / "unlocode_seaports.csv"

# rapidfuzz WRatio cutoff for a fuzzy name match. 86 accepts "ANTWERP"->"Antwerpen"
# (87.5) and terminal-suffixed names while still rejecting unrelated junk.
_FUZZY_CUTOFF = 86.0

# Tokens that carry no destination signal - stripped before name matching so a
# string that is *only* junk resolves to nothing.
_JUNK = frozenset({
    "FOR", "ORDERS", "ORDER", "ORDRES", "VIA", "TBN", "TBC", "TBA", "NIL", "NONE",
    "UNKNOWN", "NA", "OFFSHORE", "ANCHORAGE", "ANCH", "ROADS", "ROAD", "OPL",
    "BUNKERING", "BUNKER", "BUNKERS", "PILOT", "STATION", "OUTER", "INNER",
    "TERMINAL", "BERTH", "JETTY", "DOCK", "PORT", "SEA", "HIGH", "WAITING",
})


@dataclass(frozen=True)
class ResolvedPort:
    """A destination string resolved to a seaport."""

    locode: str
    name: str
    lat: float
    lon: float
    score: float          # 100 for code/exact-name, WRatio score for fuzzy
    method: str           # 'locode' | 'name-exact' | 'fuzzy'


def _norm(s: str) -> str:
    """Uppercase, de-accent, keep alnum+space, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s.upper())).strip()


class _Gazetteer:
    """Loaded seaport gazetteer with LOCODE, exact-name, and fuzzy-name indexes."""

    def __init__(self, path: Path = _GAZETTEER) -> None:
        self.by_locode: dict[str, tuple[str, float, float]] = {}
        self.by_name: dict[str, list[tuple[str, str, float, float]]] = {}
        self.names: list[str] = []
        self._name_rows: list[tuple[str, str, float, float]] = []  # aligned to self.names
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                locode, name = r["locode"], r["name"]
                lat, lon = float(r["lat"]), float(r["lon"])
                nnm = _norm(name)
                self.by_locode[locode] = (name, lat, lon)
                row = (locode, name, lat, lon)
                self.by_name.setdefault(nnm, []).append(row)
                self.names.append(nnm)
                self._name_rows.append(row)

    def locode(self, code: str) -> tuple[str, float, float] | None:
        return self.by_locode.get(code)


@lru_cache(maxsize=1)
def _gazetteer() -> _Gazetteer:
    return _Gazetteer()


def _try_locode(s: str) -> str | None:
    """A valid 5-char UN/LOCODE embedded in the string ("NLRTM", "NL RTM", leading token)."""
    g = _gazetteer()
    n = _norm(s)
    compact = n.replace(" ", "")
    if len(compact) == 5 and compact.isalnum() and compact in g.by_locode:
        return compact
    toks = n.split()
    if toks and len(toks[0]) == 5 and toks[0] in g.by_locode:
        return toks[0]
    if len(toks) >= 2 and len(toks[0]) == 2 and len(toks[1]) == 3 and (toks[0] + toks[1]) in g.by_locode:
        return toks[0] + toks[1]
    return None


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _nearest(rows: list[tuple[str, str, float, float]], lat: float | None, lon: float | None):
    """Pick the row nearest the vessel (tiebreak for same-name ports); else the first."""
    if lat is None or lon is None or len(rows) == 1:
        return rows[0]
    return min(rows, key=lambda r: _haversine_nm(lat, lon, r[2], r[3]))


def resolve(
    destination: str | None,
    vessel_lat: float | None = None,
    vessel_lon: float | None = None,
    fuzzy_cutoff: float = _FUZZY_CUTOFF,
) -> ResolvedPort | None:
    """Resolve an AIS destination string to a `ResolvedPort`, or None if unresolvable.

    `vessel_lat`/`vessel_lon` (optional) disambiguate same-name ports by proximity.
    Returns None for empty/junk strings and for names below the fuzzy cutoff - a
    deliberate non-answer rather than a low-confidence guess.
    """
    if not destination or not destination.strip():
        return None
    g = _gazetteer()

    lc = _try_locode(destination)
    if lc:
        name, lat, lon = g.by_locode[lc]
        return ResolvedPort(lc, name, lat, lon, 100.0, "locode")

    # Strip route noise: keep the leg after a route arrow, drop anything after VIA.
    q = re.split(r"\bVIA\b", _norm(destination))[0].split(">")[-1].strip()
    toks = [t for t in q.split() if t not in _JUNK]
    if not toks:
        return None
    query = " ".join(toks)

    if query in g.by_name:
        locode, name, lat, lon = _nearest(g.by_name[query], vessel_lat, vessel_lon)
        return ResolvedPort(locode, name, lat, lon, 100.0, "name-exact")

    cands = process.extract(query, g.names, scorer=fuzz.WRatio, limit=12, score_cutoff=fuzzy_cutoff)
    if not cands:
        return None
    top = cands[0][1]
    rows = [g._name_rows[c[2]] for c in cands if c[1] >= top - 1]
    locode, name, lat, lon = _nearest(rows, vessel_lat, vessel_lon)
    return ResolvedPort(locode, name, lat, lon, top, "fuzzy")
