import { useQuery } from '@tanstack/react-query'

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
  })
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
