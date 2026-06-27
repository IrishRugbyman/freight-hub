"""ETA drift watch (True ETA Phase G - monitoring half).

There is no separate "ETA refresh" timer: label mining, sample build, metric
scoring and serving already run hourly inside :mod:`analytics.build`
(`freight-analytics.timer`), so the accuracy tables are always current. The
missing piece of Phase G was *monitoring* - nothing flagged when the champion's
accuracy quietly degraded. This module fills that gap.

After the batch job rewrites ``eta_model_metrics``, it calls :func:`run_in_conn`,
which reads the champion (``physics_v1``) overall aggregate row
(``lead_bucket='all', target_type='all'``) for every run, compares the latest
run against:

1. a fixed interval-coverage band (the P10-P90 interval nominally covers 80%, so
   coverage drifting out of ``[0.70, 0.90]`` means the calibration broke), and
2. its own trailing-window median |err| (a sudden jump signals a label/pipeline
   regression or a real accuracy collapse),

and persists any degradation to ``eta_drift_alerts`` plus a ``log.warning`` (so
it surfaces in ``journalctl`` and on the accuracy scoreboard).

The pure assessment logic lives in :func:`assess_drift` and is unit-tested;
``run_in_conn`` only does I/O.
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta

import duckdb
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables (kept module-level so tests and ops can reason about them)
# ---------------------------------------------------------------------------

CHAMPION_MODEL = "physics_v1"

# The served interval is [P10, P90] -> nominal 80% coverage. Outside this band
# the calibration is materially wrong (too tight or absurdly wide).
COVERAGE_BAND = (0.70, 0.90)

# Trailing window used as the reference for the median-|err| regression check.
TRAIL_DAYS = 7
MIN_TRAIL_RUNS = 3            # need a few prior runs before a comparison is meaningful

# Flag a regression only when the latest run is BOTH this much worse in relative
# terms AND this much worse in absolute hours (the abs floor kills noise on the
# short, near-zero-error buckets).
ERR_REGRESSION_FRAC = 0.5    # +50% vs the trailing median
ERR_REGRESSION_ABS_H = 1.0   # and at least +1h


DRIFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS eta_drift_alerts (
    run_ts     TIMESTAMP,
    model      VARCHAR,
    kind       VARCHAR,        -- 'coverage' | 'med_abs_err'
    severity   VARCHAR,        -- 'warn' | 'alert'
    metric     DOUBLE,         -- current observed value
    reference  DOUBLE,         -- band edge / trailing median it was judged against
    detail     VARCHAR,        -- human-readable message
    PRIMARY KEY (run_ts, model, kind)
);
"""


# ---------------------------------------------------------------------------
# Pure assessment
# ---------------------------------------------------------------------------


