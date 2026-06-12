import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

// ---- Routes (transport-arb) ----

export interface RouteResult {
  id: string
  origin: string
  destination: string
  product_class: string
  vessel_class: string
  voyage_days: number
  description: string
  origin_spot: number
  origin_price: number
  dest_spot: number
  dest_fwd: number
  fwd_curve_effect: number
  freight: number
  freight_base: number
  freight_bwet_adjusted: boolean
  port_cost: number
  finance_cost: number
  insurance_cost: number
  total_cost: number
  gross_margin: number
  net_margin: number
  net_margin_baseline: number
  breakeven_freight: number
  status: string
  status_near: string
}

export interface ArbMatrixCell {
  origin: string
  destination: string
  net_margin: number | null
  status: string | null
  voyage_days: number | null
}

export interface BwetInfo {
  bwet_close: number | null
  bwet_baseline: number
  scale_factor: number
  source: string
  bwet_date: string | null
}

export interface RoutesResponse {
  name: string
  as_of: string
  spots: Record<string, number>
  routes: RouteResult[]
  n_open: number
  n_closed: number
  n_near: number
  hist_series: Record<string, unknown>[]
  bwet: BwetInfo
  matrix: ArbMatrixCell[]
  matrix_origins: string[]
  matrix_destinations: string[]
}

// ---- Dispersion (freight-dispersion) ----

export interface DispersionStats {
  total_return: number
  ann_return: number
  ann_volatility: number
  sharpe: number
  max_drawdown: number
  n_trades: number
  hit_rate: number
  n_years: number
}

export interface DispersionPoint {
  date: string
  value: number
}

export interface DispersionResponse {
  name: string
  strategy: string
  stats: DispersionStats
  equity: DispersionPoint[]
  price_5tc: DispersionPoint[]
  avg_dispersion: DispersionPoint[]
}

export interface AisDispersionRow {
  date: string
  kind: string
  segment: string
  vessel_count: number
  dispersion_nm: number
}

export interface Vessel {
  mmsi: number
  name: string | null
  lat: number
  lon: number
  sog: number | null
  cog: number | null
  heading: number | null
  destination: string | null
  kind: string
  segment: string | null
  region: string | null
  updated_ts: string
  imo: number | null
  draught: number | null
  nav_status: number | null
  eta: string | null
}

export interface ChokepointCount {
  region: string
  bbox: [[number, number], [number, number]]
  total: number
  by_segment: Record<string, number>
}

export interface Meta {
  kinds: string[]
  segments: string[]
  regions: string[]
  total_tracked: number
  last_update: string | null
}

export interface VesselFilters {
  kind?: string
  segment?: string
  region?: string
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

function vesselsUrl(f: VesselFilters): string {
  const q = new URLSearchParams()
  if (f.kind) q.set('kind', f.kind)
  if (f.segment) q.set('segment', f.segment)
  if (f.region) q.set('region', f.region)
  const s = q.toString()
  return `/api/vessels${s ? `?${s}` : ''}`
}

const REFETCH_MS = 60_000

export function useVessels(filters: VesselFilters) {
  return useQuery({
    queryKey: ['vessels', filters],
    queryFn: () => getJSON<Vessel[]>(vesselsUrl(filters)),
    refetchInterval: REFETCH_MS,
    placeholderData: (prev) => prev,
    retry: 3,
    retryDelay: 5000,
  })
}

/**
 * Opens an SSE connection to /api/stream and pushes vessel updates directly into
 * the TanStack Query cache, bypassing the 60s polling interval.
 * Falls back silently if EventSource is unsupported or the connection drops
 * (the polling in useVessels remains the reliability backstop).
 */
export function useVesselStream(filters: VesselFilters, enabled: boolean) {
  const queryClient = useQueryClient()
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') return

    const es = new EventSource('/api/stream')
    esRef.current = es

    es.onmessage = (evt) => {
      try {
        const raw: Vessel[] = JSON.parse(evt.data)
        // Apply the same filters that useVessels would apply server-side
        const filtered = raw.filter((v) => {
          if (filters.kind && v.kind !== filters.kind) return false
          if (filters.segment && v.segment !== filters.segment) return false
          if (filters.region && v.region !== filters.region) return false
          return true
        })
        queryClient.setQueryData(['vessels', filters], filtered)
      } catch {
        // ignore malformed events
      }
    }

    es.onerror = () => {
      // EventSource auto-reconnects; no action needed
    }

    return () => {
      es.close()
      esRef.current = null
    }
  }, [enabled, filters.kind, filters.segment, filters.region, queryClient])
}

