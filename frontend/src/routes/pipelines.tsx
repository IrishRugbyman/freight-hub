import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { MapContainer, TileLayer, ZoomControl, useMap } from 'react-leaflet'
import L from 'leaflet'
import { usePipelines, type PipelineSegment } from '@/lib/api'
import 'leaflet/dist/leaflet.css'

export const Route = createFileRoute('/pipelines')({
  component: PipelinesPage,
})

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const COUNTRY: Record<string, string> = {
  AF: 'Afghanistan', AL: 'Albania', DZ: 'Algeria', AO: 'Angola', AR: 'Argentina',
  AM: 'Armenia', AU: 'Australia', AZ: 'Azerbaijan', BH: 'Bahrain', BD: 'Bangladesh',
  BY: 'Belarus', BE: 'Belgium', BO: 'Bolivia', BA: 'Bosnia', BR: 'Brazil',
  BG: 'Bulgaria', CM: 'Cameroon', CA: 'Canada', CL: 'Chile', CN: 'China',
  CO: 'Colombia', CD: 'DR Congo', CG: 'Congo', CR: 'Costa Rica', HR: 'Croatia',
  CZ: 'Czechia', DK: 'Denmark', EC: 'Ecuador', EG: 'Egypt', ET: 'Ethiopia',
  FI: 'Finland', FR: 'France', GA: 'Gabon', GE: 'Georgia', DE: 'Germany',
  GH: 'Ghana', GR: 'Greece', GN: 'Guinea', HU: 'Hungary', IN: 'India',
  ID: 'Indonesia', IR: 'Iran', IQ: 'Iraq', IE: 'Ireland', IL: 'Israel',
  IT: 'Italy', CI: 'Ivory Coast', JP: 'Japan', JO: 'Jordan', KZ: 'Kazakhstan',
  KE: 'Kenya', KW: 'Kuwait', KG: 'Kyrgyzstan', LV: 'Latvia', LB: 'Lebanon',
  LY: 'Libya', LT: 'Lithuania', MY: 'Malaysia', ML: 'Mali', MX: 'Mexico',
  MD: 'Moldova', MN: 'Mongolia', MA: 'Morocco', MZ: 'Mozambique', MM: 'Myanmar',
  NL: 'Netherlands', NG: 'Nigeria', NO: 'Norway', OM: 'Oman', PK: 'Pakistan',
  PE: 'Peru', PH: 'Philippines', PL: 'Poland', PT: 'Portugal', QA: 'Qatar',
  RO: 'Romania', RU: 'Russia', SA: 'Saudi Arabia', SN: 'Senegal', SK: 'Slovakia',
  ZA: 'South Africa', KR: 'South Korea', SS: 'South Sudan', ES: 'Spain',
  SD: 'Sudan', SE: 'Sweden', CH: 'Switzerland', SY: 'Syria', TW: 'Taiwan',
  TJ: 'Tajikistan', TZ: 'Tanzania', TH: 'Thailand', TN: 'Tunisia', TR: 'Turkey',
  TM: 'Turkmenistan', UG: 'Uganda', UA: 'Ukraine', AE: 'UAE', GB: 'UK',
  US: 'USA', UZ: 'Uzbekistan', VE: 'Venezuela', VN: 'Vietnam', YE: 'Yemen',
  ZM: 'Zambia', ZW: 'Zimbabwe',
}

function countryName(iso2: string): string {
  return COUNTRY[iso2?.toUpperCase()] ?? iso2
}

// Commodity + state -> line color
function lineColor(p: PipelineSegment): string {
  if (p.physical_state === 'offline') return '#ef4444'   // red
  if (p.physical_state === 'reduced') return '#f97316'   // orange
  if (p.commodity === 'oil') return '#fbbf24'            // amber
  return '#38bdf8'                                       // sky
}

function lineWeight(p: PipelineSegment, selected: boolean): number {
  if (selected) return 4
  if (p.physical_state === 'offline' || p.physical_state === 'reduced') return 2
  return 1.5
}

function lineOpacity(p: PipelineSegment, selected: boolean): number {
  if (selected) return 1
  if (p.physical_state === 'offline' || p.physical_state === 'reduced') return 0.95
  return 0.7
}

/** Extract renderable latlng segments from a pipeline */
function pipelineLatLngs(p: PipelineSegment): L.LatLngExpression[][] {
  if (p.route_coords && p.route_coords.length > 0) {
    return p.route_coords as L.LatLngExpression[][]
  }
  if (p.start_lat != null && p.start_lon != null && p.end_lat != null && p.end_lon != null) {
    return [[[p.start_lat, p.start_lon], [p.end_lat, p.end_lon]]]
  }
  return []
}

