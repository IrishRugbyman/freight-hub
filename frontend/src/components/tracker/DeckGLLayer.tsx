/**
 * deck.gl WebGL overlay for the vessel tracker.
 * Replaces the canvas marker layer with GPU-rendered ScatterplotLayer + optional HeatmapLayer.
 * This file is lazy-loaded (React.lazy) to keep deck.gl out of the initial bundle.
 */
import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import { LeafletLayer } from 'deck.gl-leaflet'
import { ScatterplotLayer, PathLayer } from '@deck.gl/layers'
import { HeatmapLayer } from '@deck.gl/aggregation-layers'
import type { PickingInfo } from '@deck.gl/core'
import type { Vessel, TrackPoint } from '@/lib/api'
import { colorFor, hexToRgb } from '@/lib/segments'

export function DeckGLLayer({
  vessels,
  showHeatmap,
  onSelect,
  trailVessel,
  trailPoints,
}: {
  vessels: Vessel[]
  showHeatmap: boolean
  onSelect: (v: Vessel) => void
  trailVessel?: Vessel | null
  trailPoints?: TrackPoint[]
}) {
  const map = useMap()
  const layerRef = useRef<LeafletLayer | null>(null)

  // Build deck.gl layers from current props
  function buildLayers() {
    const layers = []

    if (showHeatmap) {
      layers.push(
        new HeatmapLayer({
          id: 'heatmap',
          data: vessels,
          getPosition: (d: Vessel) => [d.lon, d.lat],
          getWeight: 1,
          radiusPixels: 40,
          intensity: 1,
          threshold: 0.05,
        })
      )
    }

    layers.push(
      new ScatterplotLayer({
        id: 'vessels',
        data: vessels,
        getPosition: (d: Vessel) => [d.lon, d.lat],
        getFillColor: (d: Vessel) => hexToRgb(colorFor(d.kind, d.segment)),
        getRadius: (d: Vessel) => ((d.nav_status === 1 || d.nav_status === 5) || (d.sog ?? 99) < 0.3 ? 3 : 4),
        radiusUnits: 'pixels',
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 255, 255, 80],
        onClick: (info: PickingInfo) => {
          if (info.object) onSelect(info.object as Vessel)
        },
      })
    )

    if (trailVessel && trailPoints && trailPoints.length > 1) {
      const color = hexToRgb(colorFor(trailVessel.kind, trailVessel.segment))
      layers.push(
        new PathLayer({
          id: 'trail',
          data: [{ path: trailPoints.map((p) => [p.lon, p.lat]) }],
          getPath: (d) => d.path,
          getColor: [...color, 180] as [number, number, number, number],
          getWidth: 2,
          widthUnits: 'pixels',
        })
      )
    }

    return layers
  }

  // Create the LeafletLayer on mount
  useEffect(() => {
    const deckLayer = new LeafletLayer({ layers: buildLayers() })
    layerRef.current = deckLayer
    map.addLayer(deckLayer)
    return () => {
      map.removeLayer(deckLayer)
      layerRef.current = null
    }
  }, [map]) // eslint-disable-line react-hooks/exhaustive-deps

  // Update layers on every prop change without rebuilding the deck.gl instance
  useEffect(() => {
    layerRef.current?.setProps({ layers: buildLayers() })
  })

  return null
}
