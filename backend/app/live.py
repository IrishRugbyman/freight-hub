"""The in-process view of ``live_positions`` every live endpoint reads through.

Caches the full table for a few seconds so a page load that fires ~15 queries at
once reads one consistent frame instead of racing the collector's write lock.
"""

from __future__ import annotations

import threading as _threading
from datetime import UTC, datetime, timedelta

import pandas as pd
from ais.regions import REGIONS

from . import db
from .common import fresh_cutoff, iso, visible_cutoff
from .schemas import (
    FeedStatus,
)

# In-process cache for the full live_positions DataFrame.
# The AIS collector writes every ~150s holding a brief write lock. When the
# Analytics page loads it fires ~15 simultaneous DB queries; without a cache
# those that hit the lock window all get empty results and cache 0s for their
# stale period. One warm copy per 30s eliminates the problem.
_live_cache_lock = _threading.Lock()
_live_cache: dict = {"df": None, "ts": 0.0, "path": ""}
_live_visible_cache: dict = {"df": None, "ts": 0.0, "path": ""}
_LIVE_CACHE_TTL = 30.0  # seconds

# Self-healing coverage flag: a region has terrestrial AIS coverage iff the collector
# has captured any snapshot there in the trailing window. Nine of the 24 subscribed
# basins (Hormuz, Arab Gulf, Bab-el-Mandeb, etc.) are permanently empty because
# aisstream.io's free terrestrial network has no receivers there - not a config bug,
# and unfixable without paid satellite AIS. Rather than hardcode a dead-list, we derive
# coverage from real data so a basin lights up automatically if it ever gets a receiver.
_coverage_cache_lock = _threading.Lock()
_coverage_cache: dict = {"regions": None, "ts": 0.0, "path": ""}
_COVERAGE_CACHE_TTL = 3600.0  # seconds; full snapshot scan, refreshed hourly
_COVERAGE_WINDOW_DAYS = 7


def live_all():
    """Return the full live_positions DataFrame, served from a 30s in-process cache."""
    import time

    now = time.monotonic()
    current_path = str(db.db_path())
    with _live_cache_lock:
        path_match = _live_cache["path"] == current_path
        if (
            path_match
            and _live_cache["df"] is not None
            and now - _live_cache["ts"] < _LIVE_CACHE_TTL
        ):
            return _live_cache["df"]
    df = db.query(
        "SELECT * FROM live_positions WHERE updated_ts > ?",
        [fresh_cutoff()],
    )
    if not df.empty:
        with _live_cache_lock:
            _live_cache["df"] = df
            _live_cache["ts"] = now
            _live_cache["path"] = current_path
    elif _live_cache["df"] is not None and path_match:
        # DB was locked on same path - return stale cache rather than propagating 0s
        return _live_cache["df"]
    return df


def live_visible():
    """Vessels active within VISIBLE_HOURS (default 24h) for the map endpoint.

    Combines two sources so collector restarts don't erase dark vessels:
      1. live_positions: full data for any MMSI currently in the collector's memory.
      2. ais_snapshots: last-known fix for MMSIs that went dark and were evicted from
         live_positions (e.g. after a collector restart with no new transmission).

    All analytics endpoints use live_all() (3h fresh window) - this function must
    never be used for counts/flows/chokepoints or stale vessels would corrupt them.
    """
    import time

    now_mono = time.monotonic()
    current_path = str(db.db_path())
    with _live_cache_lock:
        path_match = _live_visible_cache["path"] == current_path
        if (
            path_match
            and _live_visible_cache["df"] is not None
            and now_mono - _live_visible_cache["ts"] < _LIVE_CACHE_TTL
        ):
            return _live_visible_cache["df"]

    cutoff = visible_cutoff()

    # Source 1: live fleet (may have been trimmed to 24h by collector, or shorter after restart)
    live_df = db.query("SELECT * FROM live_positions WHERE updated_ts > ?", [cutoff])
    live_mmsis: set = set(live_df["mmsi"].tolist()) if not live_df.empty else set()

    # Source 2: last-known snapshot per MMSI for vessels not in live_positions.
    # ROW_NUMBER over 24h of snapshots: DuckDB handles ~400k rows in <200ms columnar.
    ghost_df = db.query(
        """
        SELECT mmsi, kind, segment, region, lat, lon, sog, nav_status, draught,
               destination, ship_type, length_m, snapshot_ts AS updated_ts
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY mmsi ORDER BY snapshot_ts DESC) AS rn
            FROM ais_snapshots
            WHERE snapshot_ts > ?
        ) sub
        WHERE rn = 1
        """,
        [cutoff],
    )
    if not ghost_df.empty:
        if live_mmsis:
            ghost_df = ghost_df[~ghost_df["mmsi"].isin(live_mmsis)]
        if not ghost_df.empty:
            # Enrich ghost rows: pull name + imo from vessels PG table (no duplication,
            # just a keyed lookup - ais_name is written by the collector, ship_name by Equasis).
            mmsi_list = ghost_df["mmsi"].tolist()
            # MMSI is not unique in `vessels` (reuse between hulls, rows built from
            # garbled AIS static data), and a duplicate makes the .map() below raise.
            # One row per MMSI: a valid 7-digit IMO first, then an Equasis-verified
            # row, then the most recently updated.
            reg = db.pg_query(
                "SELECT DISTINCT ON (mmsi) mmsi, imo, COALESCE(ship_name, ais_name) AS name "
                "FROM vessels WHERE mmsi = ANY(%s) "
                "ORDER BY mmsi, (imo BETWEEN 1000000 AND 9999999) DESC, "
                "fetch_ok IS TRUE DESC, updated_at DESC NULLS LAST",
                [mmsi_list],
            )
            if not reg.empty:
                reg = reg.set_index("mmsi")
                ghost_df["name"] = ghost_df["mmsi"].map(reg["name"])
                ghost_df["imo"] = ghost_df["mmsi"].map(reg["imo"])
            else:
                ghost_df["name"] = None
                ghost_df["imo"] = None
            for col in ("cog", "heading", "eta"):
                ghost_df[col] = None
            live_df = (
                pd.concat([live_df, ghost_df], ignore_index=True) if not live_df.empty else ghost_df
            )

    df = live_df
    if not df.empty:
        with _live_cache_lock:
            _live_visible_cache["df"] = df
            _live_visible_cache["ts"] = now_mono
            _live_visible_cache["path"] = current_path
    elif _live_visible_cache["df"] is not None and path_match:
        return _live_visible_cache["df"]
    return df


