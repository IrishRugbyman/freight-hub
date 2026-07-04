"""Live serving for the destination predictor.

For every live underway vessel, builds its candidate shortlist (geometric ahead-
targets + the resolved AIS-reported destination, `destination_features.
candidate_frame`), attaches the port-call transition prior + per-vessel visit
frequency (`destination_labels`), scores each candidate (ML if promoted, else the
transparent heuristic - `destination_predict.score_candidates`), and persists the
top-k ranked candidates per vessel into `destination_predictions`.

`GET /api/analytics/destination` (`app.runner_destination`) reads this snapshot
straight through; the API never computes a prediction itself. Sole writer is the
analytics job, wired in right after True ETA's own serving step (`build.py`).

    python -m analytics.destination_serving     # score live vessels, write the snapshot
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd

from analytics.destination_features import (
    CANAL_BACKTRACK_WINDOW_DAYS,
    candidate_frame,
    resolve_origin_target_id,
)
from analytics.destination_labels import TransitionPriors, VisitFrequency
from analytics.destination_predict import DestinationModel, score_candidates
from analytics.eta_labels import ANALYTICS_DB, _default_ais_query
from analytics.eta_serving import _laden_map, _load_live, _load_targets

log = logging.getLogger(__name__)

# Keep the top-k ranked candidates per vessel (a vessel realistically has one true
# destination, but showing the runner-up(s) is what makes the prediction honest
# about its own uncertainty - the whole point of not just picking a hard winner).
_TOP_K = 3

DEST_PREDICTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS destination_predictions (
    mmsi            BIGINT,
    rank            INTEGER,
    target_id       VARCHAR,
    target_name     VARCHAR,
    target_type     VARCHAR,
    target_lat      DOUBLE,
    target_lon      DOUBLE,
    prob            DOUBLE,
    method          VARCHAR,          -- 'heuristic' | 'ml'
    reported_match  BOOLEAN,
    gc_dist_nm      DOUBLE,
    as_of           TIMESTAMP,
    PRIMARY KEY (mmsi, rank)
);
"""

_PERSIST_COLS = [
    "mmsi",
    "rank",
    "target_id",
    "target_name",
    "target_type",
    "target_lat",
    "target_lon",
    "prob",
    "method",
    "reported_match",
    "gc_dist_nm",
    "as_of",
]


def _prev_target_by_mmsi(conn: duckdb.DuckDBPyConnection) -> dict[int, str]:
    """Each vessel's most recent mined arrival target - the transition prior's "prev"."""
    try:
        df = conn.execute(
            "SELECT mmsi, target_id FROM ("
            "  SELECT mmsi, target_id, "
            "         row_number() OVER (PARTITION BY mmsi ORDER BY arrival_ts DESC) AS rn "
            "  FROM eta_arrivals"
            ") WHERE rn = 1"
        ).df()
    except duckdb.CatalogException:
        return {}
    return {int(r.mmsi): r.target_id for r in df.itertuples()}


def _origin_target_by_mmsi(
    live: pd.DataFrame, targets: pd.DataFrame, prev_by_mmsi: dict[int, str]
) -> dict[int, str]:
    """Cold-start fallback for the transition prior's "prev" input.

    Only resolved for vessels absent from `prev_by_mmsi` (no mined arrival
    history yet, `eta_arrivals` has never seen them) - a vessel with real history
    always uses that instead, since it's a geometrically-verified fact rather
    than a hand-typed AIS string. See `destination_features.resolve_origin_target_id`.
    """
    out: dict[int, str] = {}
    for r in live.itertuples():
        mmsi = int(r.mmsi)
        if mmsi in prev_by_mmsi:
            continue
        dest = getattr(r, "destination", None)
        if not isinstance(dest, str) or not dest.strip():
            continue
        tid = resolve_origin_target_id(dest, targets, float(r.lat), float(r.lon))
        if tid is not None:
            out[mmsi] = tid
    return out


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
            "  WHERE chokepoint IN ('suez', 'panama') AND exited_ts >= ? AND exited_ts <= ?"
            ") WHERE rn = 1",
            [cutoff, now],
        ).df()
    except duckdb.CatalogException:
        return {}
    return {int(r.mmsi): r.chokepoint for r in df.itertuples()}


