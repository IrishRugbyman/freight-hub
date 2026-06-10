"""Unit tests for analytics/detect.py pure detection functions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from analytics.detect import anchored_episodes, laden_status, transit_episodes


# ---------------------------------------------------------------------------
# laden_status
# ---------------------------------------------------------------------------


def test_laden_none_draught():
    assert laden_status(None, 22.0, "VLCC") == "unknown"


def test_laden_zero_draught():
    assert laden_status(0.0, 22.0, "VLCC") == "unknown"


def test_laden_ratio_high():
    # 20/22 = 0.909 >= 0.8
    assert laden_status(20.0, 22.0, "VLCC") == "laden"


def test_ballast_ratio_low():
    # 12/22 = 0.545 <= 0.65
    assert laden_status(12.0, 22.0, "VLCC") == "ballast"


def test_laden_unknown_mid_range():
    # 15/22 = 0.682, between 0.65 and 0.80
    assert laden_status(15.0, 22.0, "VLCC") == "unknown"


def test_laden_fallback_to_design_draught_when_max_none():
    # No history; VLCC design = 22.0. 20/22 = 0.909 -> laden
    assert laden_status(20.0, None, "VLCC") == "laden"


def test_laden_fallback_to_design_draught_when_shallow():
    # max_seen = 5.0 < 0.7 * 22.0 = 15.4 -> use design
    # 20/22 = 0.909 -> laden
    assert laden_status(20.0, 5.0, "VLCC") == "laden"


def test_laden_unknown_segment():
    # No design draught for unknown segment, max_seen provided
    assert laden_status(10.0, None, "UnknownVessel") == "unknown"


# ---------------------------------------------------------------------------
# transit_episodes helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2026, 6, 10, 0, 0, 0)


def _make_snap(mmsi, minutes_offset, lat, lon, region, sog=10.0, segment="VLCC", kind="tanker", draught=20.0):
    return {
        "mmsi": mmsi,
        "snapshot_ts": _BASE_TS + timedelta(minutes=minutes_offset),
        "lat": lat,
        "lon": lon,
        "sog": sog,
        "nav_status": 0,
        "draught": draught,
        "region": region,
        "kind": kind,
        "segment": segment,
        "destination": "TEST",
    }


def _df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# transit_episodes
# ---------------------------------------------------------------------------


def test_transit_empty_df():
    assert transit_episodes(pd.DataFrame()) == []


def test_transit_requires_two_fixes():
    # Single fix in hormuz -> not enough for a transit
    rows = [_make_snap(1001, 0, 25.5, 56.5, "hormuz")]
    assert transit_episodes(_df(rows)) == []


def test_transit_hormuz_outbound():
    # Two fixes with 0.4 deg lon displacement (outbound = east = increasing lon)
    # hormuz axis is "lon", positive = outbound -> displacement > 0 -> "outbound"... wait
    # Actually from zones.py: hormuz ("lon", "outbound", "inbound_gulf")
    # positive lon displacement = outbound
    rows = [
        _make_snap(1001, 0, 26.0, 56.0, "hormuz"),
        _make_snap(1001, 60, 26.0, 56.5, "hormuz"),  # +0.5 lon -> outbound
    ]
    result = transit_episodes(_df(rows))
    assert len(result) == 1
    t = result[0]
    assert t["mmsi"] == 1001
    assert t["chokepoint"] == "hormuz"
    assert t["direction"] == "outbound"
    assert t["laden"] is True  # draught=20/22 design=0.909 -> laden


def test_transit_hormuz_inbound():
    rows = [
        _make_snap(1001, 0, 26.0, 57.5, "hormuz"),
        _make_snap(1001, 60, 26.0, 57.0, "hormuz"),  # -0.5 lon -> inbound_gulf
    ]
    result = transit_episodes(_df(rows))
    assert len(result) == 1
    assert result[0]["direction"] == "inbound_gulf"


def test_transit_insufficient_displacement():
    # Displacement of 0.1 deg < 0.3 threshold -> no transit (anchored presence)
    rows = [
        _make_snap(1001, 0, 26.0, 56.0, "hormuz"),
        _make_snap(1001, 30, 26.0, 56.05, "hormuz"),
        _make_snap(1001, 60, 26.0, 56.1, "hormuz"),
    ]
    assert transit_episodes(_df(rows)) == []


def test_transit_gap_splits_episode():
    # Three fixes: first two close together, then a 3h gap; only the second pair qualifies
    # (the first pair has insufficient displacement but the second is a separate episode)
    rows = [
        # Episode 1: 2 fixes, 0.05 deg displacement -> too small -> no transit
        _make_snap(1001, 0, 26.0, 56.0, "hormuz"),
        _make_snap(1001, 30, 26.0, 56.05, "hormuz"),
        # Episode 2: 2 fixes 3h later, 0.5 deg displacement -> transit
        _make_snap(1001, 3 * 60 + 5, 26.0, 56.2, "hormuz"),
        _make_snap(1001, 3 * 60 + 65, 26.0, 56.7, "hormuz"),
    ]
    result = transit_episodes(_df(rows))
    assert len(result) == 1
    assert result[0]["direction"] == "outbound"


def test_transit_non_chokepoint_region_ignored():
    # Rows with region='rotterdam_port' (not a chokepoint) should be ignored
    rows = [
        _make_snap(1001, 0, 51.9, 4.0, "rotterdam_port"),
        _make_snap(1001, 60, 51.9, 4.1, "rotterdam_port"),
    ]
    assert transit_episodes(_df(rows)) == []


def test_transit_suez_northbound():
    # suez axis = lat, positive = northbound
    rows = [
        _make_snap(1001, 0, 28.5, 32.5, "suez", segment="Capesize", kind="bulk"),
        _make_snap(1001, 90, 29.0, 32.5, "suez", segment="Capesize", kind="bulk"),
    ]
    result = transit_episodes(_df(rows))
    assert len(result) == 1
    assert result[0]["direction"] == "northbound"
    assert result[0]["kind"] == "bulk"


# ---------------------------------------------------------------------------
# anchored_episodes
# ---------------------------------------------------------------------------


def _anc_snap(mmsi, minutes, lat, lon, nav_status=1, sog=0.1, zone_inside=True):
    # Use Singapore East anchorage coords: ((1.1, 104.0), (1.35, 104.5))
    if zone_inside:
        return {
            "mmsi": mmsi,
            "snapshot_ts": _BASE_TS + timedelta(minutes=minutes),
            "lat": 1.2,
            "lon": 104.2,
            "sog": sog,
            "nav_status": nav_status,
            "draught": 12.0,
            "region": "singapore_malacca",
            "kind": "bulk",
            "segment": "Capesize",
            "destination": "SGSIN",
        }
    else:
        return {
            "mmsi": mmsi,
            "snapshot_ts": _BASE_TS + timedelta(minutes=minutes),
            "lat": lat,
            "lon": lon,
            "sog": sog,
            "nav_status": nav_status,
            "draught": 12.0,
            "region": "singapore_malacca",
            "kind": "bulk",
            "segment": "Capesize",
            "destination": "SGSIN",
        }


def test_anchored_empty_df():
    assert anchored_episodes(pd.DataFrame()) == []


def test_anchored_basic():
    # 4 hourly fixes inside singapore_east with nav_status=1 -> 3h episode -> qualifies
    rows = [_anc_snap(1001, i * 60, 1.2, 104.2) for i in range(4)]
    result = anchored_episodes(_df(rows))
    assert len(result) == 1
    ep = result[0]
    assert ep["mmsi"] == 1001
    assert ep["zone"] == "singapore_east"


def test_anchored_too_short():
    # Only 1h episode -> doesn't qualify (< 2h minimum)
    rows = [_anc_snap(1001, 0, 1.2, 104.2), _anc_snap(1001, 60, 1.2, 104.2)]
    # Duration = 60 min = 1h < 2h
    result = anchored_episodes(_df(rows))
    assert result == []


def test_anchored_outside_zone():
    # Fixes with nav_status=1 but far outside any anchorage zone -> not detected
    rows = [
        {
            "mmsi": 1001,
            "snapshot_ts": _BASE_TS + timedelta(minutes=i * 60),
            "lat": 15.0,
            "lon": 60.0,
            "sog": 0.1,
            "nav_status": 1,
            "draught": 10.0,
            "region": "hormuz",
            "kind": "tanker",
            "segment": "VLCC",
            "destination": "AEFJR",
        }
        for i in range(5)
    ]
    result = anchored_episodes(_df(rows))
    assert result == []


def test_anchored_by_low_sog():
    # nav_status=0 (underway) but sog < 0.5 inside zone -> still counts
    rows = [
        {
            "mmsi": 1001,
            "snapshot_ts": _BASE_TS + timedelta(minutes=i * 60),
            "lat": 1.2,
            "lon": 104.2,
            "sog": 0.1,  # < 0.5 -> anchored by sog
            "nav_status": 0,
            "draught": 12.0,
            "region": "singapore_malacca",
            "kind": "bulk",
            "segment": "Capesize",
            "destination": "SGSIN",
        }
        for i in range(4)
    ]
    result = anchored_episodes(_df(rows))
    assert len(result) == 1
    assert result[0]["zone"] == "singapore_east"
