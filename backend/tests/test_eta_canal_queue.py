"""True ETA: measured canal staging ("canal queue") from AIS transit tracks.

Covers the pure measurement (`measure_canal_queue`), the persistence round-trip,
the transit-count gate, and the process-wide staging override that routes the
measured figure into `queue_wait` (with the nominal constant as fallback).
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest
from quant_lib.freight.eta import (
    CANAL_STAGING_HOURS,
    canal_staging_hours,
    queue_wait,
    set_measured_staging,
)

from analytics import eta_canal_queue as cq


@pytest.fixture(autouse=True)
def _reset_staging():
    """Never let an installed override leak across tests."""
    set_measured_staging({})
    yield
    set_measured_staging({})


def _canal_samples(target_id: str, n_voyages: int, loiter_fixes: int) -> pd.DataFrame:
    """n_voyages canal transits, each with `loiter_fixes` in-band slow fixes.

    Each voyage: `loiter_fixes` in-band fixes at SOG 0.5 kn (waiting) + one in-band
    transit fix at 12 kn + one out-of-band fix (must be ignored). Observed staging
    per voyage therefore = loiter_fixes * cadence(=1h).
    """
    rows = []
    for v in range(n_voyages):
        vid = hash((target_id, v)) & 0xFFFFFFFF
        for _ in range(loiter_fixes):
            rows.append((target_id, vid, True, 20.0, 0.5))  # in-band, waiting
        rows.append((target_id, vid, True, 30.0, 12.0))  # in-band, moving
        rows.append((target_id, vid, True, 400.0, 12.0))  # out-of-band, ignored
    return pd.DataFrame(rows, columns=["target_id", "voyage_id", "is_canal", "gc_dist_nm", "sog"])


def test_measure_returns_median_loiter_hours():
    s = _canal_samples("cp:suez", n_voyages=25, loiter_fixes=8)
    measured = cq.measure_canal_queue(s)
    assert measured == {"cp:suez": pytest.approx(8.0)}  # 8 slow in-band fixes * 1h


def test_thin_canal_below_min_transits_is_dropped():
    # 25 Suez transits (kept) + 5 Panama transits (below the 20-transit gate).
    s = pd.concat(
        [_canal_samples("cp:suez", 25, 6), _canal_samples("cp:panama", 5, 12)],
        ignore_index=True,
    )
    measured = cq.measure_canal_queue(s)
    assert "cp:suez" in measured
    assert "cp:panama" not in measured  # too few transits to trust


def test_non_canal_and_out_of_band_ignored():
    s = _canal_samples("cp:suez", 25, 4)
    # A port target with lots of slow in-band fixes must not produce staging.
    port = pd.DataFrame(
        [("port:rotterdam", i, False, 10.0, 0.2) for i in range(100)],
        columns=["target_id", "voyage_id", "is_canal", "gc_dist_nm", "sog"],
    )
    measured = cq.measure_canal_queue(pd.concat([s, port], ignore_index=True))
    assert set(measured) == {"cp:suez"}


def test_persist_and_load_round_trip():
    conn = duckdb.connect(":memory:")
    cq.persist(conn, {"cp:suez": 10.5, "cp:panama": 9.0}, {"cp:suez": 42, "cp:panama": 31})
    loaded = cq.load_canal_staging(conn)
    assert loaded == {"cp:suez": 10.5, "cp:panama": 9.0}


def test_load_empty_when_table_absent():
    conn = duckdb.connect(":memory:")
    assert cq.load_canal_staging(conn) == {}


def test_installed_staging_overrides_constant_in_queue_wait():
    # Nominal constant first.
    assert canal_staging_hours("cp:suez") == CANAL_STAGING_HOURS["cp:suez"]
    set_measured_staging({"cp:suez": 11.7})
    assert canal_staging_hours("cp:suez") == 11.7
    # queue_wait picks it up, still proximity-gated + canal-gated.
    assert queue_wait(True, 30.0, "cp:suez") == 11.7
    assert queue_wait(True, 500.0, "cp:suez") == 0.0  # out of band
    assert queue_wait(False, 30.0, "cp:suez") == 0.0  # not a canal
    # A canal without a measured value falls back to its constant.
    assert canal_staging_hours("cp:panama") == CANAL_STAGING_HOURS["cp:panama"]


def test_run_in_conn_persists_and_returns():
    conn = duckdb.connect(":memory:")
    s = _canal_samples("cp:suez", 25, 7)
    measured = cq.run_in_conn(conn, s)
    assert measured == {"cp:suez": pytest.approx(7.0)}
    assert cq.load_canal_staging(conn) == {"cp:suez": pytest.approx(7.0)}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
