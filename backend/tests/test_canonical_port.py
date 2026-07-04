"""Unit tests for `_canonical_port`: folding raw AIS destination spellings.

The live destination-distribution lists (`/api/analytics/ports`,
`/api/analytics/destination-flows`) showed one physical port as many rows
(NLRTM / ROTTERDAM / NL RTM / ROTTERDAM 3E PETROHA ...). The canonicaliser folds
those onto one city without merging genuinely different ports.
"""

from __future__ import annotations

import pytest
from app.main import _canonical_port


@pytest.mark.parametrize(
    "raw",
    ["NLRTM", "NL RTM", "nlrtm", "Rotterdam", "ROTTERDAM 3E PETROHA",
     "ROTTERDAM BOTLEK  BO", "EUROPOORT", "  ROTTERDAM  "],
)
def test_rotterdam_variants_fold_to_one(raw):
    assert _canonical_port(raw) == "Rotterdam"


@pytest.mark.parametrize(
    "raw",
    ["BEANR", "BE ANR", "ANTWERPEN", "ANTWERP", "ANTWERPEN KANAALDOK",
     "ANTWERPEN 6E HAVENDO", "ANTWERPEN BEVRIJDING"],
)
def test_antwerp_variants_fold_to_one(raw):
    assert _canonical_port(raw) == "Antwerp"


def test_locode_space_collapse():
    # "XX YYY" spaced LOCODE must equal the unspaced form.
    assert _canonical_port("SG SIN") == _canonical_port("SGSIN") == "Singapore"
    assert _canonical_port("KR PUS") == _canonical_port("KRPUS") == "Busan"
    assert _canonical_port("EG PSD") == _canonical_port("EGPSD") == "Port Said"
    assert _canonical_port("NL AMS") == _canonical_port("NLAMS") == "Amsterdam"


def test_distinct_cities_not_merged():
    # Amsterdam and Ghent must stay separate from Rotterdam/Antwerp (the coarse
    # _EUR_TERMINALS map lumps them; _canonical_port must not).
    assert _canonical_port("NLAMS") == "Amsterdam"
    assert _canonical_port("AMSTERDAM JAN VAN RI") == "Amsterdam"
    assert _canonical_port("GENT") == "Ghent"
    assert _canonical_port("BEGNE") == "Ghent"
    assert _canonical_port("Rotterdam") != _canonical_port("Amsterdam")


def test_istanbul_vs_trieste_locode_disambiguation():
    # "TRIST" is the spaceless TR-IST (Istanbul), NOT Italian Trieste (ITTRS).
    assert _canonical_port("TRIST") == "Istanbul"
    assert _canonical_port("ISTANBUL") == "Istanbul"
    assert _canonical_port("ITTRS") == "Trieste"
    assert _canonical_port("TRIESTE") == "Trieste"


@pytest.mark.parametrize("raw", ["FOR ORDERS", "ORDERS", "TBN", "", "  ", "...", None])
def test_junk_destinations_dropped(raw):
    assert _canonical_port(raw) is None


def test_unknown_port_is_cleaned_not_guessed():
    # An unrecognised destination is title-cased, never folded onto a real port.
    assert _canonical_port("PORT KLANG") == "Port Klang"
    assert _canonical_port("SOME RANDOM PLACE") == "Some Random Place"


def test_unknown_locode_does_not_false_match():
    # A LOCODE we do not curate must pass through cleanly, not map to a city.
    # Spaced and unspaced forms of an uncurated LOCODE still merge, but no city
    # name is fabricated (stays the raw LOCODE, not "Shanghai").
    assert _canonical_port("CN SHA") == _canonical_port("CNSHA") == "CNSHA"


# ---------------------------------------------------------------------------
# Route-style destinations ("ORIGIN>DESTINATION") and origin extraction.
# ---------------------------------------------------------------------------

from app.main import _canonical_destination, _canonical_origin, _split_route


@pytest.mark.parametrize(
    "raw,dest",
    [
        ("NLRTM>USORF", "Norfolk"),
        ("NL RTM > US ORF", "Norfolk"),
        ("BEANR>NLRTM", "Rotterdam"),
        ("US TXT>>BE ANR", "Antwerp"),
        ("FR LEH>>NL RTM", "Rotterdam"),
        ("NLRTM=>NOMON", None),            # NOMON uncurated -> title-cased, still a value
        ("GBFXT->NLRTM", "Rotterdam"),
        ("SGSIN > GBSOU", "Southampton"),
        ("SOUTHAMPTON<>E-COWES", None),    # shuttle: dest leg E-COWES uncurated
    ],
)
def test_route_destination_leg(raw, dest):
    out = _canonical_destination(raw)
    if dest is None:
        # uncurated leg: must still produce a non-empty cleaned label, not crash
        assert out is not None and out != ""
    else:
        assert out == dest


@pytest.mark.parametrize(
    "raw,origin",
    [
        ("NLRTM>USORF", "Rotterdam"),
        ("BEANR>NLRTM", "Antwerp"),
        ("FR LEH>>NL RTM", "Le Havre"),
        ("SGSIN > GBSOU", "Singapore"),
        ("ROTTERDAM", None),               # plain port: no origin
        ("NLRTM", None),
        ("FOR ORDERS", None),
    ],
)
def test_route_origin_leg(raw, origin):
    assert _canonical_origin(raw) == origin


def test_via_waypoint_is_not_origin():
    # "DEST VIA WAYPOINT": destination is the leg before VIA, no origin.
    assert _canonical_destination("ROTTERDAM VIA DOVER") == "Rotterdam"
    assert _canonical_origin("ROTTERDAM VIA DOVER") is None


def test_weak_separator_guards_against_na():
    # "/" with sub-3-char legs must NOT be parsed as a route ("N/A" -> junk).
    assert _split_route("N/A") == (None, "N/A")
    assert _canonical_destination("N/A") is None


def test_plain_port_unchanged_through_route_layer():
    assert _canonical_destination("NLRTM") == "Rotterdam"
    assert _canonical_destination("ROTTERDAM 3E PETROHA") == "Rotterdam"
    assert _canonical_origin("NLRTM") is None
