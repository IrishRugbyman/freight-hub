// Freight cycle board: presentation logic. Pure, no React — unit-tested by cycle.test.ts.
//
// Two things this file exists to keep honest:
//   1. Threshold state -> colour. "breached" must never read as neutral.
//   2. Provenance tier -> label. A hand-recorded number must never look like a live one,
//      and an unverified secondary observation must say so.

export type CycleState = 'breached' | 'approaching' | 'holding' | 'unknown'
export type CycleTier = 'live' | 'registered' | 'missing'

const STATE_STYLES: Record<CycleState, { label: string; className: string; dot: string }> = {
  breached: {
    label: 'Threshold breached',
    className: 'text-red-400 border-red-500/40 bg-red-500/10',
    dot: 'bg-red-400',
  },
  approaching: {
    label: 'Approaching',
    className: 'text-amber-400 border-amber-500/40 bg-amber-500/10',
    dot: 'bg-amber-400',
  },
  holding: {
    label: 'Holding',
    className: 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10',
    dot: 'bg-emerald-400',
  },
  unknown: {
    label: 'No reading',
    className: 'text-muted-foreground border-border bg-muted/30',
    dot: 'bg-muted-foreground',
  },
}

export function stateStyle(state: string) {
  return STATE_STYLES[(state as CycleState) in STATE_STYLES ? (state as CycleState) : 'unknown']
}

const TIER_LABELS: Record<CycleTier, { label: string; hint: string; className: string }> = {
  live: {
    label: 'Live',
    hint: 'Computed from data this site ingests.',
    className: 'text-sky-300 border-sky-500/40 bg-sky-500/10',
  },
  registered: {
    label: 'Recorded',
    hint: 'A disclosed observation entered by hand. Never interpolated, never carried forward.',
    className: 'text-violet-300 border-violet-500/40 bg-violet-500/10',
  },
  missing: {
    label: 'Gap',
    hint: 'No acceptable source. Shown rather than hidden.',
    className: 'text-muted-foreground border-border bg-muted/30',
  },
}

export function tierBadge(tier: string) {
  return TIER_LABELS[(tier as CycleTier) in TIER_LABELS ? (tier as CycleTier) : 'missing']
}

/** Sort key: gaps last, then breached first — the tiles that change the read come first. */
export function signalSortKey(s: { tier: string; state: string }): number {
  if (s.tier === 'missing') return 9
  const order: Record<string, number> = { breached: 0, approaching: 1, holding: 2, unknown: 3 }
  return order[s.state] ?? 3
}

export function sortSignals<T extends { tier: string; state: string }>(signals: T[]): T[] {
  return [...signals].sort((a, b) => signalSortKey(a) - signalSortKey(b))
}

/** Human phrasing of the distance to the threshold, direction-aware. */
export function distanceLabel(
  distancePct: number | null | undefined,
  direction: string,
): string {
  if (distancePct == null) return 'no threshold distance'
  const magnitude = Math.abs(distancePct)
  const rounded = magnitude >= 10 ? magnitude.toFixed(0) : magnitude.toFixed(1)
  if (direction === 'below') {
    return distancePct >= 0 ? `${rounded}% above the floor` : `${rounded}% below the floor`
  }
  if (direction === 'above') {
    return distancePct <= 0 ? `${rounded}% below the cap` : `${rounded}% over the cap`
  }
  return `${rounded}% from the threshold`
}

/** Value formatting by unit. Index points get thousands separators; percentages a % sign. */
export function formatValue(value: number | null | undefined, unit: string): string {
  if (value == null) return '—'
  switch (unit) {
    case 'pct':
      return `${value.toFixed(1)}%`
    case 'ratio':
      return value.toFixed(2)
    case 'per_day':
      return `${value.toFixed(0)}/day`
    default:
      return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
  }
}

/**
 * Freshness wording for a tile.
 *
 * A stale `live` signal means the pipeline is behind; a stale `registered` one means
 * nobody has re-checked the source. Those are different failures and read differently.
 */
export function freshnessLabel(args: {
  tier: string
  stale: boolean
  asOf: string | null | undefined
}): { text: string; warn: boolean } {
  const { tier, stale, asOf } = args
  if (tier === 'missing') return { text: 'no source', warn: false }
  if (!asOf) return { text: 'never recorded', warn: true }
  if (!stale) return { text: `as of ${asOf}`, warn: false }
  return {
    text: tier === 'live' ? `feed behind — last ${asOf}` : `needs review — recorded ${asOf}`,
    warn: true,
  }
}

/** Normalise a sparkline to 0..1 for drawing. Flat series render mid-height, not divided by zero. */
export function sparkPath(values: number[], width: number, height: number): string {
  if (values.length < 2) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min
  const step = width / (values.length - 1)
  return values
    .map((v, i) => {
      const y = span === 0 ? height / 2 : height - ((v - min) / span) * height
      return `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}