export interface TrackPoint {
  ts: string
  lat: number
  lon: number
  sog: number | null
}

export function useVesselTrack(mmsi: number | null, hours: 24 | 168) {
  return useQuery({
    queryKey: ['track', mmsi, hours],
    queryFn: () => getJSON<TrackPoint[]>(`/api/vessels/${mmsi}/track?hours=${hours}`),
    enabled: mmsi != null,
    staleTime: 5 * 60 * 1000,
  })
}

export function useChokepoints() {
  return useQuery({
    queryKey: ['chokepoints'],
    queryFn: () => getJSON<ChokepointCount[]>('/api/chokepoints'),
    refetchInterval: REFETCH_MS,
  })
}

export function useMeta() {
  return useQuery({
    queryKey: ['meta'],
    queryFn: () => getJSON<Meta>('/api/meta'),
    refetchInterval: REFETCH_MS,
  })
}

export function useRoutes() {
  return useQuery({
    queryKey: ['routes'],
    queryFn: () => getJSON<RoutesResponse>('/api/routes'),
    staleTime: Infinity,
  })
}

export function useDispersion() {
  return useQuery({
    queryKey: ['dispersion'],
    queryFn: () => getJSON<DispersionResponse>('/api/dispersion'),
    staleTime: Infinity,
  })
}

export function useDispersionLive(segment?: string) {
  const url = segment ? `/api/dispersion/live?segment=${encodeURIComponent(segment)}` : '/api/dispersion/live'
  return useQuery({
    queryKey: ['dispersion-live', segment],
    queryFn: () => getJSON<AisDispersionRow[]>(url),
    refetchInterval: REFETCH_MS,
  })
}

// ---- Analytics (Phase 2) ----

export interface TransitDay {
  date: string
  direction: string
  kind: string
  count: number
}

export interface TransitsResponse {
  chokepoint: string
  days: number
  series: TransitDay[]
}

export interface CongestionDay {
  date: string
  zone: string
  vessel_count: number
  median_dwell_hours: number | null
}

export interface CongestionResponse {
  zone: string
  days: number
  series: CongestionDay[]
}

export interface DensityDay {
  date: string
  kind: string
  segment: string
  laden_count: number
  ballast_count: number
  unknown_count: number
}

export interface DensityResponse {
  region: string
  days: number
  series: DensityDay[]
}

export interface LadenSegment {
  segment: string
  laden: number
  ballast: number
  unknown: number
}

export interface LadenResponse {
  kind: string
  segments: LadenSegment[]
}

export interface AnalyticsZone {
  name: string
  bbox: [[number, number], [number, number]]
  type: 'anchorage' | 'chokepoint'
}

const ANALYTICS_STALE = 10 * 60 * 1000 // 10 min; job runs hourly

export function useTransits(chokepoint: string, days: number) {
  return useQuery({
    queryKey: ['analytics-transits', chokepoint, days],
    queryFn: () =>
      getJSON<TransitsResponse>(`/api/analytics/transits?chokepoint=${encodeURIComponent(chokepoint)}&days=${days}`),
    staleTime: ANALYTICS_STALE,
  })
}

export function useCongestion(zone: string, days: number) {
  return useQuery({
    queryKey: ['analytics-congestion', zone, days],
    queryFn: () =>
      getJSON<CongestionResponse>(`/api/analytics/congestion?zone=${encodeURIComponent(zone)}&days=${days}`),
    staleTime: ANALYTICS_STALE,
  })
}

export function useDensity(region: string, days: number) {
  return useQuery({
    queryKey: ['analytics-density', region, days],
    queryFn: () =>
      getJSON<DensityResponse>(`/api/analytics/density?region=${encodeURIComponent(region)}&days=${days}`),
    staleTime: ANALYTICS_STALE,
  })
}

export function useLaden(kind: string) {
  return useQuery({
    queryKey: ['analytics-laden', kind],
    queryFn: () => getJSON<LadenResponse>(`/api/analytics/laden?kind=${encodeURIComponent(kind)}`),
    staleTime: ANALYTICS_STALE,
  })
}

export function useAnalyticsZones() {
  return useQuery({
    queryKey: ['analytics-zones'],
    queryFn: () => getJSON<AnalyticsZone[]>('/api/analytics/zones'),
    staleTime: Infinity,
  })
}

// ---- Events (Phase 3: AIS gaps, loitering, STS) ----

