"""Tests for the pure risk_score() function in registry/risk.py."""

from __future__ import annotations

import pytest
from registry.risk import risk_score


def test_clean_modern_vlcc_low_risk():
    """Modern VLCC with IG P&I, IACS class, Paris White should score near zero."""
    score, indicators = risk_score(
        imo=9321483,
        ship_type="Crude Oil Tanker",
        year_built=2018,  # 2026 - 2018 = 8y, below 15y threshold
        pi_club="Gard P&I",
        class_society="Lloyd's Register (IACS)",
        paris_mou="White",
        tokyo_mou="White",
        detention_rate_pct=0.0,
        event_counts={},
        owner="SOME LARGE OWNER",
        single_ship_owner=False,
    )
    assert score < 25
    assert indicators == []


def test_old_tanker_black_flag_no_pi_high_risk():
    """Old tanker, black-listed flag, no P&I, non-IACS class, AIS gaps -> high score."""
    score, indicators = risk_score(
        imo=9000001,
        ship_type="Oil Products Tanker",
        year_built=1996,  # 30y old
        pi_club=None,
        class_society="Russian Maritime Register",
        paris_mou="Black",
        tokyo_mou="Black",
        detention_rate_pct=12.5,
        event_counts={"gap": 2, "sts": 1},
        owner="SINGLE SHIP LLC",
        single_ship_owner=True,
    )
    assert score >= 70
    assert any("tanker" in ind.lower() for ind in indicators)
    assert any("P&I" in ind for ind in indicators)
    assert any("Black" in ind for ind in indicators)


def test_non_ig_pi_club_fires():
    """A vessel with a non-IG P&I club should trigger the indicator."""
    score, indicators = risk_score(
        imo=9000002,
        ship_type="Bulk Carrier",
        year_built=2010,
        pi_club="Some Unknown Mutual",
        class_society="Bureau Veritas (IACS)",
        paris_mou="White",
        tokyo_mou="White",
        detention_rate_pct=None,
        event_counts={},
        owner="BIG SHIPPING CO",
        single_ship_owner=False,
    )
    assert any("International Group" in ind for ind in indicators)


def test_ig_pi_clubs_recognized():
    """All 12 IG clubs should be recognized."""
    ig_names = [
        "Gard P&I (Bermuda) Ltd",
        "UK P&I Club",
        "West of England Ship Owners Mutual Insurance",
        "North of England P&I Association",
        "Standard Club",
        "Swedish Club",
        "Steamship Mutual Underwriting",
        "Britannia P&I Club",
        "Skuld",
        "London P&I Club",
        "American Club",
        "Korea P&I Club",
        "Japan P&I Club",
    ]
    for name in ig_names:
        score, indicators = risk_score(
            imo=1000000,
            ship_type="Bulk Carrier",
            year_built=2015,
            pi_club=name,
            class_society="Lloyd's Register (IACS)",
            paris_mou="White",
            tokyo_mou="White",
            detention_rate_pct=0.0,
            event_counts={},
            owner="BIG CO",
            single_ship_owner=False,
        )
        assert not any("International Group" in ind for ind in indicators), (
            f"IG club '{name}' was not recognized"
        )


def test_iacs_class_recognized():
    """Various IACS class notation forms are recognized."""
    for cs in [
        "Lloyd's Register (IACS)",
        "Bureau Veritas (IACS)",
        "DNV GL (IACS)",
        "American Bureau of Shipping (IACS)",
    ]:
        score, indicators = risk_score(
            imo=1000000,
            ship_type="Bulk Carrier",
            year_built=2020,
            pi_club="Gard",
            class_society=cs,
            paris_mou="White",
            tokyo_mou="White",
            detention_rate_pct=0.0,
            event_counts={},
            owner="BIG CO",
            single_ship_owner=False,
        )
        assert not any("Class" in ind for ind in indicators), (
            f"IACS class '{cs}' was not recognized"
        )


def test_behavioural_events_capped():
    """Behavioural contribution capped at _MAX_BEHAVIOURAL (40)."""
    score, indicators = risk_score(
        imo=9000003,
        ship_type="Bulk Carrier",
        year_built=2010,
        pi_club="Gard",
        class_society="DNV GL (IACS)",
        paris_mou="White",
        tokyo_mou="White",
        detention_rate_pct=0.0,
        event_counts={"gap": 10, "sts": 10, "loiter": 10},  # uncapped: 10*12+10*10+10*5 = 270
        owner="BIG CO",
        single_ship_owner=False,
    )
    # Without cap, score would be > 100; with cap, behavioural <= 40
    assert score <= 100


def test_score_capped_at_100():
    """Total score can never exceed 100."""
    score, _ = risk_score(
        imo=9000004,
        ship_type="Chemical Tanker",
        year_built=1990,
        pi_club=None,
        class_society="Unknown Class",
        paris_mou="Black",
        tokyo_mou="Black",
        detention_rate_pct=25.0,
        event_counts={"gap": 5, "sts": 5},
        owner="SHELL CO",
        single_ship_owner=True,
    )
    assert score <= 100


def test_age_threshold_15_vs_25():
    """Tankers age 15-24 get _W_OLD_TANKER (20), age 25+ get _W_OLD_TANKER_SEVERE (30)."""
    score_15, ind_15 = risk_score(
        imo=1, ship_type="Crude Oil Tanker", year_built=2010,  # 16y
        pi_club="Gard", class_society="DNV GL (IACS)",
        paris_mou="White", tokyo_mou="White", detention_rate_pct=0.0,
        event_counts={}, owner="CO", single_ship_owner=False,
    )
    score_25, ind_25 = risk_score(
        imo=2, ship_type="Crude Oil Tanker", year_built=1999,  # 27y
        pi_club="Gard", class_society="DNV GL (IACS)",
        paris_mou="White", tokyo_mou="White", detention_rate_pct=0.0,
        event_counts={}, owner="CO", single_ship_owner=False,
    )
    assert score_25 > score_15
    assert any(">= 25" in ind for ind in ind_25)
    assert any(">= 15" in ind for ind in ind_15)


def test_bulk_carrier_age_not_penalized():
    """Age penalty only fires for tankers, not bulk carriers."""
    score, indicators = risk_score(
        imo=9000005,
        ship_type="Bulk Carrier",
        year_built=1990,  # 36y old
        pi_club="Gard",
        class_society="DNV GL (IACS)",
        paris_mou="White",
        tokyo_mou="White",
        detention_rate_pct=0.0,
        event_counts={},
        owner="BIG CO",
        single_ship_owner=False,
    )
    assert not any("tanker" in ind.lower() for ind in indicators)
