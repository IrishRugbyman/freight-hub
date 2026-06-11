import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import { useEvents, type AisEvent } from '@/lib/api'

const TYPE_COLORS: Record<string, string> = {
  gap: '#ef4444',
  loiter: '#eab308',
  sts: '#3b82f6',
}

const TYPE_LABELS: Record<string, string> = {
  gap: 'Signal Lost',
  loiter: 'Loitering',
  sts: 'STS',
}

function pinSvg(color: string, label: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="36" viewBox="0 0 28 36">
    <path d="M14,1 C7.37,1 2,6.37 2,13 C2,22 14,35 14,35 C14,35 26,22 26,13 C26,6.37 20.63,1 14,1 Z"
      fill="${color}" stroke="rgba(0,0,0,0.4)" stroke-width="1.2"/>
    <text x="14" y="16" text-anchor="middle" font-size="8" font-family="sans-serif"
      font-weight="bold" fill="white">${label}</text>
  </svg>`
}

function makeEventMarker(ev: AisEvent): L.Marker {
  const color = TYPE_COLORS[ev.type] ?? '#6b7280'
  const label = ev.type === 'gap' ? '!' : ev.type === 'loiter' ? '~' : '⇌'
  return L.marker([ev.lat, ev.lon], {
    icon: L.divIcon({
      className: '',
      html: pinSvg(color, label),
      iconSize: [28, 36],
      iconAnchor: [14, 35],
      popupAnchor: [0, -32],
    }),
    zIndexOffset: 500,
  }).bindPopup(
    `<b>${TYPE_LABELS[ev.type]}</b><br/>
     ${ev.vessel_name || `MMSI ${ev.mmsi}`}
     ${ev.type === 'sts' && ev.vessel2_name ? ` + ${ev.vessel2_name}` : ''}<br/>
     ${ev.region?.replace(/_/g, ' ') ?? ''}<br/>
     <a href="/events" style="color:#60a5fa">View all events</a>`
  )
}

export function EventPinsLayer({ visible }: { visible: boolean }) {
  const map = useMap()
  const { data } = useEvents({ days: 2, limit: 100 }, visible)

  useEffect(() => {
    if (!visible || !data?.events.length) return

    const group = L.layerGroup().addTo(map)
    for (const ev of data.events) {
      const marker = makeEventMarker(ev)
      marker.on('click', () => {
        marker.openPopup()
      })
      group.addLayer(marker)
    }

    return () => {
      map.removeLayer(group)
    }
  }, [map, visible, data?.events])

  return null
}
