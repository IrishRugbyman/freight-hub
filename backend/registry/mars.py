"""ITU MARS ship station fetcher.

Free, official ITU database (updated daily). Queried by MMSI; returns
ship name, call sign, and administration (ITU 3-letter flag code).

No login required. Uses a session-scoped Breadcrumb CSRF token that is
refreshed from each response - keep a single session alive for a run.

Rate limit: ~1 request / 2s is polite for a government resource.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from loguru import logger

_URL = "https://www.itu.int/mmsapp/ShipStation/list"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/124.0 Safari/537.36"
    ),
    "Referer": _URL,
}


@dataclass
class MarsRecord:
    mmsi: int
    ship_name: str | None
    call_sign: str | None
    administration: str | None  # ITU 3-letter code e.g. "PAN", "DNK"


class MarsSession:
    """Stateful session that carries the CSRF Breadcrumb token between requests."""

    def __init__(self) -> None:
        self._s = requests.Session()
        self._s.headers.update(_HEADERS)
        self._breadcrumb: str = ""

    def _refresh_token(self) -> bool:
        """GET the search page to obtain a fresh Breadcrumb token."""
        try:
            r = self._s.get(_URL, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            tag = soup.find("input", {"name": "Breadcrumb"})
            if tag and tag.get("value"):
                self._breadcrumb = tag["value"]
                return True
        except requests.RequestException as e:
            logger.warning(f"MARS token refresh failed: {e}")
        return False

    def fetch(self, mmsi: int, delay: float = 2.0) -> MarsRecord | None:
        """Query MARS by MMSI. Returns None on no result or network error."""
        if not self._breadcrumb:
            if not self._refresh_token():
                return None

        time.sleep(delay)

        payload = {
            "Breadcrumb": self._breadcrumb,
            "ScrollTopValue": "",
            "Search.Name": "",
            "Search.MaritimeMobileServiceIdentity": str(mmsi),
            "Search.CallSign": "",
            "Search.VesselIdentificationNumber": "",
            "Search.EmergencyPositionIndicatingRadioBeaconHexadecimalIdentifier": "",
            "Search.SatelliteNumber": "",
            "Search.Administration.SelectedId": "",
            "Search.GeographicalArea.SelectedId": "",
            "Search.GeneralClassification.SelectedId": "",
            "viewCommand": "Search",
        }

        try:
            r = self._s.post(_URL, data=payload, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"MARS fetch mmsi={mmsi}: {e}")
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # Refresh token from the response for the next request
        tag = soup.find("input", {"name": "Breadcrumb"})
        if tag and tag.get("value"):
            self._breadcrumb = tag["value"]

        # "No record found" check
        if "No record" in r.text:
            return None

        # Parse first data row from the results table
        rows = soup.select("table tr")
        for row in rows[1:]:  # skip header
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) < 3:
                continue
            # cols: [checkbox?, ship_name, call_sign, mmsi, administration, geo_area, ...]
            # Strip leading checkbox cell (empty text)
            data_cols = [c for c in cols if c]
            if not data_cols:
                continue
            return MarsRecord(
                mmsi=mmsi,
                ship_name=data_cols[0] if len(data_cols) > 0 else None,
                call_sign=data_cols[1] if len(data_cols) > 1 else None,
                administration=data_cols[3] if len(data_cols) > 3 else None,
            )

        return None


def run(
    mmsis: list[int],
    delay: float = 2.0,
    limit: int | None = None,
) -> list[MarsRecord]:
    """Fetch MARS data for a list of MMSIs. Returns successfully resolved records."""
    candidates = mmsis[:limit] if limit else mmsis
    session = MarsSession()
    results: list[MarsRecord] = []
    for i, mmsi in enumerate(candidates):
        rec = session.fetch(mmsi, delay=delay)
        if rec:
            results.append(rec)
        if (i + 1) % 50 == 0:
            logger.info(f"MARS: {i + 1}/{len(candidates)} queried, {len(results)} found")
    logger.info(f"MARS: done - {len(results)}/{len(candidates)} resolved")
    return results
