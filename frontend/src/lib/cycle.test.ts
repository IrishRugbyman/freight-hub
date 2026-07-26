import { describe, expect, it } from 'vitest'
import {
  distanceLabel,
  formatValue,
  freshnessLabel,
  signalSortKey,
  sortSignals,
  sparkPath,
  stateStyle,
  tierBadge,
} from './cycle'

describe('stateStyle', () => {
  it('gives breached a red treatment so it cannot read as neutral', () => {
    expect(stateStyle('breached').className).toContain('red')
    expect(stateStyle('breached').label).toBe('Threshold breached')
  })

  it('falls back to the unknown style for an unrecognised state', () => {
    expect(stateStyle('sideways')).toBe(stateStyle('unknown'))
  })
})

describe('tierBadge', () => {
  it('labels a hand-recorded observation distinctly from a live one', () => {
    expect(tierBadge('live').label).toBe('Live')
    expect(tierBadge('registered').label).toBe('Recorded')
    expect(tierBadge('registered').className).not.toBe(tierBadge('live').className)
  })

  it('treats an unrecognised tier as a gap rather than as live data', () => {
    expect(tierBadge('nonsense').label).toBe('Gap')
  })
})

describe('sortSignals', () => {
  it('puts breached first and gaps last', () => {
    const signals = [
      { id: 'a', tier: 'live', state: 'holding' },
      { id: 'b', tier: 'missing', state: 'unknown' },
      { id: 'c', tier: 'live', state: 'breached' },
      { id: 'd', tier: 'registered', state: 'approaching' },
    ]

    expect(sortSignals(signals).map((s) => s.id)).toEqual(['c', 'd', 'a', 'b'])
  })

  it('does not mutate its input', () => {
    const signals = [
      { tier: 'missing', state: 'unknown' },
      { tier: 'live', state: 'breached' },
    ]
    const copy = [...signals]
    sortSignals(signals)

    expect(signals).toEqual(copy)
  })

  it('ranks a missing tier behind everything regardless of its state', () => {
    expect(signalSortKey({ tier: 'missing', state: 'breached' })).toBeGreaterThan(
      signalSortKey({ tier: 'live', state: 'unknown' }),
    )
  })
})

describe('distanceLabel', () => {
  it('reads a below-threshold signal as headroom above a floor', () => {
    expect(distanceLabel(82.9, 'below')).toBe('83% above the floor')
  })

  it('reads an above-threshold signal as headroom below a cap', () => {
    expect(distanceLabel(-53.3, 'above')).toBe('53% below the cap')
  })

  it('says when a cap has been exceeded', () => {
    expect(distanceLabel(12.0, 'above')).toBe('12% over the cap')
  })

  it('keeps one decimal for small distances', () => {
    expect(distanceLabel(4.25, 'below')).toBe('4.3% above the floor')
  })

  it('handles a missing distance without pretending to know one', () => {
    expect(distanceLabel(null, 'below')).toBe('no threshold distance')
    expect(distanceLabel(undefined, 'none')).toBe('no threshold distance')
  })
})

describe('formatValue', () => {
  it('formats index points, percentages, ratios and rates by unit', () => {
    expect(formatValue(2743, 'index')).toBe('2,743')
    expect(formatValue(7, 'pct')).toBe('7.0%')
    expect(formatValue(2.117, 'ratio')).toBe('2.12')
    expect(formatValue(109.36, 'per_day')).toBe('109/day')
  })

  it('renders a dash rather than a zero when there is no value', () => {
    expect(formatValue(null, 'index')).toBe('—')
    expect(formatValue(undefined, 'pct')).toBe('—')
  })

  it('does not swallow a genuine zero', () => {
    expect(formatValue(0, 'per_day')).toBe('0/day')
  })
})

describe('freshnessLabel', () => {
  it('is quiet when a reading is current', () => {
    expect(freshnessLabel({ tier: 'live', stale: false, asOf: '2026-07-24' })).toEqual({
      text: 'as of 2026-07-24',
      warn: false,
    })
  })

  it('distinguishes a lagging feed from an unreviewed manual entry', () => {
    expect(freshnessLabel({ tier: 'live', stale: true, asOf: '2026-07-01' }).text).toContain(
      'feed behind',
    )
    expect(freshnessLabel({ tier: 'registered', stale: true, asOf: '2025-11-10' }).text).toContain(
      'needs review',
    )
  })

  it('warns when nothing was ever recorded', () => {
    expect(freshnessLabel({ tier: 'registered', stale: true, asOf: null })).toEqual({
      text: 'never recorded',
      warn: true,
    })
  })

  it('does not warn on a published gap, which is already the worst case', () => {
    expect(freshnessLabel({ tier: 'missing', stale: false, asOf: null })).toEqual({
      text: 'no source',
      warn: false,
    })
  })
})

describe('sparkPath', () => {
  it('draws from left to right across the full width', () => {
    const d = sparkPath([1, 2, 3], 100, 20)

    expect(d.startsWith('M0.00,')).toBe(true)
    expect(d).toContain('L100.00,')
  })

  it('puts a rising series at the bottom on the left and the top on the right', () => {
    const [first, last] = sparkPath([1, 5], 10, 10).split(' ')

    expect(first).toBe('M0.00,10.00')
    expect(last).toBe('L10.00,0.00')
  })

  it('draws a flat series through the middle instead of dividing by zero', () => {
    expect(sparkPath([4, 4, 4], 10, 10)).toBe('M0.00,5.00 L5.00,5.00 L10.00,5.00')
  })

  it('returns nothing for a series too short to draw', () => {
    expect(sparkPath([], 10, 10)).toBe('')
    expect(sparkPath([1], 10, 10)).toBe('')
  })
})
