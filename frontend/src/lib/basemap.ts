/**
 * Vector basemap for the tracker and pipelines maps.
 *
 * Why vector and not the raster tiles this used to use: in late August 2026 CARTO
 * started watermarking its keyless raster endpoint ("API KEY REQUIRED") and is
 * retiring raster entirely. The watermark arrived as HTTP 200 with a valid PNG, so
 * nothing 4xx'd, nothing hit the console, and the map was defaced silently. Vector
 * is keyless today, still maintained, and - the point here - restylable at runtime.
 *
 * The palette is MarineTraffic's: near-black water, lighter blue-grey land. That is
 * the inverse of CARTO's dark-matter defaults, where water is *lighter* than land,
 * and it matters because vessel markers sit on water. On dark water the segment
 * colours read immediately; on dark-matter they muddy into the sea.
 */

import type { StyleSpecification } from 'maplibre-gl'

/** Water: near-black, so vessel-segment colours carry the contrast. */
export const WATER = '#191F24'
/** Land: lighter blue-grey, pushed behind the data rather than competing with it. */
export const LAND = '#32414E'
/** Admin boundaries: visible against LAND without drawing the eye. */
export const BORDER = '#4a5b6a'

const CARTO_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
/**
 * Keyless, unmetered, no signup. Second line of defence: if CARTO ever refuses us,
 * the map degrades to a different provider instead of going blank.
 */
const FALLBACK_STYLE = 'https://tiles.openfreemap.org/styles/dark'

/**
 * CARTO basemaps key, free tier, 5M tile requests/month across raster and vector.
 *
 * Vector does NOT require it today - CARTO confirmed in writing that the key
 * requirement is coming to vector but is not live, and that they will give notice.
 * It is threaded through anyway because a verified-harmless param now (both keyed
 * and unkeyed vector tiles return 200) is worth more than an outage later. Being
 * in the bundle is expected: a browser basemap key is public by construction, which
 * is why CARTO's own instructions put it in the tile URL.
 */
const CARTO_KEY = import.meta.env.VITE_CARTO_KEY as string | undefined

/** Append the CARTO key to a cartocdn URL, leaving other hosts (the fallback) alone. */
function withKey(url: string): string {
  if (!CARTO_KEY || !url.includes('cartocdn.com')) return url
  return url + (url.includes('?') ? '&' : '?') + `key=${encodeURIComponent(CARTO_KEY)}`
}

/**
 * Layer ids are PINNED, not matched by substring.
 *
 * Substring matching ("does the id contain 'water'") silently stops painting the
 * day the upstream style renames a layer - the same failure shape as the watermark:
 * no error, just a map that quietly looks wrong. Pinning means drift is loud, see
 * the missing-id warning in applyPalette().
 */
const WATER_FILL_LAYERS = ['water', 'water_shadow']
const WATER_LINE_LAYERS = ['waterway']
const LAND_FILL_LAYERS = [
  'landcover',
  'park_national_park',
  'park_nature_reserve',
  'landuse_residential',
  'landuse',
]

type StyleSource = { url?: string; tiles?: string[]; [k: string]: unknown }

/**
 * Loose mirror of the style document for mutation. MapLibre's own
 * `StyleSpecification` types `paint` as a discriminated union per layer type,
 * which makes "set fill-color on whichever layers I pinned" unwriteable without
 * a cast per layer. We repaint against this shape and validate once on the way
 * out (see assertStyle) rather than casting blindly at every assignment.
 */
type StyleSpec = {
  version?: unknown
  layers: Array<{ id: string; type: string; paint?: Record<string, unknown> }>
  sources?: Record<string, StyleSource>
  [k: string]: unknown
}

/**
 * Confirm the parsed document really is a style before MapLibre gets it. A CDN
 * that returns an error page with HTTP 200 - exactly what the raster watermark
 * did - should fail here and hit the fallback, not render as a broken map.
 */
function assertStyle(style: StyleSpec, origin: string): StyleSpecification {
  if (typeof style.version !== 'number' || !Array.isArray(style.layers)) {
    throw new Error(`${origin} did not return a MapLibre style document`)
  }
  return style as unknown as StyleSpecification
}

/**
 * Thread the key through every CARTO URL the style will go on to request: the
 * TileJSON document a source points at, and any inline tile templates. Without
 * this the key would only cover the style document itself, which is not where the
 * request volume - or the future key check - actually lands.
 */
function keySources(style: StyleSpec): StyleSpec {
  if (!CARTO_KEY || !style.sources) return style
  for (const source of Object.values(style.sources)) {
    if (typeof source.url === 'string') source.url = withKey(source.url)
    if (Array.isArray(source.tiles)) source.tiles = source.tiles.map(withKey)
  }
  return style
}

/**
 * Repaint an OpenMapTiles-schema style to the MarineTraffic palette.
 *
 * Mutates and returns the style. Any pinned id that is absent upstream is reported
 * through `onDrift` rather than passing unnoticed.
 */
export function applyPalette(style: StyleSpec, onDrift?: (missing: string[]) => void): StyleSpec {
  const seen = new Set<string>()

  for (const layer of style.layers) {
    if (!layer.paint) layer.paint = {}
    const paint = layer.paint

    if (layer.type === 'background') {
      paint['background-color'] = LAND
      seen.add('background')
      continue
    }
    if (WATER_FILL_LAYERS.includes(layer.id)) {
      paint['fill-color'] = WATER
      paint['fill-opacity'] = 1
      seen.add(layer.id)
      continue
    }
    if (WATER_LINE_LAYERS.includes(layer.id)) {
      paint['line-color'] = WATER
      seen.add(layer.id)
      continue
    }
    if (LAND_FILL_LAYERS.includes(layer.id)) {
      paint['fill-color'] = LAND
      paint['fill-opacity'] = 1
      seen.add(layer.id)
      continue
    }
    if (layer.id.startsWith('boundary_') || layer.id.startsWith('admin')) {
      if (layer.type === 'line') paint['line-color'] = BORDER
    }
  }

  const expected = ['background', ...WATER_FILL_LAYERS, ...WATER_LINE_LAYERS, ...LAND_FILL_LAYERS]
  const missing = expected.filter((id) => !seen.has(id))
  if (missing.length > 0) onDrift?.(missing)

  return style
}

/**
 * Fetch the basemap style and repaint it, falling back to OpenFreeMap if CARTO is
 * unreachable. Returns a style object for `maplibreGL({ style })`.
 */
export async function loadBasemapStyle(): Promise<StyleSpecification> {
  const warnDrift = (missing: string[]) =>
    console.warn(
      `[basemap] upstream style dropped pinned layer(s): ${missing.join(', ')} - ` +
        'the palette is no longer fully applied, re-pin against the live style.'
    )

  try {
    const res = await fetch(withKey(CARTO_STYLE))
    if (!res.ok) throw new Error(`carto style HTTP ${res.status}`)
    const style = keySources(applyPalette((await res.json()) as StyleSpec, warnDrift))
    return assertStyle(style, 'CARTO')
  } catch (err) {
    console.warn('[basemap] CARTO style unavailable, falling back to OpenFreeMap', err)
    const res = await fetch(FALLBACK_STYLE)
    if (!res.ok) throw new Error(`fallback style HTTP ${res.status}`)
    const style = applyPalette((await res.json()) as StyleSpec, warnDrift)
    return assertStyle(style, 'OpenFreeMap')
  }
}
