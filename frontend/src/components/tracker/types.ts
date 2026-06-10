import type { VesselFilters } from '@/lib/api'

export interface LayerState {
  clustering: boolean
  headingArrows: boolean
  counts: boolean
  chokepoints: boolean
  eventPins: boolean
}

export const DEFAULT_LAYERS: LayerState = {
  clustering: true,
  headingArrows: true,
  counts: true,
  chokepoints: false,
  eventPins: true,
}

export type Filters = VesselFilters
