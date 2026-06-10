import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { useMeta, useVessels, useVesselTrack, type Vessel } from '@/lib/api'
import { Panel } from '@/components/ui/panel'
import { ControlsPanel } from '@/components/tracker/ControlsPanel'
import { CountsPanel } from '@/components/tracker/CountsPanel'
import { VesselDetail } from '@/components/tracker/VesselDetail'
import { VesselMap } from '@/components/tracker/VesselMap'
import { DEFAULT_LAYERS, type Filters, type LayerState } from '@/components/tracker/types'

export const Route = createFileRoute('/')({ component: TrackerPage })

function TrackerPage() {
  const [filters, setFilters] = useState<Filters>({})
  const [layers, setLayers] = useState<LayerState>(DEFAULT_LAYERS)
  const [selected, setSelected] = useState<Vessel | null>(null)
  const [trailHours, setTrailHours] = useState<24 | 168>(24)
  const [focusTarget, setFocusTarget] = useState<Vessel | null>(null)

  const { data: vessels = [], isLoading, isError, dataUpdatedAt } = useVessels(filters)
  const { data: meta } = useMeta()
  const { data: trailPoints } = useVesselTrack(selected?.mmsi ?? null, trailHours)

  function handleSearchSelect(v: Vessel) {
    setSelected(v)
    setFocusTarget(v)
  }

  return (
    <div className="relative h-full">
      <VesselMap
        vessels={vessels}
        layers={layers}
        onSelect={(v) => { setSelected(v); setFocusTarget(null) }}
        trailVessel={selected}
        trailPoints={trailPoints ?? []}
        focusTarget={focusTarget}
      />

      {/* left: unified controls panel */}
      <div className="absolute left-3 top-3 z-[1000]">
        <ControlsPanel
          filters={filters}
          layers={layers}
          regions={meta?.regions ?? []}
          vessels={vessels}
          onFiltersChange={setFilters}
          onLayersChange={setLayers}
          onVesselSelect={handleSearchSelect}
        />
      </div>

      {/* right: legend/counts (toggleable) */}
      {layers.counts && (
        <div className="absolute right-3 top-3 z-[1000] w-44">
          <Panel title="Legend">
            <CountsPanel vessels={vessels} />
          </Panel>
        </div>
      )}

      {/* bottom-right: selected vessel detail (above zoom control) */}
      {selected && (
        <div className="absolute bottom-16 right-3 z-[1000]">
          <Panel>
            <VesselDetail
              vessel={selected}
              trailHours={trailHours}
              onTrailHoursChange={setTrailHours}
              onClose={() => setSelected(null)}
            />
          </Panel>
        </div>
      )}

      {/* status strip - centered bottom so it doesn't overlap left panel or zoom control */}
      <div className="absolute bottom-3 left-1/2 z-[1000] -translate-x-1/2">
        <Panel className="px-3 py-1.5 text-xs text-muted-foreground">
          {isLoading ? (
            <span className="flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" /> Loading...
            </span>
          ) : isError ? (
            <span className="text-destructive">Feed unavailable, retrying</span>
          ) : vessels.length === 0 ? (
            <span>No vessels match</span>
          ) : (
            <span>
              {vessels.length} vessels · updated{' '}
              {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '?'}
            </span>
          )}
        </Panel>
      </div>
    </div>
  )
}