def assess_drift(
    history: pd.DataFrame,
    *,
    model: str = CHAMPION_MODEL,
    coverage_band: tuple[float, float] = COVERAGE_BAND,
    trail_days: int = TRAIL_DAYS,
    min_trail_runs: int = MIN_TRAIL_RUNS,
    err_frac: float = ERR_REGRESSION_FRAC,
    err_abs_h: float = ERR_REGRESSION_ABS_H,
) -> list[dict]:
    """Assess the latest run for drift against the band and trailing history.

    Args:
        history: ``eta_model_metrics`` rows for ``model`` at the overall
            aggregate level (``lead_bucket='all', target_type='all'``). Must have
            columns ``run_ts``, ``med_abs_err_h``, ``interval_coverage``. Extra
            rows/columns are ignored; the function filters and sorts internally.
        model: champion model name (used for filtering and labels).
        coverage_band: ``(lo, hi)`` acceptable interval-coverage range.
        trail_days: lookback window (days) for the median-|err| reference.
        min_trail_runs: minimum prior runs in the window before comparing |err|.
        err_frac: relative regression threshold vs the trailing median.
        err_abs_h: absolute regression floor (hours) that must also be cleared.

    Returns:
        A list of alert dicts (possibly empty), one per triggered check, each
        with keys ``run_ts, model, kind, severity, metric, reference, detail``.
    """
    if history is None or history.empty:
        return []

    df = history[history["model"] == model] if "model" in history.columns else history.copy()
    if df.empty:
        return []

    df = df.dropna(subset=["run_ts"]).sort_values("run_ts")
    if df.empty:
        return []

    current = df.iloc[-1]
    run_ts = current["run_ts"]
    alerts: list[dict] = []

    # --- 1. Interval-coverage band -----------------------------------------
    cov = current.get("interval_coverage")
    lo, hi = coverage_band
    if cov is not None and pd.notna(cov):
        cov = float(cov)
        if cov < lo:
            alerts.append(
                {
                    "run_ts": run_ts, "model": model, "kind": "coverage",
                    "severity": "alert", "metric": cov, "reference": lo,
                    "detail": (
                        f"interval coverage {cov:.1%} below {lo:.0%} floor "
                        f"(P10-P90 band too tight / underconfident labels)"
                    ),
                }
            )
        elif cov > hi:
            alerts.append(
                {
                    "run_ts": run_ts, "model": model, "kind": "coverage",
                    "severity": "warn", "metric": cov, "reference": hi,
                    "detail": (
                        f"interval coverage {cov:.1%} above {hi:.0%} ceiling "
                        f"(P10-P90 band too wide / overcautious)"
                    ),
                }
            )

    # --- 2. Median-|err| regression vs trailing window ----------------------
    cur_err = current.get("med_abs_err_h")
    if cur_err is not None and pd.notna(cur_err):
        cur_err = float(cur_err)
        window_start = run_ts - timedelta(days=trail_days)
        prior = df[(df["run_ts"] >= window_start) & (df["run_ts"] < run_ts)]
        prior = prior.dropna(subset=["med_abs_err_h"])
        if len(prior) >= min_trail_runs:
            trail_med = float(prior["med_abs_err_h"].median())
            worse_rel = cur_err > trail_med * (1.0 + err_frac)
            worse_abs = (cur_err - trail_med) >= err_abs_h
            if worse_rel and worse_abs:
                alerts.append(
                    {
                        "run_ts": run_ts, "model": model, "kind": "med_abs_err",
                        "severity": "warn", "metric": cur_err, "reference": trail_med,
                        "detail": (
                            f"median |err| {cur_err:.1f}h is "
                            f"{(cur_err / trail_med - 1):.0%} above the "
                            f"{trail_days}d trailing median {trail_med:.1f}h"
                        ),
                    }
                )

    return alerts


# ---------------------------------------------------------------------------
# I/O wiring
# ---------------------------------------------------------------------------

_HISTORY_SQL = (
    "SELECT run_ts, model, med_abs_err_h, interval_coverage "
    "FROM eta_model_metrics "
    "WHERE model = ? AND lead_bucket = 'all' AND target_type = 'all' "
    "ORDER BY run_ts"
)


def run_in_conn(conn: duckdb.DuckDBPyConnection) -> int:
    """Assess the champion's latest run and persist any drift alerts.

    Reads the overall-aggregate history from ``eta_model_metrics`` (already
    rewritten earlier in the batch job), runs :func:`assess_drift`, and writes
    triggered alerts to ``eta_drift_alerts`` (idempotent per run_ts/kind). Logs
    a warning per alert. Returns the number of alerts raised.
    """
    conn.execute(DRIFT_SCHEMA)
    try:
        history = conn.execute(_HISTORY_SQL, [CHAMPION_MODEL]).df()
    except duckdb.Error as exc:  # eta_model_metrics absent on a fresh DB
        log.warning("drift watch: cannot read eta_model_metrics (%s); skipping", exc)
        return 0

    alerts = assess_drift(history)
    if not alerts:
        log.info("drift watch: %s within tolerance (no alerts)", CHAMPION_MODEL)
        return 0

    for a in alerts:
        conn.execute(
            "INSERT OR REPLACE INTO eta_drift_alerts "
            "(run_ts, model, kind, severity, metric, reference, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [a["run_ts"], a["model"], a["kind"], a["severity"],
             a["metric"], a["reference"], a["detail"]],
        )
        log.warning("ETA drift [%s/%s]: %s", a["severity"], a["kind"], a["detail"])

    return len(alerts)


def run() -> int:
    """Standalone entry: assess the live analytics DB and persist alerts."""
    from analytics.eta_backtest import ANALYTICS_DB  # reuse the canonical path

    conn = duckdb.connect(str(ANALYTICS_DB))
    try:
        n = run_in_conn(conn)
    finally:
        conn.close()
    print(f"{n} drift alert(s) raised" if n else "no drift detected")
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="ETA champion drift watch").parse_args()
    run()
