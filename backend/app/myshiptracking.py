"""MyShipTracking scraper - HTML scraping of myshiptracking.com vessel pages.

Complements Equasis (registry/ownership/compliance) and our own AIS analytics with
*movement* data that neither provides reliably:

- voyage history (last ~10 trips, <=3 months): origin/dest ports, dates, distance,
  duration, draught, avg/max speed, weather. Immutable once a trip has completed.
- port calls: port, arrival, departure.
- current voyage: destination, ETA, status, draught, exact lat/lon.
- particulars: GT/DWT/build/type/flag (used to backfill Equasis gaps).

The page is fully server-side rendered; the only XHR calls load ads + a "featured
company" box. So a single GET + BeautifulSoup is enough - no headless browser.

This is unofficial scraping (the real api.myshiptracking.com is paid). Treat it like
Equasis: slow and polite, on-demand per MMSI, persisted so we never re-scrape an
immutable trip. The crawler (registry/crawl_mst.py) is the only batch caller.

Live-only fields (destination/ETA/lat-lon/draught/status) are cached in-process for a
short TTL; the durable voyage/port-call history is persisted to DuckDB by the crawler.
"""

from __future__ import annotations

import html as _html
import logging
import re
import time
from dataclasses import asdict, dataclass, field

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_VESSEL_URL = "https://www.myshiptracking.com/vessels/{mmsi}-mmsi-{mmsi}-imo-{imo}"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Live fields go stale quickly; persisted history does not. Short TTL only guards
# against hammering the same vessel within one detail-panel session.
_CACHE_TTL = 15 * 60  # seconds
_cache: dict[int, tuple[float, VesselSnapshot | None]] = {}

# Rate-limit / bot-wall markers. myshiptracking throttles by IP. These are checked
# ONLY on pages that don't already look like a real vessel page - every page embeds
# the login recaptcha widget, so a bare "captcha" match is not a block signal.
_BLOCKED_RE = re.compile(
    r"too many requests|rate limit exceeded|you have been temporarily blocked|"
    r"access denied|error 1015|attention required.*cloudflare|are you a human",
    re.I,
)
# Positive marker that we got a real vessel page (not an error/empty/parked page).
_VESSEL_MARKERS = ("Current Position", "Current Trip", "Last Port Calls")


class MyShipTrackingBlocked(RuntimeError):
    """Raised when myshiptracking returns a rate-limit / bot-wall page."""


# --------------------------------------------------------------------------- #
# value normalizers
# --------------------------------------------------------------------------- #
def _num(text: str | None) -> float | None:
    """First number in a string ('30,201 Tons' -> 30201.0, '11.1 m' -> 11.1)."""
    if not text:
        return None
    # Commas are stripped first, so the pattern only has to cover plain decimals.
    # Written without overlapping quantifiers to keep matching linear.
    m = re.search(r"-?(?:\d+(?:\.\d+)?|\.\d+)", text.replace(",", ""))
    return float(m.group()) if m else None


def _int(text: str | None) -> int | None:
    v = _num(text)
    return int(v) if v is not None else None


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    t = text.strip()
    return t if t and t != "---" else None


def _eta(text: str | None) -> str | None:
    """'2026-06-29 21:30 (UTC)' -> '2026-06-29 21:30'."""
    if not text:
        return None
    m = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", text)
    return m.group() if m else None


def _data_txt(d: dict[str, str], key: str) -> str | None:
    """Read a trip data-* attr, stripping any embedded HTML (anchor markup in ports)."""
    raw = d.get(key)
    if raw is None:
        return None
    return _clean(BeautifulSoup(_html.unescape(raw), "html.parser").get_text(" ", strip=True))


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass
class Voyage:
    """A single completed trip. Immutable once arrived - dedup on (origin, deptime, dest)."""

    origin: str | None = None
    departure: str | None = None  # 'YYYY-MM-DD HH:MM'
    destination: str | None = None
    arrival: str | None = None
    distance_nm: float | None = None
    duration: str | None = None
    draught_m: float | None = None
    avg_speed_kn: float | None = None
    max_speed_kn: float | None = None
    stops: int | None = None

    def key(self) -> str:
        return f"{self.origin}|{self.departure}|{self.destination}"


