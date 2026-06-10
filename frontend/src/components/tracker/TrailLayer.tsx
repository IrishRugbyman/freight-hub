import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import type { TrackPoint } from '@/lib/api'

/** Renders a vessel trail as a Leaflet polyline. Cleaned up on unmount or when mmsi changes. */
export function TrailLayer({
  mmsi,
  points,
  color,
}: {
  mmsi: number
  points: TrackPoint[]
  color: string
}) {
  const map = useMap()
  const lineRef = useRef<L.Polyline | null>(null)

  useEffect(() => {
    if (lineRef.current) {
      map.removeLayer(lineRef.current)
      lineRef.current = null
    }
    if (points.length < 2) return

    const latlngs = points.map((p) => [p.lat, p.lon] as [number, number])
    const line = L.polyline(latlngs, { color, weight: 2, opacity: 0.7 })
    line.addTo(map)
    lineRef.current = line

    return () => {
      map.removeLayer(line)
    }
  }, [map, mmsi, points, color])

  return null
}
