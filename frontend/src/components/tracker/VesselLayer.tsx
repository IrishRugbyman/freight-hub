import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.markercluster'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'

/**
 * Renders vessels imperatively (cheap for hundreds of points). Rebuilds its layer
 * when vessels or the clustering/arrows toggles change. With arrows on, vessels that
 * broadcast a course get a rotated chevron divIcon; otherwise a colored circle.
 */
export function VesselLayer({
  vessels,
  clustering,
  headingArrows,
  onSelect,
}: {
  vessels: Vessel[]
  clustering: boolean
  headingArrows: boolean
  onSelect: (v: Vessel) => void
}) {
  const map = useMap()

  useEffect(() => {
    const group = clustering
      ? L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 50 })
      : L.layerGroup()

    for (const v of vessels) {
      const color = colorFor(v.kind, v.segment)
      const course = headingArrows ? (v.heading ?? v.cog) : null
      let marker: L.Layer

      if (course != null) {
        marker = L.marker([v.lat, v.lon], {
          icon: L.divIcon({
            className: 'vessel-arrow',
            html: `<div style="transform:rotate(${course}deg);color:${color};line-height:1;font-size:14px">▲</div>`,
            iconSize: [14, 14],
            iconAnchor: [7, 7],
          }),
        })
      } else {
        marker = L.circleMarker([v.lat, v.lon], {
          radius: 4,
          color,
          weight: 1,
          fillColor: color,
          fillOpacity: 0.8,
        })
      }
      marker.on('click', () => onSelect(v))
      group.addLayer(marker)
    }

    map.addLayer(group)
    return () => {
      map.removeLayer(group)
    }
  }, [map, vessels, clustering, headingArrows, onSelect])

  return null
}
