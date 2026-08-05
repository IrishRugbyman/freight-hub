"""Window-bound resolution for the analytics build's catch-up walk-forward.

The snapshot window is loaded into a single DataFrame, so an unbounded multi-day
backlog is what OOMs the job. These cover the bound arithmetic that makes a large
backlog recoverable in fixed-size passes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from analytics.build import _HISTORY_START, _OVERLAP_HOURS, _window_bounds

_WM = datetime(2026, 7, 26, 12, 0)


def test_window_bounds_unbounded_returns_no_upper_bound():
    since, until = _window_bounds(_WM, None)
    assert since == _WM - timedelta(hours=_OVERLAP_HOURS)
    assert until is None


def test_window_bounds_applies_overlap_behind_watermark():
    since, _ = _window_bounds(_WM, 48)
    assert since == _WM - timedelta(hours=_OVERLAP_HOURS)


def test_window_bounds_caps_window_at_requested_width():
    since, until = _window_bounds(_WM, 48)
    assert until == since + timedelta(hours=48)


def test_window_bounds_no_watermark_starts_at_history_start():
    since, until = _window_bounds(None, 48)
    assert since == _HISTORY_START
    assert until == _HISTORY_START + timedelta(hours=48)


def test_window_bounds_net_advance_is_window_minus_overlap():
    """Each pass must move the watermark forward, or a walk-forward livelocks."""
    since, until = _window_bounds(_WM, 48)
    # Next pass starts _OVERLAP_HOURS behind where this one ended.
    next_since, _ = _window_bounds(until, 48)
    assert next_since > since
    assert next_since - since == timedelta(hours=48 - _OVERLAP_HOURS)


@pytest.mark.parametrize("width", [_OVERLAP_HOURS, _OVERLAP_HOURS - 1, 0])
def test_window_bounds_rejects_width_that_cannot_advance(width):
    with pytest.raises(ValueError, match="overlap"):
        _window_bounds(_WM, width)


def test_window_bounds_accepts_width_just_above_overlap():
    since, until = _window_bounds(_WM, _OVERLAP_HOURS + 1)
    assert until == since + timedelta(hours=_OVERLAP_HOURS + 1)
