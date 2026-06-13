import type { VesselFilters } from '@/lib/api'

export interface LayerState {
  clustering: boolean
  headingArrows: boolean
  counts: boolean
  chokepoints: boolean
  eventPins: boolean
  deckgl: boolean
  heatmap: boolean
  riskOverlay: boolean
}

export const DEFAULT_LAYERS: LayerState = {
  clustering: true,
  headingArrows: true,
  counts: true,
  chokepoints: false,
  eventPins: false,
  deckgl: false,
  heatmap: false,
  riskOverlay: true,
}

export type Filters = VesselFilters
