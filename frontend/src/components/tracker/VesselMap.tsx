import { useEffect, Suspense, lazy } from 'react'
import { MapContainer, TileLayer, ZoomControl, useMap } from 'react-leaflet'
import type { Vessel, TrackPoint } from '@/lib/api'
import type { LayerState } from './types'
import { VesselLayer } from './VesselLayer'
import { ChokepointLayer } from './ChokepointLayer'
import { TrailLayer } from './TrailLayer'
import { EventPinsLayer } from './EventPinsLayer'
import { RiskLayer } from './RiskLayer'
import { useHighRiskPositions } from '@/lib/api'
import { colorFor } from '@/lib/segments'

const DeckGLLayer = lazy(() =>
  import('./DeckGLLayer').then((m) => ({ default: m.DeckGLLayer }))
)

function MapFocuser({ target }: { target: { lat: number; lon: number } | null | undefined }) {
  const map = useMap()
  useEffect(() => {
    if (target) map.setView([target.lat, target.lon], 12, { animate: true })
  }, [map, target])
  return null
}

/** The Leaflet map + its active layers. Zoom control moved to bottom-right. */
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
  focusTarget?: { lat: number; lon: number } | null
}) {
  const { data: riskData } = useHighRiskPositions(60, layers.riskOverlay)
  const riskPositions = layers.riskOverlay ? (riskData?.rows ?? []) : []

  return (
    <MapContainer
      center={[20, 40]}
      zoom={3}
      minZoom={2}
      worldCopyJump
      className="h-full w-full"
      preferCanvas
      zoomControl={false}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
      />
      <ZoomControl position="bottomright" />
      {layers.deckgl ? (
        <Suspense fallback={null}>
          <DeckGLLayer
            vessels={vessels}
            showHeatmap={layers.heatmap}
            onSelect={onSelect}
            trailVessel={trailVessel}
            trailPoints={trailPoints ?? []}
          />
        </Suspense>
      ) : (
        <>
          <VesselLayer
            vessels={vessels}
            clustering={layers.clustering}
            headingArrows={layers.headingArrows}
            onSelect={onSelect}
          />
          {trailVessel && trailPoints && trailPoints.length > 0 && (
            <TrailLayer
              mmsi={trailVessel.mmsi}
              points={trailPoints}
              color={colorFor(trailVessel.kind, trailVessel.segment)}
            />
          )}
        </>
      )}
      {layers.chokepoints && <ChokepointLayer />}
      <EventPinsLayer visible={layers.eventPins} />
      {layers.riskOverlay && riskPositions.length > 0 && (
        <RiskLayer
          positions={riskPositions}
          onSelect={(pos) => {
            const v = vessels.find((vessel) => vessel.mmsi === pos.mmsi)
            if (v) onSelect(v)
          }}
        />
      )}
      <MapFocuser target={focusTarget} />
    </MapContainer>
  )
}
