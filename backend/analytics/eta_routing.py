"""Phase B of the True ETA build: sea-route distance with a persistent cache.

The naive ETA divides the *great-circle* distance by speed. A great circle cuts
across continents: Fujairah->Rotterdam is 2,851 nm as the crow flies but 6,123 nm
by sea (2.15x), because the real voyage rounds Arabia, threads Bab-el-Mandeb, and
transits Suez. That under-distance is the dominant cause of the Phase A long-haul
optimism (-30 h bias at 24-48 h lead). This module replaces straight lines with
the distance ships actually sail.

How it works:

  * `searoute` (vendored marnet network, no runtime network call) computes the
    shortest path over a real marine graph that respects canals and capes.
  * Routing is the expensive step, so every origin is *snapped to a 0.25 deg grid
    cell* and the (cell, target) distance is memoized in `eta_route_cache`. An
    approach track sampled hourly revisits the same handful of cells, and the
    cache persists across analytics runs (it survives the atomic DB swap), so the
    steady-state cost is "route only the cells we have never seen before".
  * Fallback chain: searoute -> great-circle, with the chosen method flagged on
    every row. (The roadmap's middle "vendored marnet shortest path" tier is
    redundant in practice: `searoute` *is* the vendored marnet shortest path - it
    ships the GeoJSON inside the package and runs offline - so the honest chain is
    two real tiers, not a third copy of searoute's own data. If `searoute` cannot
    be imported at all, every row degrades cleanly to great-circle.)

Pure read of `searoute`; the only writer of `eta_route_cache` is the analytics
job (via `RouteCache.flush`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import duckdb

from analytics.eta_labels import haversine_nm

log = logging.getLogger(__name__)

# searoute returns lengths in km; convert to nautical miles.
_KM_TO_NM = 0.5399568034557235

# Origin grid resolution. 0.25 deg ~ 15 nm at the equator: fine enough that the
# routed distance from a cell centre is a good proxy for any fix inside it, coarse
# enough that an hourly approach track collapses onto a few cells. This is the
# memoization key in `eta_route_cache`.
GRID_DEG = 0.25

# Method tags written onto each routed value (and persisted in the cache).
METHOD_SEAROUTE = "searoute"
METHOD_GC = "gc"

ROUTE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_route_cache (
    from_cell      VARCHAR,            -- snapped 0.25deg grid cell of the origin
    target_id      VARCHAR,
    route_dist_nm  DOUBLE,
    method         VARCHAR,            -- 'searoute' | 'gc'
    computed_ts    TIMESTAMP,
    PRIMARY KEY (from_cell, target_id)
);
"""


# ---------------------------------------------------------------------------
# searoute access (imported lazily + tolerantly so the build never hard-fails)
# ---------------------------------------------------------------------------

_searoute_mod = None
_searoute_tried = False


def _searoute():
    """Return the searoute module, or None if it cannot be imported.

    Imported once and cached. A missing/broken searoute is not fatal: the caller
    falls back to great-circle and flags the method, so the build still produces
    a populated `eta_samples` (just without the routing improvement).
    """
    global _searoute_mod, _searoute_tried
    if not _searoute_tried:
        _searoute_tried = True
        try:  # pragma: no cover - import success is the production path
            import searoute as sr

            _searoute_mod = sr
        except Exception as exc:  # noqa: BLE001 - degrade to great-circle
            log.warning("searoute unavailable (%s); routing falls back to great-circle", exc)
            _searoute_mod = None
    return _searoute_mod


# ---------------------------------------------------------------------------
# Grid snapping
# ---------------------------------------------------------------------------


