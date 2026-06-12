"""OFAC Specially Designated Nationals (SDN) vessel screener.

Downloads the public OFAC SDN XML list from the US Treasury, extracts vessel entries,
and returns a set of IMO numbers that appear on the list.

Source: https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml
(public, no auth required; updated regularly by the Treasury)

This module is deliberately free of side effects - it returns data, never writes to DB.
The caller (crawl.py) decides how to store results.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"
_SDN_NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationsService/SDN/xml/schema"

_IMO_RE = re.compile(r"\bIMO\s*[:#]?\s*(\d{7})\b", re.IGNORECASE)


def _fetch_sdn_xml(timeout: int = 30) -> bytes:
    req = Request(_SDN_URL, headers={"User-Agent": "freight-api-ofac-screen/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _extract_vessel_imos(xml_bytes: bytes) -> set[int]:
    """Parse OFAC SDN XML and return the set of vessel IMOs on the list."""
    imos: set[int] = set()

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.error("OFAC XML parse error: %s", e)
        return imos

    # SDN entries have sdnType="vessel" (case-insensitive match as fallback)
    # The XML uses a namespace; try both namespaced and bare tags
    ns_prefix = f"{{{_SDN_NS}}}" if _SDN_NS in ET.tostring(root, encoding="unicode")[:500] else ""

    for entry in root.iter(f"{ns_prefix}sdnEntry" if ns_prefix else "sdnEntry"):
        sdn_type_el = entry.find(f"{ns_prefix}sdnType" if ns_prefix else "sdnType")
        if sdn_type_el is None or sdn_type_el.text is None:
            continue
        if sdn_type_el.text.strip().lower() != "vessel":
            continue

        # Search ID fields for IMO numbers
        for id_el in entry.iter(f"{ns_prefix}id" if ns_prefix else "id"):
            id_type = id_el.find(f"{ns_prefix}idType" if ns_prefix else "idType")
            id_number = id_el.find(f"{ns_prefix}idNumber" if ns_prefix else "idNumber")
            if id_type is not None and id_number is not None:
                if "imo" in (id_type.text or "").lower():
                    raw = (id_number.text or "").strip()
                    m = re.search(r"(\d{7})", raw)
                    if m:
                        imos.add(int(m.group(1)))

        # Also scrape remarks / aka fields for IMO mentions (belt and suspenders)
        for el in entry.iter():
            if el.text:
                for m in _IMO_RE.finditer(el.text):
                    imo_val = int(m.group(1))
                    if 1_000_000 <= imo_val <= 9_999_999:
                        imos.add(imo_val)

    return imos


def fetch_sanctioned_imos(timeout: int = 30) -> set[int]:
    """Download the OFAC SDN list and return a set of vessel IMOs on the list.

    Returns an empty set on any network or parse failure (non-fatal - caller
    should treat a missing OFAC result as unknown, not clear).
    """
    try:
        xml_bytes = _fetch_sdn_xml(timeout=timeout)
        imos = _extract_vessel_imos(xml_bytes)
        log.info("OFAC SDN: %d vessel IMOs extracted", len(imos))
        return imos
    except Exception as e:
        log.warning("OFAC fetch failed (non-fatal): %s", e)
        return set()
