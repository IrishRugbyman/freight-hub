import { useQuery } from '@tanstack/react-query'

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
