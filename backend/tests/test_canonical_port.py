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
