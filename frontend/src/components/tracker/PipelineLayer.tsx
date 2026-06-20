import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import { usePipelines, type PipelineSegment } from '@/lib/api'

const STATE_COLOR: Record<string, string> = {
  offline: '#ef4444',
  reduced: '#f97316',
  flowing: '#6b7280',
  unknown: '#9ca3af',
}

const STATE_WEIGHT: Record<string, number> = {
  offline: 3,
  reduced: 2,
  flowing: 1,
  unknown: 1,
}

function popupHtml(p: PipelineSegment): string {
  const capParts: string[] = []
  if (p.commodity === 'gas') {
    if (p.capacity_bcfd) capParts.push(`${p.capacity_bcfd} Bcf/d`)
    else if (p.capacity_bcm_yr) capParts.push(`${p.capacity_bcm_yr} bcm/yr`)
  } else if (p.capacity_mbd) {
    capParts.push(`${p.capacity_mbd} mbd`)
  }
  if (p.length_miles) capParts.push(`${p.length_miles.toLocaleString()} mi`)
  const cap = capParts.join(' &middot; ') || 'capacity unknown'

  const stateLabel =
    p.physical_state === 'offline'
      ? '<span style="color:#ef4444;font-weight:600">OFFLINE</span>'
      : p.physical_state === 'reduced'
        ? '<span style="color:#f97316;font-weight:600">REDUCED</span>'
        : p.physical_state.toUpperCase()

  const route = `${p.from_country} → ${p.to_country}`

  let html = `<div style="min-width:220px;max-width:300px;font-size:12px;line-height:1.5">
    <div style="font-weight:700;margin-bottom:4px">${p.name}</div>`

  if (p.owner) {
    html += `<div style="color:#9ca3af;margin-bottom:2px">${p.owner}</div>`
  }

  html += `<div style="margin-bottom:2px">${route} &middot; ${p.commodity.toUpperCase()}</div>
    <div style="margin-bottom:2px">${cap}</div>`

  if (p.states_served) {
    html += `<div style="margin-bottom:2px;color:#d1d5db">States: ${p.states_served}</div>`
  }

  html += `<div style="margin-bottom:6px">Status: ${stateLabel}</div>`

  if (p.disruption_description) {
    const since = p.disruption_since ? ` (since ${p.disruption_since})` : ''
    const evtType = p.disruption_event_type ? ` [${p.disruption_event_type}]` : ''
    html += `<div style="border-top:1px solid #555;padding-top:6px;color:#ccc">${p.disruption_description}${evtType}${since}</div>`
  }

  html += '</div>'
  return html
}

function makeLine(p: PipelineSegment, highlight = false): L.Polyline {
  const color = STATE_COLOR[p.physical_state] ?? '#9ca3af'
  const line = L.polyline(
    [[p.start_lat!, p.start_lon!], [p.end_lat!, p.end_lon!]],
    {
      color,
      weight: highlight ? 5 : (STATE_WEIGHT[p.physical_state] ?? 1),
      opacity: highlight ? 1 : 0.85,
      dashArray: p.physical_state === 'offline' && !highlight ? '6 4' : undefined,
    },
  )
  line.bindPopup(popupHtml(p), { maxWidth: 300 })
  return line
}

/** Renders pipeline lines on the Leaflet map. Controlled by the pipelines layer toggle. */
export function PipelineLayer({ visible, highlightId }: { visible: boolean; highlightId?: string | null }) {
  const map = useMap()
  const { data } = usePipelines(false, visible)
  const linesRef = useRef<L.Polyline[]>([])
  const haloRef = useRef<L.Polyline | null>(null)

  useEffect(() => {
    linesRef.current.forEach((l) => map.removeLayer(l))
    linesRef.current = []
    if (haloRef.current) { map.removeLayer(haloRef.current); haloRef.current = null }

    if (!visible || !data) return

    // Draw all non-highlighted pipelines first (skip RexTag-only records with no coords)
    for (const p of data.pipelines) {
      if (highlightId && p.id === highlightId) continue
      if (p.start_lat == null || p.end_lat == null) continue
      const line = makeLine(p)
      line.addTo(map)
      linesRef.current.push(line)
    }

    // Draw highlighted pipeline last (on top) with halo + precise fitBounds
    if (highlightId) {
      const hp = data.pipelines.find((p) => p.id === highlightId)
      if (hp && hp.start_lat != null && hp.start_lon != null && hp.end_lat != null && hp.end_lon != null) {
        const coords: L.LatLngTuple[] = [[hp.start_lat, hp.start_lon], [hp.end_lat, hp.end_lon]]
        const bounds = L.latLngBounds(coords)

        const halo = L.polyline(coords, { color: '#ffffff', weight: 10, opacity: 0.2, interactive: false })
        halo.addTo(map)
        haloRef.current = halo

        const line = makeLine(hp, true)
        line.addTo(map)
        linesRef.current.push(line)

        map.fitBounds(bounds, { padding: [80, 80], maxZoom: 6, animate: true })
        setTimeout(() => line.openPopup(bounds.getCenter()), 600)
      }
    }

    return () => {
      linesRef.current.forEach((l) => map.removeLayer(l))
      linesRef.current = []
      if (haloRef.current) { map.removeLayer(haloRef.current); haloRef.current = null }
    }
  }, [map, visible, data, highlightId])

  return null
}
