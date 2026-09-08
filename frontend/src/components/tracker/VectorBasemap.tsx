import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import type { Layer } from 'leaflet'
import { loadBasemapStyle } from '@/lib/basemap'

/**
 * MapLibre vector basemap mounted as a Leaflet layer.
 *
 * Deliberately a layer swap and not a migration: every vessel layer, the cluster
 * plugin, the deck.gl overlay and the chokepoint/pipeline/risk layers stay on
 * Leaflet and are untouched. Only the tiles underneath change.
 *
 * maplibre-gl is ~800 KB, so it is imported dynamically - the map routes pull it,
 * the rest of the app does not pay for it.
 */
export function VectorBasemap() {
  const map = useMap()

  useEffect(() => {
    let layer: Layer | null = null
    let cancelled = false

    void (async () => {
      const [{ maplibreGL }, style] = await Promise.all([
        import('@maplibre/maplibre-gl-leaflet'),
        loadBasemapStyle(),
      ])
      // The map can unmount while those are in flight; adding a layer to a
      // torn-down Leaflet map throws on its internal _mapPane.
      if (cancelled) return

      layer = maplibreGL({ style }) as unknown as Layer
      layer.addTo(map)
      // No attribution is set here on purpose. Both styles declare it in their own
      // TileJSON and maplibre propagates that into Leaflet's control, so adding our
      // own both duplicated the line and, worse, would have credited CARTO while the
      // OpenFreeMap fallback was actually serving the tiles.
    })()

    return () => {
      cancelled = true
      if (layer) map.removeLayer(layer)
    }
  }, [map])

  return null
}