def snap_cell(lat: float, lon: float, grid: float = GRID_DEG) -> tuple[float, float]:
    """Snap a position to the centre of its `grid`-degree cell.

    Centring (floor + half a cell) keeps the routed origin away from cell edges,
    so two fixes either side of a boundary still share a cell only when genuinely
    close. Returns (cell_lat, cell_lon).
    """
    clat = (lat // grid) * grid + grid / 2.0
    clon = (lon // grid) * grid + grid / 2.0
    return clat, clon


def cell_key(lat: float, lon: float, grid: float = GRID_DEG) -> str:
    """Stable string key for the snapped cell (the `from_cell` cache column)."""
    clat, clon = snap_cell(lat, lon, grid)
    return f"{clat:.3f},{clon:.3f}"


# ---------------------------------------------------------------------------
# Raw routing (one call; no cache)
# ---------------------------------------------------------------------------


def _route_once(cell_lat: float, cell_lon: float, target: dict) -> tuple[float, str]:
    """Route a snapped origin cell centre to a target. Returns (nm, method).

    searoute -> great-circle fallback. The result is floored at the great-circle
    distance: a sea route can never be shorter than the great circle, so a smaller
    value can only be a graph-snapping artifact (both endpoints landing on the
    same nearby node) and is clamped to the physical lower bound.
    """
    gc = haversine_nm(cell_lat, cell_lon, target["lat"], target["lon"])
    sr = _searoute()
    if sr is None:
        return gc, METHOD_GC
    try:
        # searoute takes [lon, lat]; length default unit is km.
        feat = sr.searoute([cell_lon, cell_lat], [target["lon"], target["lat"]])
        km = float(feat["properties"]["length"])
        nm = km * _KM_TO_NM
        if not (nm > 0) or nm < gc:
            nm = gc  # snapping artifact -> physical floor
        return nm, METHOD_SEAROUTE
    except Exception as exc:  # noqa: BLE001 - any routing failure -> great-circle
        log.debug("searoute failed for cell (%.3f,%.3f)->%s (%s); using gc",
                  cell_lat, cell_lon, target["target_id"], exc)
        return gc, METHOD_GC


# ---------------------------------------------------------------------------
# Cached routing
# ---------------------------------------------------------------------------


class RouteCache:
    """In-memory + on-disk memoized router keyed by (snapped cell, target_id).

    Load the existing cache once, serve `distance()` lookups, accumulate newly
    routed cells, then `flush()` them back to `eta_route_cache` in one batch. The
    on-disk table persists across analytics runs, so a backfilled cache makes
    every subsequent incremental build route only never-before-seen cells.
    """

    # Persist accumulated cells once this many new ones pile up, so a long cold
    # backfill (the first run over fresh history routes >100k distinct cells) is
    # crash-safe: an interruption keeps everything routed so far instead of
    # discarding the whole run's work. Steady-state runs route far fewer cells than
    # this and just flush once at the end.
    _FLUSH_EVERY = 2000

    def __init__(self, conn: duckdb.DuckDBPyConnection, grid: float = GRID_DEG):
        self._conn = conn
        self._grid = grid
        self._cache: dict[tuple[str, str], tuple[float, str]] = {}
        self._dirty: list[tuple[str, str, float, str]] = []
        self.hits = 0
        self.misses = 0
        self.written = 0
        conn.execute(ROUTE_CACHE_SCHEMA)
        for from_cell, target_id, dist, method in conn.execute(
            "SELECT from_cell, target_id, route_dist_nm, method FROM eta_route_cache"
        ).fetchall():
            self._cache[(from_cell, target_id)] = (float(dist), method)

    def distance(self, lat: float, lon: float, target: dict) -> tuple[float, str]:
        """Sea-route distance (nm) + method from a fix to a target, memoized."""
        key = (cell_key(lat, lon, self._grid), target["target_id"])
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        clat, clon = snap_cell(lat, lon, self._grid)
        nm, method = _route_once(clat, clon, target)
        self._cache[key] = (nm, method)
        self._dirty.append((key[0], key[1], nm, method))
        if len(self._dirty) >= self._FLUSH_EVERY:
            self.flush()
        return nm, method

    def flush(self) -> int:
        """Persist newly routed cells to `eta_route_cache`. Returns rows written."""
        if not self._dirty:
            return 0
        now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        self._conn.executemany(
            "INSERT OR REPLACE INTO eta_route_cache "
            "(from_cell, target_id, route_dist_nm, method, computed_ts) VALUES (?, ?, ?, ?, ?)",
            [(c, t, d, m, now) for (c, t, d, m) in self._dirty],
        )
        n = len(self._dirty)
        self.written += n
        self._dirty.clear()
        log.info("route cache: flushed %d cells (%d total this run; %d hits, %d misses)",
                 n, self.written, self.hits, self.misses)
        return n
