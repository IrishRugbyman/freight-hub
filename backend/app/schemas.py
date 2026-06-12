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


# ---- Phase 6: Fleet Explorer ----


class VoyageEvent(BaseModel):
    type: str  # port_call | transit | reroute
    ts: str
    end_ts: str | None = None
    zone: str | None = None
    direction: str | None = None
    laden: bool | None = None
    dwell_hours: float | None = None
    old_destination: str | None = None
    new_destination: str | None = None
    lat: float | None = None
    lon: float | None = None
    kind: str | None = None
    segment: str | None = None


class VoyagesResponse(BaseModel):
    mmsi: int
    events: list[VoyageEvent]


class VesselStateData(BaseModel):
    mmsi: int
    laden: str | None = None
    last_draught: float | None = None
    max_draught_seen: float | None = None
    updated_ts: str | None = None


class PortDestItem(BaseModel):
    destination: str
    count: int
    tankers: int
    bulkers: int


class PortFlowResponse(BaseModel):
    as_of: str
    total_with_dest: int
    ports: list[PortDestItem]


class FleetRow(BaseModel):
    # Registry fields
    imo: int
    ship_name: str | None = None
    flag: str | None = None
    flag_code: str | None = None
    ship_type: str | None = None
    year_built: int | None = None
    gross_tonnage: int | None = None
    dwt: int | None = None
    owner: str | None = None
    ism_manager: str | None = None
    class_society: str | None = None
    pi_club: str | None = None
    detention_rate_pct: float | None = None
    paris_mou: str | None = None
    tokyo_mou: str | None = None
    ship_status: str | None = None
    risk_score: int | None = None
    risk_indicators: list[str] | None = None
    ofac_sanctioned: bool | None = None
    # Live fields (None when vessel not currently tracked)
    mmsi: int | None = None
    live_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    sog: float | None = None
    region: str | None = None
    kind: str | None = None
    segment: str | None = None


class FleetFacetItem(BaseModel):
    value: str
    count: int


class FleetFacets(BaseModel):
    flags: list[FleetFacetItem]
    class_societies: list[FleetFacetItem]
    pi_clubs: list[FleetFacetItem]
    paris_mou: list[FleetFacetItem]
    tokyo_mou: list[FleetFacetItem]
    owners: list[FleetFacetItem]


class FleetSummary(BaseModel):
    total: int
    total_dwt: int | None = None
    avg_age_years: float | None = None
    top_flags: list[FleetFacetItem]
    top_owners: list[FleetFacetItem]


class FleetResponse(BaseModel):
    total: int
    page: int
    page_size: int
    summary: FleetSummary
    rows: list[FleetRow]