export interface AisEvent {
  event_id: string
  type: 'gap' | 'loiter' | 'sts' | 'reroute' | 'dark_voyage' | 'spoof'
  mmsi: number
  mmsi2: number | null
  start_ts: string
  end_ts: string
  lat: number
  lon: number
  region: string | null
  kind: string | null
  segment: string | null
  details: Record<string, unknown>
  vessel_name: string | null
  vessel2_name: string | null
}

export interface EventsResponse {
  events: AisEvent[]
  total: number
}

const EVENTS_STALE = 2 * 60 * 1000  // 2 min

// ---- Equasis registry data ----

export interface EquasisData {
  imo: number
  ship_name?: string
  flag?: string
  flag_code?: string
  call_sign?: string
  gross_tonnage?: string
  dwt?: string
  ship_type?: string
  year_built?: string
  ship_status?: string
  owner?: string
  ism_manager?: string
  ship_manager?: string
  class_society?: string
  pi_club?: string
  detention_rate_pct?: number
  paris_mou?: string
  tokyo_mou?: string
  uscg_targeting?: string
  risk_score?: number
  risk_indicators?: string[]
  ofac_sanctioned?: boolean
}

// ---- Fleet Explorer (Phase 6) ----

export interface FleetRow {
  imo: number
  ship_name?: string
  flag?: string
  flag_code?: string
  ship_type?: string
  year_built?: number
  gross_tonnage?: number
  dwt?: number
  owner?: string
  ism_manager?: string
  class_society?: string
  pi_club?: string
  detention_rate_pct?: number
  paris_mou?: string
  tokyo_mou?: string
  ship_status?: string
  risk_score?: number
  risk_indicators?: string[]
  ofac_sanctioned?: boolean
  // Live fields (null when not currently tracked)
  mmsi?: number
  live_name?: string
  lat?: number
  lon?: number
  sog?: number
  region?: string
  kind?: string
  segment?: string
}

export interface FleetFacetItem { value: string; count: number }

export interface FleetFacets {
  flags: FleetFacetItem[]
  class_societies: FleetFacetItem[]
  pi_clubs: FleetFacetItem[]
  paris_mou: FleetFacetItem[]
  tokyo_mou: FleetFacetItem[]
  owners: FleetFacetItem[]
}

export interface FleetSummary {
  total: number
  total_dwt?: number
  avg_age_years?: number
  top_flags: FleetFacetItem[]
  top_owners: FleetFacetItem[]
}

export interface FleetResponse {
  total: number
  page: number
  page_size: number
  summary: FleetSummary
  rows: FleetRow[]
}

export interface FleetParams {
  q?: string
  flag?: string
  owner?: string
  class_society?: string
  pi_club?: string
  paris_mou?: string
  tokyo_mou?: string
  kind?: string
  segment?: string
  built_min?: number
  built_max?: number
  dwt_min?: number
  dwt_max?: number
  detention_min?: number
  risk_min?: number
  live_only?: boolean
  sort?: string
  order?: 'asc' | 'desc'
  page?: number
}

function fleetUrl(p: FleetParams): string {
  const q = new URLSearchParams()
  if (p.q) q.set('q', p.q)
  if (p.flag) q.set('flag', p.flag)
  if (p.owner) q.set('owner', p.owner)
  if (p.class_society) q.set('class_society', p.class_society)
  if (p.pi_club) q.set('pi_club', p.pi_club)
  if (p.paris_mou) q.set('paris_mou', p.paris_mou)
  if (p.tokyo_mou) q.set('tokyo_mou', p.tokyo_mou)
  if (p.kind) q.set('kind', p.kind)
  if (p.segment) q.set('segment', p.segment)
  if (p.built_min != null) q.set('built_min', String(p.built_min))
  if (p.built_max != null) q.set('built_max', String(p.built_max))
  if (p.dwt_min != null) q.set('dwt_min', String(p.dwt_min))
  if (p.dwt_max != null) q.set('dwt_max', String(p.dwt_max))
  if (p.detention_min != null) q.set('detention_min', String(p.detention_min))
  if (p.risk_min != null) q.set('risk_min', String(p.risk_min))
  if (p.live_only) q.set('live_only', 'true')
  if (p.sort) q.set('sort', p.sort)
  if (p.order) q.set('order', p.order)
  if (p.page && p.page > 1) q.set('page', String(p.page))
  const s = q.toString()
  return `/api/fleet${s ? `?${s}` : ''}`
}

export function useFleet(params: FleetParams) {
  return useQuery({
    queryKey: ['fleet', params],
    queryFn: () => getJSON<FleetResponse>(fleetUrl(params)),
    staleTime: 2 * 60 * 1000,
    placeholderData: (prev) => prev,
  })
}

