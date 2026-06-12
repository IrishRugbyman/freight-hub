import { Switch } from '@/components/ui/switch'
import type { LayerState } from './types'

/** Independent on/off switches for each map layer. */
export function LayerToggles({
  layers,
  onChange,
}: {
  layers: LayerState
  onChange: (l: LayerState) => void
}) {
  const set = (k: keyof LayerState) => (v: boolean) => onChange({ ...layers, [k]: v })
  return (
    <div className="flex flex-col gap-2">
      <Switch checked={layers.clustering} onChange={set('clustering')} label="Cluster markers" />
      <Switch checked={layers.headingArrows} onChange={set('headingArrows')} label="Heading arrows" />
      <Switch checked={layers.counts} onChange={set('counts')} label="Counts panel" />
      <Switch checked={layers.chokepoints} onChange={set('chokepoints')} label="Chokepoints" />
      <Switch checked={layers.eventPins} onChange={set('eventPins')} label="Event pins" />
      <Switch checked={layers.riskOverlay} onChange={set('riskOverlay')} label="Risk overlay" />
      <div className="mt-1 border-t border-border pt-1">
        <Switch checked={layers.deckgl} onChange={set('deckgl')} label="WebGL mode" />
        {layers.deckgl && (
          <div className="ml-1 mt-1">
            <Switch checked={layers.heatmap} onChange={set('heatmap')} label="Density heatmap" />
          </div>
        )}
      </div>
    </div>
  )
}