function hasGeometry(p: PipelineSegment): boolean {
  return pipelineLatLngs(p).length > 0
}

// ---------------------------------------------------------------------------
// Map layer: draws all pipeline polylines imperatively
// ---------------------------------------------------------------------------

interface PipelineMapLinesProps {
  pipelines: PipelineSegment[]
  selectedId: string | null
  onSelect: (p: PipelineSegment) => void
}

function PipelineMapLines({ pipelines, selectedId, onSelect }: PipelineMapLinesProps) {
  const map = useMap()
  const linesRef = useRef<Map<string, L.Polyline[]>>(new Map())
  const haloRef = useRef<L.Polyline[] | null>(null)

  // Build/rebuild all lines when pipelines list changes
  useEffect(() => {
    // Clear existing lines
    linesRef.current.forEach((lines) => lines.forEach((l) => map.removeLayer(l)))
    linesRef.current.clear()
    if (haloRef.current) { haloRef.current.forEach((l) => map.removeLayer(l)); haloRef.current = null }

    for (const p of pipelines) {
      const latlngs = pipelineLatLngs(p)
      if (latlngs.length === 0) continue

      const polylines: L.Polyline[] = []
      for (const seg of latlngs) {
        const line = L.polyline(seg as L.LatLngExpression[], {
          color: lineColor(p),
          weight: lineWeight(p, p.id === selectedId),
          opacity: lineOpacity(p, p.id === selectedId),
          dashArray: p.physical_state === 'offline' ? '7 5' : undefined,
          interactive: true,
        })
        line.on('click', () => onSelect(p))
        line.addTo(map)
        polylines.push(line)
      }
      linesRef.current.set(p.id, polylines)
    }

    return () => {
      linesRef.current.forEach((lines) => lines.forEach((l) => map.removeLayer(l)))
      linesRef.current.clear()
      if (haloRef.current) { haloRef.current.forEach((l) => map.removeLayer(l)); haloRef.current = null }
    }
  }, [map, pipelines]) // eslint-disable-line react-hooks/exhaustive-deps

  // Update selection styling without full rebuild
  useEffect(() => {
    linesRef.current.forEach((lines, id) => {
      const p = pipelines.find((p) => p.id === id)
      if (!p) return
      const sel = id === selectedId
      lines.forEach((l) => l.setStyle({
        color: lineColor(p),
        weight: lineWeight(p, sel),
        opacity: lineOpacity(p, sel),
      }))
      // Bring selected to front
      if (sel) lines.forEach((l) => l.bringToFront())
    })
  }, [pipelines, selectedId])

  return null
}

// Zoom to selected pipeline bounds
function PipelineZoomer({ pipeline }: { pipeline: PipelineSegment | null }) {
  const map = useMap()
  const prevId = useRef<string | null>(null)

  useEffect(() => {
    if (!pipeline || pipeline.id === prevId.current) return
    prevId.current = pipeline.id
    const latlngs = pipelineLatLngs(pipeline)
    if (latlngs.length === 0) return
    const allPts = latlngs.flat() as [number, number][]
    const bounds = L.latLngBounds(allPts)
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 7, animate: true })
  }, [map, pipeline])

  return null
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function StateBadge({ state }: { state: string }) {
  if (state === 'offline') {
    return (
      <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-red-500/20 text-red-300">
        Offline
      </span>
    )
  }
  if (state === 'reduced') {
    return (
      <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-orange-500/20 text-orange-300">
        Reduced
      </span>
    )
  }
  return (
    <span className="text-[10px] text-muted-foreground/50 uppercase tracking-wide">{state}</span>
  )
}

