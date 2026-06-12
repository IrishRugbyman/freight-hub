"""Unit tests for analytics/detect.py pure detection functions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import json

from analytics.detect import (
    ais_gap_events,
    anchored_episodes,
    laden_status,
    loitering_events,
    sts_candidates,
    transit_episodes,
)

_NOW = datetime(2026, 6, 10, 12, 0, 0)


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


# ---------------------------------------------------------------------------
# Phase 3: ais_gap_events
# ---------------------------------------------------------------------------

_GAP_MAX_TS = _NOW  # treat now as max_ts for gap tests


def _gap_df(mmsi: int, fix_times: list, lat: float = 25.2, lon: float = 56.5,
            region: str = "hormuz", sog: float = 9.0) -> pd.DataFrame:
    rows = [
        {
            "mmsi": mmsi, "snapshot_ts": t, "lat": lat, "lon": lon,
            "sog": sog, "nav_status": 0, "draught": 12.0,
            "kind": "tanker", "segment": "Aframax", "region": region,
            "destination": None,
        }
        for t in fix_times
    ]
    df = pd.DataFrame(rows)
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])
    return df


def test_gap_detected():
    """Vessel with 8 fixes, last fix 10h ago, deep inside region: should fire."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(8, 56, 6))]
    df = _gap_df(9001, fix_times)
    max_ts = pd.Timestamp(_NOW)
    result = ais_gap_events(df, max_ts)
    assert len(result) == 1
    assert result[0]["type"] == "gap"
    assert result[0]["mmsi"] == 9001
    details = json.loads(result[0]["details"])
    assert details["silence_hours"] >= 8


def test_gap_not_fired_vessel_recently_active():
    """Vessel with fix within 6h of max_ts: no gap."""
    fix_times = [_NOW - timedelta(hours=h) for h in [10, 8, 6, 4, 2, 1]]
    df = _gap_df(9002, fix_times)
    max_ts = pd.Timestamp(_NOW)
    result = ais_gap_events(df, max_ts)
    assert len(result) == 0


def test_gap_not_fired_too_few_fixes():
    """Vessel with only 4 fixes (below _GAP_MIN_FIXES=6) before going silent."""
    fix_times = [_NOW - timedelta(hours=h) for h in [40, 35, 30, 25]]
    df = _gap_df(9003, fix_times)
    max_ts = pd.Timestamp(_NOW)
    result = ais_gap_events(df, max_ts)
    assert len(result) == 0


def test_gap_not_fired_low_sog():
    """Vessel that was anchored (sog=0.5) when it went silent: no gap."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(8, 56, 6))]
    df = _gap_df(9004, fix_times, sog=0.5)
    max_ts = pd.Timestamp(_NOW)
    result = ais_gap_events(df, max_ts)
    assert len(result) == 0


def test_gap_not_fired_at_region_edge():
    """Vessel last seen within 0.4 deg of hormuz bbox edge: coverage exit, not a gap."""
    # hormuz bbox: lat [24.5,27.5], lon [54,58.5]
    # Place vessel near lat edge (lat=24.7 = 24.5 + 0.2, inside 0.4 margin)
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(8, 56, 6))]
    df = _gap_df(9005, fix_times, lat=24.7, lon=56.0, region="hormuz")
    max_ts = pd.Timestamp(_NOW)
    result = ais_gap_events(df, max_ts)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Phase 3: loitering_events
# ---------------------------------------------------------------------------

def _loiter_df(mmsi: int, fix_times: list, lat: float = 25.2, lon: float = 56.5,
               region: str = "hormuz", sog: float = 0.3) -> pd.DataFrame:
    rows = [
        {
            "mmsi": mmsi, "snapshot_ts": t, "lat": lat, "lon": lon,
            "sog": sog, "nav_status": 0, "draught": 12.0,
            "kind": "tanker", "segment": "Aframax", "region": region,
            "destination": None,
        }
        for t in fix_times
    ]
    df = pd.DataFrame(rows)
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])
    return df


def test_loiter_detected():
    """14h of slow movement, deep inside hormuz, outside anchorages."""
    # hormuz interior: lat [24.5+0.2, 27.5-0.2] x lon [54+0.2, 58.5-0.2]
    # Use lat=26, lon=56 (well inside)
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 14))]
    df = _loiter_df(9010, fix_times, lat=26.0, lon=56.0)
    result = loitering_events(df)
    assert len(result) >= 1
    assert result[0]["type"] == "loiter"
    details = json.loads(result[0]["details"])
    assert details["duration_hours"] >= 12


def test_loiter_not_fired_short_episode():
    """Episode only 6h: below the 12h minimum."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 6))]
    df = _loiter_df(9011, fix_times, lat=26.0, lon=56.0)
    result = loitering_events(df)
    assert len(result) == 0


