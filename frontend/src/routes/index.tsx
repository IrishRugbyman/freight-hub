import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { useMeta, useVessels, type Vessel } from '@/lib/api'
import { Panel } from '@/components/ui/panel'
import { FilterControls } from '@/components/tracker/FilterControls'
import { LayerToggles } from '@/components/tracker/LayerToggles'
import { CountsPanel } from '@/components/tracker/CountsPanel'
import { VesselDetail } from '@/components/tracker/VesselDetail'
import { VesselMap } from '@/components/tracker/VesselMap'
import { DEFAULT_LAYERS, type Filters, type LayerState } from '@/components/tracker/types'

export const Route = createFileRoute('/')({ component: TrackerPage })

function TrackerPage() {
  const [filters, setFilters] = useState<Filters>({})
  const [layers, setLayers] = useState<LayerState>(DEFAULT_LAYERS)
  const [selected, setSelected] = useState<Vessel | null>(null)

  const { data: vessels = [], isLoading, isError, dataUpdatedAt } = useVessels(filters)
  const { data: meta } = useMeta()

  return (
    <div className="relative h-full">
      <VesselMap vessels={vessels} layers={layers} onSelect={setSelected} />

      {/* left: filters + layer toggles */}
      <div className="absolute left-3 top-3 z-[1000] w-56 space-y-3">
        <Panel title="Filters">
          <FilterControls filters={filters} regions={meta?.regions ?? []} onChange={setFilters} />
        </Panel>
        <Panel title="Layers">
          <LayerToggles layers={layers} onChange={setLayers} />
        </Panel>
      </div>

      {/* right: counts (toggleable) */}
      {layers.counts && (
        <div className="absolute right-3 top-3 z-[1000] w-52">
          <Panel title="Live counts">
            <CountsPanel vessels={vessels} />
          </Panel>
        </div>
      )}

      {/* bottom-right: selected vessel detail */}
      {selected && (
        <div className="absolute bottom-3 right-3 z-[1000]">
          <Panel>
            <VesselDetail vessel={selected} onClose={() => setSelected(null)} />
          </Panel>
        </div>
      )}

      {/* status strip */}
      <div className="absolute bottom-3 left-3 z-[1000]">
        <Panel className="px-3 py-1.5 text-xs text-muted-foreground">
          {isLoading ? (
            <span className="flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" /> loading vessels…
            </span>
          ) : isError ? (
            <span className="text-destructive">feed unavailable — retrying</span>
          ) : vessels.length === 0 ? (
            <span>no vessels match</span>
          ) : (
            <span>
              {vessels.length} vessels · live as of{' '}
              {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—'}
            </span>
          )}
        </Panel>
      </div>
    </div>
  )
}
