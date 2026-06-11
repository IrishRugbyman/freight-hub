"""Equasis scraper - session-based HTML scraping of equasis.org ship data.

Credentials are read from EQUASIS_EMAIL / EQUASIS_PWD env vars.
Results are cached in-process for 12 hours (Equasis data is updated at most daily).
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# Load .env from the backend directory (fallback for local dev; systemd uses EnvironmentFile)
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://www.equasis.org/EquasisWeb/authen/HomePage?fs=HomePage"
_SHIP_URL = "https://www.equasis.org/EquasisWeb/restricted/ShipInfo?fs=ShipInfo&P_IMO={imo}"

_CACHE_TTL = 12 * 3600  # seconds
_cache: dict[int, tuple[float, dict]] = {}

# Bootstrap label -> result key mapping
_LABEL_MAP = {
    "Flag": "flag",
    "Call Sign": "call_sign",
    "MMSI": "mmsi_equasis",
    "Gross tonnage": "gross_tonnage",
    "DWT": "dwt",
    "Type of ship": "ship_type",
    "Year of build": "year_built",
    "Status": "ship_status",
}
_KNOWN_LABELS = set(_LABEL_MAP)


class EquasisClient:
    def __init__(self) -> None:
        self._email = os.getenv("EQUASIS_EMAIL", "")
        self._pwd = os.getenv("EQUASIS_PWD", "")
        self._client: httpx.Client | None = None
        self._logged_in = False

    def _client_(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                follow_redirects=True,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                    )
                },
            )
        return self._client

    def _login(self) -> bool:
        if not self._email or not self._pwd:
            logger.warning("Equasis credentials not configured")
            return False
        try:
            resp = self._client_().post(
                _LOGIN_URL,
                data={"j_email": self._email, "j_password": self._pwd, "submit": "log in"},
            )
            # Successful login lands on restricted area or shows username
            ok = resp.status_code == 200 and (
                "restricted" in str(resp.url) or "My Equasis" in resp.text
            )
            self._logged_in = ok
            if not ok:
                logger.error("Equasis login failed (status %s)", resp.status_code)
            return ok
        except Exception as exc:
            logger.error("Equasis login error: %s", exc)
            return False

    @staticmethod
    def _is_expired(html: str) -> bool:
        return "authen/HomePage" in html or "j_email" in html

    def fetch_ship_info(self, imo: int) -> dict | None:
        cached = _cache.get(imo)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            return cached[1]

        client = self._client_()
        if not self._logged_in and not self._login():
            return None

        url = _SHIP_URL.format(imo=imo)
        try:
            resp = client.get(url)
        except Exception as exc:
            logger.error("Equasis fetch error for IMO %s: %s", imo, exc)
            return None

        if self._is_expired(resp.text):
            if not self._login():
                return None
            try:
                resp = client.get(url)
            except Exception as exc:
                logger.error("Equasis fetch error after re-login: %s", exc)
                return None

        if resp.status_code != 200:
            return None

        data = _parse(resp.text, imo)
        if data:
            _cache[imo] = (time.time(), data)
        return data


# Module-level singleton (one shared session)
_client = EquasisClient()


def get_ship_info(imo: int) -> dict | None:
    return _client.fetch_ship_info(imo)


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------

def _parse(html: str, imo: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {"imo": imo}

    # Ship name: first <b> that is not a known label and looks like a vessel name
    for b in soup.find_all("b"):
        text = b.get_text(strip=True)
        if text and text not in _KNOWN_LABELS and len(text) > 3 and text[0].isupper():
            # Skip picture links, IMO numbers, percentages
            if not re.match(r"^\d+", text) and "picture" not in text.lower():
                out["ship_name"] = text
                break

    # Ship particulars: Bootstrap rows with col-lg-4 label/value pairs
    for row in soup.find_all("div", class_=re.compile(r"\brow\b")):
        cols = [c for c in row.find_all("div", recursive=False) if c.get("class")]
        if len(cols) < 2:
            continue
        b = cols[0].find("b")
        if not b:
            continue
        label = b.get_text(strip=True)
        if label not in _KNOWN_LABELS:
            continue

        # Row layouts:
        #   Flag:    col1=label | col2=flag img | col3=country name "(Singapore)"
        #   Others:  col1=label | col2=value    | col3=(since date) [optional]
        # Take the first non-image non-empty col as the value; also capture flag img code.
        value = ""
        for col in cols[1:]:
            img = col.find("img")
            if img:
                src = img.get("src", "")
                m = re.search(r"/flags/(\w+)\.png", src)
                if m:
                    out["flag_code"] = m.group(1)
                # continue to next col for flag country name
            else:
                t = col.get_text(strip=True)
                if t and not value:
                    value = t  # first non-empty col wins; skip "(since ...)" extras

        key = _LABEL_MAP.get(label)
        if not key or not value:
            continue

        # Clean up: strip parentheses, extract first number for tonnage fields
        value = value.strip("()")
        if key in ("gross_tonnage", "dwt"):
            m = re.match(r"(\d[\d,]*)", value.replace(",", ""))
            if m:
                value = m.group(1)

        out[key] = value

    # Management table (first <table>): IMO, Role, Name of company, Address, Date
    tables = soup.find_all("table")
    if tables:
        for tr in tables[0].find_all("tr")[1:]:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(tds) < 3:
                continue
            role = tds[1].lower()
            name = tds[2]
            if "registered owner" in role and "owner" not in out:
                out["owner"] = name
            elif "ism manager" in role and "ism_manager" not in out:
                out["ism_manager"] = name
            elif "ship manager" in role and "ship_manager" not in out:
                out["ship_manager"] = name

    # Classification table (second <table>)
    if len(tables) > 1:
        rows = tables[1].find_all("tr")
        if len(rows) > 1:
            tds = [td.get_text(strip=True) for td in rows[1].find_all("td")]
            if tds:
                out["class_society"] = tds[0]

    # P&I section: find "P&I Information" h3, locate its collapse container, extract club
    for h3 in soup.find_all("h3"):
        if "P&I" not in h3.get_text():
            continue
        # Walk up to find the <a href="#collapseX"> ancestor (the node may itself be the <a>)
        node = h3
        collapse_id = None
        for _ in range(12):
            node = node.find_parent()
            if not node:
                break
            href = node.get("href", "") if node.name == "a" else ""
            if href.startswith("#collapse"):
                collapse_id = href.lstrip("#")
                break
            a = node.find("a", attrs={"href": re.compile(r"^#collapse")}, recursive=False)
            if a:
                collapse_id = a["href"].lstrip("#")
                break
        if not collapse_id:
            break
        collapse_div = soup.find("div", id=collapse_id)
        if not collapse_div:
            break
        orange = collapse_div.find("div", class_="round-list")
        if orange:
            col = orange.find_parent(class_=re.compile(r"\bcol-"))
            p = col.find("p") if col else None
            if p:
                out["pi_club"] = p.get_text(strip=True)
        break

    # PSC overview (.collapse div containing "detention")
    for div in soup.find_all("div", class_="collapse"):
        text = div.get_text(" ", strip=True)
        if "detention" not in text.lower():
            continue
        m = re.search(r"([\d.]+)\s*%\s*[Oo]f\s*inspections", text)
        if m:
            out["detention_rate_pct"] = float(m.group(1))

        for mou in ("Paris MOU", "Tokyo MOU"):
            m = re.search(rf"{re.escape(mou)}\s+(White|Grey|Black)", text)
            if m:
                out[mou.lower().replace(" ", "_")] = m.group(1)

        m = re.search(r"USCG[:\s]+([\w ]+?)(?:\s+RO|$)", text)
        if m:
            out["uscg_targeting"] = m.group(1).strip()
        break

    return out
