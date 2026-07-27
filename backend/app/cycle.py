"""Freight cycle board: signal registry, live resolution, threshold evaluation.

Three subsectors on three clocks. The registry (`cycle_signals.yaml`) is the single
source of truth for what is watched and what would prove the read wrong; this module
loads it, validates it, resolves whatever can be resolved from real data, and reports
the rest as a visible gap.

The provenance tiering is the point. A `live` signal is computed from a series this
repo ingests. A `registered` signal is a disclosed observation someone typed in, with
a source and an as-of date, that goes visibly stale on a stated cadence. A `missing`
signal still renders, carrying the reason it cannot be filled. Nothing is interpolated
and nothing is silently carried forward.

Registry violations raise at load time. A signal without a falsifier is a bug in the
registry, not a blank tile on the page.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_REGISTRY = Path(__file__).resolve().parent / "cycle_signals.yaml"

# How the current value sits against the threshold that would change the read.
STATE_BREACHED = "breached"  # threshold crossed
STATE_APPROACHING = "approaching"  # inside the warn band, not yet crossed
STATE_HOLDING = "holding"  # on the thesis side, outside the warn band
STATE_UNKNOWN = "unknown"  # no value to judge

VALID_STATES = {STATE_BREACHED, STATE_APPROACHING, STATE_HOLDING, STATE_UNKNOWN}
VALID_TIERS = {"live", "registered", "missing"}
VALID_DIRECTIONS = {"below", "above", "none"}
VALID_CATEGORIES = {"price", "capacity", "event", "financials"}

# Sparkline length for live signals. Enough to read a trend, small enough to inline.
SPARK_POINTS = 60


class CycleRegistryError(ValueError):
    """The signal registry is malformed. Raised at load time, never swallowed."""


@dataclass(frozen=True)
class SignalSpec:
    """One registry entry, exactly as written in cycle_signals.yaml."""

    id: str
    subsector: str
    category: str
    label: str
    tier: str
    unit: str
    expected_lag: str
    falsifier: str
    source_label: str
    direction: str = "none"
    threshold_value: float | None = None
    threshold_label: str = ""
    resolver: str | None = None
    value: float | None = None
    value_note: str = ""
    manual_state: str = STATE_UNKNOWN
    source_url: str | None = None
    as_of: date | None = None
    review_interval_days: int | None = None
    verified: bool = False
    verified_note: str = ""
    provenance: str = ""
    caveat: str = ""
    gap_reason: str = ""


@dataclass
class Resolved:
    """What a live resolver produced. Every field may be absent but `value`."""

    value: float | None
    as_of: date | None = None
    note: str = ""
    spark: list[float] = field(default_factory=list)


@dataclass
class ResolvedSignal:
    """A registry entry plus its resolved value and threshold state."""

    spec: SignalSpec
    value: float | None
    as_of: date | None
    note: str
    state: str
    distance_pct: float | None
    stale: bool
    spark: list[float]


# --------------------------------------------------------------------------- pure


def evaluate_threshold(
    value: float | None,
    threshold: float | None,
    direction: str,
    warn_band_pct: float,
) -> str:
    """Where `value` sits relative to the threshold that would change the read.

    `direction` says which side breaks the thesis: "below" means the threshold is
    breached when the value drops under it, "above" when it rises over it. "none" has
    no numeric threshold and always returns unknown - the caller substitutes the
    registry's manual_state.

    The warn band is a percentage of the threshold, so "approaching" means within
    warn_band_pct of crossing, on the un-breached side.
    """
    if direction == "none" or value is None or threshold is None:
        return STATE_UNKNOWN

    band = abs(threshold) * (warn_band_pct / 100.0)
    if direction == "below":
        if value < threshold:
            return STATE_BREACHED
        return STATE_APPROACHING if value <= threshold + band else STATE_HOLDING
    if direction == "above":
        if value > threshold:
            return STATE_BREACHED
        return STATE_APPROACHING if value >= threshold - band else STATE_HOLDING

    raise CycleRegistryError(f"unknown direction {direction!r}")


def distance_to_threshold(value: float | None, threshold: float | None) -> float | None:
    """Signed distance from threshold to value, as a percentage of the threshold.

    Positive means the value sits above the threshold. None when either side is
    missing or the threshold is zero (no meaningful percentage exists).
    """
    if value is None or threshold is None or threshold == 0:
        return None
    return (value - threshold) / abs(threshold) * 100.0


def is_stale(
    as_of: date | None,
    review_interval_days: int | None,
    today: date,
) -> bool:
    """Whether a recorded observation is past its stated review interval.

    An observation with no as-of date is stale by definition: we cannot claim it is
    current if we did not record when it was true. No interval means never reviewed,
    which is not the same as never stale, so it also counts as stale.
    """
    if as_of is None or review_interval_days is None:
        return True
    return today > as_of + timedelta(days=review_interval_days)


# ---------------------------------------------------------------------- registry


def _require(entry: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if not entry.get(k)]
    if missing:
        raise CycleRegistryError(f"{where}: missing required field(s) {missing}")


def _parse_signal(entry: dict[str, Any]) -> SignalSpec:
    """Validate one registry entry and build its spec.

    The required set is deliberately strict: a signal with no falsifier is an opinion,
    and the whole point of the board is that every claim states what would break it.
    """
    sid = entry.get("id") or "<no id>"
    where = f"signal {sid}"
    _require(entry, ("id", "subsector", "category", "label", "tier", "unit"), where)
    _require(entry, ("expected_lag", "falsifier", "source_label", "threshold_label"), where)

    tier = entry["tier"]
    if tier not in VALID_TIERS:
        raise CycleRegistryError(f"{where}: tier {tier!r} not in {sorted(VALID_TIERS)}")
    if entry["category"] not in VALID_CATEGORIES:
        raise CycleRegistryError(f"{where}: unknown category {entry['category']!r}")

    direction = entry.get("direction", "none")
    if direction not in VALID_DIRECTIONS:
        raise CycleRegistryError(f"{where}: direction {direction!r} not in {VALID_DIRECTIONS}")

    threshold = entry.get("threshold_value")
    if direction != "none" and threshold is None:
        raise CycleRegistryError(f"{where}: direction {direction!r} needs a threshold_value")
    if threshold == 0:
        raise CycleRegistryError(f"{where}: threshold_value 0 has no meaningful warn band")

    manual_state = entry.get("manual_state", STATE_UNKNOWN)
    if manual_state not in VALID_STATES:
        raise CycleRegistryError(f"{where}: manual_state {manual_state!r} not in {VALID_STATES}")

    if tier == "live" and not entry.get("resolver"):
        raise CycleRegistryError(f"{where}: live signals need a resolver")
    if tier == "registered":
        _require(entry, ("as_of", "review_interval_days"), where)
        if entry.get("value") is None:
            raise CycleRegistryError(f"{where}: registered signals need a value")
    if tier == "missing" and not entry.get("gap_reason"):
        raise CycleRegistryError(f"{where}: missing signals must say why (gap_reason)")

    unknown = set(entry) - set(SignalSpec.__dataclass_fields__)
    if unknown:
        raise CycleRegistryError(f"{where}: unknown field(s) {sorted(unknown)}")

    return SignalSpec(**entry)


@dataclass(frozen=True)
class Registry:
    """The parsed registry: metadata, subsector cards and validated signals."""

    updated: date
    warn_band_pct: float
    subsectors: tuple[dict[str, Any], ...]
    signals: tuple[SignalSpec, ...]

    def by_id(self, signal_id: str) -> SignalSpec | None:
        """The spec with this id, or None if the registry has no such signal."""
        return next((s for s in self.signals if s.id == signal_id), None)


def load_registry(path: str | Path | None = None) -> Registry:
    """Parse and validate the signal registry. Raises CycleRegistryError if malformed."""
    p = Path(path or os.environ.get("CYCLE_REGISTRY") or _DEFAULT_REGISTRY)
    raw = yaml.safe_load(p.read_text()) or {}

    meta = raw.get("meta") or {}
    signals = tuple(_parse_signal(e) for e in raw.get("signals") or [])

    ids = [s.id for s in signals]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CycleRegistryError(f"duplicate signal id(s): {dupes}")

    subsectors = tuple(raw.get("subsectors") or [])
    known_ids = set(ids)
    for sub in subsectors:
        for key in ("headline_signal", "orderbook_signal"):
            ref = sub.get(key)
            if ref and ref not in known_ids:
                raise CycleRegistryError(
                    f"subsector {sub.get('id')}: {key} {ref!r} is not a signal"
                )
    sub_ids = {s.get("id") for s in subsectors}
    orphans = sorted({s.subsector for s in signals} - sub_ids)
    if orphans:
        raise CycleRegistryError(f"signals reference unknown subsector(s): {orphans}")

    return Registry(
        updated=meta.get("updated"),
        warn_band_pct=float(meta.get("warn_band_pct", 15.0)),
        subsectors=subsectors,
        signals=signals,
    )


@lru_cache(maxsize=1)
def _cached_registry(path: str) -> Registry:
    return load_registry(path)


def get_registry() -> Registry:
    """The process-wide registry, parsed once. Validated on first access."""
    return _cached_registry(str(os.environ.get("CYCLE_REGISTRY") or _DEFAULT_REGISTRY))


# --------------------------------------------------------------------- resolvers


def _baltic(series: str) -> Resolved:
    """Latest fixing of one Baltic index, with a year-on-year note and a sparkline."""
    from loaders import load_baltic_index

    try:
        s = load_baltic_index(series)
    except Exception:  # noqa: BLE001 - a dead DB must degrade to unknown, not 500
        return Resolved(value=None)
    if s.empty:
        return Resolved(value=None)

    last_ts = s.index[-1]
    note = ""
    year_ago = s.loc[: last_ts - timedelta(days=365)]
    if not year_ago.empty and year_ago.iloc[-1]:
        pct = (s.iloc[-1] / year_ago.iloc[-1] - 1.0) * 100.0
        note = f"{pct:+.0f}% YoY"

    return Resolved(
        value=float(s.iloc[-1]),
        as_of=last_ts.date(),
        note=note,
        spark=[float(v) for v in s.iloc[-SPARK_POINTS:]],
    )


def _baltic_ratio(numerator: str, denominator: str) -> Resolved:
    """Ratio of two Baltic indices on their common trading days."""
    from loaders import load_baltic_indices

    try:
        df = load_baltic_indices([numerator, denominator])
    except Exception:  # noqa: BLE001
        return Resolved(value=None)
    if df.empty or numerator not in df or denominator not in df:
        return Resolved(value=None)

    ratio = (df[numerator] / df[denominator].replace(0, float("nan"))).dropna()
    if ratio.empty:
        return Resolved(value=None)

    return Resolved(
        value=float(ratio.iloc[-1]),
        as_of=ratio.index[-1].date(),
        note=f"{numerator} {df[numerator].iloc[-1]:,.0f} vs {denominator} {df[denominator].iloc[-1]:,.0f}",
        spark=[float(v) for v in ratio.iloc[-SPARK_POINTS:]],
    )


SCRAPPING_AGE_YEARS = 20


def _fleet_age_over_20(kind: str) -> Resolved:
    """Share of our aged sample of `kind` vessels at or past scrapping age.

    A scrapping *proxy*, not a scrapping count: we cannot see demolitions, but we can
    see how much of the fleet we track has reached the age at which it is a candidate.

    Build years come from two enrichment crawlers - the Equasis registry (PostgreSQL
    `vessels`) with MyShipTracking filling gaps - and neither has covered the whole
    live fleet, so the denominator is the vessels whose age we know, not the fleet.
    That sample is crawl-order rather than random, which is why the coverage count
    travels with the value everywhere it is shown.
    """
    import pandas as pd

    from app.db import mst_db_path, pg_query, query

    try:
        tracked = pg_query(
            "SELECT imo, year_built FROM vessels "
            "WHERE kind = %s AND ais_last_seen > now() - interval '24 hours'",
            [kind],
        )
        if tracked.empty:
            return Resolved(value=None)
        mst = query(
            "SELECT imo, year_built FROM mst_vessel_state "
            "WHERE year_built IS NOT NULL AND imo IS NOT NULL",
            db=mst_db_path(),
        )
    except Exception:  # noqa: BLE001 - a dead crawler DB degrades to unknown
        return Resolved(value=None)

    if not mst.empty:
        tracked = tracked.merge(mst, on="imo", how="left", suffixes=("", "_mst"))
        tracked["year_built"] = tracked["year_built"].fillna(tracked["year_built_mst"])

    years = pd.to_numeric(tracked["year_built"], errors="coerce").dropna()
    years = years[(years > 1900) & (years <= date.today().year)]
    if years.empty:
        return Resolved(value=None)

    cutoff = date.today().year - SCRAPPING_AGE_YEARS
    aged = int((years <= cutoff).sum())
    mean_age = date.today().year - float(years.mean())
    return Resolved(
        value=aged / len(years) * 100.0,
        as_of=date.today(),
        note=(
            f"{aged} of {len(years)} known build years "
            f"({len(years) / len(tracked) * 100:.0f}% of {len(tracked)} tracked); "
            f"mean age {mean_age:.1f}y"
        ),
    )


def _transits(chokepoint: str, window_days: int = 14) -> Resolved:
    """Mean daily transits of one chokepoint over a trailing window, from our own AIS.

    Tankers and bulk carriers only, and only since collection began - see the caveat
    carried on the signal itself. Small craft are excluded, as everywhere else.
    """
    from app.db import analytics_db_path, query

    sql = (
        "SELECT CAST(entered_ts AS DATE) AS d, count(*) AS n FROM transit_events "
        "WHERE chokepoint = ? AND segment != 'Small' "
        "AND entered_ts >= (SELECT max(entered_ts) - INTERVAL (?) DAY FROM transit_events "
        "                   WHERE chokepoint = ?) "
        "GROUP BY 1 ORDER BY 1"
    )
    try:
        df = query(sql, [chokepoint, window_days, chokepoint], db=analytics_db_path())
    except Exception:  # noqa: BLE001
        return Resolved(value=None)
    if df.empty:
        return Resolved(value=None)

    counts = [float(n) for n in df["n"]]
    last = df["d"].iloc[-1]
    return Resolved(
        value=sum(counts) / len(counts),
        as_of=last.date() if hasattr(last, "date") else last,
        note=f"mean/day over {len(counts)} days with transits",
        spark=counts[-SPARK_POINTS:],
    )


DEFAULT_RESOLVERS: dict[str, Callable[[], Resolved]] = {
    "baltic:BDI": lambda: _baltic("BDI"),
    "baltic:BDTI": lambda: _baltic("BDTI"),
    "baltic:BCTI": lambda: _baltic("BCTI"),
    "baltic:BCI": lambda: _baltic("BCI"),
    "baltic:BPI": lambda: _baltic("BPI"),
    "baltic:BCI_OVER_BPI": lambda: _baltic_ratio("BCI", "BPI"),
    "transits:suez": lambda: _transits("suez"),
    "transits:hormuz": lambda: _transits("hormuz"),
    "fleet_age:tanker": lambda: _fleet_age_over_20("tanker"),
    "fleet_age:bulk": lambda: _fleet_age_over_20("bulk"),
}


# --------------------------------------------------------------------- resolution


def resolve_signal(
    spec: SignalSpec,
    resolvers: dict[str, Callable[[], Resolved]] | None = None,
    today: date | None = None,
) -> ResolvedSignal:
    """Resolve one signal's value and threshold state.

    `live` reads its resolver, `registered` uses the recorded value, `missing` has
    none. A live signal whose resolver is unknown or fails resolves to no value and
    state unknown - the tile still renders, saying so.
    """
    today = today or date.today()
    table = DEFAULT_RESOLVERS if resolvers is None else resolvers

    if spec.tier == "live":
        fn = table.get(spec.resolver or "")
        res = fn() if fn else Resolved(value=None, note=f"no resolver {spec.resolver!r}")
        stale = is_stale(res.as_of, spec.review_interval_days, today)
    elif spec.tier == "registered":
        res = Resolved(value=spec.value, as_of=spec.as_of, note=spec.value_note)
        stale = is_stale(spec.as_of, spec.review_interval_days, today)
    else:
        res = Resolved(value=None, note=spec.value_note)
        stale = False  # a published gap cannot go stale; it is already the worst case

    if spec.direction == "none":
        # No numeric threshold to judge against: the registry states the read by hand,
        # but only where there is something to state it about.
        has_ground = res.value is not None or spec.tier == "missing"
        state = spec.manual_state if has_ground else STATE_UNKNOWN
    else:
        state = evaluate_threshold(
            res.value, spec.threshold_value, spec.direction, get_registry().warn_band_pct
        )

    return ResolvedSignal(
        spec=spec,
        value=res.value,
        as_of=res.as_of,
        note=res.note,
        state=state,
        distance_pct=distance_to_threshold(res.value, spec.threshold_value),
        stale=stale,
        spark=res.spark,
    )


def resolve_all(
    resolvers: dict[str, Callable[[], Resolved]] | None = None,
    today: date | None = None,
) -> list[ResolvedSignal]:
    """Resolve every registry signal, in registry order."""
    return [resolve_signal(s, resolvers, today) for s in get_registry().signals]
