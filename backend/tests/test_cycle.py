"""Unit tests for the freight cycle registry, threshold logic and resolution.

No network, no live DB: the shipped registry is validated as-is, and resolution is
exercised against injected resolvers. The threshold and staleness functions are pure
and get the boundary cases explicitly, because "is the thesis broken yet" is the one
thing on the page that must not be subtly wrong.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.cycle import (
    STATE_APPROACHING,
    STATE_BREACHED,
    STATE_HOLDING,
    STATE_UNKNOWN,
    CycleRegistryError,
    Resolved,
    SignalSpec,
    distance_to_threshold,
    evaluate_threshold,
    get_registry,
    is_stale,
    load_registry,
    resolve_signal,
)

# --------------------------------------------------------------- evaluate_threshold


def test_evaluate_threshold_below_direction_breached_under_threshold():
    assert evaluate_threshold(1400, 1500, "below", 15.0) == STATE_BREACHED


def test_evaluate_threshold_below_direction_holding_well_above():
    assert evaluate_threshold(2900, 1500, "below", 15.0) == STATE_HOLDING


def test_evaluate_threshold_below_direction_approaching_inside_warn_band():
    """1500 + 15% = 1725, so 1700 is inside the band but not yet breached."""
    assert evaluate_threshold(1700, 1500, "below", 15.0) == STATE_APPROACHING


def test_evaluate_threshold_exactly_at_threshold_is_not_breached():
    """A threshold is crossed, not touched: equality stays on the un-breached side."""
    assert evaluate_threshold(1500, 1500, "below", 15.0) == STATE_APPROACHING
    assert evaluate_threshold(15.0, 15.0, "above", 15.0) == STATE_APPROACHING


def test_evaluate_threshold_above_direction_breached_over_threshold():
    assert evaluate_threshold(16.0, 15.0, "above", 15.0) == STATE_BREACHED


def test_evaluate_threshold_above_direction_holding_well_under():
    assert evaluate_threshold(7.0, 15.0, "above", 15.0) == STATE_HOLDING


def test_evaluate_threshold_above_direction_approaching_inside_warn_band():
    """15 - 15% = 12.75, so 13.0 is approaching from below."""
    assert evaluate_threshold(13.0, 15.0, "above", 15.0) == STATE_APPROACHING


def test_evaluate_threshold_missing_value_is_unknown():
    assert evaluate_threshold(None, 1500, "below", 15.0) == STATE_UNKNOWN


def test_evaluate_threshold_missing_threshold_is_unknown():
    assert evaluate_threshold(2900, None, "below", 15.0) == STATE_UNKNOWN


def test_evaluate_threshold_direction_none_is_always_unknown():
    assert evaluate_threshold(2900, 1500, "none", 15.0) == STATE_UNKNOWN


def test_evaluate_threshold_zero_warn_band_removes_the_approaching_state():
    assert evaluate_threshold(1500.01, 1500, "below", 0.0) == STATE_HOLDING


def test_evaluate_threshold_rejects_an_unknown_direction():
    with pytest.raises(CycleRegistryError):
        evaluate_threshold(1.0, 1.0, "sideways", 15.0)


# ------------------------------------------------------------ distance_to_threshold


def test_distance_to_threshold_is_signed_percentage_of_threshold():
    assert distance_to_threshold(2900, 1500) == pytest.approx(93.33, abs=0.01)
    assert distance_to_threshold(7.0, 15.0) == pytest.approx(-53.33, abs=0.01)


def test_distance_to_threshold_none_when_either_side_missing_or_zero():
    assert distance_to_threshold(None, 1500) is None
    assert distance_to_threshold(2900, None) is None
    assert distance_to_threshold(2900, 0) is None


# ------------------------------------------------------------------------ is_stale


def test_is_stale_false_inside_the_review_interval():
    assert is_stale(date(2026, 7, 20), 14, date(2026, 7, 26)) is False


def test_is_stale_boundary_day_is_not_yet_stale():
    assert is_stale(date(2026, 7, 12), 14, date(2026, 7, 26)) is False


def test_is_stale_true_one_day_past_the_interval():
    assert is_stale(date(2026, 7, 11), 14, date(2026, 7, 26)) is True


def test_is_stale_true_when_no_as_of_recorded():
    """An observation with no date cannot be claimed to be current."""
    assert is_stale(None, 14, date(2026, 7, 26)) is True


def test_is_stale_true_when_no_review_interval_set():
    assert is_stale(date(2026, 7, 25), None, date(2026, 7, 26)) is True


# ------------------------------------------------------------ registry validation


def _entry(**overrides) -> dict:
    base = {
        "id": "test_signal",
        "subsector": "drybulk",
        "category": "price",
        "label": "Test",
        "tier": "live",
        "unit": "index",
        "resolver": "baltic:BDI",
        "threshold_value": 1500.0,
        "threshold_label": "under 1500",
        "direction": "below",
        "expected_lag": "1-2 quarters",
        "falsifier": "volumes disagree",
        "source_label": "test",
    }
    base.update(overrides)
    return base


def _write(tmp_path, signals: list[dict], subsectors: list[dict] | None = None):
    import yaml

    doc = {
        "meta": {"updated": date(2026, 7, 26), "warn_band_pct": 15.0},
        "subsectors": subsectors if subsectors is not None else [{"id": "drybulk", "name": "Dry"}],
        "signals": signals,
    }
    p = tmp_path / "registry.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def test_load_registry_accepts_a_minimal_valid_entry(tmp_path):
    reg = load_registry(_write(tmp_path, [_entry()]))

    assert [s.id for s in reg.signals] == ["test_signal"]
    assert reg.warn_band_pct == 15.0


def test_load_registry_rejects_a_signal_without_a_falsifier(tmp_path):
    entry = _entry()
    del entry["falsifier"]

    with pytest.raises(CycleRegistryError, match="falsifier"):
        load_registry(_write(tmp_path, [entry]))


def test_load_registry_rejects_a_live_signal_without_a_resolver(tmp_path):
    entry = _entry()
    del entry["resolver"]

    with pytest.raises(CycleRegistryError, match="resolver"):
        load_registry(_write(tmp_path, [entry]))


def test_load_registry_rejects_a_registered_signal_without_an_as_of_date(tmp_path):
    entry = _entry(tier="registered", value=7.0, review_interval_days=90)
    entry.pop("resolver")

    with pytest.raises(CycleRegistryError, match="as_of"):
        load_registry(_write(tmp_path, [entry]))


def test_load_registry_rejects_a_missing_signal_without_a_gap_reason(tmp_path):
    entry = _entry(tier="missing", direction="none", threshold_value=None)
    entry.pop("resolver")

    with pytest.raises(CycleRegistryError, match="gap_reason"):
        load_registry(_write(tmp_path, [entry]))


def test_load_registry_rejects_a_numeric_direction_without_a_threshold(tmp_path):
    entry = _entry(threshold_value=None)

    with pytest.raises(CycleRegistryError, match="threshold_value"):
        load_registry(_write(tmp_path, [entry]))


def test_load_registry_rejects_a_zero_threshold(tmp_path):
    """A zero threshold has no meaningful warn band and no meaningful distance."""
    with pytest.raises(CycleRegistryError, match="warn band"):
        load_registry(_write(tmp_path, [_entry(threshold_value=0)]))


def test_load_registry_rejects_duplicate_ids(tmp_path):
    with pytest.raises(CycleRegistryError, match="duplicate"):
        load_registry(_write(tmp_path, [_entry(), _entry()]))


def test_load_registry_rejects_an_unknown_field(tmp_path):
    """A typo in a key must fail loudly rather than be silently dropped."""
    with pytest.raises(CycleRegistryError, match="unknown field"):
        load_registry(_write(tmp_path, [_entry(falsifer="typo")]))


def test_load_registry_rejects_a_signal_in_an_unknown_subsector(tmp_path):
    with pytest.raises(CycleRegistryError, match="unknown subsector"):
        load_registry(_write(tmp_path, [_entry(subsector="ropax")]))


def test_load_registry_rejects_a_subsector_pointing_at_a_missing_signal(tmp_path):
    subs = [{"id": "drybulk", "name": "Dry", "headline_signal": "nope"}]

    with pytest.raises(CycleRegistryError, match="headline_signal"):
        load_registry(_write(tmp_path, [_entry()], subsectors=subs))


# ------------------------------------------------------------------- the real file


def test_shipped_registry_loads_and_every_signal_states_a_falsifier():
    reg = get_registry()

    assert reg.signals, "the shipped registry is empty"
    for s in reg.signals:
        assert s.falsifier.strip(), f"{s.id} has no falsifier"
        assert s.expected_lag.strip(), f"{s.id} has no expected_lag"
        assert s.threshold_label.strip(), f"{s.id} has no threshold_label"


def test_shipped_registry_marks_unverified_secondary_observations():
    """Registered numbers taken from a secondary summary must say so."""
    for s in get_registry().signals:
        if s.tier == "registered" and not s.verified:
            assert s.provenance.strip(), f"{s.id} is unverified but explains nothing"


def test_shipped_registry_records_how_each_verified_number_was_checked():
    """`verified: true` is a claim someone read the primary; it must say what and when."""
    for s in get_registry().signals:
        if s.verified:
            assert s.verified_note.strip(), f"{s.id} claims verified with no note"
            assert s.source_url, f"{s.id} claims verified with no source URL"


def test_shipped_registry_covers_all_three_subsectors():
    assert {s["id"] for s in get_registry().subsectors} == {"container", "drybulk", "tanker"}


# ---------------------------------------------------------------------- resolution


def _spec(**overrides) -> SignalSpec:
    return SignalSpec(**_entry(**overrides))


def test_resolve_signal_live_uses_its_resolver():
    resolved = resolve_signal(
        _spec(),
        resolvers={"baltic:BDI": lambda: Resolved(value=2743.0, as_of=date(2026, 7, 24))},
        today=date(2026, 7, 26),
    )

    assert resolved.value == 2743.0
    assert resolved.state == STATE_HOLDING
    assert resolved.as_of == date(2026, 7, 24)


def test_resolve_signal_live_with_a_failed_resolver_still_renders():
    """A dead upstream must produce an unknown tile, never an exception."""
    resolved = resolve_signal(
        _spec(),
        resolvers={"baltic:BDI": lambda: Resolved(value=None)},
        today=date(2026, 7, 26),
    )

    assert resolved.value is None
    assert resolved.state == STATE_UNKNOWN
    assert resolved.stale is True


def test_resolve_signal_live_with_an_unregistered_resolver_key_is_unknown():
    resolved = resolve_signal(_spec(resolver="baltic:NOPE"), resolvers={}, today=date(2026, 7, 26))

    assert resolved.value is None
    assert resolved.state == STATE_UNKNOWN
    assert "no resolver" in resolved.note


def test_resolve_signal_registered_uses_the_recorded_value_and_goes_stale():
    spec = _spec(
        tier="registered",
        resolver=None,
        value=7.0,
        direction="above",
        threshold_value=15.0,
        as_of=date(2026, 2, 16),
        review_interval_days=90,
    )

    resolved = resolve_signal(spec, resolvers={}, today=date(2026, 7, 26))

    assert resolved.value == 7.0
    assert resolved.state == STATE_HOLDING
    assert resolved.stale is True


def test_resolve_signal_missing_tier_has_no_value_and_never_reports_stale():
    """A published gap is already the worst case; calling it stale adds nothing."""
    spec = _spec(
        tier="missing",
        resolver=None,
        direction="none",
        threshold_value=None,
        gap_reason="no receiver coverage",
    )

    resolved = resolve_signal(spec, resolvers={}, today=date(2026, 7, 26))

    assert resolved.value is None
    assert resolved.stale is False
    assert resolved.state == STATE_UNKNOWN


def test_resolve_signal_qualitative_signal_uses_its_manual_state():
    spec = _spec(
        direction="none",
        threshold_value=None,
        manual_state=STATE_HOLDING,
        resolver="transits:suez",
    )

    resolved = resolve_signal(
        spec,
        resolvers={"transits:suez": lambda: Resolved(value=109.4, as_of=date(2026, 7, 23))},
        today=date(2026, 7, 26),
    )

    assert resolved.state == STATE_HOLDING
    assert resolved.distance_pct is None


def test_resolve_signal_qualitative_signal_without_a_value_stays_unknown():
    """manual_state must not assert a read when there is no observation behind it."""
    spec = _spec(
        direction="none",
        threshold_value=None,
        manual_state=STATE_HOLDING,
        resolver="transits:suez",
    )

    resolved = resolve_signal(
        spec, resolvers={"transits:suez": lambda: Resolved(value=None)}, today=date(2026, 7, 26)
    )

    assert resolved.state == STATE_UNKNOWN