def build_destination_predictions(
    conn: duckdb.DuckDBPyConnection,
    ais_query,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Score every live underway vessel's candidate destinations.

    Returns a frame ready for `persist_destination_predictions` (top `_TOP_K`
    ranked rows per vessel). Pure read of the AIS DB via the injected `ais_query`.
    """
    now = now or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    targets = _load_targets(conn)
    live = _load_live(ais_query, now)
    if targets.empty or live.empty:
        return pd.DataFrame(columns=_PERSIST_COLS)

    candidates = candidate_frame(
        live,
        targets,
        recent_canal_transit=_recent_canal_transit_by_mmsi(conn, now),
        laden_by_mmsi=_laden_map(conn),
    )
    if candidates.empty:
        return pd.DataFrame(columns=_PERSIST_COLS)

    trans = TransitionPriors.load(conn)
    visits = VisitFrequency.load(conn)
    prev_by_mmsi = _prev_target_by_mmsi(conn)
    origin_by_mmsi = _origin_target_by_mmsi(live, targets, prev_by_mmsi)

    candidates["transition_prior"] = [
        trans.prior(
            prev_by_mmsi.get(int(r.mmsi), origin_by_mmsi.get(int(r.mmsi))),
            r.target_id,
            r.segment,
        )
        for r in candidates.itertuples()
    ]
    candidates["visit_freq"] = [
        visits.freq(int(r.mmsi), r.target_id) for r in candidates.itertuples()
    ]

    model = DestinationModel.load()
    if model is not None:
        log.info(
            "build_destination_predictions: loaded destination ML model (promoted=%s)",
            model.promoted,
        )
    scored = score_candidates(candidates, model)
    if scored.empty:
        return pd.DataFrame(columns=_PERSIST_COLS)

    rows: list[dict] = []
    for mmsi, g in scored.groupby("mmsi", sort=False):
        g = g.sort_values("prob", ascending=False).head(_TOP_K)
        for rank, r in enumerate(g.itertuples(), start=1):
            rows.append(
                {
                    "mmsi": int(mmsi),
                    "rank": rank,
                    "target_id": r.target_id,
                    "target_name": r.target_name,
                    "target_type": r.target_type,
                    "target_lat": float(r.target_lat),
                    "target_lon": float(r.target_lon),
                    "prob": round(float(r.prob), 4),
                    "method": r.method,
                    "reported_match": bool(r.reported_match),
                    "gc_dist_nm": round(float(r.gc_dist_nm), 1),
                    "as_of": now,
                }
            )
    return pd.DataFrame(rows, columns=_PERSIST_COLS)


def persist_destination_predictions(conn: duckdb.DuckDBPyConnection, preds: pd.DataFrame) -> int:
    """Rewrite `destination_predictions` with the fresh live snapshot."""
    conn.execute(DEST_PREDICTIONS_SCHEMA)
    conn.execute("DELETE FROM destination_predictions")
    if preds.empty:
        log.info("no destination_predictions to persist (no live underway vessels?)")
        return 0
    frame = preds[_PERSIST_COLS]
    conn.register("_dest_preds_frame", frame)
    conn.execute(
        "INSERT OR REPLACE INTO destination_predictions ("
        + ", ".join(_PERSIST_COLS)
        + ") SELECT "
        + ", ".join(_PERSIST_COLS)
        + " FROM _dest_preds_frame"
    )
    conn.unregister("_dest_preds_frame")
    n = conn.execute("SELECT count(*) FROM destination_predictions").fetchone()[0]
    log.info("persisted %d destination_predictions across %d vessels", n, preds["mmsi"].nunique())
    return int(n)


def run_in_conn(conn: duckdb.DuckDBPyConnection, ais_query, now: datetime | None = None) -> int:
    """Build + persist live destination predictions into an open analytics connection.

    Called by build.py right after True ETA's own serving step (7d), so the
    priors it reads (`dest_transitions`/`dest_port_visits`) are this run's fresh
    `destination_labels` output.
    """
    conn.execute(DEST_PREDICTIONS_SCHEMA)
    preds = build_destination_predictions(conn, ais_query, now=now)
    return persist_destination_predictions(conn, preds)


def run() -> int:
    """Standalone entry: score live vessels straight into the live analytics DB."""
    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        return run_in_conn(conn, _default_ais_query)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(
        description="Score live vessels into destination_predictions"
    ).parse_args()
    run()
