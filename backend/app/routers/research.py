"""Research-backed tabs: transport-arb routes and the Capesize dispersion backtest."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter
from loaders.freight import load_ais_dispersion

from ..runner_dispersion import run_dispersion_default
from ..runner_routes import run_routes_default
from ..schemas import (
    AisDispersionRow,
    DispersionResponse,
    RoutesResponse,
)

router = APIRouter()


_STATIC = Path(__file__).resolve().parent.parent / "static"  # app/static
_STATIC_ROUTES = _STATIC / "routes_default.json"
_STATIC_DISPERSION = _STATIC / "dispersion_default.json"


def _serve_cached(static_path: Path, compute_fn, schema_class):
    if static_path.exists():
        return schema_class.model_validate_json(static_path.read_text())
    return compute_fn()


@router.get("/api/routes", response_model=RoutesResponse)
def routes():
    """Transport-arb route matrix (precomputed, static JSON fallback to live compute)."""
    return _serve_cached(_STATIC_ROUTES, run_routes_default, RoutesResponse)


@router.get("/api/dispersion", response_model=DispersionResponse)
def dispersion():
    """Freight-dispersion backtest results (precomputed, static JSON fallback)."""
    return _serve_cached(_STATIC_DISPERSION, run_dispersion_default, DispersionResponse)


@router.get("/api/dispersion/live", response_model=list[AisDispersionRow])
def dispersion_live(segment: str | None = None):
    """Live AIS fleet-dispersion series from commo.duckdb (last 2 years, long format)."""
    end = date.today()
    start = end - timedelta(days=730)
    df = load_ais_dispersion(start, end, segment=segment)
    if df.empty:
        return []
    rows = []
    for idx, row in df.iterrows():
        rows.append(
            AisDispersionRow(
                date=str(idx.date()),
                kind=str(row.get("kind", "")),
                segment=str(row.get("segment", "")),
                vessel_count=int(row.get("vessel_count", 0)),
                dispersion_nm=round(float(row.get("dispersion_nm", 0)), 2),
            )
        )
    return rows
