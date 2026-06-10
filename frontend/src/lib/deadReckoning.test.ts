import { describe, it, expect } from 'vitest'
import { projectPosition } from './deadReckoning'

const NEAR_ZERO = 1e-9

describe('projectPosition', () => {
  it('returns null for null sog', () => {
    expect(projectPosition(0, 0, null, 90, 60)).toBeNull()
  })

  it('returns null for null cog', () => {
    expect(projectPosition(0, 0, 10, null, 60)).toBeNull()
  })

  it('returns null when sog < 0.3 kn (anchored)', () => {
    expect(projectPosition(0, 0, 0.2, 90, 60)).toBeNull()
    expect(projectPosition(0, 0, 0, 0, 3600)).toBeNull()
  })

  it('moves north when cog=0', () => {
    // 6 knots due north for 600s = 1 nm = 1/60 deg lat
    const r = projectPosition(0, 0, 6, 0, 600)
    expect(r).not.toBeNull()
    expect(r!.lat).toBeCloseTo(1 / 60, 8)
    expect(Math.abs(r!.lon)).toBeLessThan(NEAR_ZERO)
  })

  it('moves east when cog=90 at equator', () => {
    // 6 knots due east at equator for 600s = 1 nm = 1/60 deg lon
    const r = projectPosition(0, 0, 6, 90, 600)
    expect(r).not.toBeNull()
    expect(Math.abs(r!.lat)).toBeLessThan(NEAR_ZERO)
    expect(r!.lon).toBeCloseTo(1 / 60, 8)
  })

  it('lon displacement is larger at low latitude than high latitude for same cog=90', () => {
    // At 60N, cos(60)=0.5, so same nm moves 2x more degrees lon than at equator
    const eq = projectPosition(0, 0, 10, 90, 600)!
    const hi = projectPosition(60, 0, 10, 90, 600)!
    expect(hi.lon).toBeCloseTo(eq.lon * 2, 5)
  })

  it('caps dt at 600s to prevent large jumps', () => {
    const capped = projectPosition(0, 0, 10, 0, 600)!
    const long = projectPosition(0, 0, 10, 0, 6000)!
    // Both should produce the same result since 6000 is capped to 600
    expect(long.lat).toBeCloseTo(capped.lat, 10)
  })

  it('works at 0.3 kn boundary (not null)', () => {
    expect(projectPosition(0, 0, 0.3, 0, 60)).not.toBeNull()
  })
})
