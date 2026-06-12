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
    type: str  # port_call | transit | reroute | cargo_load | cargo_discharge | sts
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
    # cargo transition fields (type = cargo_load / cargo_discharge)
    draught_before: float | None = None
    draught_after: float | None = None
    change_m: float | None = None
    # STS co-participant
    mmsi2: int | None = None
    name2: str | None = None


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


class OwnerRiskItem(BaseModel):
    owner: str
    vessel_count: int
    avg_risk_score: float
    max_risk_score: int
    high_risk_count: int  # risk_score >= 50
    ofac_count: int
    flags: list[str]

class OwnerRiskResponse(BaseModel):
    as_of: str
    rows: list[OwnerRiskItem]

class SpeedTrendPoint(BaseModel):
    date: str          # YYYY-MM-DD
    avg_sog: float | None
    underway_count: int
    total_count: int


class SpeedTrendResponse(BaseModel):
    kind: str
    segment: str | None
    days: int
    series: list[SpeedTrendPoint]


class FlagRiskRow(BaseModel):
    flag: str
    flag_code: str | None
    vessel_count: int
    avg_risk_score: float
    max_risk_score: int
    high_risk_count: int  # score >= 50
    ofac_count: int
    paris_mou: str | None   # Black / Grey / White / None (most common for this flag)
    tokyo_mou: str | None


class FlagRiskResponse(BaseModel):
    as_of: str
    rows: list[FlagRiskRow]


class HighRiskPosition(BaseModel):
    mmsi: int
    imo: int
    lat: float
    lon: float
    name: str | None
    segment: str | None
    kind: str | None
    risk_score: int
    ofac_sanctioned: bool


class HighRiskPositionsResponse(BaseModel):
    as_of: str
    min_risk: int
    rows: list[HighRiskPosition]


class SpeedSegmentRow(BaseModel):
    segment: str
    kind: str
    underway: int         # nav_status 0 (under way using engine)
    anchored: int         # nav_status 1 (at anchor)
    moored: int           # nav_status 5 (moored)
    other: int
    total: int
    avg_sog_underway: float | None   # avg SOG for nav_status=0, SOG > 0.2
    p50_sog: float | None            # median SOG all vessels
    pct_underway: float  # underway / total


class SpeedAnalyticsResponse(BaseModel):
    as_of: str
    total_vessels: int
    rows: list[SpeedSegmentRow]


class RegionUtilRow(BaseModel):
    region: str
    total: int
    underway: int
    anchored: int
    moored: int
    pct_underway: float
    avg_sog: float | None


class RegionUtilResponse(BaseModel):
    as_of: str
    rows: list[RegionUtilRow]


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


class AnchoredVessel(BaseModel):
    mmsi: int
    name: str | None
    zone: str
    kind: str | None
    segment: str | None
    start_ts: str
    dwell_hours: float       # time anchored so far
    laden: str | None        # laden / ballast / unknown from vessel_state
    risk_score: int | None
    ofac: bool


class AnchorageDwellResponse(BaseModel):
    as_of: str
    zone: str
    rows: list[AnchoredVessel]


class CargoTransitionEvent(BaseModel):
    mmsi: int
    name: str | None
    kind: str | None
    segment: str | None
    region: str | None
    direction: str           # "loading" or "discharging"
    draught_before: float    # median draught in bucket before the step change
    draught_after: float     # median draught in bucket after the step change
    change_m: float          # absolute draught change in metres
    transition_ts: str       # start of the 6h bucket where the step occurred
    lat: float | None        # vessel position at transition
    lon: float | None
    risk_score: int | None
    ofac: bool


class CargoTransitionsResponse(BaseModel):
    as_of: str
    days: int
    min_change: float
    rows: list[CargoTransitionEvent]


class FleetAgeBand(BaseModel):
    age_band: str            # "0-4", "5-9", "10-14", "15-19", "20-24", "25+"
    vessel_count: int
    avg_risk_score: float | None
    high_risk_count: int    # score >= 50
    avg_dwt: float | None


class FleetAgeResponse(BaseModel):
    as_of: str
    reference_year: int
    bands: list[FleetAgeBand]


