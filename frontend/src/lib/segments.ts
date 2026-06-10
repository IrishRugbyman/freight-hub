// Vessel segment ordering + colors. Pure, no React — unit-tested by segments.test.ts.

export type Kind = 'bulk' | 'tanker'

export const SEGMENTS_BY_KIND: Record<Kind, string[]> = {
  // largest -> smallest
  bulk: ['Capesize', 'Panamax', 'Supramax', 'Handymax', 'Handysize', 'Small'],
  tanker: ['ULCC', 'VLCC', 'Suezmax', 'Aframax', 'Panamax', 'Handymax', 'Handysize', 'Small'],
}

const BULK_COLORS: Record<string, string> = {
  Capesize: '#ef4444',
  Panamax: '#f97316',
  Supramax: '#f59e0b',
  Handymax: '#eab308',
  Handysize: '#a3a635',
  Small: '#6b7280',
}

const TANKER_COLORS: Record<string, string> = {
  ULCC: '#7c3aed',
  VLCC: '#3b82f6',
  Suezmax: '#06b6d4',
  Aframax: '#14b8a6',
  Panamax: '#10b981',
  Handymax: '#34d399',
  Handysize: '#6ee7b7',
  Small: '#64748b',
}

const FALLBACK = '#9ca3af'

/** Marker color for a vessel, keyed by (kind, segment) since segment names repeat across kinds. */
export function colorFor(kind: string | null | undefined, segment: string | null | undefined): string {
  if (!segment) return FALLBACK
  const table = kind === 'tanker' ? TANKER_COLORS : BULK_COLORS
  return table[segment] ?? FALLBACK
}

/** Segments available for a kind filter (empty kind → both, bulk first). */
export function segmentsForKind(kind: Kind | '' | null | undefined): string[] {
  if (kind === 'bulk' || kind === 'tanker') return SEGMENTS_BY_KIND[kind]
  return [...SEGMENTS_BY_KIND.bulk, ...SEGMENTS_BY_KIND.tanker.filter((s) => !SEGMENTS_BY_KIND.bulk.includes(s))]
}
