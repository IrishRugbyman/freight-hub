"""Unit tests for the freight-api 5xx watcher's parsing and suppression (no journal I/O)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "api_error_watch.py"
_SPEC = importlib.util.spec_from_file_location("api_error_watch", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
w = importlib.util.module_from_spec(_SPEC)
# @dataclass resolves its module through sys.modules, so register before executing.
sys.modules["api_error_watch"] = w
_SPEC.loader.exec_module(w)

_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

# Shape of the real 2026-09-28 outage, as uvicorn wrote it to the journal.
_JOURNAL = [
    'INFO:     104.23.223.84:0 - "GET /api/meta HTTP/1.0" 200 OK',
    'INFO:     104.23.223.84:0 - "GET /api/vessels?kind=tanker HTTP/1.0" 500 Internal Server Error',
    "ERROR:    Exception in ASGI application",
    "Traceback (most recent call last):",
    '  File "/home/lbzgiu/quant/freight/backend/app/live.py", line 131, in live_visible',
    "    raise InvalidIndexError(self._requires_unique_msg)",
    "pandas.errors.InvalidIndexError: Reindexing only valid with uniquely valued Index objects",
    'INFO:     104.23.223.85:0 - "GET /api/vessels HTTP/1.0" 500 Internal Server Error',
    'INFO:     127.0.0.1:49486 - "GET /api/vessels/9668972/equasis HTTP/1.1" 500 Internal Server Error',
    'INFO:     127.0.0.1:49486 - "GET /api/vessels/9321483/equasis HTTP/1.1" 500 Internal Server Error',
    'INFO:     127.0.0.1:49486 - "GET /api/vessels/1/equasis HTTP/1.1" 404 Not Found',
]


def test_normalise_path_collapses_numeric_segments_only():
    assert w.normalise_path("/api/vessels/9668972/equasis") == "/api/vessels/{id}/equasis"
    assert w.normalise_path("/api/vessels/338924075") == "/api/vessels/{id}"
    assert w.normalise_path("/api/analytics/eta-by-target") == "/api/analytics/eta-by-target"


def test_scan_counts_5xx_by_signature_ignoring_query_and_non_5xx():
    result = w.scan(_JOURNAL)

    assert result.errors == {
        "500 GET /api/vessels": 2,
        "500 GET /api/vessels/{id}/equasis": 2,
    }


def test_scan_extracts_traceback_and_final_exception_line():
    result = w.scan(_JOURNAL)

    assert len(result.tracebacks) == 1
    assert result.tracebacks[0][0] == "Traceback (most recent call last):"
    assert result.exception_summaries == [
        "pandas.errors.InvalidIndexError: Reindexing only valid with uniquely valued Index objects"
    ]


def test_scan_keeps_a_traceback_that_runs_to_the_end_of_the_window():
    result = w.scan(
        [
            "ERROR:    Exception in ASGI application",
            "Traceback (most recent call last):",
            "ValueError: boom",
        ]
    )

    assert result.exception_summaries == ["ValueError: boom"]


def test_scan_of_a_clean_journal_finds_nothing():
    result = w.scan(['INFO:     1.2.3.4:0 - "GET /api/health HTTP/1.0" 200 OK'])

    assert result.errors == {} and result.tracebacks == []


def test_new_signatures_alerts_unseen_and_expired_but_suppresses_recent():
    errors = {"500 GET /a": 1, "500 GET /b": 3, "503 GET /c": 1}
    last = {
        "500 GET /b": (_NOW - timedelta(hours=1)).isoformat(),  # recent: suppressed
        "503 GET /c": (_NOW - timedelta(hours=w.SUPPRESS_HOURS, minutes=1)).isoformat(),  # expired
    }

    assert w.new_signatures(errors, last, _NOW) == ["500 GET /a", "503 GET /c"]


def test_new_signatures_is_empty_without_errors():
    assert w.new_signatures({}, {}, _NOW) == []


def test_report_names_the_new_signatures_and_carries_the_traceback():
    result = w.scan(_JOURNAL)

    text = w.report(result, ["500 GET /api/vessels"])

    assert "new since last alert: 500 GET /api/vessels" in text
    assert "2 x 500 GET /api/vessels/{id}/equasis" in text
    assert "InvalidIndexError" in text
    assert "live.py" in text
