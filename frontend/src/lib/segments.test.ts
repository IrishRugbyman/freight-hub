import { describe, expect, it } from 'vitest'
import { colorFor, segmentsForKind, SEGMENTS_BY_KIND } from './segments'

describe('colorFor', () => {
  it('distinguishes same-named segments across kinds', () => {
    // both kinds have "Panamax" — colors must differ
    expect(colorFor('bulk', 'Panamax')).not.toBe(colorFor('tanker', 'Panamax'))
  })

  it('returns a stable hex per (kind, segment)', () => {
    expect(colorFor('tanker', 'VLCC')).toBe('#3b82f6')
    expect(colorFor('bulk', 'Capesize')).toBe('#ef4444')
  })

  it('falls back for unknown / missing segment', () => {
    expect(colorFor('bulk', null)).toBe('#9ca3af')
    expect(colorFor('tanker', 'Nope')).toBe('#9ca3af')
  })
})

describe('segmentsForKind', () => {
  it('returns the kind-specific ordered list', () => {
    expect(segmentsForKind('bulk')).toEqual(SEGMENTS_BY_KIND.bulk)
    expect(segmentsForKind('tanker')[0]).toBe('ULCC')
  })

  it('merges both (deduped) when no kind selected', () => {
    const all = segmentsForKind('')
    expect(all).toContain('Capesize')
    expect(all).toContain('VLCC')
    // "Small" appears in both but only once
    expect(all.filter((s) => s === 'Small')).toHaveLength(1)
  })
})
