import { describe, it, expect } from 'vitest'
import { searchVessels } from './VesselSearch'
import type { Vessel } from '@/lib/api'

const BASE: Omit<Vessel, 'mmsi' | 'name' | 'destination'> = {
  lat: 0, lon: 0, kind: 'tanker', segment: 'VLCC', region: null,
  sog: null, cog: null, heading: null, updated_ts: '2026-01-01T00:00:00',
  imo: null, draught: null, nav_status: null, eta: null,
  flag: null, flag_code: null, flag_foc: false, flag_shadow: false,
}

const VESSELS: Vessel[] = [
  { ...BASE, mmsi: 123456789, name: 'EAGLE CAPE', destination: 'CNSHA' },
  { ...BASE, mmsi: 987654321, name: 'SEA LION', destination: 'USGVS' },
  { ...BASE, mmsi: 111111111, name: null, destination: 'SGSIN' },
]

describe('searchVessels', () => {
  it('returns empty for blank query', () => {
    expect(searchVessels(VESSELS, '')).toEqual([])
    expect(searchVessels(VESSELS, '  ')).toEqual([])
  })

  it('matches vessel name case-insensitively', () => {
    expect(searchVessels(VESSELS, 'eagle')).toHaveLength(1)
    expect(searchVessels(VESSELS, 'EAGLE')).toHaveLength(1)
  })

  it('matches MMSI string', () => {
    expect(searchVessels(VESSELS, '987654')).toHaveLength(1)
    expect(searchVessels(VESSELS, '987654')[0].name).toBe('SEA LION')
  })

  it('matches destination', () => {
    expect(searchVessels(VESSELS, 'sgsin')).toHaveLength(1)
  })

  it('caps results at 20', () => {
    const many: Vessel[] = Array.from({ length: 50 }, (_, i) => ({
      ...BASE, mmsi: i, name: `VESSEL ${i}`, destination: null,
    }))
    expect(searchVessels(many, 'vessel')).toHaveLength(20)
  })
})
