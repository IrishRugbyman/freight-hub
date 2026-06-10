import { useEffect } from 'react'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import type { Vessel, TrackPoint } from '@/lib/api'
import type { LayerState } from './types'
import { VesselLayer } from './VesselLayer'
import { ChokepointLayer } from './ChokepointLayer'
import { TrailLayer } from './TrailLayer'
import { colorFor } from '@/lib/segments'

function MapFocuser({ target }: { target: Vessel | null | undefined }) {
  const map = useMap()
  useEffect(() => {
    if (target) map.setView([target.lat, target.lon], 9, { animate: true })
  }, [map, target])
  return null
}

/** The Leaflet map + its active layers. */
export function VesselMap({
  vessels,
  layers,
  onSelect,
  trailVessel,
  trailPoints,
  focusTarget,
}: {
  vessels: Vessel[]
  layers: LayerState
  onSelect: (v: Vessel) => void
  trailVessel?: Vessel | null
  trailPoints?: TrackPoint[]
  focusTarget?: Vessel | null
}) {
  return (
    <MapContainer
      center={[20, 40]}
      zoom={3}
      minZoom={2}
      worldCopyJump
      className="h-full w-full"
      preferCanvas
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
      />
      <VesselLayer
        vessels={vessels}
        clustering={layers.clustering}
        headingArrows={layers.headingArrows}
        onSelect={onSelect}
      />
      {layers.chokepoints && <ChokepointLayer />}
      {trailVessel && trailPoints && trailPoints.length > 0 && (
        <TrailLayer
          mmsi={trailVessel.mmsi}
          points={trailPoints}
          color={colorFor(trailVessel.kind, trailVessel.segment)}
        />
      )}
      <MapFocuser target={focusTarget} />
    </MapContainer>
  )
}