@dataclass
class PortCall:
    port: str | None = None
    arrival: str | None = None
    departure: str | None = None


@dataclass
class VesselSnapshot:
    """Full parse of a vessel page: durable particulars + history + volatile live state."""

    mmsi: int | None = None
    imo: int | None = None
    name: str | None = None
    # particulars (quasi-static)
    flag: str | None = None
    call_sign: str | None = None
    ship_type: str | None = None
    length_m: float | None = None
    beam_m: float | None = None
    gross_tonnage: int | None = None
    dwt: int | None = None
    year_built: int | None = None
    # live (volatile)
    lat: float | None = None
    lon: float | None = None
    status: str | None = None
    course: float | None = None
    area: str | None = None
    station: str | None = None  # T-AIS (terrestrial) / S-AIS (satellite)
    draught_m: float | None = None
    destination: str | None = None
    eta: str | None = None
    position_received_utc: str | None = None
    # durable history
    voyages: list[Voyage] = field(default_factory=list)
    port_calls: list[PortCall] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _th_td(table) -> dict[str, str]:
    out: dict[str, str] = {}
    for tr in table.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            out[th.get_text(" ", strip=True)] = td.get_text(" ", strip=True)
    return out


def is_blocked(html: str) -> bool:
    return bool(_BLOCKED_RE.search(html))


def looks_like_vessel_page(html: str) -> bool:
    return any(m in html for m in _VESSEL_MARKERS)


