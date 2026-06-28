import type { VesselFilters } from '@/lib/api'

export interface LayerState {
  clustering: boolean
  headingArrows: boolean
  counts: boolean
  chokepoints: boolean
  eventPins: boolean
  pipelines: boolean
  deckgl: boolean
  heatmap: boolean
  riskOverlay: boolean
  oceanOnly: boolean
  hideStale: boolean
}

export const DEFAULT_LAYERS: LayerState = {
  clustering: false,
  headingArrows: true,
  counts: true,
  chokepoints: false,
  eventPins: false,
  pipelines: false,
  deckgl: false,
  heatmap: false,
  riskOverlay: false,
  oceanOnly: true,
  hideStale: false,
}

export type Filters = VesselFilters
