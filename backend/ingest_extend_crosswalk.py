"""Extend rextag_wm_crosswalk with additional WM->RexTag mappings.

These WM US gas pipeline IDs can be matched to existing RexTag slugs that
already have EIA route geometry. Adding them to the crosswalk makes those
routes immediately visible in the map loader without any new downloads.

Usage:
    cd backend
    .venv/bin/python ingest_extend_crosswalk.py [--db <path>] [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

DB_DEFAULT = Path(__file__).parent / "data" / "freight_analytics.duckdb"

# Manually curated WM ID -> RexTag slug mappings.
# Only includes cases where the RexTag slug is confirmed to have an EIA route.
NEW_MAPPINGS: list[tuple[str, str]] = [
    # ANR Pipeline (TransCanada / TC Energy, runs Great Lakes to Gulf)
    ("anr-gas-pipeline-us", "anr-pipeline"),
    # El Paso Natural Gas (Southern California / Southwest)
    ("el-paso-gas-pipeline-us", "el-paso-natural-gas"),
    # Rockies Express Pipeline (Wyoming to Ohio)
    ("rockies-express-gas-pipeline-us", "rockies-express-pipeline"),
    # Panhandle Eastern (Kansas to Michigan)
    ("panhandle-eastern-gas-pipeline-us", "panhandle-eastern-pipe-line"),
    # Kern River Gas Transmission (Wyoming to California)
    ("kern-river-gas-pipeline-us", "kern-river-gas-transmission"),
    # East Tennessee Natural Gas (Appalachian region)
    ("east-tennessee-gas-pipeline-us", "east-tennessee-natural-gas"),
    # Natural Gas Pipeline Company of America (Illinois to Texas)
    ("natural-gas-pipeline-company-of-america-system-us", "natural-gas-pipeline-company-of-america-ngpl"),
    # Alliance Pipeline (Canada to Chicago)
    ("alliance-gas-pipeline-ca", "alliance-pipeline"),
    # Gulf South Pipeline (Louisiana/Mississippi/Alabama)
    ("gulf-south-gas-pipeline-system-network-info-us", "gulf-south-pipeline"),
    # Northwest Pipeline (Pacific Northwest)
    ("northwest-gas-pipeline-system-network-info-us", "northwest-pipeline"),
    # Northern Border Pipeline (Canada to Midwest)
    ("northern-border-gas-pipeline-ca", "northern-border-pipeline"),
    # Mississippi River Transmission (Enable Midstream, now OGE Energy)
    ("mississippi-river-transmission-us", "enable-mississipi-river-transmission"),
    # WBI Energy Transmission (Williston Basin Interstate)
    ("williston-basin-gas-pipeline-us", "wbi-energy-transmission"),
    # Enable Oklahoma Intrastate Transmission (EOIT)
    ("enable-oklahoma-instrastate-transmission-eoit-us", "enable-gas-transmission"),
    # MountainWest Pipeline (Utah/Wyoming/Colorado - Overthrust backbone)
    ("mountainwest-gas-pipeline-us", "mountainwest-overthrust-pipeline"),
    # Midcontinent Express Pipeline (Oklahoma to Alabama)
    ("midcontinent-express-gas-pipeline-us", "midcontinent-express-pipeline"),
    # Gulfstream Natural Gas System (offshore Gulf of Mexico to Florida)
    ("gulfstream-natural-gas-pipeline-us", "gulfstream-natural-gas-system"),
    # Maritimes & Northeast Pipeline (Nova Scotia to New England)
    ("maritimes-and-northeast-gas-pipeline-ca", "maritimes-and-northeast-pipeline"),
    # Mojave Pipeline (Arizona to California)
    ("mojave-gas-pipeline-us", "mojave-pipeline"),
    # Iroquois Gas Transmission (New York/New England)
    ("iroquois-gas-transmission-system-pipeline-us", "iroquois-gas-transmission-system"),
    # Empire Pipeline (New York)
    ("empire-gas-pipeline-us", "empire-pipeline"),
    # Ruby Pipeline (Wyoming to Oregon)
    ("ruby-gas-pipeline-us", "ruby-pipeline"),
    # Sabal Trail Transmission (Georgia to Florida)
    ("sabal-trail-transmission-gas-pipeline-us", "sabal-trail-transmission"),
    # Algonquin Gas Transmission (already in crosswalk but check slug variant)
    # Tucked in for completeness; insert will be skipped if already present.
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--dry-run", action="store_true", help="Show what would be added")
    args = parser.parse_args()

    con = duckdb.connect(args.db, read_only=args.dry_run)

    # Load existing crosswalk
    existing = {r[0] for r in con.execute("SELECT wm_id FROM rextag_wm_crosswalk").fetchall()}
    print(f"Current crosswalk entries: {len(existing)}")

    # Verify each proposed RexTag slug actually has an EIA route
    eia_slugs = {r[0] for r in con.execute("SELECT rextag_slug FROM eia_pipeline_routes").fetchall()}
    print(f"RexTag slugs with EIA routes: {len(eia_slugs)}")

    to_add: list[tuple[str, str]] = []
    skipped_no_eia: list[tuple[str, str]] = []
    skipped_exists: list[tuple[str, str]] = []

    for wm_id, slug in NEW_MAPPINGS:
        if wm_id in existing:
            skipped_exists.append((wm_id, slug))
        elif slug not in eia_slugs:
            skipped_no_eia.append((wm_id, slug))
        else:
            to_add.append((wm_id, slug))

    print(f"\nSkipped (already in crosswalk): {len(skipped_exists)}")
    for wm_id, slug in skipped_exists:
        print(f"  {wm_id} -> {slug}")

    print(f"\nSkipped (no EIA route for slug): {len(skipped_no_eia)}")
    for wm_id, slug in skipped_no_eia:
        print(f"  {wm_id} -> {slug}")

    print(f"\nNew entries to add: {len(to_add)}")
    for wm_id, slug in to_add:
        print(f"  {wm_id} -> {slug}")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        con.close()
        return

    added = 0
    for wm_id, slug in to_add:
        try:
            # Table has no PK - check manually before inserting
            exists = con.execute(
                "SELECT COUNT(*) FROM rextag_wm_crosswalk WHERE wm_id = ?", [wm_id]
            ).fetchone()[0]
            if not exists:
                con.execute(
                    "INSERT INTO rextag_wm_crosswalk (wm_id, rextag_slug) VALUES (?, ?)",
                    [wm_id, slug],
                )
                added += 1
        except Exception as e:
            print(f"  WARN: {wm_id}: {e}")

    total = con.execute("SELECT COUNT(*) FROM rextag_wm_crosswalk").fetchone()[0]
    con.close()

    print(f"\nAdded {added} new crosswalk entries.")
    print(f"Total crosswalk entries: {total}")


if __name__ == "__main__":
    main()