def test_loiter_not_fired_in_anchorage():
    """Slow vessel inside fujairah anchorage: should NOT loiter-detect."""
    # fujairah: lat [24.9, 25.4], lon [56.3, 56.85]
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 14))]
    df = _loiter_df(9012, fix_times, lat=25.1, lon=56.5, region="hormuz")
    result = loitering_events(df)
    assert len(result) == 0


def test_loiter_not_fired_fast_vessel():
    """Mean SOG >= 1 kn: not a loiter."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 14))]
    df = _loiter_df(9013, fix_times, lat=26.0, lon=56.0, sog=2.5)
    result = loitering_events(df)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Phase 3: sts_candidates
# ---------------------------------------------------------------------------

def _sts_df(pairs: list[dict]) -> pd.DataFrame:
    """pairs: list of dicts with keys mmsi, snapshot_ts, lat, lon, kind, segment."""
    rows = []
    for p in pairs:
        rows.append({
            "mmsi": p["mmsi"], "snapshot_ts": p["snapshot_ts"],
            "lat": p.get("lat", 26.0), "lon": p.get("lon", 56.0),
            "sog": p.get("sog", 0.1), "nav_status": 1, "draught": 12.0,
            "kind": p.get("kind", "tanker"), "segment": p.get("segment", "Aframax"),
            "region": p.get("region", "hormuz"), "destination": None,
        })
    df = pd.DataFrame(rows)
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"])
    return df


def test_sts_detected():
    """Two tankers within 50m for 3h, outside anchorages."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 3))]
    rows = []
    for t in fix_times:
        rows.append({"mmsi": 9020, "snapshot_ts": t, "lat": 26.0, "lon": 56.0})
        # ~44m offset at lat=26
        rows.append({"mmsi": 9021, "snapshot_ts": t, "lat": 26.0004, "lon": 56.0})
    df = _sts_df(rows)
    result = sts_candidates(df)
    assert len(result) >= 1
    assert result[0]["type"] == "sts"
    assert {result[0]["mmsi"], result[0]["mmsi2"]} == {9020, 9021}


