"""Pure unit tests for MMSI -> flag-state derivation (quant_lib.freight.flags).

quant_lib is editable-installed into this venv, so the freight backend suite is
where these run during verification.
"""

from quant_lib.freight import (
    FLAGS_OF_CONVENIENCE,
    MID_COUNTRY,
    SHADOW_FLEET_FLAGS,
    flag_from_mmsi,
    to_iso2,
)


def test_known_mappings():
    assert flag_from_mmsi(636012345).country == "Liberia"
    assert flag_from_mmsi(636012345).code == "LR"
    assert flag_from_mmsi(538099999).country == "Marshall Islands"
    assert flag_from_mmsi(235012345).country == "United Kingdom"
    assert flag_from_mmsi(477123456).country == "Hong Kong"
    assert flag_from_mmsi(351234567).country == "Panama"


def test_foc_flag():
    # Liberia, Panama, Marshall Islands are flags of convenience.
    assert flag_from_mmsi(636012345).is_foc is True
    assert flag_from_mmsi(351234567).is_foc is True
    assert flag_from_mmsi(538099999).is_foc is True
    # United Kingdom and Hong Kong are not.
    assert flag_from_mmsi(235012345).is_foc is False
    assert flag_from_mmsi(477123456).is_foc is False


def test_shadow_flag():
    # Gabon and Cook Islands are on the curated high-shadow-activity list.
    assert flag_from_mmsi(626012345).is_shadow is True
    assert flag_from_mmsi(518012345).is_shadow is True
    # FOC but not shadow.
    assert flag_from_mmsi(636012345).is_shadow is False


def test_non_ship_mmsi_returns_none():
    assert flag_from_mmsi(111232500) is None  # SAR aircraft (MID first digit 1)
    assert flag_from_mmsi(992351000) is None  # aids to navigation (9xx)
    assert flag_from_mmsi(982351000) is None  # craft associated (8xx)
    assert flag_from_mmsi(2573210) is None  # 7-digit coast station, out of range


def test_invalid_inputs_return_none():
    assert flag_from_mmsi(None) is None
    assert flag_from_mmsi(0) is None
    assert flag_from_mmsi(123) is None
    assert flag_from_mmsi("not a number") is None
    # A valid ship-station MMSI with an unassigned MID gap (209-279 has gaps).
    assert flag_from_mmsi(217000000) is None  # 217 unassigned


def test_string_numeric_mmsi_resolves():
    # aisstream/duckdb may hand us a numeric string; it should still resolve.
    assert flag_from_mmsi("636012345").code == "LR"


def test_to_iso2_normalization():
    assert to_iso2("LBR") == "LR"  # ISO3 -> ISO2
    assert to_iso2("PAN") == "PA"
    assert to_iso2("gbr") == "GB"  # case-insensitive
    assert to_iso2("LR") == "LR"   # already ISO2
    assert to_iso2("XCA") is None  # Equasis special code, unmappable
    assert to_iso2(None) is None
    assert to_iso2("") is None


def test_reference_data_integrity():
    # Every FOC/shadow code should be a real ISO2 present in the MID table
    # (except historical AN which has no live MID), and codes are 2 chars.
    codes_in_table = {code for _, code in MID_COUNTRY.values()}
    for code in FLAGS_OF_CONVENIENCE | SHADOW_FLEET_FLAGS:
        assert len(code) == 2 and code.isupper()
        if code != "AN":  # Netherlands Antilles, dissolved, no MID
            assert code in codes_in_table, f"{code} not reachable from any MID"
