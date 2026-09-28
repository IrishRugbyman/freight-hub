"""Port and terminal reference data, and the destination-string canonicalisation
that maps free-text AIS destinations onto it.

Pure data plus pure functions: no FastAPI, no DB. The analytics batch job imports
the terminal dictionaries from here (``analytics/eta_labels.py``).
"""

from __future__ import annotations

import re


def norm_dest(s: str) -> str:
    """Normalize an AIS destination string: strip garbage chars, collapse whitespace, uppercase."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", s.strip().upper())).strip()


# Canonical port resolver for the live destination-distribution lists.
#
# Raw AIS `destination` is free-text: the same physical port shows up as a
# UN/LOCODE ("NLRTM"), the spaced LOCODE ("NL RTM"), the city name ("ROTTERDAM"),
# and name+berth strings ("ROTTERDAM 3E PETROHA", "ROTTERDAM BOTLEK BO"). Grouped
# raw, one port becomes five rows. This is purely for *descriptive* aggregation of
# what the fleet is broadcasting - it is NOT an ETA target (those stay geometric,
# per the True ETA rule). `EUR_TERMINALS` is deliberately coarse (it lumps
# Amsterdam/Ghent into Rotterdam/Antwerp energy clusters), so it is unsuitable
# here; this map keeps each city distinct.
#
# canonical display name -> aliases (LOCODEs without the internal space + name
# spellings, all in norm_dest form). The first token of a name+berth string is
# matched too, so unlisted berths still fold into the city.
_PORT_CANON: dict[str, list[str]] = {
    # NW Europe - Netherlands
    "Rotterdam": [
        "NLRTM",
        "ROTTERDAM",
        "EUROPOORT",
        "BOTLEK",
        "MAASVLAKTE",
        "PERNIS",
        "HARTELHAVEN",
        "CALANDKANAAL",
        "MAASHAVEN",
        "MERWEHAVEN",
        "NECKARHAVEN",
        "DINTELHAVEN",
        "YANGTZEKANAAL",
        "PRINSES AMALIAHAVEN",
    ],
    "Amsterdam": ["NLAMS", "AMSTERDAM"],
    "Dordrecht": ["NLDOR", "DORDRECHT"],
    "Moerdijk": ["NLMOE", "MOERDIJK"],
    "Vlaardingen": ["NLVLA", "VLAARDINGEN"],
    "Harlingen": ["NLHAR", "HARLINGEN"],
    "Delfzijl": ["NLDZL", "DELFZIJL", "EEMSHAVEN", "NLEEM"],
    "Terneuzen": ["NLTNZ", "TERNEUZEN"],
    "Vlissingen": ["NLVLI", "VLISSINGEN", "FLUSHING"],
    # NW Europe - Belgium
    "Antwerp": [
        "BEANR",
        "ANTWERPEN",
        "ANTWERP",
        "KALLO",
        "DEURGANCKDOK",
        "LEOPOLDDOK",
        "CHURCHILLDOK",
        "KANAALDOK",
    ],
    "Ghent": ["BEGNE", "GENT", "GHENT", "KLUIZENDOK"],
    "Zeebrugge": ["BEZEE", "ZEEBRUGGE", "ZEEBRUGE"],
    # NW Europe - France / UK / Ireland
    "Dunkirk": ["FRDKK", "DUNKIRK", "DUNKERQUE"],
    "Le Havre": ["FRLEH", "LEHAVRE", "LE HAVRE", "HAVRE"],
    "Southampton": ["GBSOU", "SOUTHAMPTON"],
    "Fawley": ["GBFAW", "FAWLEY"],
    "Tilbury": ["GBTIL", "TILBURY"],
    "Immingham": ["GBIMM", "IMMINGHAM"],
    "Teesside": ["GBTEE", "TEESSIDE", "TEESPORT"],
    "Liverpool": ["GBLIV", "LIVERPOOL"],
    "Dublin": ["IEDUB", "DUBLIN"],
    # Mediterranean / Iberia
    "Gibraltar": ["GIGIB", "GIBRALTAR"],
    "Algeciras": ["ESALG", "ALGECIRAS"],
    "Huelva": ["ESHUV", "HUELVA"],
    "Valencia": ["ESVLC", "VALENCIA"],
    "Trieste": ["ITTRS", "TRIESTE"],
    # Scandinavia / Baltic states
    "Gothenburg": ["SEGOT", "GOTHENBURG", "GOTEBORG"],
    "Helsinki": ["FIHEL", "HELSINKI"],
    "Turku": ["FITKU", "TURKU"],
    "Hamina": ["FIHMN", "HAMINA"],
    "Gdansk": ["PLGDN", "GDANSK"],
    "Klaipeda": ["LTKLJ", "KLAIPEDA"],
    # Russia / Eastern Europe
    "St. Petersburg": ["RULED", "ST PETERSBURG", "SAINT PETERSBURG"],
    "Ust-Luga": ["RUULU", "USTLUGA"],
    "Kaliningrad": ["RUKGD", "KALININGRAD"],
    "Novorossiysk": ["RUNVS", "NOVOROSSIYSK"],
    "Taman": ["RUTAM", "TAMAN"],
    "Constanta": ["ROCND", "CONSTANTA"],
    # Turkey
    "Istanbul": ["TRIST", "ISTANBUL"],
    "Tuzla": ["TRTUZ", "TUZLA"],
    "Izmit": ["TRIZT", "IZMIT"],
    "Izmir": ["TRIZM", "IZMIR"],
    "Ambarli": ["TRAMB", "AMBARLI"],
    "Dilovasi": ["TRDIL", "DILOVASI"],
    "Tekirdag": ["TRTEK", "TEKIRDAG"],
    # Black Sea / Caucasus
    "Bourgas": ["BGBOJ", "BOURGAS", "BURGAS"],
    "Poti": ["GEPTI", "POTI"],
    "Batumi": ["GEBUS", "BATUMI"],
    # Egypt / Middle East
    "Port Said": [
        "EGPSD",
        "EGPSE",
        "PORTSAID",
        "PORT SAID",
        "PORTSAIDOPL",
        "OPLPORTSAID",
        "PORTSAIDEGYPT",
    ],
    "Suez": ["EGSUZ", "SUEZ"],
    # Africa
    "Cape Town": ["ZACPT", "CAPE TOWN"],
    "Durban": ["ZADUR", "DURBAN"],
    # Morocco
    "Tangier Med": ["MAPTM", "TANGIER MED", "TANGIERMED"],
    # Israel
    "Ashdod": ["ILASH", "ASHDOD"],
    # Singapore
    "Singapore": ["SGSIN", "SINGAPORE"],
    # East Asia
    "Ulsan": ["KRUSN", "ULSAN"],
    "Busan": ["KRPUS", "BUSAN", "PUSAN"],
    "Incheon": ["KRINC", "INCHEON"],
    "Nagoya": ["JPNGO", "NAGOYA"],
    "Tokyo": ["JPTYO", "TOKYO"],
    # Americas
    "Houston": ["USHOU", "HOUSTON"],
    "Corpus Christi": ["USCRP", "CORPUS CHRISTI"],
    "Beaumont": ["USBPT", "BEAUMONT"],
    "New York": ["USNYC", "NEW YORK"],
    "Seattle": ["USSEA", "SEATTLE"],
    "Norfolk": ["USORF", "NORFOLK"],
    "Santos": ["BRSSZ", "SANTOS"],
    # Germany (Rhine/Kiel Canal)
    "Brunsbuttel": ["DEBRV", "BRUNSBUTTEL"],
    "Tallinn": ["EETLL", "TALLINN", "MUUGA"],
}

# Flat alias -> canonical name (normalised; LOCODE spaces removed so "NL RTM"
# collapses onto "NLRTM"). Longest aliases first so multi-word names win.
_PORT_ALIAS: dict[str, str] = {}
for _pcanon, _paliases in _PORT_CANON.items():
    for _pa in _paliases:
        _PORT_ALIAS[_pa.replace(" ", "")] = _pcanon

_LOCODE_RE = re.compile(r"^([A-Z]{2}) ([A-Z]{3})$")


def canonical_port(raw: str | None) -> str | None:
    """Fold a raw AIS destination onto a canonical port name for aggregation.

    Returns the canonical city for known ports (LOCODE / spaced LOCODE / name /
    name+berth), a title-cased clean string for unrecognised destinations, and
    None for empty/junk so callers can drop it. Never fabricates a match it is
    not confident about: unknown strings are normalised, not guessed at.
    """
    if not raw:
        return None
    norm = norm_dest(raw)
    if not norm or norm in {
        "FOR ORDERS",
        "FOR ORDER",
        "FORORDERS",
        "TO ORDER",
        "ORDERS",
        "ORDER",
        "TBN",
        "UNKNOWN",
        "NA",
        "NONE",
        "AT SEA",
        "AT ANCHOR",
        "DRIFTING",
        "GOF FOR ORDER",
    }:
        return None
    # Collapse a "XX YYY" spaced UN/LOCODE to "XXYYY" before lookup.
    m = _LOCODE_RE.match(norm)
    key = (m.group(1) + m.group(2)) if m else norm.replace(" ", "")
    if key in _PORT_ALIAS:
        return _PORT_ALIAS[key]
    # Name + berth/terminal suffix: fold on the leading token ("ROTTERDAM 3E ...").
    first = norm.split(" ", 1)[0]
    if first in _PORT_ALIAS:
        return _PORT_ALIAS[first]
    # Unrecognised: present a clean, de-duplicated label without guessing a port.
    # A LOCODE-shaped token (5 letters, e.g. "CN SHA"/"CNSHA") keeps its collapsed
    # uppercase form so spaced and unspaced spellings of an uncurated port merge;
    # everything else is title-cased with its words intact. No city is fabricated.
    if key.isalpha() and len(key) == 5:
        return key
    return norm.title()


# Route-style AIS destinations encode "ORIGIN>DESTINATION" (e.g. "NLRTM>USORF").
# Arrow separators are trusted unconditionally; the weak ones ("/", " - ", " TO ")
# only split when both legs look like a real port (>= 3 chars) so "N/A" is not
# mistaken for a route. "VIA" is special: "DEST VIA WAYPOINT" - the leg before
# VIA is the destination and there is no origin (the rest is a routing waypoint).
_ROUTE_ARROW_SEPS = ("<>", ">>", "=>", "->", ">")
_ROUTE_WEAK_SEPS = (" TO ", " - ", "/")


def split_route(raw: str | None) -> tuple[str | None, str | None]:
    """Split a raw AIS destination into ``(origin_raw, dest_raw)``.

    Returns ``(None, raw)`` when no route separator is present (a plain single
    port). Both legs are returned uppercased; canonicalisation is case-insensitive.
    """
    if not raw:
        return None, raw
    up = raw.strip().upper()
    if " VIA " in up:
        return None, up.split(" VIA ", 1)[0].strip()
    for sep in _ROUTE_ARROW_SEPS:
        if sep in up:
            parts = [p.strip() for p in up.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts[0], parts[-1]
            if len(parts) == 1:
                return None, parts[0]
    for sep in _ROUTE_WEAK_SEPS:
        if sep in up:
            parts = [p.strip() for p in up.split(sep) if len(p.strip()) >= 3]
            if len(parts) >= 2:
                return parts[0], parts[-1]
    return None, up


def canonical_destination(raw: str | None) -> str | None:
    """Harmonised arrival port for display/aggregation.

    Parses any ``origin>destination`` route and canonicalises only the
    destination leg, so "NLRTM>USORF" and "NL RTM > US ORF" both fold to
    "Norfolk". Returns None for empty/junk (caller drops it).
    """
    _, dest = split_route(raw)
    return canonical_port(dest)


def canonical_origin(raw: str | None) -> str | None:
    """Harmonised origin port from a route string, or None when the destination
    encodes no origin (plain single port, or a 'VIA' waypoint string)."""
    origin, _ = split_route(raw)
    return canonical_port(origin) if origin else None


# ---------------------------------------------------------------------------
# Curated ports (port-arrivals forecast targets)
# ---------------------------------------------------------------------------

# Curated major ports: LOCODE aliases + lat/lon for ETA computation.
# Alias list is used with substring matching after destination normalization.
CURATED_PORTS: dict[str, dict] = {
    "Rotterdam": {
        "lat": 51.96,
        "lon": 4.10,
        "aliases": ["NLRTM", "ROTTERDAM", "NLAMS", "AMSTERDAM", "NL RTM", "NLRTM", "DORDRECHT"],
    },
    "Antwerp": {
        "lat": 51.26,
        "lon": 4.40,
        "aliases": ["BEANR", "ANTWERPEN", "ANTWERP", "BE ANR", "GENT", "GHENT"],
    },
    "Singapore": {
        "lat": 1.26,
        "lon": 103.82,
        "aliases": ["SGSIN", "SINGAPORE", "SG SIN"],
    },
    "Busan": {
        "lat": 35.11,
        "lon": 129.04,
        "aliases": ["KRPUS", "BUSAN", "KR PUS", "PUSAN"],
    },
    "Ulsan": {
        "lat": 35.54,
        "lon": 129.39,
        "aliases": ["KRUSN", "ULSAN", "KR USN"],
    },
    "Houston": {
        "lat": 29.73,
        "lon": -95.08,
        "aliases": ["USHOU", "HOUSTON", "GALVESTON", "USGLS"],
    },
    "Fujairah": {
        "lat": 25.13,
        "lon": 56.34,
        "aliases": ["AEFJR", "FUJAIRAH", "FUJAIRA", "AEFUJ"],
    },
    "Port Said": {
        "lat": 31.28,
        "lon": 32.30,
        "aliases": ["EGPSD", "PORT SAID", "PORTSAID"],
    },
    "Gibraltar": {
        "lat": 36.14,
        "lon": -5.35,
        "aliases": ["GIGIB", "GIBRALTAR"],
    },
    "Port Klang": {
        "lat": 3.00,
        "lon": 101.37,
        "aliases": ["MYPKG", "PORT KLANG", "WESTPORT", "KLANG"],
    },
    "Durban": {
        "lat": -29.88,
        "lon": 31.04,
        "aliases": ["ZADUR", "DURBAN", "ZA DUR"],
    },
    "Trieste": {
        "lat": 45.65,
        "lon": 13.76,
        "aliases": ["ITTRS", "TRIESTE", "TRIST"],
    },
    "Algeciras": {
        "lat": 36.13,
        "lon": -5.45,
        "aliases": ["ESALG", "ALGECIRAS"],
    },
    "Qingdao": {
        "lat": 36.07,
        "lon": 120.33,
        "aliases": ["CNTAO", "QINGDAO", "TSINGTAO"],
    },
    "Shanghai": {
        "lat": 30.78,
        "lon": 121.96,
        "aliases": ["CNSHA", "SHANGHAI"],
    },
}

# Pre-build a flat lookup: normalized alias -> port name
_ALIAS_TO_PORT: dict[str, str] = {}
for _pname, _pdata in CURATED_PORTS.items():
    for _alias in _pdata["aliases"]:
        _ALIAS_TO_PORT[_alias] = _pname


def match_port(destination: str | None) -> str | None:
    """Return curated port name if destination matches any known alias, else None."""
    if not destination:
        return None
    norm = norm_dest(destination)
    if not norm:
        return None
    # Direct lookup first
    if norm in _ALIAS_TO_PORT:
        return _ALIAS_TO_PORT[norm]
    # Substring: alias appears inside destination (handles "FOR ORDERS ROTTERDAM")
    for alias, pname in _ALIAS_TO_PORT.items():
        if alias in norm:
            return pname
    return None


# ---------------------------------------------------------------------------
# European energy import terminals (european-inbound, ETA targets)
# ---------------------------------------------------------------------------

# Major European energy import terminals (crude + products + LNG).
# Separate from CURATED_PORTS (global) so port-arrivals is unaffected.
EUR_TERMINALS: dict[str, dict] = {
    # NW Europe
    "Rotterdam": {
        "lat": 51.96,
        "lon": 4.10,
        "region": "NW Europe",
        "aliases": [
            "NLRTM",
            "ROTTERDAM",
            "AMSTERDAM",
            "NLAMS",
            "NL RTM",
            "EUROPOORT",
            "DORDRECHT",
            "PERNIS",
            "BOTLEK",
        ],
    },
    "Antwerp": {
        "lat": 51.26,
        "lon": 4.40,
        "region": "NW Europe",
        "aliases": ["BEANR", "ANTWERPEN", "ANTWERP", "GENT", "GHENT", "BE ANR"],
    },
    "Zeebrugge": {
        "lat": 51.35,
        "lon": 3.20,
        "region": "NW Europe",
        "aliases": ["BEZEE", "ZEEBRUGGE", "BE ZEE", "ZEEBRUGE"],
    },
    "Hamburg": {
        "lat": 53.53,
        "lon": 9.97,
        "region": "NW Europe",
        "aliases": ["DEHAM", "HAMBURG", "DE HAM", "BRUNSBUETEL", "BRUNSBÜTTEL"],
    },
    "Wilhelmshaven": {
        "lat": 53.52,
        "lon": 8.16,
        "region": "NW Europe",
        "aliases": ["DEWVN", "WILHELMSHAVEN", "WILHELMSH"],
    },
    "Le Havre": {
        "lat": 49.49,
        "lon": 0.11,
        "region": "NW Europe",
        "aliases": ["FRLEH", "LE HAVRE", "LEHAVRE", "HAVRE"],
    },
    "Milford Haven": {
        "lat": 51.71,
        "lon": -5.03,
        "region": "NW Europe",
        "aliases": ["GBMFH", "MILFORD HAVEN", "MILFORDHAVEN", "PEMBROKE", "SOUTH HOOK"],
    },
    # Mediterranean
    "Fos-Marseille": {
        "lat": 43.40,
        "lon": 5.10,
        "region": "Mediterranean",
        "aliases": [
            "FRFOS",
            "FOS SUR MER",
            "FOSSURMER",
            "FOS-SUR-MER",
            "MARSEILLE",
            "FRMRS",
            "LAVERA",
        ],
    },
    "Barcelona": {
        "lat": 41.32,
        "lon": 2.16,
        "region": "Mediterranean",
        "aliases": ["ESBCN", "BARCELONA"],
    },
    "Huelva": {
        "lat": 37.26,
        "lon": -6.94,
        "region": "Mediterranean",
        "aliases": ["ESHUE", "HUELVA"],
    },
    "Sines": {
        "lat": 37.95,
        "lon": -8.87,
        "region": "Mediterranean",
        "aliases": ["PTSIN", "SINES"],
    },
    "Genova": {
        "lat": 44.40,
        "lon": 8.93,
        "region": "Mediterranean",
        "aliases": ["ITGOA", "GENOVA", "GENOA"],
    },
    "Trieste": {
        "lat": 45.65,
        "lon": 13.76,
        "region": "Mediterranean",
        "aliases": ["ITTRS", "TRIESTE"],
    },
    "Augusta": {
        "lat": 37.22,
        "lon": 15.22,
        "region": "Mediterranean",
        "aliases": ["ITAUG", "AUGUSTA", "MILAZZO"],
    },
    "Algeciras": {
        "lat": 36.13,
        "lon": -5.45,
        "region": "Mediterranean",
        "aliases": ["ESALG", "ALGECIRAS"],
    },
    # Baltic
    "Gdansk": {
        "lat": 54.40,
        "lon": 18.66,
        "region": "Baltic",
        "aliases": ["PLGDN", "GDANSK", "GDYNIA", "PL GDN", "GDYNIA"],
    },
    # UK terminals (LNG + crude)
    "Immingham": {
        "lat": 53.62,
        "lon": -0.19,
        "region": "NW Europe",
        "aliases": ["GBIMM", "IMMINGHAM", "HUMBER"],
    },
    "Grangemouth": {
        "lat": 56.02,
        "lon": -3.72,
        "region": "NW Europe",
        "aliases": ["GBGMO", "GRANGEMOUTH", "HOUND POINT", "FORTH"],
    },
    "Teesside": {
        "lat": 54.62,
        "lon": -1.16,
        "region": "NW Europe",
        "aliases": ["GBTES", "TEESSIDE", "TEES", "MIDDLESBROUGH"],
    },
}

# Pre-built flat lookup: normalised alias -> port name
_EUR_ALIAS_TO_PORT: dict[str, str] = {}
for _epname, _epdata in EUR_TERMINALS.items():
    for _ealias in _epdata["aliases"]:
        _EUR_ALIAS_TO_PORT[_ealias] = _epname


def match_eur_port(destination: str | None) -> str | None:
    """Return European terminal name if the AIS destination matches, else None."""
    if not destination:
        return None
    norm = norm_dest(destination)
    if not norm:
        return None
    if norm in _EUR_ALIAS_TO_PORT:
        return _EUR_ALIAS_TO_PORT[norm]
    for alias, pname in _EUR_ALIAS_TO_PORT.items():
        if alias in norm:
            return pname
    return None


# ---------------------------------------------------------------------------
# LNG terminals: European regas and US loading (lng-inbound, ETA targets)
# ---------------------------------------------------------------------------

# European LNG import terminals (key regas facilities only)
LNG_EU_TERMINALS: dict[str, dict] = {
    "Gate LNG Rotterdam": {
        "lat": 51.93,
        "lon": 3.95,
        "country": "Netherlands",
        "aliases": ["NLRTM", "ROTTERDAM", "GATE", "MAASVLAKTE"],
    },
    "Zeebrugge": {
        "lat": 51.36,
        "lon": 3.19,
        "country": "Belgium",
        "aliases": ["BEZEE", "BEEBRUGGE", "ZEEBRUGGE"],
    },
    "Dunkerque LNG": {
        "lat": 51.03,
        "lon": 2.37,
        "country": "France",
        "aliases": ["FRDKK", "DUNKERQUE", "DUNKIRK", "DUNKIRQUE"],
    },
    "Montoir de Bretagne": {
        "lat": 47.26,
        "lon": -2.14,
        "country": "France",
        "aliases": ["FRMTX", "MONTOIR", "NANTES", "SAINT NAZAIRE"],
    },
    "South Hook / Milford Haven": {
        "lat": 51.70,
        "lon": -5.04,
        "country": "UK",
        "aliases": ["GBMIL", "MILFORD", "SOUTH HOOK", "SOUTHHOOK", "GBSWH"],
    },
    "Isle of Grain": {
        "lat": 51.44,
        "lon": 0.72,
        "country": "UK",
        "aliases": ["GBGRA", "ISLE OF GRAIN", "GRAIN", "GBCAN", "CANVEY"],
    },
    "Dragon LNG": {
        "lat": 51.69,
        "lon": -5.00,
        "country": "UK",
        "aliases": ["GBDRA", "DRAGON", "DRAGON LNG"],
    },
    "Eemshaven": {
        "lat": 53.42,
        "lon": 6.85,
        "country": "Netherlands",
        "aliases": ["NLEEM", "EEMSHAVEN", "EEMS"],
    },
    "Świnoujście": {
        "lat": 53.93,
        "lon": 14.22,
        "country": "Poland",
        "aliases": ["PLSWI", "SWINOUJSCIE", "SWINOUJŚCIE", "SWINEMUNDE", "POLAND LNG"],
    },
    "Revithoussa": {
        "lat": 37.96,
        "lon": 23.37,
        "country": "Greece",
        "aliases": ["GRREV", "REVITHOUSSA", "REVITHUSA", "PIRAEUS LNG"],
    },
    "Porto Levante": {
        "lat": 44.99,
        "lon": 12.32,
        "country": "Italy",
        "aliases": ["ITLEV", "PORTO LEVANTE", "ADRIA", "OLT"],
    },
    "Panigaglia": {
        "lat": 44.10,
        "lon": 9.86,
        "country": "Italy",
        "aliases": ["ITPAN", "PANIGAGLIA", "LA SPEZIA LNG"],
    },
    "Livorno FSRU": {
        "lat": 43.57,
        "lon": 10.32,
        "country": "Italy",
        "aliases": ["ITLIV", "LIVORNO", "TOSCANA LNG"],
    },
    "Barcelona LNG": {
        "lat": 41.35,
        "lon": 2.18,
        "country": "Spain",
        "aliases": ["ESBCN", "BARCELONA LNG", "BCN LNG"],
    },
    "Mugardos / Ferrol": {
        "lat": 43.46,
        "lon": -8.23,
        "country": "Spain",
        "aliases": ["ESFER", "MUGARDOS", "FERROL", "GNLC"],
    },
    "Huelva LNG": {
        "lat": 37.24,
        "lon": -6.95,
        "country": "Spain",
        "aliases": ["ESHUE", "ESHUV", "HUELVA", "ESHU"],
    },
    "Sagunto": {
        "lat": 39.66,
        "lon": -0.23,
        "country": "Spain",
        "aliases": ["ESSAG", "SAGUNTO"],
    },
    "Cartagena LNG": {
        "lat": 37.60,
        "lon": -0.98,
        "country": "Spain",
        "aliases": ["ESACT", "CARTAGENA LNG"],
    },
    "Krk FSRU": {
        "lat": 45.10,
        "lon": 14.60,
        "country": "Croatia",
        "aliases": ["HRKRK", "KRK", "OMISALJ", "LNG CROATIA"],
    },
    "Klaipeda FSRU": {
        "lat": 55.72,
        "lon": 21.13,
        "country": "Lithuania",
        "aliases": ["LTKLA", "KLAIPEDA", "INDEPENDENCE", "LITGAS"],
    },
    "Nynashamn FSRU": {
        "lat": 58.91,
        "lon": 17.95,
        "country": "Sweden",
        "aliases": ["SENYN", "NYNASHAMN", "NYNASHAM", "FSRU ENERGOS POWER"],
    },
    "Manga LNG": {
        "lat": 65.03,
        "lon": 25.43,
        "country": "Finland",
        "aliases": ["FIMNG", "MANGA", "MANGA LNG", "FITKU", "OULU"],
    },
}

_LNG_ALIAS_MAP: dict[str, str] = {}
for _tname, _tdata in LNG_EU_TERMINALS.items():
    for _alias in _tdata["aliases"]:
        _LNG_ALIAS_MAP[_alias] = _tname


def match_lng_terminal(destination: str | None) -> str | None:
    """Resolve a raw AIS destination to a European LNG terminal name, or ``None``."""
    if not destination:
        return None
    norm = norm_dest(destination)
    if not norm:
        return None
    if norm in _LNG_ALIAS_MAP:
        return _LNG_ALIAS_MAP[norm]
    for alias, tname in _LNG_ALIAS_MAP.items():
        if alias in norm:
            return tname
    return None


# US LNG loading terminals (lat/lon + 60nm radius for proximity match)
US_LNG_LOADING_TERMINALS: list[dict] = [
    {"name": "Sabine Pass", "lat": 29.73, "lon": -93.87},
    {"name": "Calcasieu Pass (Venture Global)", "lat": 29.72, "lon": -93.34},
    {"name": "Corpus Christi LNG", "lat": 27.83, "lon": -97.40},
    {"name": "Freeport LNG", "lat": 28.94, "lon": -95.36},
    {"name": "Cove Point", "lat": 38.42, "lon": -76.38},
    {"name": "Elba Island", "lat": 31.93, "lon": -81.12},
]
