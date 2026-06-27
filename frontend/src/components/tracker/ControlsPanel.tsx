import { useState } from 'react'
import { SlidersHorizontal, X } from 'lucide-react'
import { useFleetFlags, type Vessel } from '@/lib/api'
import type { Filters, LayerState } from './types'
import { FilterControls } from './FilterControls'
import { LayerToggles } from './LayerToggles'
import { VesselSearch } from './VesselSearch'
import { Panel } from '@/components/ui/panel'

/** Collapsible left panel combining search, filters, and layer toggles. */
export function ControlsPanel({
  filters,
  layers,
  regions,
  vessels,
  onFiltersChange,
  onLayersChange,
  onVesselSelect,
}: {
  filters: Filters
  layers: LayerState
  regions: string[]
  vessels: Vessel[]
  onFiltersChange: (f: Filters) => void
  onLayersChange: (l: LayerState) => void
  onVesselSelect: (v: Vessel) => void
}) {
  const [open, setOpen] = useState(true)
  const { data: fleetFlags } = useFleetFlags(100)
  const flagOptions = (fleetFlags?.rows ?? [])
    .filter((r) => r.flag_code)
    .map((r) => ({ code: r.flag_code as string, name: r.flag }))

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card/90 shadow-lg backdrop-blur-sm hover:bg-card"
        title="Open controls"
      >
        <SlidersHorizontal size={16} />
      </button>
    )
  }

  return (
    <Panel className="flex w-56 max-h-[calc(100vh-5rem)] flex-col overflow-hidden">
      {/* sticky header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <span className="text-[11px] font-semibold text-muted-foreground">Controls</span>
        <button
          onClick={() => setOpen(false)}
          className="text-muted-foreground hover:text-foreground"
          title="Collapse"
        >
          <X size={14} />
        </button>
      </div>

      {/* scrollable body */}
      <div className="overflow-y-auto">
        {/* Search */}
        <div className="border-b border-border px-3 py-2">
          <VesselSearch vessels={vessels} onSelect={onVesselSelect} />
        </div>

        {/* Filters */}
        <FilterControls
          filters={filters}
          regions={regions}
          flags={flagOptions}
          onChange={onFiltersChange}
        />

        {/* Layer toggles */}
        <div className="border-t border-border px-3 py-2">
          <div className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">Layers</div>
          <LayerToggles layers={layers} onChange={onLayersChange} />
        </div>
      </div>
    </Panel>
  )
}
