# Canal-Backtrack Destination Predictor Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `canal_backtrack` soft-penalty feature to the vessel destination
predictor: a candidate port that would require a vessel to re-transit Suez or
Panama in reverse (right after it just cleared that gate) scores worse, without
being hard-excluded.

**Architecture:** One new pure geometry function (`destination_features.canal_backtrack`)
computes the signal from data already collected (`transit_events`,
`_CHOKEPOINT_GATES`, `CHOKEPOINT_AXES`). Two callers feed it the vessel's
"most recent qualifying canal transit": live serving looks it up as-of now;
the historical training-set builder looks it up as-of each observation's own
timestamp (leakage-free). The scorer (heuristic + LightGBM) picks up the new
column like any other feature.

**Tech Stack:** Python, pandas, numpy, DuckDB, LightGBM, pytest.

## Global Constraints

- No new dependencies, no schema migrations (`transit_events` already exists,
  written by `analytics/detect.py`'s `transit_episodes`).
- Soft penalty feature only - no candidate is ever removed from the frame.
- Scope: Suez + Panama only (`_CANAL_CHOKEPOINTS = {"suez", "panama"}`).
- Recency window: 21 days (`CANAL_BACKTRACK_WINDOW_DAYS = 21.0`).
- Leakage discipline: the training-set builder must only ever see transits with
  `exited_ts <= obs_ts` for that observation.
- Spec: `docs/superpowers/specs/2026-07-03-canal-backtrack-feature-design.md`.

---

## Task 1: `canal_backtrack` pure function + `candidate_frame` wiring

**Files:**
- Modify: `backend/analytics/destination_features.py`
- Test: `backend/tests/test_destination_features.py`

**Interfaces:**
- Produces: `canal_backtrack(lat: float, lon: float, t_lat: float, t_lon: float, chokepoint: str | None) -> int`
- Produces: `CANAL_BACKTRACK_WINDOW_DAYS: float = 21.0` (module-level constant)
- Produces: `candidate_frame(live, targets, recent_canal_transit: dict[int, str] | None = None) -> pd.DataFrame` (new optional 3rd param; every row now carries a `canal_backtrack` int column)
- Consumes: `analytics.eta_labels._CHOKEPOINT_GATES` (existing), `analytics.zones.CHOKEPOINT_AXES` (existing)

- [ ] **Step 1: Write the failing tests for the pure function**

Append to `backend/tests/test_destination_features.py`, just before the
`test_bearing_alignment_neutral_without_course` test (keep the file's existing
`if __name__ == "__main__":` block at the end):

```python
def test_canal_backtrack_penalizes_wrong_side_of_suez():
    # Vessel just north of the Suez gate (Med side); a Persian Gulf candidate
    # (south of the gate) would require backtracking through Suez again.
    assert feat.canal_backtrack(31.0, 32.34, 26.0, 50.0, "suez") == 1


def test_canal_backtrack_allows_same_side_of_suez():
    # A Rotterdam candidate is also north of the gate - no backtrack required.
    assert feat.canal_backtrack(31.0, 32.34, 51.96, 4.10, "suez") == 0


def test_canal_backtrack_penalizes_wrong_side_of_panama():
    # Panama gate lon is -79.75. Vessel just east of it (Atlantic side); a
    # Pacific-side candidate would require backtracking through Panama again.
    assert feat.canal_backtrack(9.12, -70.0, 9.12, -90.0, "panama") == 1


def test_canal_backtrack_neutral_without_recent_transit():
    assert feat.canal_backtrack(31.0, 32.34, 26.0, 50.0, None) == 0


def test_canal_backtrack_neutral_for_non_canal_chokepoint():
    # Hormuz is a strait, not one of the two scoped canals - always neutral.
    assert feat.canal_backtrack(1.2, 103.8, 26.0, 50.0, "hormuz") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_features.py -k canal_backtrack -v`
Expected: FAIL with `AttributeError: module 'analytics.destination_features' has no attribute 'canal_backtrack'`

- [ ] **Step 3: Implement `canal_backtrack` and the window constant**

In `backend/analytics/destination_features.py`, change the import line (currently
line 32):

```python
from analytics.eta_labels import haversine_nm
```

to:

```python
from analytics.eta_labels import _CHOKEPOINT_GATES, haversine_nm
from analytics.zones import CHOKEPOINT_AXES
```

Add these constants right after `_SAME_PORT_NM = 20.0` (currently line 39):

```python

# Suez/Panama only - the two `is_canal=True` targets where the alternate route
# is a genuine ocean-scale diversion (Cape of Good Hope / Cape Horn). For
# straits, "wrong side" mostly restates a low `bearing_align` and would be
# redundant with that signal.
_CANAL_CHOKEPOINTS = {"suez", "panama"}

# A qualifying transit older than this no longer penalizes - stale rather than
# permanent. 21 days comfortably covers a full Suez -> NW-Europe or
# Panama -> Asia leg.
CANAL_BACKTRACK_WINDOW_DAYS = 21.0
```

Add `"canal_backtrack"` to `_CANDIDATE_COLS` (currently lines 41-58), right
after `"reported_match"`:

```python
_CANDIDATE_COLS = [
    "mmsi",
    "lat",
    "lon",
    "sog",
    "cog",
    "segment",
    "draught",
    "target_id",
    "target_type",
    "target_name",
    "target_lat",
    "target_lon",
    "gc_dist_nm",
    "bearing_align",
    "reported_match",
    "canal_backtrack",
    "resolver_score",
]
```

Add the new function right after `bearing_alignment` (currently ends at line 71,
before `_bearing_to_many`):

```python
def canal_backtrack(
    lat: float, lon: float, t_lat: float, t_lon: float, chokepoint: str | None
) -> int:
    """1 if reaching (t_lat, t_lon) from (lat, lon) would require re-transiting
    `chokepoint` (a recent Suez/Panama transit) in reverse; else 0.

    `chokepoint` is None when the vessel has no qualifying recent transit -
    absence of signal, not a penalty (mirrors `bearing_alignment`'s neutral
    default when course is unknown). Side-of-gate is computed purely from the
    chokepoint's axis (`CHOKEPOINT_AXES`) and gate coordinate
    (`_CHOKEPOINT_GATES`) - both already production-validated by the transit
    detector and True ETA - so this needs no independent geography check, and
    it never looks at the transit's direction label, only which side of the
    gate each point falls on.
    """
    if chokepoint is None or chokepoint not in _CANAL_CHOKEPOINTS:
        return 0
    axis, _, _ = CHOKEPOINT_AXES[chokepoint]
    gate_lat, gate_lon = _CHOKEPOINT_GATES[chokepoint]
    if axis == "lat":
        vessel_side = lat - gate_lat
        target_side = t_lat - gate_lat
    else:
        vessel_side = lon - gate_lon
        target_side = t_lon - gate_lon
    if vessel_side == 0 or target_side == 0:
        return 0
    return int((vessel_side > 0) != (target_side > 0))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_features.py -k canal_backtrack -v`
Expected: 5 passed

- [ ] **Step 5: Write the failing test for `candidate_frame` wiring**

Add to `backend/tests/test_destination_features.py`:

```python
def test_candidate_frame_flags_canal_backtrack_on_geometric_candidate():
    # Vessel just north of the Suez gate (Med side), steaming due south toward
    # a target also south of the gate - which would require transiting Suez
    # again in reverse.
    live = _live_row(lat=31.5, lon=32.34, cog=180.0, heading=180.0)
    targets = pd.DataFrame(
        [{"target_id": "port:south", "target_type": "port", "name": "South Port", "lat": 26.0, "lon": 32.34}]
    )
    cands = feat.candidate_frame(live, targets, recent_canal_transit={7001: "suez"})
    assert not cands.empty
    assert cands["canal_backtrack"].iloc[0] == 1

    # Without a recent transit on record, the same candidate isn't penalized.
    cands_no_transit = feat.candidate_frame(live, targets)
    assert cands_no_transit["canal_backtrack"].iloc[0] == 0


def test_candidate_frame_canal_backtrack_on_resolved_destination_row():
    live = _live_row(lat=31.5, lon=32.34, destination="ROTTERDAM")
    cands = feat.candidate_frame(live, _TARGETS, recent_canal_transit={7001: "suez"})
    dest_row = cands[cands["target_type"] == "destination"].iloc[0]
    # Rotterdam is north of the Suez gate - same side as the vessel, no backtrack.
    assert dest_row["canal_backtrack"] == 0
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_features.py -k canal_backtrack -v`
Expected: the two new tests FAIL with `TypeError: candidate_frame() got an unexpected keyword argument 'recent_canal_transit'`

- [ ] **Step 7: Wire `recent_canal_transit` into `candidate_frame`**

In `backend/analytics/destination_features.py`, change the `candidate_frame`
signature and docstring (currently lines 102-109):

```python
def candidate_frame(
    live: pd.DataFrame,
    targets: pd.DataFrame,
    recent_canal_transit: dict[int, str] | None = None,
) -> pd.DataFrame:
    """One row per (vessel, candidate target). Empty frame if no live/targets.

    `live` must already be underway-filtered (mirrors `eta_serving._load_live`);
    this module applies no speed/segment filtering of its own.

    `recent_canal_transit` maps mmsi -> chokepoint ('suez' | 'panama') for
    vessels with a qualifying recent canal transit on record (see
    `canal_backtrack`); omit or pass `None`/`{}` when that lookup isn't
    available - every candidate then gets the neutral `canal_backtrack=0`.
    """
    if live.empty or targets.empty:
        return pd.DataFrame(columns=_CANDIDATE_COLS)

    recent_canal_transit = recent_canal_transit or {}
    pairs = _candidate_pairs(live, targets)
```

Change the loop body (currently lines 117-133) to look up the vessel's
chokepoint once per vessel. This inserts one new line
(`chokepoint = recent_canal_transit.get(mmsi)`) after the existing
`course = ...` block - every other line shown here (`dest_str`, `rp`,
`matched_reported`) is unchanged, just reproduced for placement clarity:

```python
    for vi, v in enumerate(live.itertuples()):
        mmsi = int(v.mmsi)
        lat, lon = float(v.lat), float(v.lon)
        sog = float(v.sog) if pd.notna(getattr(v, "sog", None)) else None
        segment = str(v.segment) if pd.notna(getattr(v, "segment", None)) else None
        draught = float(v.draught) if pd.notna(getattr(v, "draught", None)) else None
        course = None
        if pd.notna(getattr(v, "cog", None)):
            course = float(v.cog)
        elif pd.notna(getattr(v, "heading", None)):
            course = float(v.heading)
        chokepoint = recent_canal_transit.get(mmsi)

        dest_str = getattr(v, "destination", None)
        rp = None
        if isinstance(dest_str, str) and dest_str.strip():
            rp = resolve_destination(dest_str, lat, lon)

        matched_reported = False
```

Add `"canal_backtrack"` to the geometric-candidate row dict (currently lines
143-162):

```python
            rows.append(
                {
                    "mmsi": mmsi,
                    "lat": lat,
                    "lon": lon,
                    "sog": sog,
                    "cog": course,
                    "segment": segment,
                    "draught": draught,
                    "target_id": str(t["target_id"]),
                    "target_type": str(t["target_type"]),
                    "target_name": str(t["name"]),
                    "target_lat": t_lat,
                    "target_lon": t_lon,
                    "gc_dist_nm": float(gc),
                    "bearing_align": bearing_alignment(lat, lon, course, t_lat, t_lon),
                    "reported_match": reported_match,
                    "canal_backtrack": canal_backtrack(lat, lon, t_lat, t_lon, chokepoint),
                    "resolver_score": float(rp.score) if reported_match else None,
                }
            )
```

Add `"canal_backtrack"` to the resolved-destination row dict (currently lines
164-185):

```python
        if rp is not None and not matched_reported:
            gc_r = haversine_nm(lat, lon, rp.lat, rp.lon)
            rows.append(
                {
                    "mmsi": mmsi,
                    "lat": lat,
                    "lon": lon,
                    "sog": sog,
                    "cog": course,
                    "segment": segment,
                    "draught": draught,
                    "target_id": f"dest:{rp.locode}",
                    "target_type": "destination",
                    "target_name": rp.name,
                    "target_lat": rp.lat,
                    "target_lon": rp.lon,
                    "gc_dist_nm": gc_r,
                    "bearing_align": bearing_alignment(lat, lon, course, rp.lat, rp.lon),
                    "reported_match": True,
                    "canal_backtrack": canal_backtrack(lat, lon, rp.lat, rp.lon, chokepoint),
                    "resolver_score": float(rp.score),
                }
            )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_features.py -v`
Expected: all tests in the file PASS (12 total: 5 original-untouched + 5 new
`canal_backtrack` unit tests + 2 new `candidate_frame` wiring tests)

- [ ] **Step 9: Commit**

```bash
cd backend
git add analytics/destination_features.py tests/test_destination_features.py
git commit -m "feat: canal-backtrack candidate feature in destination predictor"
```

---

## Task 2: Live serving - recent canal transit lookup

**Files:**
- Modify: `backend/analytics/destination_serving.py`
- Test: `backend/tests/test_destination_serving.py`

**Interfaces:**
- Consumes: `candidate_frame(live, targets, recent_canal_transit=...)` (Task 1)
- Consumes: `CANAL_BACKTRACK_WINDOW_DAYS` (Task 1)
- Produces: `_recent_canal_transit_by_mmsi(conn: duckdb.DuckDBPyConnection, now: datetime) -> dict[int, str]`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_destination_serving.py`. First change the imports at
the top of the file (currently):

```python
from datetime import UTC, datetime

import duckdb
import pytest
from analytics.eta_labels import ETA_SCHEMA
from analytics.destination_serving import build_destination_predictions, run_in_conn
from fastapi.testclient import TestClient
```

to:

```python
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from analytics.eta_labels import ETA_SCHEMA
from analytics.destination_serving import (
    _recent_canal_transit_by_mmsi,
    build_destination_predictions,
    run_in_conn,
)
from fastapi.testclient import TestClient
```

Then add these tests near the top-level test functions (after `_seed_targets`,
before `_fake_ais_query`):

```python
def _seed_transit_events(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "CREATE TABLE transit_events (mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, "
        "exited_ts TIMESTAMP, direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN)"
    )


def test_recent_canal_transit_by_mmsi_reads_recent_suez_transit(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_transit_events(conn)
    conn.execute(
        "INSERT INTO transit_events VALUES (7001, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE)",
        [_NOW - timedelta(days=2), _NOW - timedelta(days=1)],
    )
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {7001: "suez"}


def test_recent_canal_transit_by_mmsi_ignores_stale_transit():
    conn = duckdb.connect(":memory:")
    _seed_transit_events(conn)
    conn.execute(
        "INSERT INTO transit_events VALUES (7001, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE)",
        [_NOW - timedelta(days=40), _NOW - timedelta(days=39)],
    )
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {}


def test_recent_canal_transit_by_mmsi_keeps_only_most_recent_per_vessel():
    conn = duckdb.connect(":memory:")
    _seed_transit_events(conn)
    conn.execute(
        "INSERT INTO transit_events VALUES "
        "(7001, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE), "
        "(7001, 'panama', ?, ?, 'eastbound', 'tanker', 'VLCC', TRUE)",
        [
            _NOW - timedelta(days=10), _NOW - timedelta(days=9),
            _NOW - timedelta(days=3), _NOW - timedelta(days=2),
        ],
    )
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {7001: "panama"}


def test_recent_canal_transit_by_mmsi_empty_without_table():
    conn = duckdb.connect(":memory:")
    assert _recent_canal_transit_by_mmsi(conn, _NOW) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_serving.py -k recent_canal_transit -v`
Expected: FAIL with `ImportError: cannot import name '_recent_canal_transit_by_mmsi'`

- [ ] **Step 3: Implement `_recent_canal_transit_by_mmsi`**

In `backend/analytics/destination_serving.py`, change the imports (currently
lines 19-30):

```python
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd

from analytics.destination_features import CANAL_BACKTRACK_WINDOW_DAYS, candidate_frame
from analytics.destination_labels import TransitionPriors, VisitFrequency
from analytics.destination_predict import DestinationModel, score_candidates
from analytics.eta_labels import ANALYTICS_DB, _default_ais_query
from analytics.eta_serving import _load_live, _load_targets
```

Add the new function right after `_prev_target_by_mmsi` (currently ends at
line 85, before `build_destination_predictions`):

```python
def _recent_canal_transit_by_mmsi(conn: duckdb.DuckDBPyConnection, now: datetime) -> dict[int, str]:
    """Each vessel's most recent Suez/Panama transit within the backtrack
    window, keyed by mmsi -> chokepoint. Empty dict (not an error) if
    `transit_events` doesn't exist yet (e.g. a fresh analytics DB) -
    `canal_backtrack` degrades to its neutral default in that case, same as
    any other missing-signal case in this predictor."""
    cutoff = now - timedelta(days=CANAL_BACKTRACK_WINDOW_DAYS)
    try:
        df = conn.execute(
            "SELECT mmsi, chokepoint FROM ("
            "  SELECT mmsi, chokepoint, "
            "         row_number() OVER (PARTITION BY mmsi ORDER BY exited_ts DESC) AS rn "
            "  FROM transit_events "
            "  WHERE chokepoint IN ('suez', 'panama') AND exited_ts >= ?"
            ") WHERE rn = 1",
            [cutoff],
        ).df()
    except duckdb.CatalogException:
        return {}
    return {int(r.mmsi): r.chokepoint for r in df.itertuples()}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_serving.py -k recent_canal_transit -v`
Expected: 4 passed

- [ ] **Step 5: Wire the lookup into `build_destination_predictions`**

In `backend/analytics/destination_serving.py`, change the candidate-frame call
(currently line 104):

```python
    candidates = candidate_frame(
        live, targets, recent_canal_transit=_recent_canal_transit_by_mmsi(conn, now)
    )
```

- [ ] **Step 6: Run the full serving test file to confirm no regression**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_serving.py -v`
Expected: all tests PASS, including the pre-existing
`test_build_destination_predictions_scores_underway_vessel` (whose `conn` never
creates `transit_events`, exercising the graceful-degradation path for free)

- [ ] **Step 7: Commit**

```bash
cd backend
git add analytics/destination_serving.py tests/test_destination_serving.py
git commit -m "feat: wire recent canal-transit lookup into live destination serving"
```

---

## Task 3: Leakage-safe training-set wiring

**Files:**
- Modify: `backend/analytics/destination_predict.py`
- Test: `backend/tests/test_destination_predict.py`

**Interfaces:**
- Consumes: `canal_backtrack`, `CANAL_BACKTRACK_WINDOW_DAYS` (Task 1)
- Produces: `_load_canal_transits(conn) -> dict[int, list[tuple[pd.Timestamp, str]]]`
- Produces: `_chokepoint_as_of(history: list[tuple[pd.Timestamp, str]], obs_ts: pd.Timestamp, window_days: float) -> str | None`
- Produces: `build_training_candidates(conn)` rows now carry a `canal_backtrack` column

- [ ] **Step 1: Write the failing tests for `_chokepoint_as_of`**

Add to `backend/tests/test_destination_predict.py`, in the "Training-set
reconstruction from completed voyages" section (after `_seed_voyage_db`, before
`test_build_training_candidates_labels_true_target_positive`):

```python
def test_chokepoint_as_of_returns_most_recent_within_window():
    history = [(pd.Timestamp("2026-06-01"), "suez"), (pd.Timestamp("2026-06-10"), "panama")]
    assert dp._chokepoint_as_of(history, pd.Timestamp("2026-06-15"), 21.0) == "panama"


def test_chokepoint_as_of_ignores_future_transit():
    # A transit dated after obs_ts must never be visible - the leakage guard.
    history = [(pd.Timestamp("2026-06-20"), "suez")]
    assert dp._chokepoint_as_of(history, pd.Timestamp("2026-06-15"), 21.0) is None


def test_chokepoint_as_of_ignores_stale_transit():
    history = [(pd.Timestamp("2026-05-01"), "suez")]
    assert dp._chokepoint_as_of(history, pd.Timestamp("2026-06-15"), 21.0) is None


def test_chokepoint_as_of_empty_history():
    assert dp._chokepoint_as_of([], pd.Timestamp("2026-06-15"), 21.0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k chokepoint_as_of -v`
Expected: FAIL with `AttributeError: module 'analytics.destination_predict' has no attribute '_chokepoint_as_of'`

- [ ] **Step 3: Implement `_load_canal_transits` and `_chokepoint_as_of`**

In `backend/analytics/destination_predict.py`, change the import line
(currently line 51):

```python
from analytics.destination_features import CANAL_BACKTRACK_WINDOW_DAYS, bearing_alignment_vec, canal_backtrack
```

Add the two functions right after `_load_arrivals_with_prev` (currently ends at
line 298, before `build_training_candidates`):

```python
def _load_canal_transits(conn: duckdb.DuckDBPyConnection) -> dict[int, list[tuple]]:
    """Each vessel's Suez/Panama transit history, sorted by exited_ts ascending
    - `mmsi -> [(exited_ts, chokepoint), ...]`. Used to look up, for any
    historical training observation, the most recent qualifying transit
    strictly before it (leakage-free: a transit dated after `obs_ts` must never
    be visible - see `_chokepoint_as_of`)."""
    try:
        df = conn.execute(
            "SELECT mmsi, chokepoint, exited_ts FROM transit_events "
            "WHERE chokepoint IN ('suez', 'panama') ORDER BY mmsi, exited_ts"
        ).df()
    except duckdb.CatalogException:
        return {}
    if df.empty:
        return {}
    df["exited_ts"] = pd.to_datetime(df["exited_ts"])
    out: dict[int, list[tuple]] = {}
    for mmsi, g in df.groupby("mmsi", sort=False):
        out[int(mmsi)] = list(zip(g["exited_ts"], g["chokepoint"]))
    return out


def _chokepoint_as_of(history: list[tuple], obs_ts, window_days: float) -> str | None:
    """Most recent chokepoint transit at/before `obs_ts` within `window_days`,
    or None. `history` must be sorted by exited_ts ascending
    (`_load_canal_transits` already returns it that way)."""
    best: str | None = None
    for ts, cp in history:
        if ts > obs_ts:
            break
        if (obs_ts - ts).total_seconds() / 86400.0 <= window_days:
            best = cp
    return best
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k chokepoint_as_of -v`
Expected: 4 passed

- [ ] **Step 5: Write the failing test for `build_training_candidates` wiring**

Add to `backend/tests/test_destination_predict.py`, after
`test_build_training_candidates_bearing_favours_true_target`:

```python
def test_build_training_candidates_populates_canal_backtrack(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    conn.execute(ETA_SCHEMA)
    from analytics.eta_samples import ETA_SAMPLES_SCHEMA

    conn.execute(ETA_SAMPLES_SCHEMA)
    conn.execute(
        "CREATE TABLE transit_events (mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP, "
        "exited_ts TIMESTAMP, direction VARCHAR, kind VARCHAR, segment VARCHAR, laden BOOLEAN)"
    )
    # Two targets straddling the Suez gate (lat 30.50): one north (reachable
    # without re-transiting), one south (would require backtracking through
    # the gate the vessel just cleared).
    for t in [
        ("port:north", "port", "North Port", 45.0, 20.0, 15.0, False),
        ("port:south", "port", "South Port", 20.0, 40.0, 15.0, False),
    ]:
        conn.execute(
            "INSERT INTO eta_targets (target_id, target_type, name, lat, lon, reach_nm, is_canal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(t),
        )
    arrival = pd.Timestamp("2026-06-10")
    obs_ts = arrival - pd.Timedelta(hours=5)
    conn.execute(
        "INSERT INTO eta_samples (voyage_id, mmsi, target_id, arrival_ts, obs_ts, obs_lat, obs_lon, "
        "remaining_h, segment, target_type) VALUES (0, 2000, 'port:north', ?, ?, 32.0, 30.0, 5.0, 'VLCC', 'port')",
        [arrival, obs_ts],
    )
    conn.execute(
        "INSERT INTO eta_arrivals (mmsi, target_id, arrival_ts, min_dist_nm, segment, laden, approach_start_ts) "
        "VALUES (2000, 'port:north', ?, 1.0, 'VLCC', TRUE, ?)",
        [arrival, arrival - pd.Timedelta(hours=5)],
    )
    # Vessel transited Suez northbound 1 day before this observation - well
    # within the 21-day backtrack window.
    conn.execute(
        "INSERT INTO transit_events VALUES (2000, 'suez', ?, ?, 'northbound', 'tanker', 'VLCC', TRUE)",
        [obs_ts - pd.Timedelta(days=2), obs_ts - pd.Timedelta(days=1)],
    )
    cands = dp.build_training_candidates(conn)
    south = cands[cands["target_id"] == "port:south"]
    north = cands[cands["target_id"] == "port:north"]
    assert not south.empty and not north.empty
    assert (south["canal_backtrack"] == 1).all()
    assert (north["canal_backtrack"] == 0).all()


def test_build_training_candidates_canal_backtrack_defaults_without_transit_events(tmp_path):
    # No transit_events table at all (fresh DB) - must not crash, and the
    # column must still exist with the neutral default.
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    _seed_voyage_db(conn)
    cands = dp.build_training_candidates(conn)
    assert not cands.empty
    assert (cands["canal_backtrack"] == 0).all()
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k canal_backtrack -v`
Expected: FAIL with `KeyError: 'canal_backtrack'`

- [ ] **Step 7: Wire `canal_backtrack` into `build_training_candidates`**

In `backend/analytics/destination_predict.py`, add the transit-history load
right after the targets arrays are built (currently lines 326-332, just before
`arrivals = _load_arrivals_with_prev(conn)`):

```python
    targets = conn.execute("SELECT target_id, target_type, name, lat, lon FROM eta_targets").df()
    if targets.empty:
        return pd.DataFrame()
    t_lat = targets["lat"].to_numpy(dtype=float)
    t_lon = targets["lon"].to_numpy(dtype=float)
    t_ids = targets["target_id"].to_numpy(dtype=object)
    t_types = targets["target_type"].to_numpy(dtype=object)
    canal_transits = _load_canal_transits(conn)

    arrivals = _load_arrivals_with_prev(conn)
```

Change the per-observation loop (currently lines 370-405) to compute the
chokepoint once per observation and pass it through to each row:

```python
        for i in idxs:
            lat, lon = float(lats[i]), float(lons[i])
            course = float(courses[i]) if np.isfinite(courses[i]) else None
            obs_ts = grp["obs_ts"].iloc[i]
            chokepoint = _chokepoint_as_of(
                canal_transits.get(mmsi, []), obs_ts, CANAL_BACKTRACK_WINDOW_DAYS
            )
            gc = haversine_nm_vec(t_lat, t_lon, lat, lon)
            align = bearing_alignment_vec(lat, lon, course, t_lat, t_lon)

            order = np.argsort(gc)
            true_idx = np.where(t_ids == true_target)[0]
            chosen: list[int] = list(true_idx[:1])
            for j in order:
                if len(chosen) >= _MAX_NEG_PER_OBS + 1:
                    break
                if int(j) in chosen:
                    continue
                chosen.append(int(j))

            seg_val = grp["segment"].iloc[i]
            seg = str(seg_val) if pd.notna(seg_val) else None
            for j in chosen:
                rows.append(
                    {
                        "voyage_id": vid,
                        "mmsi": mmsi,
                        "arrival_ts": arrival_ts,
                        "obs_ts": obs_ts,
                        "remaining_h": float(grp["remaining_h"].iloc[i]),
                        "target_id": t_ids[j],
                        "target_type": t_types[j],
                        "gc_dist_nm": float(gc[j]),
                        "bearing_align": float(align[j]),
                        "canal_backtrack": canal_backtrack(
                            lat, lon, float(t_lat[j]), float(t_lon[j]), chokepoint
                        ),
                        "segment": seg,
                        "is_destination": int(t_ids[j] == true_target),
                        "prev_target_id": prev_target,
                    }
                )
```

Note this removes the old, later `obs_ts = grp["obs_ts"].iloc[i]` line (it now
happens earlier in the loop, right after `course` is computed) - make sure
there is exactly one assignment of `obs_ts` per iteration, not two.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k "canal_backtrack or chokepoint_as_of" -v`
Expected: 6 passed

- [ ] **Step 9: Run the full predictor test file to confirm no regression**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -v`
Expected: all tests PASS (the pre-existing `_seed_voyage_db`-based tests never
create `transit_events`, exercising the graceful-degradation path)

- [ ] **Step 10: Commit**

```bash
cd backend
git add analytics/destination_predict.py tests/test_destination_predict.py
git commit -m "feat: leakage-safe canal-backtrack feature in destination training set"
```

---

## Task 4: Register the feature with both scorers

**Files:**
- Modify: `backend/analytics/destination_predict.py`
- Test: `backend/tests/test_destination_predict.py`

**Interfaces:**
- Consumes: `canal_backtrack` column now present on both live and training
  candidate frames (Tasks 1 and 3)
- Produces: `NUMERIC_FEATURES` includes `"canal_backtrack"`
- Produces: `heuristic_raw_score` penalizes `canal_backtrack=1`
- Produces: `score_candidates` falls back to the heuristic if the loaded ML
  booster rejects the current feature set (stale-artifact safety net)

- [ ] **Step 1: Write the failing test for the heuristic penalty**

Add to `backend/tests/test_destination_predict.py`, in the "Heuristic scorer"
section, after `test_heuristic_score_rewards_reported_match_and_history`:

```python
def test_heuristic_score_penalizes_canal_backtrack():
    base = {"gc_dist_nm": 200.0, "bearing_align": 0.5}
    with_backtrack = {**base, "canal_backtrack": 1}
    assert dp.heuristic_raw_score(with_backtrack) < dp.heuristic_raw_score(base)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k penalizes_canal_backtrack -v`
Expected: FAIL (`with_backtrack` scores equal to `base` since the weight and
term don't exist yet)

- [ ] **Step 3: Add the weight, the feature list entry, and the scoring term**

In `backend/analytics/destination_predict.py`, change `_HEURISTIC_WEIGHTS`
(currently lines 73-79):

```python
_HEURISTIC_WEIGHTS = {
    "bearing_align": 2.0,
    "inv_dist": 3.0,
    "reported_match": 2.5,
    "transition_prior": 1.5,
    "visit_freq": 1.0,
    "canal_backtrack": -2.0,
}
```

Change `heuristic_raw_score` (currently lines 83-107):

```python
def heuristic_raw_score(row: dict) -> float:
    """Un-normalized plausibility score for one (vessel, candidate) row.

    Missing optional fields (`route_dist_nm`, `reported_match`, priors,
    `canal_backtrack`) degrade gracefully to their neutral value rather than
    raising - this lets the same function score both the full live candidate
    frame and the training frame, which does not carry `reported_match`/
    `resolver_score` (see module docstring).
    """
    dist = row.get("route_dist_nm")
    if dist is None or not np.isfinite(dist):
        dist = row.get("gc_dist_nm") or 0.0
    inv_dist = 1.0 / (1.0 + dist / _DIST_SCALE_NM)
    bearing = row.get("bearing_align")
    bearing = bearing if bearing is not None and np.isfinite(bearing) else 0.5
    reported = 1.0 if row.get("reported_match") else 0.0
    transition = row.get("transition_prior") or 0.0
    visit = row.get("visit_freq") or 0.0
    backtrack = 1.0 if row.get("canal_backtrack") else 0.0
    w = _HEURISTIC_WEIGHTS
    return (
        w["bearing_align"] * bearing
        + w["inv_dist"] * inv_dist
        + w["reported_match"] * reported
        + w["transition_prior"] * transition
        + w["visit_freq"] * visit
        + w["canal_backtrack"] * backtrack
    )
```

Change `NUMERIC_FEATURES` (currently line 135):

```python
NUMERIC_FEATURES = ["gc_dist_nm", "bearing_align", "transition_prior", "visit_freq", "canal_backtrack"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k penalizes_canal_backtrack -v`
Expected: 1 passed

- [ ] **Step 5: Write the failing test for `_prepare`'s missing-column default**

Add to `backend/tests/test_destination_predict.py`, in the "LightGBM
challenger" section, right before `test_train_and_evaluate_runs_and_reports_metrics`:

```python
def test_prepare_defaults_missing_canal_backtrack_to_zero():
    # A candidate frame built before this feature existed (or a hand-built
    # test frame) may not carry canal_backtrack at all - _prepare must not
    # KeyError, and must treat it as 0 (no penalty).
    df = pd.DataFrame(
        [{"gc_dist_nm": 10.0, "bearing_align": 1.0, "transition_prior": 0.5,
          "visit_freq": 0.5, "target_type": "port", "segment": "VLCC"}]
    )
    out = dp._prepare(df)
    assert out["canal_backtrack"].iloc[0] == 0
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k prepare_defaults -v`
Expected: FAIL with `KeyError: "['canal_backtrack'] not in index"`

- [ ] **Step 7: Make `_prepare` default-fill the column**

In `backend/analytics/destination_predict.py`, change `_prepare` (currently
lines 182-188):

```python
def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "canal_backtrack" not in out.columns:
        out["canal_backtrack"] = 0
    out = out[FEATURES].copy()
    for col in NUMERIC_FEATURES:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].astype("category")
    return out
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k prepare_defaults -v`
Expected: 1 passed

- [ ] **Step 9: Write the failing test for the stale-artifact safety net**

A model artifact trained before this feature existed has a booster expecting
6 columns; `_prepare` now always produces 7. Loading that old booster and
calling `predict_proba` on it must not crash live serving - it must fall back
to the heuristic, exactly like `score_candidates` already does when no model
is promoted.

Add to `backend/tests/test_destination_predict.py`, right after
`test_score_candidates_uses_ml_when_promoted`:

```python
def test_score_candidates_falls_back_to_heuristic_on_stale_model_feature_mismatch():
    # Simulate a booster trained before canal_backtrack existed: it only knows
    # 4 numeric features instead of the current 5.
    old_features = ["gc_dist_nm", "bearing_align", "transition_prior", "visit_freq"]
    train = pd.DataFrame(
        [
            {"gc_dist_nm": 20.0, "bearing_align": 0.9, "transition_prior": 0.5, "visit_freq": 0.5,
             "target_type": "port", "segment": "VLCC", "is_destination": 1},
            {"gc_dist_nm": 900.0, "bearing_align": 0.1, "transition_prior": 0.1, "visit_freq": 0.1,
             "target_type": "port", "segment": "VLCC", "is_destination": 0},
        ]
    )
    X = train[old_features].copy()
    for c in old_features:
        X[c] = pd.to_numeric(X[c])
    X["target_type"] = train["target_type"].astype("category")
    X["segment"] = train["segment"].astype("category")
    import lightgbm as lgb

    dtrain = lgb.Dataset(
        X, label=train["is_destination"].to_numpy(dtype=float),
        categorical_feature=["target_type", "segment"], free_raw_data=False,
    )
    stale_booster = lgb.train({**dp.LGB_PARAMS, "objective": "binary"}, dtrain, num_boost_round=5)
    stale_model = dp.DestinationModel(stale_booster, promoted=True, metrics={})

    candidates = pd.DataFrame(
        [{"mmsi": 1, "target_id": "port:a", "target_type": "port", "segment": "VLCC",
          "gc_dist_nm": 20.0, "bearing_align": 0.9, "transition_prior": 0.5, "visit_freq": 0.5,
          "canal_backtrack": 0}]
    )
    scored = dp.score_candidates(candidates, stale_model)
    assert scored["method"].iloc[0] == "heuristic"
```

- [ ] **Step 10: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k stale_model_feature_mismatch -v`
Expected: FAIL - `score_candidates` currently propagates the LightGBM
column-count exception instead of catching it

- [ ] **Step 11: Add the fallback in `score_candidates`**

In `backend/analytics/destination_predict.py`, change `score_candidates`
(currently lines 252-273):

```python
def score_candidates(
    candidates: pd.DataFrame, model: DestinationModel | None, group_col: str = "mmsi"
) -> pd.DataFrame:
    """Attach `prob` + `method` to a live candidate frame: ml if promoted, else heuristic.

    `candidates` must already carry `transition_prior`/`visit_freq` (attached by
    the caller from the persisted `dest_transitions`/`dest_port_visits` priors) in
    addition to the geometric columns `destination_features.candidate_frame`
    produces. Falls back to the heuristic whenever no model is loaded, it was
    not promoted, or the loaded booster's feature set doesn't match `_prepare`'s
    current output (e.g. a model artifact trained before a feature was added,
    the same conservative default as True ETA's physics fallback and
    `DestinationModel.load`'s own artifact-corruption guard).
    """
    if candidates.empty:
        return candidates.assign(prob=pd.Series(dtype=float), method=pd.Series(dtype=str))
    if model is not None and model.fitted and model.promoted:
        try:
            out = candidates.copy()
            out["_raw"] = model.predict_proba(out)
            sums = out.groupby(group_col)["_raw"].transform("sum")
            sizes = out.groupby(group_col)["_raw"].transform("size")
            out["prob"] = np.where(sums > 0, out["_raw"] / sums.replace(0, np.nan), 1.0 / sizes)
            out["method"] = "ml"
            return out.drop(columns=["_raw"])
        except Exception as exc:  # noqa: BLE001 - serving must never crash on a stale artifact
            log.warning("destination ML model rejected the current feature set (%s); falling back", exc)
    return heuristic_score_candidates(candidates, group_col)
```

- [ ] **Step 12: Run the test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -k stale_model_feature_mismatch -v`
Expected: 1 passed

- [ ] **Step 13: Run the full predictor test file to confirm no regression**

Run: `cd backend && .venv/bin/python -m pytest tests/test_destination_predict.py -v`
Expected: all tests PASS

- [ ] **Step 14: Commit**

```bash
cd backend
git add analytics/destination_predict.py tests/test_destination_predict.py
git commit -m "feat: register canal_backtrack with heuristic + ML scorers, add stale-model fallback"
```

---

## Task 5: Full suite, retrain, redeploy

This task has no new source code - it verifies the four prior tasks integrate
cleanly and regenerates the production model artifact, which is required
before deploy: `analytics/models/dest_lgbm.txt` was trained today (2026-07-03)
on the 6-feature set and does not know about `canal_backtrack`. Task 4's
fallback keeps serving safe either way, but retraining is what actually lets
the new feature earn its keep.

**Files:** none (verification + a gitignored artifact regeneration; no commit
of `analytics/models/` - confirmed gitignored by `backend/.gitignore:26`)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: all tests pass (no failures, no errors)

- [ ] **Step 2: Retrain the destination predictor challenger**

Run: `cd backend && .venv/bin/python -m analytics.destination_predict`
Expected: prints `rows=...  voyages=...`, `heuristic: ...`, `ml: ...`, and
`promoted: True` or `promoted: False`. Either outcome is fine - the gate is
supposed to be honest about whether the new feature earns promotion, not
force it.

- [ ] **Step 3: Confirm the artifact was rewritten**

Run: `ls -la backend/analytics/models/dest_lgbm.txt backend/analytics/models/dest_ml_meta.json`
Expected: both files have a modification time from Step 2's run. Then:

Run: `cat backend/analytics/models/dest_ml_meta.json`
Expected: the JSON's `"features"` array now includes `"canal_backtrack"`.

- [ ] **Step 4: Restart the live service**

Run: `sudo systemctl restart freight-api`
Run: `sudo systemctl status freight-api --no-pager`
Expected: `active (running)`, no crash on startup (the analytics job is a
separate systemd timer, not the API process, but the API loads the model
artifact on each request path through `destination_serving`, so this confirms
nothing is broken)

- [ ] **Step 5: No commit for this task**

Nothing to commit - the test suite run is verification-only and the model
artifact under `analytics/models/` is gitignored. If Step 2 reports
`promoted: True`, note it for the CHANGELOG entry (next task outside this
plan, per the project's changelog skill).
