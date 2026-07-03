"""Destination predictor candidate generation (`analytics.destination_features`).

Covers the union logic: geometric ahead-targets (reusing the exact True-ETA
bearing/range gate) plus the resolved AIS-reported destination, deduped against
a geometric candidate that is already the same real place.
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics import destination_features as feat

_TARGETS = pd.DataFrame(
    [
        {"target_id": "cp:suez", "target_type": "chokepoint", "name": "suez", "lat": 30.50, "lon": 32.34},
        {"target_id": "port:rotterdam", "target_type": "port", "name": "Rotterdam", "lat": 51.96, "lon": 4.10},
    ]
)


def _live_row(**overrides) -> pd.DataFrame:
    base = {
        "mmsi": 7001,
        "name": "TESTSHIP",
        "lat": 29.0,
        "lon": 32.34,
        "sog": 12.0,
        "cog": 0.0,
        "heading": 1.0,
        "kind": "tanker",
        "segment": "VLCC",
        "region": "suez",
        "imo": 9000001,
        "draught": 20.0,
        "destination": None,
    }
    base.update(overrides)
    return pd.DataFrame([base])


def test_candidate_frame_includes_geometric_ahead_target():
    live = _live_row()  # steaming due north, straight at the Suez gate
    cands = feat.candidate_frame(live, _TARGETS)
    assert not cands.empty
    suez = cands[cands["target_id"] == "cp:suez"]
    assert not suez.empty
    assert suez["bearing_align"].iloc[0] > 0.9  # nearly dead-on
    assert not bool(suez["reported_match"].iloc[0])


def test_candidate_frame_excludes_target_behind_the_vessel():
    # Steering due south: Suez (to the north) is behind it -> bearing-gated out.
    live = _live_row(cog=180.0, heading=180.0)
    cands = feat.candidate_frame(live, _TARGETS)
    assert "cp:suez" not in set(cands["target_id"])


def test_resolved_destination_added_as_its_own_candidate():
    # "ROTTERDAM" resolves but Rotterdam is nowhere near this vessel's course/range,
    # so it must appear as its own destination-typed candidate, not merged in.
    live = _live_row(destination="ROTTERDAM")
    cands = feat.candidate_frame(live, _TARGETS)
    dest_rows = cands[cands["target_type"] == "destination"]
    assert len(dest_rows) == 1
    row = dest_rows.iloc[0]
    assert row["target_id"] == "dest:NLRTM"
    assert bool(row["reported_match"]) is True
    assert row["resolver_score"] == 100.0


def test_resolved_destination_matching_geometric_candidate_flags_not_duplicates():
    # Vessel south of Rotterdam, steaming north straight at it (a geometric
    # candidate); reporting "ROTTERDAM" resolves to the same real place, so it
    # must flag that existing row rather than add a duplicate.
    live = _live_row(lat=50.0, lon=4.10, destination="ROTTERDAM")
    targets = pd.DataFrame(
        [{"target_id": "port:rotterdam", "target_type": "port", "name": "Rotterdam", "lat": 51.96, "lon": 4.10}]
    )
    cands = feat.candidate_frame(live, targets)
    # Only one row (the geometric one), flagged as matching the reported dest.
    assert len(cands) == 1
    assert cands.iloc[0]["target_id"] == "port:rotterdam"
    assert bool(cands.iloc[0]["reported_match"]) is True


def test_junk_destination_yields_no_extra_candidate():
    live = _live_row(destination="FOR ORDERS")
    cands = feat.candidate_frame(live, _TARGETS)
    assert "destination" not in set(cands["target_type"])


def test_empty_inputs_return_empty_frame():
    assert feat.candidate_frame(pd.DataFrame(), _TARGETS).empty
    assert feat.candidate_frame(_live_row(), pd.DataFrame()).empty


def test_bearing_alignment_neutral_without_course():
    assert feat.bearing_alignment(29.0, 32.34, None, 30.50, 32.34) == 0.5


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