def test_sts_not_fired_far_apart():
    """Two tankers 2km apart: below 500m threshold."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 3))]
    rows = []
    for t in fix_times:
        rows.append({"mmsi": 9022, "snapshot_ts": t, "lat": 26.0, "lon": 56.0})
        rows.append({"mmsi": 9023, "snapshot_ts": t, "lat": 26.02, "lon": 56.0})  # ~2.2km
    df = _sts_df(rows)
    result = sts_candidates(df)
    assert len(result) == 0


def test_sts_not_fired_short_duration():
    """Two tankers within 50m but only for 1h (below 2h minimum)."""
    fix_times = [_NOW - timedelta(hours=h) for h in reversed(range(0, 1))]
    rows = []
    for t in fix_times:
        rows.append({"mmsi": 9024, "snapshot_ts": t, "lat": 26.0, "lon": 56.0})
        rows.append({"mmsi": 9025, "snapshot_ts": t, "lat": 26.0004, "lon": 56.0})
    df = _sts_df(rows)
    result = sts_candidates(df)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# dark_voyage_events
# ---------------------------------------------------------------------------

from analytics.detect import dark_voyage_events


def _dark_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["start_ts"] = pd.to_datetime(df["start_ts"])
    df["end_ts"] = pd.to_datetime(df["end_ts"])
    for col in ("mmsi2", "lat", "lon", "region", "kind", "segment", "details"):
        if col not in df.columns:
            df[col] = None
    return df


def test_dark_voyage_fires_gap_sts_gap():
    """Gap -> STS -> trailing gap within 72h should fire a dark_voyage event."""
    rows = [
        {"event_id": "gap0001", "type": "gap",     "mmsi": 1111,
         "start_ts": _NOW - timedelta(hours=50), "end_ts": _NOW - timedelta(hours=40),
         "lat": 26.0, "lon": 56.0},
        {"event_id": "sts0001", "type": "sts",     "mmsi": 1111,
         "start_ts": _NOW - timedelta(hours=38), "end_ts": _NOW - timedelta(hours=30),
         "lat": 26.0, "lon": 56.0},
        {"event_id": "gap0002", "type": "gap",     "mmsi": 1111,
         "start_ts": _NOW - timedelta(hours=28), "end_ts": _NOW - timedelta(hours=20),
         "lat": 26.0, "lon": 56.0},
    ]
    result = dark_voyage_events(_dark_df(rows))
    assert len(result) == 1
    d = result[0]
    assert d["type"] == "dark_voyage"
    assert d["mmsi"] == 1111
    import json
    det = json.loads(d["details"])
    assert det["sts_count"] == 1


def test_dark_voyage_fires_gap_loiter_gap():
    """Gap -> loiter -> trailing gap should also fire."""
    rows = [
        {"event_id": "gap0003", "type": "gap",    "mmsi": 2222,
         "start_ts": _NOW - timedelta(hours=60), "end_ts": _NOW - timedelta(hours=50)},
        {"event_id": "loi0001", "type": "loiter", "mmsi": 2222,
         "start_ts": _NOW - timedelta(hours=48), "end_ts": _NOW - timedelta(hours=36)},
        {"event_id": "gap0004", "type": "gap",    "mmsi": 2222,
         "start_ts": _NOW - timedelta(hours=30), "end_ts": _NOW - timedelta(hours=20)},
    ]
    result = dark_voyage_events(_dark_df(rows))
    assert len(result) == 1
    assert result[0]["type"] == "dark_voyage"


def test_dark_voyage_no_trailing_gap():
    """Gap -> STS with no trailing gap should NOT fire."""
    rows = [
        {"event_id": "gap0005", "type": "gap", "mmsi": 3333,
         "start_ts": _NOW - timedelta(hours=40), "end_ts": _NOW - timedelta(hours=30)},
        {"event_id": "sts0002", "type": "sts", "mmsi": 3333,
         "start_ts": _NOW - timedelta(hours=28), "end_ts": _NOW - timedelta(hours=20)},
    ]
    result = dark_voyage_events(_dark_df(rows))
    assert len(result) == 0


def test_dark_voyage_covert_too_late():
    """STS starting > 24h after gap start should not trigger (too large a gap)."""
    rows = [
        {"event_id": "gap0006", "type": "gap", "mmsi": 4444,
         "start_ts": _NOW - timedelta(hours=60), "end_ts": _NOW - timedelta(hours=50)},
        {"event_id": "sts0003", "type": "sts", "mmsi": 4444,
         "start_ts": _NOW - timedelta(hours=30), "end_ts": _NOW - timedelta(hours=20)},  # 30h after gap start
        {"event_id": "gap0007", "type": "gap", "mmsi": 4444,
         "start_ts": _NOW - timedelta(hours=10), "end_ts": _NOW - timedelta(hours=5)},
    ]
    result = dark_voyage_events(_dark_df(rows))
    assert len(result) == 0


def test_dark_voyage_empty_input():
    result = dark_voyage_events(pd.DataFrame())
    assert result == []


def test_dark_voyage_no_duplicates():
    """Same vessel with multiple qualifying windows should deduplicate by gap start."""
    rows = [
        {"event_id": "gap0008", "type": "gap",    "mmsi": 5555,
         "start_ts": _NOW - timedelta(hours=50), "end_ts": _NOW - timedelta(hours=40)},
        {"event_id": "sts0004", "type": "sts",    "mmsi": 5555,
         "start_ts": _NOW - timedelta(hours=38), "end_ts": _NOW - timedelta(hours=30)},
        {"event_id": "gap0009", "type": "gap",    "mmsi": 5555,
         "start_ts": _NOW - timedelta(hours=28), "end_ts": _NOW - timedelta(hours=20)},
        {"event_id": "sts0005", "type": "sts",    "mmsi": 5555,
         "start_ts": _NOW - timedelta(hours=18), "end_ts": _NOW - timedelta(hours=10)},
    ]
    result = dark_voyage_events(_dark_df(rows))
    event_ids = [r["event_id"] for r in result]
    assert len(event_ids) == len(set(event_ids))
