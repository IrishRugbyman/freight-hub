"""Write ITU MARS call signs into the PG vessels master.

Targets vessels that have a known MMSI but no call_sign yet.
Safe to re-run: only updates null call_sign rows.

Usage:
    cd backend
    python -m registry.mars_enrich [--limit N] [--delay F]
"""

from __future__ import annotations

import argparse
import os

import psycopg2
from loguru import logger

from .mars import MarsSession

_PG_DSN = os.environ.get("DATABASE_URL", "postgresql:///market_data")
_DEFAULT_DELAY = 2.0
_DEFAULT_LIMIT = 200


def run(limit: int = _DEFAULT_LIMIT, delay: float = _DEFAULT_DELAY) -> None:
    pg = psycopg2.connect(_PG_DSN)
    cur = pg.cursor()

    cur.execute(
        "SELECT mmsi FROM vessels "
        "WHERE mmsi IS NOT NULL AND call_sign IS NULL "
        "ORDER BY ais_last_seen DESC NULLS LAST "
        "LIMIT %s",
        [limit],
    )
    mmsis = [row[0] for row in cur.fetchall()]
    logger.info(f"MARS enrich: {len(mmsis)} vessels without call_sign")

    if not mmsis:
        logger.info("Nothing to do")
        cur.close()
        pg.close()
        return

    session = MarsSession()
    found = updated = 0

    for i, mmsi in enumerate(mmsis):
        rec = session.fetch(int(mmsi), delay=delay)
        if rec and rec.call_sign:
            found += 1
            cur.execute(
                "UPDATE vessels SET call_sign = %s WHERE mmsi = %s AND call_sign IS NULL",
                [rec.call_sign, mmsi],
            )
            if cur.rowcount:
                updated += 1
        if (i + 1) % 50 == 0:
            pg.commit()
            logger.info(f"  {i + 1}/{len(mmsis)} queried - {found} found, {updated} written")

    pg.commit()
    cur.close()
    pg.close()
    logger.info(f"MARS enrich done: {found}/{len(mmsis)} resolved, {updated} call signs written")


def main() -> None:
    import logging
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    ap.add_argument("--delay", type=float, default=_DEFAULT_DELAY)
    args = ap.parse_args()
    run(limit=args.limit, delay=args.delay)


if __name__ == "__main__":
    main()
