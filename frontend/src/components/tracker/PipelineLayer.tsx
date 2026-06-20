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
  const cap =
    p.commodity === 'gas' && p.capacity_bcm_yr
      ? `${p.capacity_bcm_yr} bcm/yr`
      : p.capacity_mbd
        ? `${p.capacity_mbd} mbd`
        : 'unknown capacity'

  const stateLabel =
    p.physical_state === 'offline'
      ? '<span style="color:#ef4444;font-weight:600">OFFLINE</span>'
      : p.physical_state === 'reduced'
        ? '<span style="color:#f97316;font-weight:600">REDUCED</span>'
        : p.physical_state.toUpperCase()

  const route = `${p.from_country} → ${p.to_country}`

  let html = `<div style="min-width:220px;max-width:280px;font-size:12px;line-height:1.5">
    <div style="font-weight:700;margin-bottom:4px">${p.name}</div>
    <div style="margin-bottom:2px">${route} &middot; ${p.commodity.toUpperCase()} &middot; ${cap}</div>
    <div style="margin-bottom:6px">Status: ${stateLabel}</div>`

  if (p.disruption_description) {
    const since = p.disruption_since ? ` (since ${p.disruption_since})` : ''
    const evtType = p.disruption_event_type
      ? ` [${p.disruption_event_type}]`
      : ''
    html += `<div style="border-top:1px solid #555;padding-top:6px;color:#ccc">${p.disruption_description}${evtType}${since}</div>`
  }

  html += '</div>'
  return html
}

/** Renders pipeline lines on the Leaflet map. Controlled by the pipelines layer toggle. */
export function PipelineLayer({ visible }: { visible: boolean }) {
  const map = useMap()
  const { data } = usePipelines(true, visible)
  const linesRef = useRef<L.Polyline[]>([])

  useEffect(() => {
    // Remove existing lines
    linesRef.current.forEach((l) => map.removeLayer(l))
    linesRef.current = []

    if (!visible || !data) return

    for (const p of data.pipelines) {
      const color = STATE_COLOR[p.physical_state] ?? '#9ca3af'
      const weight = STATE_WEIGHT[p.physical_state] ?? 1
      const line = L.polyline(
        [
          [p.start_lat, p.start_lon],
          [p.end_lat, p.end_lon],
        ],
        {
          color,
          weight,
          opacity: 0.85,
          dashArray: p.physical_state === 'offline' ? '6 4' : undefined,
        },
      )
      line.bindPopup(popupHtml(p), { maxWidth: 300 })
      line.addTo(map)
      linesRef.current.push(line)
    }

    return () => {
      linesRef.current.forEach((l) => map.removeLayer(l))
      linesRef.current = []
    }
  }, [map, visible, data])

  return null
}