export function useFleetFacets() {
  return useQuery({
    queryKey: ['fleet-facets'],
    queryFn: () => getJSON<FleetFacets>('/api/fleet/facets'),
    staleTime: 5 * 60 * 1000,
  })
}

export function fleetExportUrl(params: FleetParams): string {
  const url = fleetUrl(params).replace('/api/fleet', '/api/fleet/export')
  return url
}

export function useEquasis(imo: number | null | undefined) {
  return useQuery({
    queryKey: ['equasis', imo],
    queryFn: () => getJSON<EquasisData>(`/api/vessels/${imo}/equasis`),
    enabled: imo != null,
    staleTime: 12 * 60 * 60 * 1000, // 12h - Equasis data is static
    retry: 1,
  })
}

// ---- Vessel voyages + state (new features) ----

export interface VoyageEvent {
  type: 'port_call' | 'transit' | 'reroute'
  ts: string
  end_ts: string | null
  zone: string | null
  direction: string | null
  laden: boolean | null
  dwell_hours: number | null
  old_destination: string | null
  new_destination: string | null
  lat: number | null
  lon: number | null
  kind: string | null
  segment: string | null
}

export interface VoyagesResponse {
  mmsi: number
  events: VoyageEvent[]
}

export interface VesselStateData {
  mmsi: number
  laden: string | null
  last_draught: number | null
  max_draught_seen: number | null
  updated_ts: string | null
}

export interface PortDestItem {
  destination: string
  count: number
  tankers: number
  bulkers: number
}

export interface PortFlowResponse {
  as_of: string
  total_with_dest: number
  ports: PortDestItem[]
}

export function useVoyages(mmsi: number | null | undefined, days = 14) {
  return useQuery({
    queryKey: ['voyages', mmsi, days],
    queryFn: () => getJSON<VoyagesResponse>(`/api/vessels/${mmsi}/voyages?days=${days}`),
    enabled: mmsi != null,
    staleTime: ANALYTICS_STALE,
  })
}

export function useVesselState(mmsi: number | null | undefined) {
  return useQuery({
    queryKey: ['vessel-state', mmsi],
    queryFn: () => getJSON<VesselStateData | null>(`/api/vessels/${mmsi}/state`),
    enabled: mmsi != null,
    staleTime: ANALYTICS_STALE,
  })
}

