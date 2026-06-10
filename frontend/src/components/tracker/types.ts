import type { VesselFilters } from '@/lib/api'

export interface LayerState {
  clustering: boolean
  headingArrows: boolean
  counts: boolean
  chokepoints: boolean
}

export const DEFAULT_LAYERS: LayerState = {
  clustering: true,
  headingArrows: true,
  counts: true,
  chokepoints: false,
}

export type Filters = VesselFilters
