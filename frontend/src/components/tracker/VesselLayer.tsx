import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.markercluster'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'
import { projectPosition } from '@/lib/deadReckoning'

const DR_HZ = 2 // dead-reckoning update rate (times per second)

/**
 * Top-down vessel silhouette SVG: pointed bow at top (12 o'clock), wider amidships,
 * flat stern. CSS transform rotates it to heading/cog. Width=10, Height=16.
 */
function shipSvg(color: string, anchored: boolean): string {
  if (anchored) {
    // Stationary: filled square-ish diamond, no direction indicator
    return `<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">
      <rect x="1" y="1" width="8" height="8" rx="1.5" fill="${color}" stroke="rgba(0,0,0,0.5)" stroke-width="1"/>
    </svg>`
  }
  // Underway: elongated hull with pointed bow at top
  return `<svg xmlns="http://www.w3.org/2000/svg" width="10" height="16" viewBox="0 0 10 16">
    <path d="M5,0 L9.5,5 L8.5,16 L1.5,16 L0.5,5 Z"
      fill="${color}" stroke="rgba(0,0,0,0.55)" stroke-width="0.8" stroke-linejoin="round"/>
    <line x1="5" y1="1" x2="5" y2="14" stroke="rgba(0,0,0,0.25)" stroke-width="0.6"/>
  </svg>`
}

function makeMarker(v: Vessel, headingArrows: boolean): L.Layer {
  const color = colorFor(v.kind, v.segment)
  const anchored = (v.nav_status === 1 || v.nav_status === 5) || ((v.sog ?? 99) < 0.3)
  const course = headingArrows && !anchored ? (v.heading ?? v.cog) : null

  if (headingArrows) {
    return L.marker([v.lat, v.lon], {
      icon: L.divIcon({
        className: '',
        html: `<div style="transform:rotate(${course ?? 0}deg);line-height:0">${shipSvg(color, anchored)}</div>`,
        iconSize: anchored ? [10, 10] : [10, 16],
        iconAnchor: anchored ? [5, 5] : [5, 8],
      }),
    })
  }
  return L.circleMarker([v.lat, v.lon], {
    radius: anchored ? 3 : 4,
    color,
    weight: anchored ? 2 : 1,
    fillColor: color,
    fillOpacity: anchored ? 0.5 : 0.85,
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
  }, [vessels, headingArrows, clustering])

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
