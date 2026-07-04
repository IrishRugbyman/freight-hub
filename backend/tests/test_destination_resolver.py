"""AIS destination free-text -> UN/LOCODE seaport resolver.

Covers the resolution cascade (LOCODE code, exact name, fuzzy name), junk
rejection, terminal-suffix handling, same-name proximity disambiguation, and the
headline case that motivated it: "MACAS" is the UN/LOCODE for Casablanca, not junk.
"""

from __future__ import annotations

import pytest

from analytics.destination_resolver import resolve, resolve_origin


def test_locode_exact():
    r = resolve("NLRTM")
    assert r is not None and r.locode == "NLRTM" and r.method == "locode"
    assert "Rotterdam" in r.name


def test_locode_with_internal_space():
    # Crews often type "BE ANR" / "NL RTM".
    assert resolve("BE ANR").locode == "BEANR"
    assert resolve("NL RTM").locode == "NLRTM"


def test_macas_is_casablanca_not_junk():
    # The case that started it: MA + CAS = Casablanca, Morocco.
    r = resolve("MACAS")
    assert r is not None and r.locode == "MACAS"
    assert "Casablanca" in r.name
    assert 33.0 < r.lat < 34.0 and -8.0 < r.lon < -7.0


def test_exact_name_prefers_primary_over_terminal():
    # "BUSAN" -> Busan (KRPUS), not "Busan New Port".
    r = resolve("BUSAN")
    assert r is not None and r.name == "Busan" and r.method == "name-exact"


def test_fuzzy_alias_and_terminal_suffix():
    assert resolve("ANTWERP").locode == "BEANR"          # English alias
    assert resolve("ROTTERDAM 3E PETROHA").locode == "NLRTM"  # terminal suffix stripped


def test_junk_is_unresolved():
    for junk in ["FOR ORDERS", "FOR ORDER", "ORDER", "TBN", "", "   ", None]:
        assert resolve(junk) is None


def test_returns_coordinates():
    r = resolve("SINGAPORE")
    assert r is not None and r.lat is not None and r.lon is not None
    assert 1.0 < r.lat < 1.6 and 103.0 < r.lon < 104.5


def test_proximity_disambiguates_same_name():
    # "Rotterdam" exists in NL and (as a small place) in the US. A vessel off NW
    # Europe should resolve to the Dutch port; one off the US east coast should not
    # pick the Dutch one. We assert the European vessel gets the NL port.
    r = resolve("ROTTERDAM", vessel_lat=51.0, vessel_lon=2.0)
    assert r is not None and 51.0 < r.lat < 52.5 and 3.5 < r.lon < 5.0


def test_non_string_input_is_none():
    # AIS destination can arrive as a NaN float / None from the DB - must not crash.
    import math

    assert resolve(math.nan) is None
    assert resolve(123) is None
    assert resolve(None) is None


def test_score_and_method_fields():
    r = resolve("NLRTM")
    assert r.score == 100.0 and r.method == "locode"
    fuzzy = resolve("ANTWERP")
    assert fuzzy.method == "fuzzy" and 86.0 <= fuzzy.score <= 100.0


# ---------------------------------------------------------------------------
# Route-style destinations ("ORIGIN>DESTINATION"): resolve() must resolve the
# DESTINATION leg (a regression - it used to pick up the origin leg's LOCODE
# via _try_locode's first-token heuristic), and resolve_origin() the other.
# ---------------------------------------------------------------------------


def test_route_resolves_destination_leg_not_origin():
    r = resolve("NLRTM>USORF")
    assert r is not None and r.locode == "USORF" and "Norfolk" in r.name


def test_route_resolve_origin_leg():
    r = resolve_origin("NLRTM>USORF")
    assert r is not None and r.locode == "NLRTM" and "Rotterdam" in r.name


def test_route_double_arrow_and_arrow_variants():
    assert resolve("BEANR>>NLRTM").locode == "NLRTM"
    assert resolve_origin("BEANR>>NLRTM").locode == "BEANR"
    assert resolve("BEANR->NLRTM").locode == "NLRTM"


def test_plain_port_has_no_origin():
    assert resolve_origin("ROTTERDAM") is None
    assert resolve_origin("NLRTM") is None
    assert resolve_origin("FOR ORDERS") is None


def test_via_waypoint_destination_leg_has_no_origin():
    assert resolve("ROTTERDAM VIA DOVER").locode == "NLRTM"
    assert resolve_origin("ROTTERDAM VIA DOVER") is None


def test_weak_separator_na_stays_unresolved():
    # "N/A" must not fuzzy-match some unrelated short port name.
    assert resolve("N/A") is None
    assert resolve_origin("N/A") is None


def test_norfolk_locode_has_correct_coordinates():
    # Regression: the gazetteer once carried USORF at Tasmanian coordinates
    # (a coord-fill error), silently misrouting every Norfolk, VA-bound vessel.
    r = resolve("USORF")
    assert r is not None
    assert 35.0 < r.lat < 38.0 and -78.0 < r.lon < -75.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