def covered_regions() -> set[str]:
    """Regions with any AIS snapshot in the trailing window (1h-cached).

    The discriminator for "has terrestrial coverage": basins with real receivers are
    never empty over a week, the nine dead basins have produced zero rows since
    collection began. Self-heals within the window if a basin ever comes online.
    """
    import time

    now = time.monotonic()
    current_path = str(db.db_path())
    with _coverage_cache_lock:
        path_match = _coverage_cache["path"] == current_path
        fresh = now - _coverage_cache["ts"] < _COVERAGE_CACHE_TTL
        if path_match and _coverage_cache["regions"] is not None and fresh:
            return _coverage_cache["regions"]
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=_COVERAGE_WINDOW_DAYS)
    df = db.query(
        "SELECT DISTINCT region FROM ais_snapshots WHERE snapshot_ts > ? AND region IS NOT NULL",
        [cutoff],
    )
    if df.empty:
        # Missing table or locked DB: don't cache an empty set (would flag every zone
        # as dead). Fall back to a stale set if we have one, else treat all as covered.
        with _coverage_cache_lock:
            if _coverage_cache["regions"] is not None and path_match:
                return _coverage_cache["regions"]
        return set(REGIONS)
    regions = set(df["region"].tolist())
    with _coverage_cache_lock:
        _coverage_cache["regions"] = regions
        _coverage_cache["ts"] = now
        _coverage_cache["path"] = current_path
    return regions


def query_live(where: str = "", params: list | None = None):
    """Fresh live_positions rows with an optional extra WHERE clause."""
    if not where and not params:
        return live_all()
    clause = f" AND {where}" if where else ""
    return db.query(
        f"SELECT * FROM live_positions WHERE updated_ts > ?{clause}",
        [fresh_cutoff(), *(params or [])],
    )


def feed_status() -> FeedStatus:
    """State of the upstream AIS feed, read past every freshness filter.

    The queries are deliberately unfiltered. Every other read applies the
    VISIBLE_HOURS window, so during an outage longer than a day they all return
    nothing and can no longer say when the feed last worked - which is exactly
    when a visitor most needs to be told.

    `live_positions` alone is not enough, and the reason is easy to miss: the
    collector *prunes* rows from it once they age past its staleness window, so
    a long outage empties the table completely and it destroys the very evidence
    of when the feed died. `ais_snapshots` is append-only and therefore the
    durable record. Falling back to it is what turns "we have never had a feed"
    into "the feed stopped on this date", which are very different things to
    show a visitor. Only when both are empty is the answer genuinely unknown.
    """
    df = db.query("SELECT max(updated_ts) AS last_seen FROM live_positions")
    last_seen = None if df.empty else df["last_seen"].iloc[0]
    if last_seen is None or pd.isna(last_seen):
        snaps = db.query("SELECT max(snapshot_ts) AS last_seen FROM ais_snapshots")
        last_seen = None if snaps.empty else snaps["last_seen"].iloc[0]
    if last_seen is None or pd.isna(last_seen):
        return FeedStatus(
            state="unknown",
            stale_hours=db.STALE_HOURS,
            visible_hours=db.VISIBLE_HOURS,
        )
    age_min = (
        datetime.now(UTC).replace(tzinfo=None) - pd.Timestamp(last_seen)
    ).total_seconds() / 60
    if age_min <= db.STALE_HOURS * 60:
        state = "live"
    elif age_min <= db.VISIBLE_HOURS * 60:
        state = "stale"
    else:
        state = "down"
    return FeedStatus(
        state=state,
        last_seen=iso(last_seen),
        age_minutes=round(age_min, 1),
        stale_hours=db.STALE_HOURS,
        visible_hours=db.VISIBLE_HOURS,
    )
