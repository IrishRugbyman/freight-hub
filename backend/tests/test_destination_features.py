"""Destination predictor candidate generation (`analytics.destination_features`).

Covers the union logic: geometric ahead-targets (reusing the exact True-ETA
bearing/range gate) plus the resolved AIS-reported destination, deduped against
a geometric candidate that is already the same real place.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest
from analytics import destination_features as feat
from analytics.eta_routing import RouteCache

_TARGETS = pd.DataFrame(
    [
        {
            "target_id": "cp:suez",
            "target_type": "chokepoint",
            "name": "suez",
            "lat": 30.50,
            "lon": 32.34,
        },
        {
            "target_id": "port:rotterdam",
            "target_type": "port",
            "name": "Rotterdam",
            "lat": 51.96,
            "lon": 4.10,
        },
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
        [
            {
                "target_id": "port:rotterdam",
                "target_type": "port",
                "name": "Rotterdam",
                "lat": 51.96,
                "lon": 4.10,
            }
        ]
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


def test_canal_backtrack_penalizes_wrong_side_of_suez():
    # Vessel just north of the Suez gate (Med side); a Persian Gulf candidate
    # (south of the gate) would require backtracking through Suez again.
    assert feat.canal_backtrack(31.0, 32.34, 26.0, 50.0, "suez") == 1


def test_canal_backtrack_allows_same_side_of_suez():
    # A Rotterdam candidate is also north of the gate - no backtrack required.
    assert feat.canal_backtrack(31.0, 32.34, 51.96, 4.10, "suez") == 0


def test_canal_backtrack_penalizes_wrong_side_of_panama():
    # Panama gate lon is -79.75. Vessel just east of it (Atlantic side); a
    # Pacific-side candidate would require backtracking through Panama again.
    assert feat.canal_backtrack(9.12, -70.0, 9.12, -90.0, "panama") == 1


def test_canal_backtrack_neutral_without_recent_transit():
    assert feat.canal_backtrack(31.0, 32.34, 26.0, 50.0, None) == 0


def test_canal_backtrack_neutral_for_non_canal_chokepoint():
    # Hormuz is a strait, not one of the two scoped canals - always neutral.
    assert feat.canal_backtrack(1.2, 103.8, 26.0, 50.0, "hormuz") == 0


def test_bearing_alignment_neutral_without_course():
    assert feat.bearing_alignment(29.0, 32.34, None, 30.50, 32.34) == 0.5


def test_candidate_frame_flags_canal_backtrack_on_geometric_candidate():
    # Vessel just north of the Suez gate (Med side), steaming due south toward
    # a target also south of the gate - which would require transiting Suez
    # again in reverse.
    live = _live_row(lat=31.5, lon=32.34, cog=180.0, heading=180.0)
    targets = pd.DataFrame(
        [
            {
                "target_id": "port:south",
                "target_type": "port",
                "name": "South Port",
                "lat": 26.0,
                "lon": 32.34,
            }
        ]
    )
    cands = feat.candidate_frame(live, targets, recent_canal_transit={7001: "suez"})
    assert not cands.empty
    assert cands["canal_backtrack"].iloc[0] == 1

    # Without a recent transit on record, the same candidate isn't penalized.
    cands_no_transit = feat.candidate_frame(live, targets)
    assert cands_no_transit["canal_backtrack"].iloc[0] == 0


def test_candidate_frame_canal_backtrack_on_resolved_destination_row():
    live = _live_row(lat=31.5, lon=32.34, destination="ROTTERDAM")
    cands = feat.candidate_frame(live, _TARGETS, recent_canal_transit={7001: "suez"})
    dest_row = cands[cands["target_type"] == "destination"].iloc[0]
    # Rotterdam is north of the Suez gate - same side as the vessel, no backtrack.
    assert dest_row["canal_backtrack"] == 0


# ---------------------------------------------------------------------------
# resolve_origin_target_id: cold-start "prev_target" substitute for the
# transition prior, mined from a route-style AIS destination's origin leg.
# ---------------------------------------------------------------------------


def test_resolve_origin_target_id_matches_curated_target():
    # "NLRTM>EGPSD": origin leg NLRTM resolves near the port:rotterdam target.
    assert feat.resolve_origin_target_id("NLRTM>EGPSD", _TARGETS) == "port:rotterdam"


def test_resolve_origin_target_id_no_route_is_none():
    # A plain single-port string has no origin leg to resolve.
    assert feat.resolve_origin_target_id("ROTTERDAM", _TARGETS) is None


def test_resolve_origin_target_id_far_from_any_target_is_none():
    # BEANR (Antwerp) resolves fine but isn't near either curated target.
    assert feat.resolve_origin_target_id("BEANR>EGPSD", _TARGETS) is None


def test_resolve_origin_target_id_empty_inputs():
    assert feat.resolve_origin_target_id(None, _TARGETS) is None
    assert feat.resolve_origin_target_id("NLRTM>EGPSD", pd.DataFrame()) is None


# ---------------------------------------------------------------------------
# laden/ballast passthrough - candidate_frame carries the per-vessel laden
# state (True ETA's own True/False/None encoding) onto every candidate row.
# ---------------------------------------------------------------------------


def test_candidate_frame_carries_laden_state():
    live = _live_row()
    cands = feat.candidate_frame(live, _TARGETS, laden_by_mmsi={7001: True})
    assert (cands["laden"] == True).all()  # noqa: E712


def test_candidate_frame_defaults_laden_to_none_when_unknown():
    live = _live_row()
    cands = feat.candidate_frame(live, _TARGETS)
    assert cands["laden"].isna().all()


# ---------------------------------------------------------------------------
# sog_trail6h passthrough - mirrors the laden passthrough above.
# ---------------------------------------------------------------------------


def test_candidate_frame_carries_trailing_speed():
    live = _live_row()
    cands = feat.candidate_frame(live, _TARGETS, trail_by_mmsi={7001: 8.5})
    assert (cands["sog_trail6h"] == 8.5).all()


def test_candidate_frame_defaults_trailing_speed_to_none_when_unknown():
    live = _live_row()
    cands = feat.candidate_frame(live, _TARGETS)
    assert cands["sog_trail6h"].isna().all()


# ---------------------------------------------------------------------------
# route_dist_nm - sea-route-corrected distance via a shared eta_route_cache.
# ---------------------------------------------------------------------------


def test_candidate_frame_populates_route_dist_nm_when_cache_given(tmp_path):
    conn = duckdb.connect(str(tmp_path / "an.duckdb"))
    cache = RouteCache(conn)
    live = _live_row()  # steaming due north, straight at the Suez gate
    cands = feat.candidate_frame(live, _TARGETS, route_cache=cache)
    suez = cands[cands["target_id"] == "cp:suez"].iloc[0]
    # A sea route can never be shorter than the great-circle distance.
    assert suez["route_dist_nm"] >= suez["gc_dist_nm"]


def test_candidate_frame_defaults_route_dist_nm_to_none_without_cache():
    live = _live_row()
    cands = feat.candidate_frame(live, _TARGETS)
    assert cands["route_dist_nm"].isna().all()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
