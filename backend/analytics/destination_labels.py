"""Phase 0 of the destination predictor: the port-call transition graph.

The whole point of predicting a vessel's *actual* destination is to stop trusting
the free-text AIS `destination` field, which the ETA serving code already measured
to be unreliable (~half of tracked vessels resolve to a genuinely different real
port at least once while still >50nm out, `eta_serving.py:90-97`). The standard fix
in the literature (DEBS-2018, Maritime Optima) is a port-call transition graph: a
smoothed Markov prior `P(next port | previous port, segment)` mined from completed
voyages, used as one input feature alongside geometry.

Ground truth is essentially free: `eta_arrivals` (True ETA Phase A) already holds
one row per (mmsi, target, arrival_ts) - a real, geometrically-verified arrival at
a chokepoint or port, never a parsed destination string. This module orders each
vessel's arrivals by time and counts consecutive (prev_target, next_target) pairs.

Two tables in `freight_analytics.duckdb` (sole writer = the analytics job):

  * `dest_transitions` - smoothed transition counts, `(prev_target_id,
    next_target_id, segment)` with a `segment='__all__'` aggregate row and a
    `prev_target_id='__any__'` marginal-fallback row (used when a vessel's prior
    port is unknown, e.g. its very first tracked arrival).
  * `dest_port_visits` - per-vessel historical visit counts to each target (the
    "this vessel usually goes to X" feature).

    python -m analytics.destination_labels     # rebuild both tables
"""

from __future__ import annotations

import argparse
import logging

import duckdb
import pandas as pd

from analytics.eta_labels import ANALYTICS_DB

log = logging.getLogger(__name__)

# Additive (Laplace) smoothing strength. alpha=1 is the standard weak prior: an
# unseen (prev, next) pair gets a small non-zero probability rather than zero,
# without needing a hand-tuned strength.
_ALPHA = 1.0

# Sentinel keys for the aggregate/fallback rows.
_ALL_SEGMENTS = "__all__"
_ANY_PREV = "__any__"

DEST_LABELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dest_transitions (
    prev_target_id  VARCHAR,
    next_target_id  VARCHAR,
    segment         VARCHAR,          -- a real segment, or '__all__' for the rollup
    cnt             INTEGER,
    PRIMARY KEY (prev_target_id, next_target_id, segment)
);

