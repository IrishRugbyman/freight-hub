import { MapContainer, TileLayer } from 'react-leaflet'
import type { Vessel } from '@/lib/api'
import type { LayerState } from './types'
import { VesselLayer } from './VesselLayer'
import { ChokepointLayer } from './ChokepointLayer'

/** The Leaflet map + its active layers. */
export function VesselMap({
  vessels,
  layers,
  onSelect,
}: {
  vessels: Vessel[]
  layers: LayerState
  onSelect: (v: Vessel) => void
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
    </MapContainer>
  )
}
