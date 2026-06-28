import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import type { TrackPoint } from '@/lib/api'

/** Renders a vessel trail as a Leaflet polyline. Cleaned up on unmount or when mmsi changes.
 *  liveTip: the vessel's current live position, appended so the line reaches the marker. */
export function TrailLayer({
  mmsi,
  points,
  color,
  liveTip,
}: {
  mmsi: number
  points: TrackPoint[]
  color: string
  liveTip?: { lat: number; lon: number }
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
    if (liveTip) latlngs.push([liveTip.lat, liveTip.lon])
    const line = L.polyline(latlngs, { color, weight: 2, opacity: 0.7 })
    line.addTo(map)
    lineRef.current = line

    return () => {
      map.removeLayer(line)
    }
  }, [map, mmsi, points, color, liveTip])

  return null
}