CREATE TABLE IF NOT EXISTS dest_port_visits (
    mmsi        BIGINT,
    target_id   VARCHAR,
    visits      INTEGER,
    last_ts     TIMESTAMP,
    PRIMARY KEY (mmsi, target_id)
);
"""


# ---------------------------------------------------------------------------
# Mining
# ---------------------------------------------------------------------------


def _load_arrivals(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    try:
        df = conn.execute(
            "SELECT mmsi, target_id, arrival_ts, segment FROM eta_arrivals "
            "ORDER BY mmsi, arrival_ts"
        ).df()
    except duckdb.CatalogException:
        return pd.DataFrame()
    if not df.empty:
        df["arrival_ts"] = pd.to_datetime(df["arrival_ts"])
    return df


def build_transitions(arrivals: pd.DataFrame) -> pd.DataFrame:
    """Count (prev_target, next_target, segment) transitions from ordered arrivals.

    One row of counts per distinct (prev, next, segment) triple, plus a
    `segment='__all__'` rollup per (prev, next) and a `prev_target_id='__any__'`
    marginal (ignoring the prior port entirely) at both the per-segment and
    all-segment level - the fallback used when a vessel has no prior arrival on
    record yet.
    """
    if arrivals.empty:
        return pd.DataFrame(columns=["prev_target_id", "next_target_id", "segment", "cnt"])

    rows: list[dict] = []
    for _mmsi, grp in arrivals.groupby("mmsi", sort=False):
        grp = grp.sort_values("arrival_ts")
        prev_ids = grp["target_id"].shift(1)
        for prev, next_, seg in zip(prev_ids, grp["target_id"], grp["segment"], strict=True):
            seg = str(seg) if seg is not None and pd.notna(seg) else None
            if pd.isna(prev):
                # No prior arrival on record for this vessel yet: contributes only
                # to the marginal fallback, not to a real (prev, next) pair.
                rows.append({"prev_target_id": _ANY_PREV, "next_target_id": next_, "segment": seg})
                rows.append(
                    {"prev_target_id": _ANY_PREV, "next_target_id": next_, "segment": _ALL_SEGMENTS}
                )
                continue
            rows.append({"prev_target_id": prev, "next_target_id": next_, "segment": seg})
            rows.append({"prev_target_id": prev, "next_target_id": next_, "segment": _ALL_SEGMENTS})
            # Marginal fallback also accumulates real transitions (ignoring which
            # specific port preceded it) so `_ANY_PREV` reflects true visit
            # frequency, not just first-ever sightings.
            rows.append({"prev_target_id": _ANY_PREV, "next_target_id": next_, "segment": seg})
            rows.append(
                {"prev_target_id": _ANY_PREV, "next_target_id": next_, "segment": _ALL_SEGMENTS}
            )

    if not rows:
        return pd.DataFrame(columns=["prev_target_id", "next_target_id", "segment", "cnt"])
    df = pd.DataFrame(rows)
    df = df[df["segment"].notna() | (df["segment"] == _ALL_SEGMENTS)]
    return (
        df.groupby(["prev_target_id", "next_target_id", "segment"], as_index=False)
        .size()
        .rename(columns={"size": "cnt"})
    )


def build_visit_freq(arrivals: pd.DataFrame) -> pd.DataFrame:
    """Per-(mmsi, target) visit counts - the "this vessel usually goes to X" feature."""
    if arrivals.empty:
        return pd.DataFrame(columns=["mmsi", "target_id", "visits", "last_ts"])
    return arrivals.groupby(["mmsi", "target_id"], as_index=False).agg(
        visits=("arrival_ts", "size"), last_ts=("arrival_ts", "max")
    )


def persist_transitions(conn: duckdb.DuckDBPyConnection, transitions: pd.DataFrame) -> int:
    """Rewrite `dest_transitions` with a freshly mined transition-count frame."""
    conn.execute(DEST_LABELS_SCHEMA)
    conn.execute("DELETE FROM dest_transitions")
    if transitions.empty:
        return 0
    conn.register(
        "_dest_trans_frame", transitions[["prev_target_id", "next_target_id", "segment", "cnt"]]
    )
    conn.execute(
        "INSERT INTO dest_transitions (prev_target_id, next_target_id, segment, cnt) "
        "SELECT prev_target_id, next_target_id, segment, cnt FROM _dest_trans_frame"
    )
    conn.unregister("_dest_trans_frame")
    n = conn.execute("SELECT count(*) FROM dest_transitions").fetchone()[0]
    return int(n)


def persist_visit_freq(conn: duckdb.DuckDBPyConnection, visits: pd.DataFrame) -> int:
    """Rewrite `dest_port_visits` with a freshly mined per-vessel visit-count frame."""
    conn.execute(DEST_LABELS_SCHEMA)
    conn.execute("DELETE FROM dest_port_visits")
    if visits.empty:
        return 0
    conn.register("_dest_visits_frame", visits[["mmsi", "target_id", "visits", "last_ts"]])
    conn.execute(
        "INSERT INTO dest_port_visits (mmsi, target_id, visits, last_ts) "
        "SELECT mmsi, target_id, visits, last_ts FROM _dest_visits_frame"
    )
    conn.unregister("_dest_visits_frame")
    n = conn.execute("SELECT count(*) FROM dest_port_visits").fetchone()[0]
    return int(n)


# ---------------------------------------------------------------------------
# Read-side priors (used by destination_features / destination_predict)
# ---------------------------------------------------------------------------


class TransitionPriors:
    """Smoothed `P(next_target | prev_target, segment)` served from `dest_transitions`.

    Loaded once per build/serving pass (the table is small - at most a few thousand
    rows over a curated target universe). `prior()` falls back from the specific
    (prev, next, segment) cell to the segment-agnostic rollup, and from a known
    prior port to the `__any__` marginal when the vessel's prior port is unseen.
    """

    def __init__(self, counts: pd.DataFrame, vocab_size: int) -> None:
        """Build the (prev, next, segment) count index + per-group totals."""
        self._vocab = max(1, vocab_size)
        self._group_total: dict[tuple[str, str], int] = {}
        self._cell: dict[tuple[str, str, str], int] = {}
        for r in counts.itertuples():
            prev, next_, seg, cnt = r.prev_target_id, r.next_target_id, r.segment, int(r.cnt)
            self._cell[(prev, next_, seg)] = cnt
            key = (prev, seg)
            self._group_total[key] = self._group_total.get(key, 0) + cnt

    @classmethod
    def load(cls, conn: duckdb.DuckDBPyConnection) -> TransitionPriors:
        """Load `dest_transitions` + the target-universe size from an open connection."""
        try:
            counts = conn.execute(
                "SELECT prev_target_id, next_target_id, segment, cnt FROM dest_transitions"
            ).df()
        except duckdb.CatalogException:
            counts = pd.DataFrame(columns=["prev_target_id", "next_target_id", "segment", "cnt"])
        try:
            vocab = conn.execute("SELECT count(*) FROM eta_targets").fetchone()[0]
        except duckdb.CatalogException:
            vocab = int(counts["next_target_id"].nunique()) if not counts.empty else 1
        return cls(counts, int(vocab) or 1)

    def prior(self, prev_target_id: str | None, next_target_id: str, segment: str | None) -> float:
        """Smoothed transition probability, always in (0, 1]."""
        prev = prev_target_id if prev_target_id is not None else _ANY_PREV
        seg = segment if segment is not None else _ALL_SEGMENTS

        # Prefer the specific segment cell; fall back to the segment-agnostic
        # rollup if this (prev, segment) combination was never observed.
        group_key = (prev, seg)
        if group_key not in self._group_total:
            group_key = (prev, _ALL_SEGMENTS)
            seg = _ALL_SEGMENTS
        if group_key not in self._group_total:
            # This vessel's prior port has no history at all: fall back to the
            # marginal (ignore prior port entirely).
            group_key = (_ANY_PREV, seg)
            prev = _ANY_PREV
        if group_key not in self._group_total:
            group_key = (_ANY_PREV, _ALL_SEGMENTS)
            prev, seg = _ANY_PREV, _ALL_SEGMENTS

        total = self._group_total.get(group_key, 0)
        cnt = self._cell.get((prev, next_target_id, seg), 0)
        return (cnt + _ALPHA) / (total + _ALPHA * self._vocab)


class VisitFrequency:
    """Per-vessel historical visit share, `P(this vessel's next port is X)` prior."""

    def __init__(self, visits: pd.DataFrame) -> None:
        """Build the per-(mmsi, target) visit-count index + per-vessel totals."""
        self._by_mmsi_target: dict[tuple[int, str], int] = {}
        self._total_by_mmsi: dict[int, int] = {}
        for r in visits.itertuples():
            mmsi, tid, v = int(r.mmsi), r.target_id, int(r.visits)
            self._by_mmsi_target[(mmsi, tid)] = v
            self._total_by_mmsi[mmsi] = self._total_by_mmsi.get(mmsi, 0) + v

    @classmethod
    def load(cls, conn: duckdb.DuckDBPyConnection) -> VisitFrequency:
        """Load `dest_port_visits` from an open connection."""
        try:
            visits = conn.execute("SELECT mmsi, target_id, visits FROM dest_port_visits").df()
        except duckdb.CatalogException:
            visits = pd.DataFrame(columns=["mmsi", "target_id", "visits"])
        return cls(visits)

    def freq(self, mmsi: int, target_id: str) -> float:
        """Share of this vessel's historical arrivals that were at `target_id`.

        0.0 for an unseen vessel or target - an honest "no history" rather than a
        fabricated prior.
        """
        total = self._total_by_mmsi.get(int(mmsi), 0)
        if total == 0:
            return 0.0
        return self._by_mmsi_target.get((int(mmsi), target_id), 0) / total


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_in_conn(conn: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Rebuild both tables from the freshly-mined `eta_arrivals`. Returns row counts.

    Called by build.py right after Phase A (7b) so transitions reflect this run's
    arrivals; pure read of `eta_arrivals`, no AIS access needed.
    """
    conn.execute(DEST_LABELS_SCHEMA)
    arrivals = _load_arrivals(conn)
    transitions = build_transitions(arrivals)
    visits = build_visit_freq(arrivals)
    n_t = persist_transitions(conn, transitions)
    n_v = persist_visit_freq(conn, visits)
    log.info("destination_labels: %d dest_transitions, %d dest_port_visits", n_t, n_v)
    return n_t, n_v


def run() -> tuple[int, int]:
    """Standalone entry: rebuild against the live analytics DB."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        return run_in_conn(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Build the port-call transition graph").parse_args()
    run()
