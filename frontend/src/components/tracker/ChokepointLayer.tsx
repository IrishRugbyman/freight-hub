import { Rectangle, Tooltip } from 'react-leaflet'
import { useChokepoints } from '@/lib/api'

/** Draws each region bbox with a live vessel count label. */
export function ChokepointLayer() {
  const { data } = useChokepoints()
  if (!data) return null
  return (
    <>
      {data.map((c) => (
        <Rectangle
          key={c.region}
          bounds={c.bbox}
          pathOptions={{ color: '#38bdf8', weight: 1, fillOpacity: c.total > 0 ? 0.06 : 0 }}
        >
          <Tooltip direction="center" permanent className="chokepoint-label">
            {c.region.replace(/_/g, ' ')} · {c.total}
          </Tooltip>
        </Rectangle>
      ))}
    </>
  )
}
