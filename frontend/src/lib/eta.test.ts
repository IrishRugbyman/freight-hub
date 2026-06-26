import { describe, expect, it } from 'vitest'

import { etaMethodBadgeClass, etaUrgencyClass, formatEtaHours, resolveEta } from './eta'

describe('formatEtaHours', () => {
  it('renders minutes under an hour', () => {
    expect(formatEtaHours(0.5)).toBe('30m')
    expect(formatEtaHours(0.05)).toBe('3m')
  })
  it('renders hours under two days', () => {
    expect(formatEtaHours(12.34)).toBe('12.3h')
    expect(formatEtaHours(47.9)).toBe('47.9h')
  })
  it('renders days at two days and beyond', () => {
    expect(formatEtaHours(48)).toBe('2.0d')
    expect(formatEtaHours(120)).toBe('5.0d')
  })
  it('handles null / invalid / negative', () => {
    expect(formatEtaHours(null)).toBe('-')
    expect(formatEtaHours(undefined)).toBe('-')
    expect(formatEtaHours(Number.NaN)).toBe('-')
    expect(formatEtaHours(-3)).toBe('-')
  })
})

describe('etaUrgencyClass', () => {
  it('ramps from hot (soon) to muted (far)', () => {
    expect(etaUrgencyClass(3)).toContain('orange')
    expect(etaUrgencyClass(12)).toContain('yellow')
    expect(etaUrgencyClass(40)).toContain('muted')
    expect(etaUrgencyClass(null)).toContain('muted')
  })
})

describe('etaMethodBadgeClass', () => {
  it('maps known methods and falls back to naive styling', () => {
    expect(etaMethodBadgeClass('physics')).toContain('sky')
    expect(etaMethodBadgeClass('ml')).toContain('violet')
    expect(etaMethodBadgeClass('naive')).toContain('zinc')
    expect(etaMethodBadgeClass(null)).toContain('zinc')
    expect(etaMethodBadgeClass('???')).toContain('zinc')
  })
})

describe('resolveEta', () => {
  const base = { eta_true_h: null, eta_low_h: null, eta_high_h: null, eta_naive_h: null, eta_method: null }

  it('uses the true ETA + band when present', () => {
    const d = resolveEta(
      { ...base, eta_true_h: 10, eta_low_h: 8, eta_high_h: 14, eta_naive_h: 9, eta_method: 'physics' },
      9,
    )
    expect(d.hasTrue).toBe(true)
    expect(d.primaryH).toBe(10)
    expect(d.primaryLabel).toBe('10.0h')
    expect(d.bandLabel).toBe('8.0h-14.0h')
    expect(d.method).toBe('physics')
    expect(d.deltaH).toBeCloseTo(1)
    expect(d.tooltip).toContain('vs naive')
  })

  it('falls back to the naive value when no true estimate resolved', () => {
    const d = resolveEta(base, 5.5)
    expect(d.hasTrue).toBe(false)
    expect(d.primaryH).toBe(5.5)
    expect(d.primaryLabel).toBe('5.5h')
    expect(d.bandLabel).toBeNull()
    expect(d.method).toBeNull()
    expect(d.deltaH).toBeNull()
  })

  it('omits the band when only a point true ETA is present', () => {
    const d = resolveEta({ ...base, eta_true_h: 20, eta_method: 'physics' }, 18)
    expect(d.hasTrue).toBe(true)
    expect(d.bandLabel).toBeNull()
  })
})