function CommodityDot({ commodity }: { commodity: string }) {
  const isOil = commodity === 'oil'
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${isOil ? 'text-amber-400' : 'text-sky-400'}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${isOil ? 'bg-amber-400' : 'bg-sky-400'}`} />
      {commodity}
    </span>
  )
}

function formatCap(p: PipelineSegment): string {
  const parts: string[] = []
  if (p.commodity === 'gas') {
    if (p.capacity_bcfd && p.capacity_bcfd > 0) parts.push(`${p.capacity_bcfd.toFixed(2)} Bcf/d`)
    else if (p.capacity_bcm_yr && p.capacity_bcm_yr > 0) parts.push(`${p.capacity_bcm_yr.toFixed(1)} bcm/yr`)
  } else if (p.capacity_mbd && p.capacity_mbd > 0) {
    parts.push(`${p.capacity_mbd.toFixed(1)} mbd`)
  }
  if (p.length_miles && p.length_miles > 0) parts.push(`${p.length_miles.toLocaleString()} mi`)
  return parts.join(' · ') || ''
}

// ---------------------------------------------------------------------------
// Detail card (right panel top)
// ---------------------------------------------------------------------------

function PipelineDetailCard({ p, onClose }: { p: PipelineSegment; onClose: () => void }) {
  const cap = formatCap(p)
  const disrupted = p.physical_state === 'offline' || p.physical_state === 'reduced'
  return (
    <div className="border-b border-border/60 bg-muted/20 p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold leading-snug text-foreground">{p.name}</div>
          {p.owner && <div className="mt-0.5 text-xs text-muted-foreground">{p.owner}</div>}
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded p-0.5 text-muted-foreground/40 hover:bg-muted hover:text-foreground"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <CommodityDot commodity={p.commodity} />
        <StateBadge state={p.physical_state} />
        {cap && <span className="text-muted-foreground">{cap}</span>}
      </div>
      <div className="mt-1.5 text-xs text-muted-foreground">
        {countryName(p.from_country)}
        {p.from_country !== p.to_country && <> → {countryName(p.to_country)}</>}
      </div>
      {p.states_served && (
        <div className="mt-1 text-[11px] text-muted-foreground/60">{p.states_served}</div>
      )}
      {disrupted && p.disruption_description && (
        <div className="mt-2 rounded border border-red-500/20 bg-red-500/5 p-2">
          {p.disruption_event_type && (
            <div className="mb-1 text-[9px] uppercase tracking-wide text-red-300/70">
              {p.disruption_event_type}
              {p.disruption_since ? ` · since ${p.disruption_since.slice(0, 10)}` : ''}
            </div>
          )}
          <p className="text-[11px] leading-relaxed text-foreground/80">{p.disruption_description}</p>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pipeline list row
// ---------------------------------------------------------------------------

function PipelineRow({
  p,
  selected,
  onClick,
}: {
  p: PipelineSegment
  selected: boolean
  onClick: () => void
}) {
  const cap = formatCap(p)
  const disrupted = p.physical_state === 'offline' || p.physical_state === 'reduced'
  const geo = hasGeometry(p)
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-border/20 transition-colors ${
        selected
          ? 'bg-primary/10 border-l-2 border-l-primary'
          : 'hover:bg-muted/30 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div
            className={`text-xs font-medium leading-snug truncate ${
              disrupted ? 'text-foreground' : 'text-foreground/70'
            }`}
          >
            {p.name}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground/60">
            <CommodityDot commodity={p.commodity} />
            {cap && <span>{cap}</span>}
            {!geo && (
              <span className="text-muted-foreground/30" title="No geometry">no route</span>
            )}
          </div>
        </div>
        <div className="shrink-0 mt-0.5">
          <StateBadge state={p.physical_state} />
        </div>
      </div>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type CommodityFilter = 'all' | 'oil' | 'gas'
type StateFilter = 'all' | 'offline' | 'reduced' | 'disrupted' | 'flowing'

export default function PipelinesPage() {
  const { data, isLoading } = usePipelines(false, true)
  const pipelines = data?.pipelines ?? []

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [commodityFilter, setCommodityFilter] = useState<CommodityFilter>('all')
  const [stateFilter, setStateFilter] = useState<StateFilter>('all')

  const selected = useMemo(
    () => pipelines.find((p) => p.id === selectedId) ?? null,
    [pipelines, selectedId],
  )

  const filtered = useMemo(() => {
    const qLow = q.toLowerCase()
    return pipelines.filter((p) => {
      if (commodityFilter !== 'all' && p.commodity !== commodityFilter) return false
      if (stateFilter === 'disrupted' && p.physical_state !== 'offline' && p.physical_state !== 'reduced') return false
      if (stateFilter !== 'all' && stateFilter !== 'disrupted' && p.physical_state !== stateFilter) return false
      if (qLow && !p.name.toLowerCase().includes(qLow) && !(p.owner ?? '').toLowerCase().includes(qLow)) return false
      return true
    })
  }, [pipelines, q, commodityFilter, stateFilter])

  // Pipelines shown on map: use filtered set
  const mapPipelines = useMemo(() => filtered.filter(hasGeometry), [filtered])

  const handleSelect = useCallback(
    (p: PipelineSegment) => {
      setSelectedId((prev) => (prev === p.id ? null : p.id))
    },
    [],
  )

  // KPI counts
  const offline = pipelines.filter((p) => p.physical_state === 'offline')
  const reduced = pipelines.filter((p) => p.physical_state === 'reduced')
  const routed = pipelines.filter(hasGeometry)

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Top bar: KPIs + filters */}
      <div className="shrink-0 border-b border-border/60 bg-background/80 px-4 py-2 flex flex-wrap items-center gap-4">
        {/* KPIs */}
        <div className="flex items-center gap-3 text-xs">
          <span className="text-muted-foreground">
            <span className="font-semibold text-foreground">{pipelines.length}</span> pipelines
          </span>
          <span className="h-3 w-px bg-border" />
          <span className="text-muted-foreground">
            <span className="font-semibold text-foreground">{routed.length}</span> routed
          </span>
          {offline.length > 0 && (
            <>
              <span className="h-3 w-px bg-border" />
              <span className="text-red-400 font-medium">{offline.length} offline</span>
            </>
          )}
          {reduced.length > 0 && (
            <>
              <span className="h-3 w-px bg-border" />
              <span className="text-orange-400 font-medium">{reduced.length} reduced</span>
            </>
          )}
        </div>

        <div className="h-4 w-px bg-border ml-1" />

        {/* Search */}
        <input
          className="h-7 w-48 rounded border border-border bg-muted/40 px-2.5 text-xs placeholder:text-muted-foreground focus:border-primary/60 focus:outline-none"
          placeholder="Search pipelines..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />

        {/* Commodity toggle */}
        <div className="flex rounded border border-border overflow-hidden text-xs">
          {(['all', 'oil', 'gas'] as CommodityFilter[]).map((c) => (
            <button
              key={c}
              onClick={() => setCommodityFilter(c)}
              className={`px-2.5 py-1 transition-colors ${
                commodityFilter === c
                  ? 'bg-primary/20 text-primary font-medium'
                  : 'text-muted-foreground hover:bg-muted/40'
              }`}
            >
              {c === 'all' ? 'All' : c === 'oil' ? 'Oil' : 'Gas'}
            </button>
          ))}
        </div>

        {/* State toggle */}
        <div className="flex rounded border border-border overflow-hidden text-xs">
          {(['all', 'disrupted', 'flowing'] as StateFilter[]).map((s) => (
            <button
              key={s}
              onClick={() => setStateFilter(s)}
              className={`px-2.5 py-1 transition-colors capitalize ${
                stateFilter === s
                  ? 'bg-primary/20 text-primary font-medium'
                  : 'text-muted-foreground hover:bg-muted/40'
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        <span className="ml-auto text-xs text-muted-foreground">
          {filtered.length} shown / {mapPipelines.length} on map
        </span>
      </div>

      {/* Body: map left + list right */}
      <div className="min-h-0 flex-1 flex overflow-hidden">
        {/* Map */}
        <div className="relative flex-1 min-w-0">
          {isLoading && (
            <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-background/50 text-sm text-muted-foreground">
              Loading...
            </div>
          )}
          <MapContainer
            center={[25, 20]}
            zoom={2}
            minZoom={2}
            worldCopyJump
            className="h-full w-full"
            preferCanvas
            zoomControl={false}
          >
            <TileLayer
              attribution='&copy; <a href="https://carto.com">CARTO</a>'
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              subdomains="abcd"
              maxZoom={19}
            />
            <ZoomControl position="bottomright" />
            <PipelineMapLines
              pipelines={mapPipelines}
              selectedId={selectedId}
              onSelect={handleSelect}
            />
            <PipelineZoomer pipeline={selected} />
          </MapContainer>

          {/* Legend */}
          <div className="absolute bottom-8 left-3 z-[1000] rounded border border-border bg-background/90 p-2 text-[10px] text-muted-foreground backdrop-blur">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="h-0.5 w-5 bg-amber-400 rounded" /> Oil
            </div>
            <div className="flex items-center gap-1.5 mb-1">
              <span className="h-0.5 w-5 bg-sky-400 rounded" /> Gas
            </div>
            <div className="flex items-center gap-1.5 mb-1">
              <span className="h-0.5 w-5 bg-orange-400 rounded" /> Reduced
            </div>
            <div className="flex items-center gap-1.5">
              <span className="h-0.5 w-5 bg-red-400 rounded" style={{ borderTop: '2px dashed #ef4444', background: 'none' }} /> Offline
            </div>
          </div>
        </div>

        {/* Right panel: detail + list */}
        <div className="w-80 shrink-0 border-l border-border/60 bg-background flex flex-col overflow-hidden">
          {/* Selected detail card */}
          {selected && (
            <PipelineDetailCard
              p={selected}
              onClose={() => setSelectedId(null)}
            />
          )}

          {/* Pipeline list */}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {filtered.length === 0 && !isLoading && (
              <div className="flex items-center justify-center py-12 text-xs text-muted-foreground">
                No pipelines match.
              </div>
            )}
            {filtered.map((p) => (
              <PipelineRow
                key={p.id}
                p={p}
                selected={p.id === selectedId}
                onClick={() => handleSelect(p)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
