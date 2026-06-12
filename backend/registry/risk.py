"""Shadow-fleet risk scoring.

Produces a transparent 0-100 risk score for each vessel from owned data only:
static registry facts (flag, class, P&I, age) + observed AIS behaviour (gaps, STS).

IMPORTANT: these are "risk indicators", never accusations. The score surfaces vessels
worth closer attention; it does not imply sanctions violations or misconduct.

Weights are constants at the top of this file - tune after eyeballing the top-20.
"""

from __future__ import annotations

from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# International Group of P&I Clubs (as of 2024-2025)
# Equasis lists the full name; we match substrings (case-insensitive).
# ---------------------------------------------------------------------------
_IG_CLUBS = [
    "gard",
    "uk p&i",
    "uk p and i",
    "west of england",
    "north of england",
    "standard",
    "swedish club",
    "steamship",           # Steamship Mutual, Britannia Steamship
    "britannia",
    "skuld",
    "london p&i",
    "american club",
    "korea p&i",
    "japan p&i",
]

# ---------------------------------------------------------------------------
# Scoring weights (0-100 scale)
# ---------------------------------------------------------------------------
_W_OFAC = 100               # OFAC SDN sanction (immediate max score)
_W_OLD_TANKER = 20          # tanker AND age >= 15y
_W_OLD_TANKER_SEVERE = 30   # tanker AND age >= 25y (override of above)
_W_NO_PI = 20               # no P&I or not an IG member
_W_NON_IACS = 15            # class society not in IACS
_W_PARIS_GREY = 8
_W_PARIS_BLACK = 18
_W_TOKYO_GREY = 8
_W_TOKYO_BLACK = 18
_W_DETENTION_5 = 8          # detention_rate_pct >= 5%
_W_DETENTION_10 = 16        # detention_rate_pct >= 10% (overrides above)
_W_GAP_PER = 12             # per AIS gap event (90d)
_W_STS_PER = 10             # per STS event (90d)
_W_LOITER_PER = 5           # per loitering event (90d)
_W_DARK_VOYAGE = 25         # dark voyage composite (gap -> STS/loiter -> gap within 72h)
_W_SINGLE_SHIP = 8          # single-vessel owner
_MAX_BEHAVIOURAL = 50       # cap on behavioural contribution (raised for dark voyage)


def _is_ig_member(pi_club: str | None) -> bool:
    if not pi_club:
        return False
    lower = pi_club.lower()
    return any(term in lower for term in _IG_CLUBS)


def _is_iacs(class_society: str | None) -> bool:
    if not class_society:
        return False
    return "(iacs)" in class_society.lower()


def risk_score(
    imo: int,
    ship_type: str | None,
    year_built: int | None,
    pi_club: str | None,
    class_society: str | None,
    paris_mou: str | None,
    tokyo_mou: str | None,
    detention_rate_pct: float | None,
    event_counts: dict[str, int],   # {"gap": N, "sts": N, "loiter": N, "dark_voyage": N} for last 90d
    owner: str | None,
    single_ship_owner: bool,
    ofac_sanctioned: bool = False,
) -> tuple[int, list[str]]:
    """Compute a 0-100 risk score and list the indicators that fired.

    Returns (score, [list of fired indicator descriptions]).
    OFAC sanction immediately returns score=100 (overrides all other indicators).
    """
    if ofac_sanctioned:
        return 100, ["OFAC SDN sanctioned vessel"]

    score = 0
    fired: list[str] = []
    current_year = datetime.now(UTC).year

    # Age + tanker check
    if ship_type and "tanker" in ship_type.lower():
        if year_built and (current_year - year_built) >= 25:
            score += _W_OLD_TANKER_SEVERE
            fired.append(f"Old tanker ({current_year - year_built}y, age >= 25)")
        elif year_built and (current_year - year_built) >= 15:
            score += _W_OLD_TANKER
            fired.append(f"Old tanker ({current_year - year_built}y, age >= 15)")

    # P&I club
    if not _is_ig_member(pi_club):
        score += _W_NO_PI
        fired.append(
            "P&I club not in International Group" if pi_club else "No P&I club on record"
        )

    # Classification society
    if not _is_iacs(class_society):
        score += _W_NON_IACS
        fired.append(
            f"Class not IACS: {class_society}" if class_society else "No class society on record"
        )

    # Paris MOU
    if paris_mou == "Black":
        score += _W_PARIS_BLACK
        fired.append("Flag on Paris MOU Black list")
    elif paris_mou == "Grey":
        score += _W_PARIS_GREY
        fired.append("Flag on Paris MOU Grey list")

    # Tokyo MOU
    if tokyo_mou == "Black":
        score += _W_TOKYO_BLACK
        fired.append("Flag on Tokyo MOU Black list")
    elif tokyo_mou == "Grey":
        score += _W_TOKYO_GREY
        fired.append("Flag on Tokyo MOU Grey list")

    # Detention rate
    if detention_rate_pct is not None:
        if detention_rate_pct >= 10:
            score += _W_DETENTION_10
            fired.append(f"High detention rate ({detention_rate_pct:.0f}%)")
        elif detention_rate_pct >= 5:
            score += _W_DETENTION_5
            fired.append(f"Elevated detention rate ({detention_rate_pct:.0f}%)")

    # Behavioural events (capped)
    behavioural = 0
    gap_n = event_counts.get("gap", 0)
    sts_n = event_counts.get("sts", 0)
    loiter_n = event_counts.get("loiter", 0)
    dark_n = event_counts.get("dark_voyage", 0)
    if dark_n:
        behavioural += dark_n * _W_DARK_VOYAGE
        fired.append(f"{dark_n} dark voyage pattern{'s' if dark_n > 1 else ''} in last 90 days")
    if gap_n:
        behavioural += gap_n * _W_GAP_PER
        fired.append(f"{gap_n} AIS gap event{'s' if gap_n > 1 else ''} in last 90 days")
    if sts_n:
        behavioural += sts_n * _W_STS_PER
        fired.append(f"{sts_n} STS candidate event{'s' if sts_n > 1 else ''} in last 90 days")
    if loiter_n:
        behavioural += loiter_n * _W_LOITER_PER
        fired.append(f"{loiter_n} loitering event{'s' if loiter_n > 1 else ''} in last 90 days")
    score += min(behavioural, _MAX_BEHAVIOURAL)

    # Single-vessel owner
    if single_ship_owner:
        score += _W_SINGLE_SHIP
        fired.append("Single-vessel owner (possible shell company)")

    return min(score, 100), fired
