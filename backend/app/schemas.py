from __future__ import annotations

from pydantic import BaseModel


# ---- AIS live vessel tracker ----


class Vessel(BaseModel):
    mmsi: int
    name: str | None = None
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None
    destination: str | None = None
    kind: str
    segment: str | None = None
    region: str | None = None
    updated_ts: str
    imo: int | None = None
    draught: float | None = None
    nav_status: int | None = None
    eta: str | None = None


class TrackPoint(BaseModel):
    ts: str
    lat: float
    lon: float
    sog: float | None = None


class ChokepointCount(BaseModel):
    region: str
    bbox: list[list[float]]  # [[lat_min, lon_min], [lat_max, lon_max]]
    total: int
    by_segment: dict[str, int]


class Meta(BaseModel):
    kinds: list[str]
    segments: list[str]
    regions: list[str]
    total_tracked: int
    last_update: str | None = None


# ---- Transport-arb routes ----


class RouteResult(BaseModel):
    id: str
    origin: str
    destination: str
    product_class: str
    vessel_class: str
    voyage_days: int
    description: str
    origin_spot: float
    origin_price: float
    dest_spot: float
    dest_fwd: float
    fwd_curve_effect: float
    freight: float
    freight_base: float
    freight_bwet_adjusted: bool
    port_cost: float
    finance_cost: float
    insurance_cost: float
    total_cost: float
    gross_margin: float
    net_margin: float
    net_margin_baseline: float = 0.0
    breakeven_freight: float
    status: str
    status_near: str


class BwetInfo(BaseModel):
    bwet_close: float | None
    bwet_baseline: float
    scale_factor: float
    source: str
    bwet_date: str | None


class ArbMatrixCell(BaseModel):
    origin: str
    destination: str
    net_margin: float | None = None
    status: str | None = None
    voyage_days: int | None = None


class RoutesResponse(BaseModel):
    name: str
    as_of: str
    spots: dict[str, float]
    routes: list[RouteResult]
    n_open: int
    n_closed: int
    n_near: int
    hist_series: list[dict]
    bwet: BwetInfo
    matrix: list[ArbMatrixCell] = []
    matrix_origins: list[str] = []
    matrix_destinations: list[str] = []


# ---- Freight-dispersion ----


class DispersionStats(BaseModel):
    total_return: float
    ann_return: float
    ann_volatility: float
    sharpe: float
    max_drawdown: float
    n_trades: int
    hit_rate: float
    n_years: float


class DispersionPoint(BaseModel):
    date: str
    value: float


class DispersionResponse(BaseModel):
    name: str
    strategy: str
    stats: DispersionStats
    equity: list[DispersionPoint]
    price_5tc: list[DispersionPoint]
    avg_dispersion: list[DispersionPoint]


class AisDispersionRow(BaseModel):
    date: str
    kind: str
    segment: str
    vessel_count: int
    dispersion_nm: float


# ---- Phase 2 analytics ----


class TransitDay(BaseModel):
    date: str
    direction: str
    kind: str
    count: int


class TransitsResponse(BaseModel):
    chokepoint: str
    days: int
    series: list[TransitDay]


class CongestionDay(BaseModel):
    date: str
    zone: str
    vessel_count: int
    median_dwell_hours: float | None = None


class CongestionResponse(BaseModel):
    zone: str
    days: int
    series: list[CongestionDay]


class DensityDay(BaseModel):
    date: str
    kind: str
    segment: str
    laden_count: int
    ballast_count: int
    unknown_count: int


class DensityResponse(BaseModel):
    region: str
    days: int
    series: list[DensityDay]


class LadenSegment(BaseModel):
    segment: str
    laden: int
    ballast: int
    unknown: int


class LadenResponse(BaseModel):
    kind: str
    segments: list[LadenSegment]


class AnalyticsZone(BaseModel):
    name: str
    bbox: list[list[float]]  # [[lat_min, lon_min], [lat_max, lon_max]]
    type: str  # 'anchorage' or 'chokepoint'


class AisEvent(BaseModel):
    event_id: str
    type: str  # 'gap', 'loiter', 'sts'
    mmsi: int
    mmsi2: int | None
    start_ts: str
    end_ts: str
    lat: float
    lon: float
    region: str | None
    kind: str | None
    segment: str | None
    details: dict
    vessel_name: str | None = None
    vessel2_name: str | None = None


class EventsResponse(BaseModel):
    events: list[AisEvent]
    total: int
