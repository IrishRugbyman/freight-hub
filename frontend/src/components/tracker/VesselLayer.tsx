import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.markercluster'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'
import { projectPosition } from '@/lib/deadReckoning'

const DR_HZ = 2 // dead-reckoning update rate (times per second)

function makeMarker(v: Vessel, headingArrows: boolean): L.Layer {
  const color = colorFor(v.kind, v.segment)
  const course = headingArrows ? (v.heading ?? v.cog) : null
  if (course != null) {
    return L.marker([v.lat, v.lon], {
      icon: L.divIcon({
        className: 'vessel-arrow',
        html: `<div style="transform:rotate(${course}deg);color:${color};line-height:1;font-size:14px">&#9650;</div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      }),
    })
  }
  return L.circleMarker([v.lat, v.lon], {
    radius: 4,
    color,
    weight: 1,
    fillColor: color,
    fillOpacity: 0.8,
  })
}

/**
 * Renders vessels imperatively. The layer group is kept alive across 60s data polls;
 * only position/click updates are applied per vessel (no full teardown). The group is
 * rebuilt only when the clustering or heading-arrows toggle changes.
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
  const groupRef = useRef<L.MarkerClusterGroup | L.LayerGroup | null>(null)
  const markerMapRef = useRef<Map<number, L.Layer>>(new Map())
  // Keep onSelect in a ref so data-diff effect can use latest version without re-running
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  // Rebuild the group when clustering or headingArrows toggle changes
  useEffect(() => {
    if (groupRef.current) map.removeLayer(groupRef.current)
    markerMapRef.current.clear()

    const group = clustering
      ? L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 50 })
      : L.layerGroup()

    groupRef.current = group
    map.addLayer(group)

    return () => {
      map.removeLayer(group)
      groupRef.current = null
    }
  }, [map, clustering, headingArrows]) // headingArrows here so icons rebuild on toggle

  // Diff vessel updates without tearing down the group (no flash)
  // Also stores latest vessel data in a ref so the DR loop can read it
  const vesselsRef = useRef<Vessel[]>(vessels)
  useEffect(() => {
    const group = groupRef.current
    if (!group) return
    const markerMap = markerMapRef.current

    vesselsRef.current = vessels
    const incoming = new Map(vessels.map((v) => [v.mmsi, v]))

    // Remove departed vessels
    for (const [mmsi, marker] of markerMap) {
      if (!incoming.has(mmsi)) {
        group.removeLayer(marker)
        markerMap.delete(mmsi)
      }
    }

    // Update existing markers, add new ones
    for (const v of vessels) {
      const existing = markerMap.get(v.mmsi)
      if (existing) {
        // Update position (most common change each poll)
        if (existing instanceof L.Marker) existing.setLatLng([v.lat, v.lon])
        else if (existing instanceof L.CircleMarker) existing.setLatLng([v.lat, v.lon])
        // Refresh click to carry new vessel data
        existing.off('click')
        existing.on('click', () => onSelectRef.current(v))
      } else {
        const marker = makeMarker(v, headingArrows)
        marker.on('click', () => onSelectRef.current(v))
        group.addLayer(marker)
        markerMap.set(v.mmsi, marker)
      }
    }
  }, [vessels, headingArrows])

  // Dead-reckoning animation: nudge markers at ~2 Hz between polls
  useEffect(() => {
    let timerId: ReturnType<typeof setTimeout>

    function tick() {
      if (document.hidden) {
        timerId = setTimeout(tick, 1000 / DR_HZ)
        return
      }
      const markerMap = markerMapRef.current
      const now = Date.now()
      for (const v of vesselsRef.current) {
        const marker = markerMap.get(v.mmsi)
        if (!marker) continue
        const dtSec = (now - new Date(v.updated_ts).getTime()) / 1000
        const proj = projectPosition(v.lat, v.lon, v.sog, v.cog, dtSec)
        if (!proj) continue
        if (marker instanceof L.Marker || marker instanceof L.CircleMarker) {
          marker.setLatLng([proj.lat, proj.lon])
        }
      }
      timerId = setTimeout(tick, 1000 / DR_HZ)
    }

    timerId = setTimeout(tick, 1000 / DR_HZ)
    return () => clearTimeout(timerId)
  }, []) // runs once per mount; reads latest data via refs

  return null
}
