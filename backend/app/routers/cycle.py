"""Freight Cycle board (``/cycle``): signals, subsectors and index series."""

from __future__ import annotations

import threading as _threading
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter

from .. import cycle as _cycle
from ..common import iso
from ..schemas import (
    CycleSeriesPoint,
    CycleSeriesResponse,
    CycleSignal,
    CycleSignalsResponse,
    CycleSubsector,
    CycleSubsectorsResponse,
)

router = APIRouter()


_CYCLE_SERIES_LABELS = {
    "BDI": "Baltic Dry Index",
    "BCI": "Baltic Capesize Index",
    "BPI": "Baltic Panamax Index",
    "BDTI": "Baltic Dirty Tanker Index",
    "BCTI": "Baltic Clean Tanker Index",
}

# The underlying series move once a day at best (Baltic fixings, hourly transit rebuild),
# so a 5-minute cache is generous and keeps a page refresh off PostgreSQL entirely.
_CYCLE_CACHE_TTL = 300.0
_cycle_cache_lock = _threading.Lock()
_cycle_cache: dict = {"signals": None, "ts": 0.0}


def _cycle_signal_model(rs) -> CycleSignal:
    """Map a resolved registry signal onto the wire schema."""
    spec = rs.spec
    return CycleSignal(
        id=spec.id,
        subsector=spec.subsector,
        category=spec.category,
        label=spec.label,
        tier=spec.tier,
        unit=spec.unit,
        value=rs.value,
        value_note=rs.note or spec.value_note,
        as_of=rs.as_of.isoformat() if rs.as_of else None,
        state=rs.state,
        threshold_value=spec.threshold_value,
        threshold_label=spec.threshold_label,
        direction=spec.direction,
        distance_pct=round(rs.distance_pct, 1) if rs.distance_pct is not None else None,
        expected_lag=spec.expected_lag,
        falsifier=spec.falsifier,
        source_label=spec.source_label,
        source_url=spec.source_url,
        stale=rs.stale,
        review_interval_days=spec.review_interval_days,
        verified=spec.verified,
        provenance=spec.provenance,
        caveat=spec.caveat,
        gap_reason=spec.gap_reason,
        spark=[round(v, 2) for v in rs.spark],
    )


def _cycle_signals_cached() -> list[CycleSignal]:
    """Resolve every signal, from a 5-minute in-process cache."""
    import time

    now = time.monotonic()
    with _cycle_cache_lock:
        if _cycle_cache["signals"] is not None and now - _cycle_cache["ts"] < _CYCLE_CACHE_TTL:
            return _cycle_cache["signals"]

    signals = [_cycle_signal_model(rs) for rs in _cycle.resolve_all()]
    with _cycle_cache_lock:
        _cycle_cache["signals"] = signals
        _cycle_cache["ts"] = now
    return signals


@router.get("/api/cycle/signals", response_model=CycleSignalsResponse)
def cycle_signals():
    """Every freight-cycle signal with its threshold state, provenance tier and falsifier.

    Gaps are returned, not filtered: a signal we cannot source is part of the answer.
    """
    reg = _cycle.get_registry()
    return CycleSignalsResponse(
        as_of=iso(datetime.now(UTC)) or "",
        registry_updated=str(reg.updated) if reg.updated else None,
        warn_band_pct=reg.warn_band_pct,
        signals=_cycle_signals_cached(),
    )


@router.get("/api/cycle/subsectors", response_model=CycleSubsectorsResponse)
def cycle_subsectors():
    """The three clocks: container, dry bulk, tanker - stage, headline rate, orderbook."""
    reg = _cycle.get_registry()
    by_id = {s.id: s for s in _cycle_signals_cached()}
    return CycleSubsectorsResponse(
        as_of=iso(datetime.now(UTC)) or "",
        subsectors=[
            CycleSubsector(
                id=sub["id"],
                name=sub.get("name", sub["id"]),
                stage=sub.get("stage", ""),
                stage_note=sub.get("stage_note", ""),
                coverage_note=sub.get("coverage_note", ""),
                headline=by_id.get(sub.get("headline_signal", "")),
                orderbook=by_id.get(sub.get("orderbook_signal", "")),
            )
            for sub in reg.subsectors
        ],
    )


@router.get("/api/cycle/series", response_model=CycleSeriesResponse)
def cycle_series(series: str = "BDI", years: int = 5):
    """History of one Baltic index. One series per request - nothing is spliced here.

    years is clamped to 1-40; BDI reaches back to 1988, the tanker pair to 2001.
    """
    from fastapi import HTTPException
    from loaders import BALTIC_SERIES, load_baltic_index

    key = series.upper()
    if key not in BALTIC_SERIES:
        raise HTTPException(status_code=404, detail=f"unknown series {series!r}")

    yrs = max(1, min(int(years), 40))
    start = date.today() - timedelta(days=365 * yrs)
    try:
        s = load_baltic_index(key, start)
    except Exception:
        s = None

    points = (
        []
        if s is None or s.empty
        else [CycleSeriesPoint(date=ts.date().isoformat(), value=float(v)) for ts, v in s.items()]
    )
    return CycleSeriesResponse(
        series=key,
        label=_CYCLE_SERIES_LABELS.get(key, key),
        source_label="Baltic Exchange daily fixing via akshare",
        points=points,
    )
