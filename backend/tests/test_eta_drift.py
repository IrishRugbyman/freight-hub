"""Phase G ETA drift-watch tests.

Covers the pure `assess_drift` logic (clean / coverage-out-of-band /
err-regression / insufficient-history) and the `run_in_conn` persistence path
against a seeded temp analytics DuckDB.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest
from analytics import eta_drift as dr
from analytics.eta_labels import ETA_SCHEMA

_T0 = datetime(2026, 6, 20, 0, 0, 0)


def _history(rows):
    """Build an overall-aggregate history frame from (hours_offset, med, cov)."""
    return pd.DataFrame(
        [
            {
                "run_ts": _T0 + timedelta(hours=h),
                "model": dr.CHAMPION_MODEL,
                "med_abs_err_h": med,
                "interval_coverage": cov,
            }
            for (h, med, cov) in rows
        ]
    )


def test_clean_run_no_alerts():
    # Stable median |err| and coverage inside the band -> nothing fires.
    hist = _history([(i, 10.0, 0.80) for i in range(10)])
    assert dr.assess_drift(hist) == []


def test_empty_history():
    assert dr.assess_drift(pd.DataFrame()) == []


def test_coverage_below_band_is_alert():
    hist = _history([(i, 10.0, 0.80) for i in range(5)] + [(5, 10.0, 0.55)])
    alerts = dr.assess_drift(hist)
    cov = [a for a in alerts if a["kind"] == "coverage"]
    assert len(cov) == 1
    assert cov[0]["severity"] == "alert"
    assert cov[0]["metric"] == pytest.approx(0.55)
    assert cov[0]["reference"] == pytest.approx(dr.COVERAGE_BAND[0])


def test_coverage_above_band_is_warn():
    hist = _history([(i, 10.0, 0.80) for i in range(5)] + [(5, 10.0, 0.97)])
    cov = [a for a in dr.assess_drift(hist) if a["kind"] == "coverage"]
    assert len(cov) == 1
    assert cov[0]["severity"] == "warn"


def test_err_regression_fires_when_both_thresholds_cleared():
    # Trailing median ~10h, latest jumps to 18h (+80%, +8h) -> warn.
    hist = _history([(i, 10.0, 0.80) for i in range(6)] + [(6, 18.0, 0.80)])
    errs = [a for a in dr.assess_drift(hist) if a["kind"] == "med_abs_err"]
    assert len(errs) == 1
    assert errs[0]["metric"] == pytest.approx(18.0)
    assert errs[0]["reference"] == pytest.approx(10.0)


def test_err_regression_respects_absolute_floor():
    # +60% relative but only +0.6h absolute (below the 1h floor) -> no alert.
    hist = _history([(i, 1.0, 0.80) for i in range(6)] + [(6, 1.6, 0.80)])
    errs = [a for a in dr.assess_drift(hist) if a["kind"] == "med_abs_err"]
    assert errs == []


def test_err_regression_needs_min_trailing_runs():
    # Only 2 prior runs (< MIN_TRAIL_RUNS) -> the |err| check is skipped.
    hist = _history([(0, 10.0, 0.80), (1, 10.0, 0.80), (2, 40.0, 0.80)])
    errs = [a for a in dr.assess_drift(hist) if a["kind"] == "med_abs_err"]
    assert errs == []


def test_run_in_conn_persists_alert(tmp_path):
    db = tmp_path / "analytics.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(ETA_SCHEMA)
    # Seed a clean baseline then one degraded run (coverage collapse).
    rows = [(i, 10.0, 0.80) for i in range(5)] + [(5, 10.0, 0.50)]
    for h, med, cov in rows:
        conn.execute(
            "INSERT INTO eta_model_metrics "
            "(run_ts, model, lead_bucket, target_type, n, med_abs_err_h, bias_h, "
            " mape, p90_abs_err_h, interval_coverage) "
            "VALUES (?, ?, 'all', 'all', 100, ?, 0, 0, 0, ?)",
            [_T0 + timedelta(hours=h), dr.CHAMPION_MODEL, med, cov],
        )
    n = dr.run_in_conn(conn)
    assert n == 1
    stored = conn.execute(
        "SELECT kind, severity FROM eta_drift_alerts"
    ).fetchall()
    assert stored == [("coverage", "alert")]
    # Idempotent: a second pass over the same data must not duplicate rows.
    dr.run_in_conn(conn)
    assert conn.execute("SELECT count(*) FROM eta_drift_alerts").fetchone()[0] == 1
    conn.close()


def test_run_in_conn_missing_table_is_safe(tmp_path):
    db = tmp_path / "empty.duckdb"
    conn = duckdb.connect(str(db))
    assert dr.run_in_conn(conn) == 0
    conn.close()