export function usePortFlow(kind?: string, topN?: number) {
  const q = new URLSearchParams()
  if (kind) q.set('kind', kind)
  if (topN != null) q.set('top_n', String(topN))
  const qs = q.toString()
  return useQuery({
    queryKey: ['port-flow', kind, topN],
    queryFn: () => getJSON<PortFlowResponse>(`/api/analytics/ports${qs ? '?' + qs : ''}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export interface FlagRiskRow {
  flag: string
  flag_code: string | null
  vessel_count: number
  avg_risk_score: number
  max_risk_score: number
  high_risk_count: number
  ofac_count: number
  paris_mou: string | null
  tokyo_mou: string | null
}

export interface FlagRiskResponse {
  as_of: string
  rows: FlagRiskRow[]
}

export function useFlagRisk(topN = 30) {
  return useQuery({
    queryKey: ['flag-risk', topN],
    queryFn: () => getJSON<FlagRiskResponse>(`/api/fleet/flag-risk?top_n=${topN}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export interface OwnerRiskItem {
  owner: string
  vessel_count: number
  avg_risk_score: number
  max_risk_score: number
  high_risk_count: number
  ofac_count: number
  flags: string[]
}

export interface OwnerRiskResponse {
  as_of: string
  rows: OwnerRiskItem[]
}

export function useOwnerRisk(minVessels = 2, topN = 30) {
  const qs = `min_vessels=${minVessels}&top_n=${topN}`
  return useQuery({
    queryKey: ['owner-risk', qs],
    queryFn: () => getJSON<OwnerRiskResponse>(`/api/fleet/owner-risk?${qs}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export interface SpeedSegmentRow {
  segment: string
  kind: string
  underway: number
  anchored: number
  moored: number
  other: number
  total: number
  avg_sog_underway: number | null
  p50_sog: number | null
  pct_underway: number
}

export interface SpeedAnalyticsResponse {
  as_of: string
  total_vessels: number
  rows: SpeedSegmentRow[]
}

export interface RegionUtilRow {
  region: string
  total: number
  underway: number
  anchored: number
  moored: number
  pct_underway: number
  avg_sog: number | null
}

export interface RegionUtilResponse {
  as_of: string
  rows: RegionUtilRow[]
}

export interface SpeedTrendPoint {
  date: string
  avg_sog: number | null
  underway_count: number
  total_count: number
}

export interface SpeedTrendResponse {
  kind: string
  segment: string | null
  days: number
  series: SpeedTrendPoint[]
}

export function useSpeedTrend(kind: string, segment?: string, days = 14) {
  const qs = new URLSearchParams({ kind, days: String(days) })
  if (segment) qs.set('segment', segment)
  return useQuery({
    queryKey: ['speed-trend', kind, segment ?? '', days],
    queryFn: () => getJSON<SpeedTrendResponse>(`/api/analytics/speed-trend?${qs}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export function useFleetSpeed() {
  return useQuery({
    queryKey: ['fleet-speed'],
    queryFn: () => getJSON<SpeedAnalyticsResponse>('/api/analytics/speed'),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export function useRegionUtil() {
  return useQuery({
    queryKey: ['region-util'],
    queryFn: () => getJSON<RegionUtilResponse>('/api/analytics/region-util'),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface HighRiskPosition {
  mmsi: number
  imo: number
  lat: number
  lon: number
  name: string | null
  segment: string | null
  kind: string | null
  risk_score: number
  ofac_sanctioned: boolean
}

export interface HighRiskPositionsResponse {
  as_of: string
  min_risk: number
  rows: HighRiskPosition[]
}

export function useHighRiskPositions(minRisk = 60, enabled = true) {
  return useQuery({
    queryKey: ['high-risk-positions', minRisk],
    queryFn: () => getJSON<HighRiskPositionsResponse>(`/api/analytics/high-risk-positions?min_risk=${minRisk}`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
    enabled,
  })
}

export function useEvents(params?: { type?: string; days?: number; limit?: number }, enabled = true) {
  const searchParams = new URLSearchParams()
  if (params?.type) searchParams.set('type', params.type)
  if (params?.days) searchParams.set('days', String(params.days))
  if (params?.limit) searchParams.set('limit', String(params.limit))
  const qs = searchParams.toString()
  return useQuery({
    queryKey: ['events', qs],
    queryFn: () => getJSON<EventsResponse>(`/api/events${qs ? '?' + qs : ''}`),
    staleTime: EVENTS_STALE,
    enabled,
  })
}

export interface StsRiskEvent {
  event_id: string
  start_ts: string
  region: string | null
  kind: string | null
  segment: string | null
  mmsi: number
  mmsi2: number | null
  name: string | null
  name2: string | null
  duration_hours: number | null
  co_location_fixes: number | null
  risk_score: number | null
  risk_score2: number | null
  ofac: boolean
  ofac2: boolean
  max_risk: number
}

export interface StsRiskResponse {
  as_of: string
  days: number
  total_events: number
  enriched_events: number
  rows: StsRiskEvent[]
}

export function useStsRisk(days = 30, minRisk = 0) {
  return useQuery({
    queryKey: ['sts-risk', days, minRisk],
    queryFn: () => getJSON<StsRiskResponse>(`/api/analytics/sts-risk?days=${days}&min_risk=${minRisk}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export interface RerouteRiskEvent {
  event_id: string
  start_ts: string
  region: string | null
  kind: string | null
  segment: string | null
  mmsi: number
  name: string | null
  old_destination: string | null
  new_destination: string | null
  fixes_at_old: number | null
  risk_score: number | null
  ofac: boolean
}

export interface RerouteRiskResponse {
  as_of: string
  days: number
  total_events: number
  rows: RerouteRiskEvent[]
}

export function useReroutes(days = 7, minRisk = 0, segment?: string) {
  const qs = new URLSearchParams({ days: String(days), min_risk: String(minRisk) })
  if (segment) qs.set('segment', segment)
  return useQuery({
    queryKey: ['reroutes', days, minRisk, segment ?? ''],
    queryFn: () => getJSON<RerouteRiskResponse>(`/api/analytics/reroutes?${qs}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export interface FleetKPIs {
  as_of: string
  total_registry: number
  scored: number
  elevated: number
  high_risk: number
  critical: number
  ofac_count: number
  avg_risk_score: number | null
  pct_scored: number
}

export function useFleetKPIs() {
  return useQuery({
    queryKey: ['fleet-kpis'],
    queryFn: () => getJSON<FleetKPIs>('/api/fleet/kpis'),
    staleTime: 5 * 60 * 1000,   // KPIs change slowly, 5 min stale
    refetchInterval: 5 * 60 * 1000,
  })
}