class SlowSteamerEvent(BaseModel):
    mmsi: int
    name: str | None
    kind: str | None
    segment: str | None
    region: str | None
    sog: float                  # current speed over ground (knots)
    segment_median_sog: float   # median for this segment among underway vessels
    pct_of_median: float        # sog / segment_median_sog * 100
    risk_score: int | None
    ofac: bool


class SlowSteamersResponse(BaseModel):
    as_of: str
    total_fleet_underway: int   # live vessels with sog > 0.5 and not anchored/moored
    rows: list[SlowSteamerEvent]


class FleetUtilizationRow(BaseModel):
    segment: str
    kind: str              # "tanker" | "bulk"
    total: int
    underway_count: int    # sog > 2 and nav_status = 0 or None
    idle_count: int        # sog < 0.5 or nav_status in (1, 5)
    unknown_count: int     # everything else (slow but not confirmed anchored)
    underway_pct: float
    idle_pct: float
    avg_sog_underway: float | None


class FleetUtilizationResponse(BaseModel):
    as_of: str
    total_fleet: int
    rows: list[FleetUtilizationRow]


class TransitRiskEvent(BaseModel):
    mmsi: int
    name: str | None
    imo: int | None
    chokepoint: str
    entered_ts: str
    exited_ts: str | None
    direction: str | None
    kind: str | None
    segment: str | None
    laden: bool | None
    risk_score: int | None
    ofac: bool


class TransitRiskResponse(BaseModel):
    as_of: str
    days: int
    chokepoint: str
    total_transits: int
    enriched: int
    rows: list[TransitRiskEvent]


class StsRiskEvent(BaseModel):
    event_id: str
    start_ts: str
    region: str | None
    kind: str | None
    segment: str | None
    mmsi: int
    mmsi2: int | None
    name: str | None
    name2: str | None
    duration_hours: float | None
    co_location_fixes: int | None
    risk_score: int | None       # mmsi vessel
    risk_score2: int | None      # mmsi2 vessel
    ofac: bool
    ofac2: bool
    max_risk: int                # max(risk_score, risk_score2, 0)


class StsRiskResponse(BaseModel):
    as_of: str
    days: int
    total_events: int
    enriched_events: int         # events where at least one party has a risk score
    rows: list[StsRiskEvent]


class RerouteRiskEvent(BaseModel):
    event_id: str
    start_ts: str
    region: str | None
    kind: str | None
    segment: str | None
    mmsi: int
    name: str | None
    old_destination: str | None
    new_destination: str | None
    fixes_at_old: int | None
    risk_score: int | None
    ofac: bool


class RerouteRiskResponse(BaseModel):
    as_of: str
    days: int
    total_events: int
    rows: list[RerouteRiskEvent]


class FleetKPIs(BaseModel):
    as_of: str
    total_registry: int          # all fetch_ok vessels
    scored: int                  # have risk_score
    elevated: int                # risk_score >= 25
    high_risk: int               # risk_score >= 50
    critical: int                # risk_score >= 75
    ofac_count: int
    avg_risk_score: float | None  # among scored vessels
    pct_scored: float            # scored / total_registry


class RiskEventItem(BaseModel):
    event_id: str
    event_type: str              # "sts" | "reroute"
    event_ts: str
    mmsi: int
    name: str | None
    imo: int | None
    risk_score: int | None
    ofac: bool
    mmsi2: int | None
    name2: str | None
    imo2: int | None
    risk_score2: int | None
    ofac2: bool
    max_risk: int
    region: str | None
    kind: str | None
    segment: str | None
    lat: float | None
    lon: float | None
    old_destination: str | None
    new_destination: str | None


class RiskEventsResponse(BaseModel):
    as_of: str
    min_risk: int
    days: int
    total_high_risk_vessels: int
    rows: list[RiskEventItem]


class PortCongestionRow(BaseModel):
    zone: str
    region: str | None
    kind: str | None
    current_vessels: int
    avg_current_dwell_hours: float | None  # hours for open episodes
    baseline_avg_vessels: float | None     # avg simultaneous vessels over history
    baseline_avg_dwell_hours: float | None
    congestion_factor: float               # current_vessels / baseline_avg (or 1.0 if no baseline)


class PortCongestionResponse(BaseModel):
    as_of: str
    days_baseline: int
    rows: list[PortCongestionRow]
