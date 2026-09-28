import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import type { components } from './api-schema.gen'

/** Response models, generated from the backend's OpenAPI schema (`npm run gen:api`). */
type Schemas = components['schemas']

// ---- Routes (transport-arb) ----

export type RouteResult = Schemas['RouteResult']

export type ArbMatrixCell = Schemas['ArbMatrixCell']

export type BwetInfo = Schemas['BwetInfo']

export type RoutesResponse = Schemas['RoutesResponse']

// ---- Dispersion (freight-dispersion) ----

export type DispersionStats = Schemas['DispersionStats']

export type DispersionPoint = Schemas['DispersionPoint']

export type DispersionResponse = Schemas['DispersionResponse']

export type AisDispersionRow = Schemas['AisDispersionRow']

export type Vessel = Schemas['Vessel']

export type ChokepointCount = Schemas['ChokepointCount']

export type FeedStatus = Schemas['FeedStatus']

export type Meta = Schemas['Meta']

export interface VesselFilters {
  kind?: string
  segment?: string
  region?: string
  flag?: string
  foc?: boolean
  shadow?: boolean
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
  if (f.flag) q.set('flag', f.flag)
  if (f.foc) q.set('foc', 'true')
  if (f.shadow) q.set('shadow', 'true')
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

export type TrackPoint = Schemas['TrackPoint']

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

export type TransitDay = Schemas['TransitDay']

export type TransitsResponse = Schemas['TransitsResponse']

export type CongestionDay = Schemas['CongestionDay']

export type CongestionResponse = Schemas['CongestionResponse']

export type DensityDay = Schemas['DensityDay']

export type DensityResponse = Schemas['DensityResponse']

export type LadenSegment = Schemas['LadenSegment']

export type LadenResponse = Schemas['LadenResponse']

export type AnalyticsZone = Schemas['AnalyticsZone']

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

export type AisEvent = Schemas['AisEvent']

export type EventsResponse = Schemas['EventsResponse']

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

export type FleetRow = Schemas['FleetRow']

export type FleetFacetItem = Schemas['FleetFacetItem']

export type FleetFacets = Schemas['FleetFacets']

export type FleetSummary = Schemas['FleetSummary']

export type FleetResponse = Schemas['FleetResponse']

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

export type VoyageEvent = Schemas['VoyageEvent']

export type VoyagesResponse = Schemas['VoyagesResponse']

export type VesselStateData = Schemas['VesselStateData']

export type PortDestItem = Schemas['PortDestItem']

export type PortFlowResponse = Schemas['PortFlowResponse']

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

export type ArrivalTarget = Schemas['ArrivalTarget']

export type ArrivalsResponse = Schemas['ArrivalsResponse']

export function useArrivals(days = 14, targetType = 'all', topN = 20) {
  const q = new URLSearchParams({ days: String(days), target_type: targetType, top_n: String(topN) })
  return useQuery({
    queryKey: ['arrivals', days, targetType, topN],
    queryFn: () => getJSON<ArrivalsResponse>(`/api/analytics/arrivals?${q.toString()}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export type FlagRiskRow = Schemas['FlagRiskRow']

export type FlagRiskResponse = Schemas['FlagRiskResponse']

export type FleetFlagRow = Schemas['FleetFlagRow']

export type FleetFlagsResponse = Schemas['FleetFlagsResponse']

export type FlagMismatchRow = Schemas['FlagMismatchRow']

export type FlagMismatchResponse = Schemas['FlagMismatchResponse']

export function useFlagRisk(topN = 30) {
  return useQuery({
    queryKey: ['flag-risk', topN],
    queryFn: () => getJSON<FlagRiskResponse>(`/api/fleet/flag-risk?top_n=${topN}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export function useFleetFlags(topN = 40) {
  return useQuery({
    queryKey: ['fleet-flags', topN],
    queryFn: () => getJSON<FleetFlagsResponse>(`/api/analytics/fleet-flags?top_n=${topN}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export function useFlagMismatches() {
  return useQuery({
    queryKey: ['flag-mismatches'],
    queryFn: () => getJSON<FlagMismatchResponse>('/api/analytics/flag-mismatches'),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export type OwnerRiskItem = Schemas['OwnerRiskItem']

export type OwnerRiskResponse = Schemas['OwnerRiskResponse']

export function useOwnerRisk(minVessels = 2, topN = 30) {
  const qs = `min_vessels=${minVessels}&top_n=${topN}`
  return useQuery({
    queryKey: ['owner-risk', qs],
    queryFn: () => getJSON<OwnerRiskResponse>(`/api/fleet/owner-risk?${qs}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export type SpeedSegmentRow = Schemas['SpeedSegmentRow']

export type SpeedAnalyticsResponse = Schemas['SpeedAnalyticsResponse']

export type RegionUtilRow = Schemas['RegionUtilRow']

export type RegionUtilResponse = Schemas['RegionUtilResponse']

export type SpeedTrendPoint = Schemas['SpeedTrendPoint']

export type SpeedTrendResponse = Schemas['SpeedTrendResponse']

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

export type HighRiskPosition = Schemas['HighRiskPosition']

export type HighRiskPositionsResponse = Schemas['HighRiskPositionsResponse']

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

export type AnchoredVessel = Schemas['AnchoredVessel']

export type AnchorageDwellResponse = Schemas['AnchorageDwellResponse']

export function useAnchorageDwell(zone = 'singapore_west', limit = 50) {
  return useQuery({
    queryKey: ['anchorage-dwell', zone, limit],
    queryFn: () => getJSON<AnchorageDwellResponse>(`/api/analytics/anchorage-dwell?zone=${zone}&limit=${limit}`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export type CargoTransitionEvent = Schemas['CargoTransitionEvent']

export type CargoTransitionsResponse = Schemas['CargoTransitionsResponse']

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

export type FleetUtilizationRow = Schemas['FleetUtilizationRow']

export type FleetUtilizationResponse = Schemas['FleetUtilizationResponse']

export function useFleetUtilization() {
  return useQuery({
    queryKey: ['fleet-utilization'],
    queryFn: () => getJSON<FleetUtilizationResponse>('/api/analytics/fleet-utilization'),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export type SlowSteamerEvent = Schemas['SlowSteamerEvent']

export type SlowSteamersResponse = Schemas['SlowSteamersResponse']

export function useSlowSteamers(kind = '') {
  return useQuery({
    queryKey: ['slow-steamers', kind],
    queryFn: () =>
      getJSON<SlowSteamersResponse>(`/api/analytics/slow-steamers${kind ? `?kind=${kind}` : ''}`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export type FleetAgeBand = Schemas['FleetAgeBand']

export type FleetAgeResponse = Schemas['FleetAgeResponse']

export function useFleetAge() {
  return useQuery({
    queryKey: ['fleet-age'],
    queryFn: () => getJSON<FleetAgeResponse>('/api/fleet/age'),
    staleTime: 10 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })
}

export type TransitRiskEvent = Schemas['TransitRiskEvent']

export type TransitRiskResponse = Schemas['TransitRiskResponse']

export function useTransitRisk(chokepoint = 'hormuz', days = 30, minRisk = 0) {
  return useQuery({
    queryKey: ['transit-risk', chokepoint, days, minRisk],
    queryFn: () => getJSON<TransitRiskResponse>(`/api/analytics/transit-risk?chokepoint=${chokepoint}&days=${days}&min_risk=${minRisk}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export type StsRiskEvent = Schemas['StsRiskEvent']

export type StsRiskResponse = Schemas['StsRiskResponse']

export function useStsRisk(days = 30, minRisk = 0) {
  return useQuery({
    queryKey: ['sts-risk', days, minRisk],
    queryFn: () => getJSON<StsRiskResponse>(`/api/analytics/sts-risk?days=${days}&min_risk=${minRisk}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: REFETCH_MS,
  })
}

export type RerouteRiskEvent = Schemas['RerouteRiskEvent']

export type RerouteRiskResponse = Schemas['RerouteRiskResponse']

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

export type FleetKPIs = Schemas['FleetKPIs']

export function useFleetKPIs() {
  return useQuery({
    queryKey: ['fleet-kpis'],
    queryFn: () => getJSON<FleetKPIs>('/api/fleet/kpis'),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export type RiskEventItem = Schemas['RiskEventItem']

export type RiskEventsResponse = Schemas['RiskEventsResponse']

export function useRiskEvents(minRisk = 25, days = 2) {
  return useQuery({
    queryKey: ['risk-events', minRisk, days],
    queryFn: () => getJSON<RiskEventsResponse>(`/api/analytics/risk-events?min_risk=${minRisk}&days=${days}&limit=100`),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export type MarketSegmentSummary = Schemas['MarketSegmentSummary']

export type MarketSummaryResponse = Schemas['MarketSummaryResponse']

export function useMarketSummary() {
  return useQuery({
    queryKey: ['market-summary'],
    queryFn: () => getJSON<MarketSummaryResponse>('/api/analytics/market-summary'),
    staleTime: REFETCH_MS,
    refetchInterval: REFETCH_MS,
  })
}

export type DestinationFlowRow = Schemas['DestinationFlowRow']

export type DestinationFlowsResponse = Schemas['DestinationFlowsResponse']

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

export type PortCongestionRow = Schemas['PortCongestionRow']

export type PortCongestionResponse = Schemas['PortCongestionResponse']

export function usePortCongestion(kind = '', days = 14) {
  return useQuery({
    queryKey: ['port-congestion', kind, days],
    queryFn: () => getJSON<PortCongestionResponse>(`/api/analytics/port-congestion?kind=${kind}&days=${days}`),
    staleTime: 3 * 60 * 1000,
    refetchInterval: 3 * 60 * 1000,
  })
}

export type ChokepointCongestionRow = Schemas['ChokepointCongestionRow']

export type ChokepointCongestionResponse = Schemas['ChokepointCongestionResponse']

export function useChokepointCongestion(kind = '', days = 14) {
  return useQuery({
    queryKey: ['chokepoint-congestion', kind, days],
    queryFn: () => getJSON<ChokepointCongestionResponse>(`/api/analytics/chokepoint-congestion?kind=${kind}&days=${days}`),
    staleTime: 3 * 60 * 1000,
    refetchInterval: 3 * 60 * 1000,
  })
}


export type ChokepointHeatmapCell = Schemas['ChokepointHeatmapCell']

export type ChokepointHeatmapResponse = Schemas['ChokepointHeatmapResponse']

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

export type VesselRiskRow = Schemas['VesselRiskRow']

export type VesselRiskResponse = Schemas['VesselRiskResponse']

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

export type TradeLaneCell = Schemas['TradeLaneCell']

export type TradeLaneMatrixResponse = Schemas['TradeLaneMatrixResponse']

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

export type VesselBehavioralRisk = Schemas['VesselBehavioralRisk']

export function useVesselBehavioralRisk(mmsi: number | null | undefined, days = 30) {
  return useQuery({
    queryKey: ['vessel-behavioral-risk', mmsi, days],
    queryFn: () => getJSON<VesselBehavioralRisk>(`/api/vessels/${mmsi}/behavioral-risk?days=${days}`),
    enabled: mmsi != null,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export type AnomalyWatchlistItem = Schemas['AnomalyWatchlistItem']

export type AnomalyWatchlistResponse = Schemas['AnomalyWatchlistResponse']

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

export type StsProximityPair = Schemas['StsProximityPair']

export type StsProximityResponse = Schemas['StsProximityResponse']

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

export type RegionMomentumRow = Schemas['RegionMomentumRow']

export type RegionMomentumResponse = Schemas['RegionMomentumResponse']

export function useRegionMomentum(hoursBack = 24, oceanOnly = true) {
  return useQuery({
    queryKey: ['region-momentum', hoursBack, oceanOnly],
    queryFn: () =>
      getJSON<RegionMomentumResponse>(
        `/api/analytics/region-momentum?hours_back=${hoursBack}&ocean_only=${oceanOnly}`,
      ),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export type EventRatePoint = Schemas['EventRatePoint']

export type EventRateTimelineResponse = Schemas['EventRateTimelineResponse']

export function useEventRateTimeline(hours = 72) {
  return useQuery({
    queryKey: ['event-rate-timeline', hours],
    queryFn: () =>
      getJSON<EventRateTimelineResponse>(`/api/analytics/event-rate-timeline?hours=${hours}`),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export type TransitRatePoint = Schemas['TransitRatePoint']

export type TransitRateTimelineResponse = Schemas['TransitRateTimelineResponse']

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

export type AnchorageOccupancyPoint = Schemas['AnchorageOccupancyPoint']

export type AnchorageOccupancyResponse = Schemas['AnchorageOccupancyResponse']

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

export type StsOffenderRow = Schemas['StsOffenderRow']

export type StsOffendersResponse = Schemas['StsOffendersResponse']

export function useStsOffenders(days = 30, limit = 50) {
  return useQuery({
    queryKey: ['sts-offenders', days, limit],
    queryFn: () =>
      getJSON<StsOffendersResponse>(`/api/analytics/sts-offenders?days=${days}&limit=${limit}`),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

export type FleetHistorySegmentRow = Schemas['FleetHistorySegmentRow']

export type FleetHistoryResponse = Schemas['FleetHistoryResponse']

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

export type DestinationChangeRow = Schemas['DestinationChangeRow']

export type DestinationChangesResponse = Schemas['DestinationChangesResponse']

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

export type OwnerIntelRow = Schemas['OwnerIntelRow']

export type OwnerIntelResponse = Schemas['OwnerIntelResponse']

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

export type ChokepointAnomalyRow = Schemas['ChokepointAnomalyRow']

export type ChokepointAnomalyResponse = Schemas['ChokepointAnomalyResponse']

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

export type CargoStateChangeRow = Schemas['CargoStateChangeRow']

export type CargoStateChangesResponse = Schemas['CargoStateChangesResponse']

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
export type SpeedAnomalyRow = Schemas['SpeedAnomalyRow']

export type SpeedAnomalyResponse = Schemas['SpeedAnomalyResponse']

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
export type ArrivalVessel = Schemas['ArrivalVessel']

export type PortArrivalForecast = Schemas['PortArrivalForecast']

export type PortArrivalResponse = Schemas['PortArrivalResponse']

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
export type CrudeSegmentRow = Schemas['CrudeSegmentRow']

export type InboundRegionRow = Schemas['InboundRegionRow']

export type CrudeOnWaterResponse = Schemas['CrudeOnWaterResponse']

export function useCrudeOnWater() {
  return useQuery({
    queryKey: ['crude-on-water'],
    queryFn: () => getJSON<CrudeOnWaterResponse>('/api/analytics/crude-on-water'),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}

// Phase 49: Chokepoint Live Status
export type ChokepointStatusRow = Schemas['ChokepointStatusRow']

export type ChokepointStatusResponse = Schemas['ChokepointStatusResponse']

export function useChokepointStatus() {
  return useQuery({
    queryKey: ['chokepoint-status'],
    queryFn: () => getJSON<ChokepointStatusResponse>('/api/analytics/chokepoint-status'),
    staleTime: 2 * 60 * 1000,
    refetchInterval: 2 * 60 * 1000,
  })
}

// ---- Fleet trend (Phase 51) ----

export type FleetTrendDay = Schemas['FleetTrendDay']

export type FleetTrendResponse = Schemas['FleetTrendResponse']

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

// ---- Shadow fleet monitor (Phase 52) ----

export type ShadowFleetRow = Schemas['ShadowFleetRow']

export type ShadowFleetResponse = Schemas['ShadowFleetResponse']

export function useShadowFleet(days = 7, limit = 50) {
  return useQuery({
    queryKey: ['shadow-fleet', days, limit],
    queryFn: () => getJSON<ShadowFleetResponse>(`/api/analytics/shadow-fleet?days=${days}&limit=${limit}`),
    staleTime: ANALYTICS_STALE,
    refetchInterval: 5 * 60_000,
  })
}

// Phase 54: Pipeline disruption layer
export type PipelineSegment = Schemas['PipelineSegment']

export type PipelinesResponse = Schemas['PipelinesResponse']

export function usePipelines(disruptedOnly = true, enabled = true) {
  return useQuery({
    queryKey: ['pipelines', disruptedOnly],
    queryFn: () => getJSON<PipelinesResponse>(`/api/pipelines?disrupted_only=${disruptedOnly}`),
    staleTime: 60 * 60_000,
    refetchInterval: 60 * 60_000,
    enabled,
  })
}

// ---------------------------------------------------------------------------
// Phase 55: Owner Fleet Status
// ---------------------------------------------------------------------------

export type OwnerFleetStatusRow = Schemas['OwnerFleetStatusRow']

export type OwnerFleetStatusResponse = Schemas['OwnerFleetStatusResponse']

export function useOwnerFleetStatus(kind?: string, minVessels = 1, limit = 30) {
  const qs = new URLSearchParams({ min_vessels: String(minVessels), limit: String(limit) })
  if (kind) qs.set('kind', kind)
  return useQuery({
    queryKey: ['owner-fleet-status', kind, minVessels, limit],
    queryFn: () => getJSON<OwnerFleetStatusResponse>(`/api/analytics/owner-fleet-status?${qs}`),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
  })
}

// Phase 54: European Supply Intelligence
export type EuropeanInboundVessel = Schemas['EuropeanInboundVessel']

export type EuropeanInboundResponse = Schemas['EuropeanInboundResponse']

export function useEuropeanInbound(horizonH = 48, ladenOnly = false) {
  const qs = new URLSearchParams({ horizon_h: String(horizonH), laden_only: String(ladenOnly) })
  return useQuery({
    queryKey: ['european-inbound', horizonH, ladenOnly],
    queryFn: () => getJSON<EuropeanInboundResponse>(`/api/analytics/european-inbound?${qs}`),
    staleTime: 4 * 60_000,
    refetchInterval: 4 * 60_000,
  })
}

// Phase 55: LNG Intelligence
export type LngVessel = Schemas['LngVessel']

export type LngLoadingVessel = Schemas['LngLoadingVessel']

export type LngInboundResponse = Schemas['LngInboundResponse']

export function useLngInbound(horizonH = 72) {
  return useQuery({
    queryKey: ['lng-inbound', horizonH],
    queryFn: () => getJSON<LngInboundResponse>(`/api/analytics/lng-inbound?horizon_h=${horizonH}`),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
  })
}

// True ETA Phase F: accuracy scoreboard (leakage-free backtest metrics)
export type EtaAccuracyRow = Schemas['EtaAccuracyRow']

export type EtaDriftAlert = Schemas['EtaDriftAlert']

export type EtaAccuracyResponse = Schemas['EtaAccuracyResponse']

export type EtaLeadBasis = 'actual' | 'predicted'

// True ETA Phase E/F: per-vessel resolvable-target ETAs (vessel-detail popup)
export type EtaPrediction = Schemas['EtaPrediction']

export type VesselEtaResponse = Schemas['EtaResponse']

export function useVesselEta(mmsi: number | null | undefined) {
  return useQuery({
    queryKey: ['vessel-eta', mmsi],
    queryFn: () => getJSON<VesselEtaResponse>(`/api/analytics/eta?mmsi=${mmsi}`),
    enabled: mmsi != null,
    staleTime: 4 * 60_000,
    refetchInterval: 4 * 60_000,
  })
}

// Destination predictor: ranked candidate ports + probability (vessel-detail popup)
export type DestinationCandidate = Schemas['DestinationCandidate']

export type VesselDestinationResponse = Schemas['DestinationResponse']

export function useVesselDestination(mmsi: number | null | undefined) {
  return useQuery({
    queryKey: ['vessel-destination', mmsi],
    queryFn: () => getJSON<VesselDestinationResponse>(`/api/analytics/destination?mmsi=${mmsi}`),
    enabled: mmsi != null,
    staleTime: 4 * 60_000,
    refetchInterval: 4 * 60_000,
  })
}

export function useEtaAccuracy(targetType = 'all', leadBasis: EtaLeadBasis = 'actual') {
  return useQuery({
    queryKey: ['eta-accuracy', targetType, leadBasis],
    queryFn: () =>
      getJSON<EtaAccuracyResponse>(
        `/api/analytics/eta-accuracy?target_type=${targetType}&lead_basis=${leadBasis}`,
      ),
    staleTime: 10 * 60_000,
    refetchInterval: 10 * 60_000,
  })
}

export type EtaByTargetRow = Schemas['EtaByTargetRow']

export type EtaByTargetResponse = Schemas['EtaByTargetResponse']

export function useEtaByTarget() {
  return useQuery({
    queryKey: ['eta-by-target'],
    queryFn: () => getJSON<EtaByTargetResponse>('/api/analytics/eta-by-target'),
    staleTime: 10 * 60_000,
    refetchInterval: 10 * 60_000,
  })
}

export type EtaTrendPoint = Schemas['EtaTrendPoint']

export type EtaTrendResponse = Schemas['EtaTrendResponse']

export function useEtaTrend() {
  return useQuery({
    queryKey: ['eta-trend'],
    queryFn: () => getJSON<EtaTrendResponse>('/api/analytics/eta-trend'),
    staleTime: 10 * 60_000,
    refetchInterval: 10 * 60_000,
  })
}

export type UpcomingVessel = Schemas['UpcomingVessel']

export type UpcomingArrivalsResponse = Schemas['UpcomingArrivalsResponse']

export function useUpcomingArrivals(horizonH = 96, targetId?: string, targetType = 'all') {
  const params = new URLSearchParams({ horizon_h: String(horizonH), target_type: targetType })
  if (targetId) params.set('target_id', targetId)
  return useQuery({
    queryKey: ['eta-upcoming', horizonH, targetId ?? 'all', targetType],
    queryFn: () => getJSON<UpcomingArrivalsResponse>(`/api/analytics/eta-upcoming?${params}`),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
  })
}

// ---- Freight cycle board ----

export type CycleSignal = Schemas['CycleSignal']

export type CycleSignalsResponse = Schemas['CycleSignalsResponse']

export type CycleSubsector = Schemas['CycleSubsector']

export type CycleSubsectorsResponse = Schemas['CycleSubsectorsResponse']

export type CycleSeriesResponse = Schemas['CycleSeriesResponse']

// Baltic fixings land once a day; nothing here justifies the 60s tracker cadence.
const CYCLE_REFETCH_MS = 15 * 60_000

export function useCycleSignals() {
  return useQuery({
    queryKey: ['cycle-signals'],
    queryFn: () => getJSON<CycleSignalsResponse>('/api/cycle/signals'),
    staleTime: CYCLE_REFETCH_MS,
    refetchInterval: CYCLE_REFETCH_MS,
  })
}

export function useCycleSubsectors() {
  return useQuery({
    queryKey: ['cycle-subsectors'],
    queryFn: () => getJSON<CycleSubsectorsResponse>('/api/cycle/subsectors'),
    staleTime: CYCLE_REFETCH_MS,
    refetchInterval: CYCLE_REFETCH_MS,
  })
}

export function useCycleSeries(series: string, years = 5) {
  return useQuery({
    queryKey: ['cycle-series', series, years],
    queryFn: () =>
      getJSON<CycleSeriesResponse>(`/api/cycle/series?series=${series}&years=${years}`),
    staleTime: CYCLE_REFETCH_MS,
  })
}
