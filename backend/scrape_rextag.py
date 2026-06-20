"""
Scrape US natural gas pipeline specs from RexTag.com.

Usage:
    .venv/bin/python scrape_rextag.py [--out rextag_pipelines.json] [--resume]

Fetches ~125 FERC-regulated pipeline/storage pages and extracts:
  name, slug, owner, operator, length_miles, capacity_bcfd,
  compressor_stations, seasonal_storage, states_served

Rate-limited to 1.5 s/request.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://rextag.com"
DIRECTORY_URL = f"{BASE}/pages/texas-eastern-transmission"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-scraper/1.0)"}
DELAY = 1.5

# Slugs that are NOT pipeline pages (marketing, legal, dataset product pages)
_NON_PIPELINE_PREFIXES = (
    "accelerating-", "cutting-", "infrastructure-data-",
    "upstream-", "power-renewables-", "crude-oil-dataset",
    "telecommunications-", "other-", "refined-", "energy-datalink-",
    "natural-gas-pipelines-informational-", "natural-gas-dataset",
    "american-gas-", "services", "about-us", "faq", "contact-us",
    "rextag-directory", "oil-gas-production", "terms-conditions",
    "rextag-energy-datalink-", "privacy-policy", "return-policy",
)

_LABEL_MAP = {
    "owner:": "owner",
    "operator:": "operator",
    "miles of pipeline:": "length_miles",
    "pipeline length:": "length_miles",
    "system capacity:": "capacity_bcfd",
    "seasonal storage:": "seasonal_storage",
    "compressor stations:": "compressor_stations",
}

# US state abbreviations for extracting states_served from description text
_STATE_RE = re.compile(
    r'\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|'
    r'Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|'
    r'Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|'
    r'Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|'
    r'North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|'
    r'South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|'
    r'Wisconsin|Wyoming)\b'
)


def get_soup(url: str, session: requests.Session) -> BeautifulSoup:
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def is_pipeline_slug(slug: str) -> bool:
    return not any(slug.startswith(p) or slug == p.rstrip("-") for p in _NON_PIPELINE_PREFIXES)


def fetch_slugs(session: requests.Session) -> list[tuple[str, str]]:
    soup = get_soup(DIRECTORY_URL, session)
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("/pages/"):
            continue
        slug = href[len("/pages/"):]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if is_pipeline_slug(slug):
            results.append((a.get_text(strip=True) or slug, slug))
    return results


def parse_pipeline_page(soup: BeautifulSoup, name: str, slug: str) -> dict:
    lines = [l for l in soup.get_text("\n", strip=True).split("\n") if l]
    record: dict = {"name": name, "slug": slug}

    # Line-by-line: label line followed by value line
    for i, line in enumerate(lines):
        label_key = _LABEL_MAP.get(line.lower())
        if label_key and i + 1 < len(lines):
            val = lines[i + 1].strip()
            # Skip if the next line is itself a label
            if val.lower() in _LABEL_MAP:
                continue
            if label_key in ("length_miles", "capacity_bcfd", "seasonal_storage", "compressor_stations"):
                m = re.search(r"[\d.]+", val.replace(",", ""))
                record[label_key] = float(m.group()) if m else None
            else:
                record.setdefault(label_key, val)

    # Extract states from the description paragraph
    desc_match = re.search(r"Pipeline Description:\s*(.+?)(?:\n|Major Receipt)", "\n".join(lines))
    if desc_match:
        desc = desc_match.group(1)
        states = sorted(set(_STATE_RE.findall(desc)))
        if states:
            record["states_served"] = states
        record["description_snippet"] = desc[:300]

    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/rextag_pipelines.json")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    out_path = Path(args.out)
    existing: dict[str, dict] = {}
    if args.resume and out_path.exists():
        for rec in json.loads(out_path.read_text()):
            existing[rec["slug"]] = rec
        print(f"Resuming: {len(existing)} already scraped")

    session = requests.Session()
    print("Fetching directory page...")
    slugs = fetch_slugs(session)
    print(f"Found {len(slugs)} pipeline pages")
    time.sleep(DELAY)

    results = list(existing.values())
    for i, (name, slug) in enumerate(slugs, 1):
        if slug in existing:
            print(f"  [{i}/{len(slugs)}] {slug} (cached)")
            continue

        url = f"{BASE}/pages/{slug}"
        print(f"  [{i}/{len(slugs)}] {slug}...")
        try:
            soup = get_soup(url, session)
            record = parse_pipeline_page(soup, name, slug)
            results.append(record)
            print(
                f"    -> cap={record.get('capacity_bcfd')} Bcf/d  "
                f"len={record.get('length_miles')} mi  "
                f"owner={str(record.get('owner',''))[:40]}"
            )
        except Exception as exc:
            print(f"    ERROR: {exc}")
            results.append({"name": name, "slug": slug, "error": str(exc)})

        out_path.write_text(json.dumps(results, indent=2))
        time.sleep(DELAY)

    print(f"\nDone. {len(results)} records -> {out_path}")
    actual = [r for r in results if not r.get("error") and r.get("length_miles")]
    print(f"  pipeline records:  {len(actual)}")
    print(f"  with capacity:     {sum(1 for r in actual if r.get('capacity_bcfd'))}")
    print(f"  with owner:        {sum(1 for r in actual if r.get('owner'))}")
    print(f"  with states:       {sum(1 for r in actual if r.get('states_served'))}")


if __name__ == "__main__":
    main()