def parse(html: str, mmsi: int | None = None) -> VesselSnapshot:
    """Parse a vessel page into a VesselSnapshot. Pure (no I/O)."""
    soup = BeautifulSoup(html, "html.parser")
    snap = VesselSnapshot(mmsi=mmsi)

    # name from <title>: "HAPPY LADY - Oil/Chemical Tanker (IMO: ..., MMSI: ...)"
    if soup.title:
        snap.name = _clean(soup.title.get_text().split(" - ")[0])

    # merge every th/td table (particulars + position + voyage stats)
    flat: dict[str, str] = {}
    for t in soup.find_all("table"):
        for k, v in _th_td(t).items():
            flat.setdefault(k, v)  # first occurrence wins (particulars table is first)

    snap.imo = _int(flat.get("IMO"))
    if snap.mmsi is None:
        snap.mmsi = _int(flat.get("MMSI"))
    snap.flag = _clean(flat.get("Flag"))
    snap.call_sign = _clean(flat.get("Call Sign"))
    snap.ship_type = _clean(flat.get("Type"))
    snap.gross_tonnage = _int(flat.get("GT"))
    snap.dwt = _int(flat.get("DWT"))
    snap.year_built = _int(flat.get("Build"))
    snap.draught_m = _num(flat.get("Draught"))

    # live nav fields: read from the Current Position card specifically. The
    # particulars table also has a <th>Status</th> ("Active" = registry status), so
    # the merged dict would shadow the navigational status ("At anchor", "Under way").
    pos_card = soup.select_one("#ft-position")
    pos_table = pos_card.find("table") if pos_card else None
    pos = _th_td(pos_table) if pos_table else flat
    snap.status = _clean(pos.get("Status"))
    snap.course = _num(pos.get("Course"))
    snap.area = _clean(pos.get("Area"))
    snap.station = _clean(pos.get("Station"))

    # ship type also lives in the page header <h2>
    if not snap.ship_type:
        h2 = soup.find("h2")
        if h2:
            snap.ship_type = _clean(h2.get_text(strip=True))

    # size "183 x 32 m" -> length, beam
    size = flat.get("Size")
    if size:
        m = re.search(r"([\d.]+)\s*[x×]\s*([\d.]+)", size)
        if m:
            snap.length_m = float(m.group(1))
            snap.beam_m = float(m.group(2))

    # exact lat/lon: position table masks them ('---') for anonymous visitors, but
    # they leak in the contributorMap.php ajax URL embedded in the page.
    m = re.search(
        r"contributorMap\.php\?lat=(-?\d+(?:\.\d+)?)&lng=(-?\d+(?:\.\d+)?)",
        html,
    )
    if m:
        snap.lat = float(m.group(1))
        snap.lon = float(m.group(2))

    # precise position timestamp (tooltip title attr, UTC)
    m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"></i>', html)
    if m:
        snap.position_received_utc = m.group(1)

    # current trip destination + ETA: two .myst-arrival-cont blocks (origin + dest);
    # the destination one carries the ETA* label.
    for arr in soup.select(".myst-arrival-cont"):
        eta_small = next((s for s in arr.find_all("small") if "ETA" in s.get_text()), None)
        if not eta_small:
            continue
        dest_h = arr.find("h3")
        if dest_h:
            snap.destination = _clean(dest_h.get_text(strip=True))
        eta_div = eta_small.find_parent("div").find_next_sibling("div")
        if eta_div:
            snap.eta = _eta(eta_div.get_text(" ", strip=True))
        break

    # last trips: rich data-* attrs (up to 10, <=3 months)
    for td in soup.select("td.tbl-ta-3m"):
        d = {k[5:]: v for k, v in td.attrs.items() if k.startswith("data-")}
        if not d:
            continue
        snap.voyages.append(
            Voyage(
                origin=_data_txt(d, "origin"),
                departure=_data_txt(d, "deptime"),
                destination=_data_txt(d, "dest"),
                arrival=_data_txt(d, "arrtime"),
                distance_nm=_num(_data_txt(d, "dist")),
                duration=_data_txt(d, "dur"),
                draught_m=_num(_data_txt(d, "draught")),
                avg_speed_kn=_num(_data_txt(d, "avg")),
                max_speed_kn=_num(_data_txt(d, "max")),
                stops=_int(_data_txt(d, "stops")),
            )
        )

    # last port calls
    for h3 in soup.find_all("h3"):
        if "Last Port Calls" not in h3.get_text():
            continue
        card = h3.find_parent("div", class_="card")
        tbl = card.find("table") if card else None
        if not tbl:
            break
        for tr in tbl.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) >= 3:
                snap.port_calls.append(
                    PortCall(
                        port=_clean(tds[0].get_text(" ", strip=True)),
                        arrival=_clean(tds[1].get_text(" ", strip=True)),
                        departure=_clean(tds[2].get_text(" ", strip=True)),
                    )
                )
        break

    return snap


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def fetch_html(mmsi: int | str, imo: int | str = "", *, client: httpx.Client | None = None) -> str:
    url = _VESSEL_URL.format(mmsi=mmsi, imo=imo)
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": _UA})
    try:
        r = client.get(url)
        r.raise_for_status()
        return r.text
    finally:
        if own:
            client.close()


def get_vessel(mmsi: int, imo: int | str = "", *, use_cache: bool = True) -> VesselSnapshot | None:
    """Fetch + parse a vessel by MMSI. Returns None on a non-vessel page.

    Raises MyShipTrackingBlocked on a rate-limit / bot-wall page so the caller can
    back off instead of retrying.
    """
    if use_cache and mmsi in _cache:
        ts, snap = _cache[mmsi]
        if time.time() - ts < _CACHE_TTL:
            return snap

    html = fetch_html(mmsi, imo)
    if not looks_like_vessel_page(html):
        # Distinguish a genuine bot-wall from an empty/missing vessel page so the
        # crawler can back off on the former and skip the latter.
        if is_blocked(html):
            raise MyShipTrackingBlocked(f"myshiptracking blocked request for mmsi={mmsi}")
        logger.info("mmsi=%s: not a vessel page (no data)", mmsi)
        _cache[mmsi] = (time.time(), None)
        return None

    snap = parse(html, mmsi=mmsi)
    _cache[mmsi] = (time.time(), snap)
    return snap
