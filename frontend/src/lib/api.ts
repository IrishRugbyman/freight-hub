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
  // Track the last good count so we can detect transient empty responses.
  // When the AIS DB write lock is held longer than the retry window, the API
  // returns HTTP 200 [] instead of an error. TanStack Query accepts [] as valid
  // data and removes all markers. Throwing here converts it to a retriable error;
  // placeholderData keeps the previous vessels visible during the retry window.
  const lastGoodCount = useRef(0)
  return useQuery({
    queryKey: ['vessels', filters],
    queryFn: async () => {
      const data = await getJSON<Vessel[]>(vesselsUrl(filters))
      if (data.length === 0 && lastGoodCount.current > 20) {
        throw new Error('vessel list unexpectedly empty - treating as transient failure')
      }
      if (data.length > 0) lastGoodCount.current = data.length
      return data
    },
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
        // Merge into existing cache rather than replacing. The SSE endpoint uses a
        // 30-minute window for payload efficiency; the REST poll uses 3 hours. If we
        // replaced the full cache with the SSE batch, vessels seen 31-180 min ago
        // would silently disappear from the map until the next 60s poll.
        queryClient.setQueryData(['vessels', filters], (prev: Vessel[] | undefined) => {
          if (!prev || prev.length === 0) return filtered
          const merged = new Map(prev.map((v) => [v.mmsi, v]))
          for (const v of filtered) merged.set(v.mmsi, v)
          return Array.from(merged.values())
        })
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
  type: 'port_call' | 'transit' | 'reroute' | 'cargo_load' | 'cargo_discharge' | 'sts'
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
  draught_before: number | null
  draught_after: number | null
  change_m: number | null
  mmsi2: number | null
  name2: string | null
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

export function useRecentEventCount() {
  return useQuery({
    queryKey: ['events-count-24h'],
    queryFn: () => getJSON<EventsResponse>('/api/events?days=1&limit=200').then(r => r.total),
    staleTime: EVENTS_STALE,
    refetchInterval: 5 * 60_000,
  })
}

export interface AnchoredVessel {
  mmsi: number
  name: string | null
  zone: string
  kind: string | null
  segment: string | null
  start_ts: string
  dwell_hours: number
  laden: string | null
  risk_score: number | null
  ofac: boolean
}

export interface AnchorageDwellResponse {
  as_of: string
  zone: string
  rows: AnchoredVessel[]
}

export function useAnchorageDwell(zone = 'singapore_west', limit = 50) {
  return useQuery({
    queryKey: ['anchorage-dwell', zone, limit],
    queryFn: () => getJSON<AnchorageDwellResponse>(`/api/analytics/anchorage-dwell?zone=${zone}&limit=${limit}`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface CargoTransitionEvent {
  mmsi: number
  name: string | null
  kind: string | null
  segment: string | null
  region: string | null
  direction: 'loading' | 'discharging'
  draught_before: number
  draught_after: number
  change_m: number
  transition_ts: string
  lat: number | null
  lon: number | null
  risk_score: number | null
  ofac: boolean
}

export interface CargoTransitionsResponse {
  as_of: string
  days: number
  min_change: number
  rows: CargoTransitionEvent[]
}

export function useCargoTransitions(days = 7, minChange = 2.0, segment = '') {
  return useQuery({
    queryKey: ['cargo-transitions', days, minChange, segment],
    queryFn: () =>
      getJSON<CargoTransitionsResponse>(
        `/api/analytics/cargo-transitions?days=${days}&min_change=${minChange}${segment ? `&segment=${segment}` : ''}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface FleetUtilizationRow {
  segment: string
  kind: string
  total: number
  underway_count: number
  idle_count: number
  unknown_count: number
  underway_pct: number
  idle_pct: number
  avg_sog_underway: number | null
}

export interface FleetUtilizationResponse {
  as_of: string
  total_fleet: number
  rows: FleetUtilizationRow[]
}

export function useFleetUtilization() {
  return useQuery({
    queryKey: ['fleet-utilization'],
    queryFn: () => getJSON<FleetUtilizationResponse>('/api/analytics/fleet-utilization'),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface SlowSteamerEvent {
  mmsi: number
  name: string | null
  kind: string | null
  segment: string | null
  region: string | null
  sog: number
  segment_median_sog: number
  pct_of_median: number
  risk_score: number | null
  ofac: boolean
}

export interface SlowSteamersResponse {
  as_of: string
  total_fleet_underway: number
  rows: SlowSteamerEvent[]
}

export function useSlowSteamers(kind = '') {
  return useQuery({
    queryKey: ['slow-steamers', kind],
    queryFn: () =>
      getJSON<SlowSteamersResponse>(`/api/analytics/slow-steamers${kind ? `?kind=${kind}` : ''}`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface FleetAgeBand {
  age_band: string
  vessel_count: number
  avg_risk_score: number | null
  high_risk_count: number
  avg_dwt: number | null
}

export interface FleetAgeResponse {
  as_of: string
  reference_year: number
  bands: FleetAgeBand[]
}

export function useFleetAge() {
  return useQuery({
    queryKey: ['fleet-age'],
    queryFn: () => getJSON<FleetAgeResponse>('/api/fleet/age'),
    staleTime: 10 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })
}

export interface TransitRiskEvent {
  mmsi: number
  name: string | null
  imo: number | null
  chokepoint: string
  entered_ts: string
  exited_ts: string | null
  direction: string | null
  kind: string | null
  segment: string | null
  laden: boolean | null
  risk_score: number | null
  ofac: boolean
}

export interface TransitRiskResponse {
  as_of: string
  days: number
  chokepoint: string
  total_transits: number
  enriched: number
  rows: TransitRiskEvent[]
}

export function useTransitRisk(chokepoint = 'hormuz', days = 30, minRisk = 0) {
  return useQuery({
    queryKey: ['transit-risk', chokepoint, days, minRisk],
    queryFn: () => getJSON<TransitRiskResponse>(`/api/analytics/transit-risk?chokepoint=${chokepoint}&days=${days}&min_risk=${minRisk}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
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
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface RiskEventItem {
  event_id: string
  event_type: string
  event_ts: string
  mmsi: number
  name: string | null
  imo: number | null
  risk_score: number | null
  ofac: boolean
  mmsi2: number | null
  name2: string | null
  imo2: number | null
  risk_score2: number | null
  ofac2: boolean
  max_risk: number
  region: string | null
  kind: string | null
  segment: string | null
  lat: number | null
  lon: number | null
  old_destination: string | null
  new_destination: string | null
}

export interface RiskEventsResponse {
  as_of: string
  min_risk: number
  days: number
  total_high_risk_vessels: number
  rows: RiskEventItem[]
}

export function useRiskEvents(minRisk = 25, days = 2) {
  return useQuery({
    queryKey: ['risk-events', minRisk, days],
    queryFn: () => getJSON<RiskEventsResponse>(`/api/analytics/risk-events?min_risk=${minRisk}&days=${days}&limit=100`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface MarketSegmentSummary {
  segment: string
  kind: string
  total: number
  laden: number
  ballast: number
  unknown: number
  laden_pct: number
  underway_pct: number
}

export interface MarketSummaryResponse {
  as_of: string
  total_fleet: number
  total_laden: number
  total_ballast: number
  laden_pct: number
  transits_24h: number
  reroutes_24h: number
  sts_24h: number
  gaps_24h: number
  by_segment: MarketSegmentSummary[]
}

export function useMarketSummary() {
  return useQuery({
    queryKey: ['market-summary'],
    queryFn: () => getJSON<MarketSummaryResponse>('/api/analytics/market-summary'),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface DestinationFlowRow {
  origin_region: string
  destination: string
  segment: string | null
  kind: string | null
  vessel_count: number
}

export interface DestinationFlowsResponse {
  as_of: string
  laden_only: boolean
  total_laden: number
  rows: DestinationFlowRow[]
}

export function useDestinationFlows(kind = '', segment = '', region = '', ladenOnly = true) {
  return useQuery({
    queryKey: ['destination-flows', kind, segment, region, ladenOnly],
    queryFn: () => getJSON<DestinationFlowsResponse>(
      `/api/analytics/destination-flows?kind=${kind}&segment=${segment}&region=${region}&laden_only=${ladenOnly}&top_n=30`
    ),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface PortCongestionRow {
  zone: string
  region: string | null
  kind: string | null
  current_vessels: number
  avg_current_dwell_hours: number | null
  baseline_avg_vessels: number | null
  baseline_avg_dwell_hours: number | null
  congestion_factor: number
}

export interface PortCongestionResponse {
  as_of: string
  days_baseline: number
  rows: PortCongestionRow[]
}

export function usePortCongestion(kind = '', days = 14) {
  return useQuery({
    queryKey: ['port-congestion', kind, days],
    queryFn: () => getJSON<PortCongestionResponse>(`/api/analytics/port-congestion?kind=${kind}&days=${days}`),
    staleTime: 3 * 60 * 1000,
    refetchInterval: 3 * 60 * 1000,
  })
}


export interface ChokepointHeatmapCell {
  date: string
  chokepoint: string
  total: number
  tanker: number
  bulk: number
}

export interface ChokepointHeatmapResponse {
  as_of: string
  days: number
  kind: string
  chokepoints: string[]
  cells: ChokepointHeatmapCell[]
}

export function useChokepointHeatmap(days = 30, kind = '') {
  return useQuery({
    queryKey: ['chokepoint-heatmap', days, kind],
    queryFn: () =>
      getJSON<ChokepointHeatmapResponse>(
        `/api/analytics/chokepoint-heatmap?days=${days}&kind=${kind}`
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface VesselRiskRow {
  mmsi: number
  imo: number | null
  name: string | null
  kind: string | null
  segment: string | null
  region: string | null
  lat: number | null
  lon: number | null
  sts_count: number
  reroute_count: number
  registry_risk: number | null
  ofac: boolean
  behavioral_score: number
  total_score: number
}

export interface VesselRiskResponse {
  as_of: string
  days: number
  top_n: number
  total_candidates: number
  rows: VesselRiskRow[]
}

export function useVesselRiskScores(topN = 50, days = 30, segment = '', kind = '', minScore = 5) {
  return useQuery({
    queryKey: ['vessel-risk-scores', topN, days, segment, kind, minScore],
    queryFn: () =>
      getJSON<VesselRiskResponse>(
        `/api/analytics/vessel-risk-scores?top_n=${topN}&days=${days}&segment=${segment}&kind=${kind}&min_score=${minScore}`
      ),
    staleTime: 2 * 60 * 1000,
    refetchInterval: 2 * 60 * 1000,
  })
}

export interface TradeLaneCell {
  origin_region: string
  dest_region: string
  vessel_count: number
  high_risk_count: number
  laden_count: number
}

export interface TradeLaneMatrixResponse {
  as_of: string
  kind: string
  laden_only: boolean
  origin_regions: string[]
  dest_regions: string[]
  cells: TradeLaneCell[]
}

export function useTradeLaneMatrix(kind = '', ladenOnly = true) {
  return useQuery({
    queryKey: ['trade-lane-matrix', kind, ladenOnly],
    queryFn: () =>
      getJSON<TradeLaneMatrixResponse>(
        `/api/analytics/trade-lane-matrix?kind=${kind}&laden_only=${ladenOnly}`
      ),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export interface RecentEvent {
  type: string
  ts: string
  lat: number | null
  lon: number | null
  old_destination?: string
  new_destination?: string
}

export interface VesselBehavioralRisk {
  mmsi: number
  imo: number | null
  sts_count: number
  reroute_count: number
  days: number
  behavioral_score: number
  registry_risk: number | null
  ofac: boolean
  total_score: number
  risk_level: 'Low' | 'Elevated' | 'High' | 'Critical'
  recent_events: RecentEvent[]
}

export function useVesselBehavioralRisk(mmsi: number | null | undefined, days = 30) {
  return useQuery({
    queryKey: ['vessel-behavioral-risk', mmsi, days],
    queryFn: () => getJSON<VesselBehavioralRisk>(`/api/vessels/${mmsi}/behavioral-risk?days=${days}`),
    enabled: mmsi != null,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface AnomalyWatchlistItem {
  mmsi: number
  imo: number | null
  name: string | null
  kind: string | null
  segment: string | null
  region: string | null
  lat: number | null
  lon: number | null
  sog: number | null
  destination: string | null
  laden: string | null
  total_score: number
  behavioral_score: number
  registry_risk: number | null
  ofac: boolean
  risk_level: 'Low' | 'Elevated' | 'High' | 'Critical'
  sts_count_7d: number
  reroute_count_7d: number
  signals: string[]
}

export interface AnomalyWatchlistResponse {
  as_of: string
  min_score: number
  total_flagged: number
  rows: AnomalyWatchlistItem[]
}

export function useAnomalyWatchlist(minScore = 50, limit = 30) {
  return useQuery({
    queryKey: ['anomaly-watchlist', minScore, limit],
    queryFn: () =>
      getJSON<AnomalyWatchlistResponse>(
        `/api/analytics/anomaly-watchlist?min_score=${minScore}&limit=${limit}`
      ),
    staleTime: 2 * 60 * 1000,
    refetchInterval: 2 * 60 * 1000,
  })
}

export interface StsProximityPair {
  mmsi_a: number
  name_a: string | null
  imo_a: number | null
  kind_a: string | null
  segment_a: string | null
  sog_a: number | null
  mmsi_b: number
  name_b: string | null
  imo_b: number | null
  kind_b: string | null
  segment_b: string | null
  sog_b: number | null
  dist_m: number
  lat: number
  lon: number
  region: string | null
  risk_region: boolean
}

export interface StsProximityResponse {
  as_of: string
  max_dist_m: number
  max_sog: number
  total_pairs: number
  pairs: StsProximityPair[]
}

export function useStsProximity(maxDistM = 2000, maxSog = 3.0) {
  return useQuery({
    queryKey: ['sts-proximity', maxDistM, maxSog],
    queryFn: () =>
      getJSON<StsProximityResponse>(
        `/api/analytics/sts-proximity?max_dist_m=${maxDistM}&max_sog=${maxSog}`,
      ),
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
  })
}

export interface RegionMomentumRow {
  region: string
  current_total: number
  prev_total: number
  delta: number
  laden_count: number
  ballast_count: number
  laden_ratio_pct: number
}

export interface RegionMomentumResponse {
  as_of: string
  hours_back: number
  rows: RegionMomentumRow[]
}

export function useRegionMomentum(hoursBack = 24) {
  return useQuery({
    queryKey: ['region-momentum', hoursBack],
    queryFn: () =>
      getJSON<RegionMomentumResponse>(`/api/analytics/region-momentum?hours_back=${hoursBack}`),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface EventRatePoint {
  hour: string
  reroute_count: number
  sts_count: number
  total_count: number
}

export interface EventRateTimelineResponse {
  as_of: string
  hours: number
  points: EventRatePoint[]
}

export function useEventRateTimeline(hours = 72) {
  return useQuery({
    queryKey: ['event-rate-timeline', hours],
    queryFn: () =>
      getJSON<EventRateTimelineResponse>(`/api/analytics/event-rate-timeline?hours=${hours}`),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface TransitRatePoint {
  hour: string
  chokepoint: string
  count: number
  laden_count: number
}

export interface TransitRateTimelineResponse {
  as_of: string
  hours: number
  chokepoints: string[]
  points: TransitRatePoint[]
}

export function useTransitRateTimeline(hours = 72, chopointsCSV = '') {
  return useQuery({
    queryKey: ['transit-rate-timeline', hours, chopointsCSV],
    queryFn: () =>
      getJSON<TransitRateTimelineResponse>(
        `/api/analytics/transit-rate-timeline?hours=${hours}&chokepoints_csv=${encodeURIComponent(chopointsCSV)}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface AnchorageOccupancyPoint {
  hour: string
  zone: string
  vessel_count: number
}

export interface AnchorageOccupancyResponse {
  as_of: string
  hours: number
  zones: string[]
  points: AnchorageOccupancyPoint[]
}

export function useAnchorageOccupancy(hours = 72, zonesCSV = '') {
  return useQuery({
    queryKey: ['anchorage-occupancy', hours, zonesCSV],
    queryFn: () =>
      getJSON<AnchorageOccupancyResponse>(
        `/api/analytics/anchorage-occupancy?hours=${hours}&zones_csv=${encodeURIComponent(zonesCSV)}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface StsOffenderRow {
  mmsi: number
  name: string | null
  imo: number | null
  kind: string | null
  segment: string | null
  region: string | null
  lat: number | null
  lon: number | null
  sog: number | null
  sts_events: number
  as_initiator: number
  as_counterpart: number
  registry_risk: number | null
  ofac: boolean
}

export interface StsOffendersResponse {
  as_of: string
  days: number
  total_vessels: number
  rows: StsOffenderRow[]
}

export function useStsOffenders(days = 30, limit = 50) {
  return useQuery({
    queryKey: ['sts-offenders', days, limit],
    queryFn: () =>
      getJSON<StsOffendersResponse>(`/api/analytics/sts-offenders?days=${days}&limit=${limit}`),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface FleetHistorySegmentRow {
  kind: string
  segment: string
  count: number
  laden: number
  ballast: number
  underway: number
  avg_sog: number | null
}

export interface FleetHistoryResponse {
  queried_ts: string
  actual_ts: string
  region: string | null
  total_vessels: number
  segments: FleetHistorySegmentRow[]
}

export function useFleetAtTime(ts = '', region = '') {
  return useQuery({
    queryKey: ['fleet-at-time', ts, region],
    queryFn: () =>
      getJSON<FleetHistoryResponse>(
        `/api/analytics/fleet-at-time?ts=${encodeURIComponent(ts)}&region=${encodeURIComponent(region)}`,
      ),
    staleTime: 10 * 60 * 1000,
    refetchInterval: false,
  })
}

export interface DestinationChangeRow {
  mmsi: number
  name: string | null
  kind: string | null
  segment: string | null
  region: string | null
  lat: number | null
  lon: number | null
  changed_ts: string
  from_dest: string
  to_dest: string
  hours_ago: number
}

export interface DestinationChangesResponse {
  as_of: string
  hours: number
  total_changes: number
  rows: DestinationChangeRow[]
}

export function useDestinationChanges(hours = 72, kind = '') {
  return useQuery({
    queryKey: ['destination-changes', hours, kind],
    queryFn: () =>
      getJSON<DestinationChangesResponse>(
        `/api/analytics/destination-changes?hours=${hours}&kind=${encodeURIComponent(kind)}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface OwnerIntelRow {
  owner: string
  vessel_count: number
  risk_weighted: number
  avg_risk: number | null
  max_risk: number | null
  high_risk_count: number
  tanker_count: number
  bulk_count: number
  flags: string[]
  top_segment: string | null
}

export interface OwnerIntelResponse {
  as_of: string
  total_owners: number
  rows: OwnerIntelRow[]
}

export function useOwnerIntelligence(minVessels = 2, limit = 50) {
  return useQuery({
    queryKey: ['owner-intelligence', minVessels, limit],
    queryFn: () =>
      getJSON<OwnerIntelResponse>(
        `/api/analytics/owner-intelligence?min_vessels=${minVessels}&limit=${limit}`,
      ),
    staleTime: 10 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })
}

export interface ChokepointAnomalyRow {
  chokepoint: string
  recent_count: number
  baseline_avg: number | null
  baseline_std: number | null
  z_score: number | null
  pct_change: number | null
  direction: string
  window_hours: number
  baseline_hours: number
}

export interface ChokepointAnomalyResponse {
  as_of: string
  window_hours: number
  baseline_hours: number
  rows: ChokepointAnomalyRow[]
}

export function useChokepointAnomaly(windowHours = 6, baselineHours = 48) {
  return useQuery({
    queryKey: ['chokepoint-anomaly', windowHours, baselineHours],
    queryFn: () =>
      getJSON<ChokepointAnomalyResponse>(
        `/api/analytics/chokepoint-anomaly?window_hours=${windowHours}&baseline_hours=${baselineHours}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export interface CargoStateChangeRow {
  mmsi: number
  name: string | null
  imo: number | null
  kind: string | null
  segment: string | null
  zone: string
  region: string | null
  start_ts: string
  end_ts: string
  dwell_hours: number
  draught_entry: number | null
  draught_exit: number | null
  draught_change_m: number | null
  cargo_state: string
  lat: number | null
  lon: number | null
  registry_risk: number | null
}

export interface CargoStateChangesResponse {
  as_of: string
  days: number
  total_events: number
  rows: CargoStateChangeRow[]
}

export function useCargoStateChanges(days = 7, kind = 'tanker', minChangeM = 1.5) {
  return useQuery({
    queryKey: ['cargo-state-changes', days, kind, minChangeM],
    queryFn: () =>
      getJSON<CargoStateChangesResponse>(
        `/api/analytics/cargo-state-changes?days=${days}&kind=${encodeURIComponent(kind)}&min_change_m=${minChangeM}`,
      ),
    staleTime: 10 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })
}

// Phase 46: Speed Anomaly Detection
export interface SpeedAnomalyRow {
  mmsi: number
  imo: number | null
  name: string | null
  kind: string | null
  segment: string | null
  region: string | null
  lat: number | null
  lon: number | null
  sog: number
  segment_median_sog: number
  z_score: number
  anomaly_type: 'fast' | 'slow'
  destination: string | null
  nav_status: number | null
  registry_risk: number | null
}

export interface SpeedAnomalyResponse {
  as_of: string
  total_vessels_checked: number
  anomaly_count: number
  rows: SpeedAnomalyRow[]
}

export function useSpeedAnomalies(kind = 'tanker', minZ = 2.5, limit = 50) {
  return useQuery({
    queryKey: ['speed-anomalies', kind, minZ, limit],
    queryFn: () =>
      getJSON<SpeedAnomalyResponse>(
        `/api/analytics/speed-anomalies?kind=${encodeURIComponent(kind)}&min_z=${minZ}&limit=${limit}`,
      ),
    staleTime: 3 * 60 * 1000,
    refetchInterval: 3 * 60 * 1000,
  })
}

// Phase 47: 48h Port Arrival Forecast
export interface ArrivalVessel {
  mmsi: number
  name: string | null
  segment: string | null
  kind: string | null
  laden: string | null
  eta_hours: number
  distance_nm: number
  sog: number
  destination_raw: string | null
  registry_risk: number | null
}

export interface PortArrivalForecast {
  port: string
  arrivals_24h: number
  arrivals_48h: number
  vessels: ArrivalVessel[]
}

export interface PortArrivalResponse {
  as_of: string
  total_inbound: number
  ports: PortArrivalForecast[]
}

export function usePortArrivals(kind = 'tanker', horizonH = 48) {
  return useQuery({
    queryKey: ['port-arrivals', kind, horizonH],
    queryFn: () =>
      getJSON<PortArrivalResponse>(
        `/api/analytics/port-arrivals?kind=${encodeURIComponent(kind)}&horizon_h=${horizonH}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

// Phase 48: Crude Oil on Water
export interface CrudeSegmentRow {
  segment: string
  laden_count: number
  ballast_count: number
  unknown_count: number
  estimated_mb: number
}

export interface InboundRegionRow {
  region: string
  vessel_count: number
  estimated_mb: number
  top_segments: string[]
}

export interface CrudeOnWaterResponse {
  as_of: string
  total_laden_tankers: number
  total_ballast_tankers: number
  estimated_mb_on_water: number
  by_segment: CrudeSegmentRow[]
  inbound_regions: InboundRegionRow[]
}

export function useCrudeOnWater() {
  return useQuery({
    queryKey: ['crude-on-water'],
    queryFn: () => getJSON<CrudeOnWaterResponse>('/api/analytics/crude-on-water'),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

// Phase 49: Chokepoint Live Status
export interface ChokepointStatusRow {
  chokepoint: string
  live_total: number
  live_transiting: number
  live_waiting: number
  avg_transit_h_7d: number | null
  n_transits_24h: number
  n_transits_7d: number
  pct_fwd_direction: number | null
}

export interface ChokepointStatusResponse {
  as_of: string
  rows: ChokepointStatusRow[]
}

export function useChokepointStatus() {
  return useQuery({
    queryKey: ['chokepoint-status'],
    queryFn: () => getJSON<ChokepointStatusResponse>('/api/analytics/chokepoint-status'),
    staleTime: 2 * 60 * 1000,
    refetchInterval: 2 * 60 * 1000,
  })
}

// ---- Fleet trend (Phase 51) ----

export interface FleetTrendDay {
  date: string
  laden: number
  ballast: number
  unknown: number
  total: number
}

export interface FleetTrendResponse {
  as_of: string
  days: number
  region: string | null
  series: FleetTrendDay[]
}

export function useFleetTrend(days = 30, region?: string) {
  const qs = new URLSearchParams({ days: String(days) })
  if (region) qs.set('region', region)
  return useQuery({
    queryKey: ['fleet-trend', days, region ?? ''],
    queryFn: () => getJSON<FleetTrendResponse>(`/api/analytics/fleet-trend?${qs}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: 5 * 60_000,
  })
}
