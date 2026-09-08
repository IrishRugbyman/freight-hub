import { describe, it, expect, vi } from 'vitest'
import { applyPalette, WATER, LAND, BORDER } from './basemap'

/**
 * Minimal stand-in for the CARTO dark-matter document, using its real layer ids.
 * Built per-test rather than shared: applyPalette mutates, so a module-level
 * fixture would leak paint between tests and let a broken case pass on a
 * neighbour's writes.
 */
function styleFixture() {
  return {
    version: 8,
    layers: [
      { id: 'background', type: 'background' as const },
      { id: 'water', type: 'fill' as const },
      { id: 'water_shadow', type: 'fill' as const },
      { id: 'waterway', type: 'line' as const },
      { id: 'landcover', type: 'fill' as const },
      { id: 'park_national_park', type: 'fill' as const },
      { id: 'park_nature_reserve', type: 'fill' as const },
      { id: 'landuse_residential', type: 'fill' as const },
      { id: 'landuse', type: 'fill' as const },
      { id: 'boundary_country', type: 'line' as const },
      { id: 'place_label', type: 'symbol' as const, paint: { 'text-color': '#fff' } },
    ],
  }
}

function paintOf(style: ReturnType<typeof styleFixture>, id: string): Record<string, unknown> {
  return style.layers.find((l) => l.id === id)?.paint ?? {}
}

describe('applyPalette', () => {
  it('paints water near-black so vessel markers keep the contrast', () => {
    const style = styleFixture()
    applyPalette(style)
    expect(paintOf(style, 'water')['fill-color']).toBe(WATER)
    expect(paintOf(style, 'water_shadow')['fill-color']).toBe(WATER)
    expect(paintOf(style, 'waterway')['line-color']).toBe(WATER)
  })

  it('paints land lighter than water, the inverse of the upstream default', () => {
    const style = styleFixture()
    applyPalette(style)
    expect(paintOf(style, 'background')['background-color']).toBe(LAND)
    expect(paintOf(style, 'landcover')['fill-color']).toBe(LAND)
    expect(paintOf(style, 'landuse')['fill-color']).toBe(LAND)
    // The whole point of the palette: land must be brighter than water, or the
    // markers muddy into the sea. Compare luminance, not the literals.
    const lum = (hex: string) => {
      const n = parseInt(hex.slice(1), 16)
      return ((n >> 16) & 255) * 0.299 + ((n >> 8) & 255) * 0.587 + (n & 255) * 0.114
    }
    expect(lum(LAND)).toBeGreaterThan(lum(WATER))
  })

  it('forces water and land fills fully opaque', () => {
    // dark-matter ships fractional fill-opacity on some fills; leaving it alone
    // lets the layer beneath bleed through and desaturates the palette.
    const style = styleFixture()
    applyPalette(style)
    expect(paintOf(style, 'water')['fill-opacity']).toBe(1)
    expect(paintOf(style, 'landcover')['fill-opacity']).toBe(1)
  })

  it('recolours admin boundaries without touching other line layers', () => {
    const style = styleFixture()
    applyPalette(style)
    expect(paintOf(style, 'boundary_country')['line-color']).toBe(BORDER)
  })

  it('leaves unpinned layers alone', () => {
    const style = styleFixture()
    applyPalette(style)
    expect(paintOf(style, 'place_label')['text-color']).toBe('#fff')
  })

  it('reports drift when upstream drops a pinned layer', () => {
    // The failure this guards: CARTO renames or removes a layer, the palette
    // silently stops applying, and the map just looks wrong with no error -
    // the same shape as the raster watermark that started all this.
    const style = styleFixture()
    style.layers = style.layers.filter((l) => l.id !== 'water' && l.id !== 'landuse')
    const onDrift = vi.fn()
    applyPalette(style, onDrift)
    expect(onDrift).toHaveBeenCalledTimes(1)
    expect(onDrift.mock.calls[0][0].sort()).toEqual(['landuse', 'water'])
  })

  it('does not report drift when every pinned layer is present', () => {
    const onDrift = vi.fn()
    applyPalette(styleFixture(), onDrift)
    expect(onDrift).not.toHaveBeenCalled()
  })

  it('creates a paint object on layers that ship without one', () => {
    const layer: { id: string; type: string; paint?: Record<string, unknown> } = {
      id: 'water',
      type: 'fill',
    }
    expect(layer).not.toHaveProperty('paint')
    applyPalette({ version: 8, layers: [layer] })
    expect(layer.paint).toEqual({ 'fill-color': WATER, 'fill-opacity': 1 })
  })

  it('handles a style with no layers without throwing', () => {
    const onDrift = vi.fn()
    expect(() => applyPalette({ version: 8, layers: [] }, onDrift)).not.toThrow()
    // Everything is missing, so drift must fire rather than pass silently.
    expect(onDrift).toHaveBeenCalledTimes(1)
  })
})
