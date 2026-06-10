import { X } from 'lucide-react'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono tabular-nums">{value}</span>
    </div>
  )
}

/** Slide-over detail for the selected vessel. */
export function VesselDetail({ vessel, onClose }: { vessel: Vessel; onClose: () => void }) {
  return (
    <div className="w-72 p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="mt-1 inline-block h-3 w-3 shrink-0 rounded-full"
            style={{ background: colorFor(vessel.kind, vessel.segment) }}
          />
          <div>
            <div className="font-medium leading-tight">{vessel.name ?? `MMSI ${vessel.mmsi}`}</div>
            <div className="text-xs text-muted-foreground">
              {vessel.segment ?? '—'} · {vessel.kind}
            </div>
          </div>
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <Row label="MMSI" value={vessel.mmsi} />
      <Row label="Speed" value={vessel.sog != null ? `${vessel.sog.toFixed(1)} kn` : null} />
      <Row label="Course" value={vessel.cog != null ? `${Math.round(vessel.cog)}°` : null} />
      <Row label="Heading" value={vessel.heading != null ? `${Math.round(vessel.heading)}°` : null} />
      <Row label="Destination" value={vessel.destination} />
      <Row label="Region" value={vessel.region?.replace(/_/g, ' ')} />
      <Row label="Position" value={`${vessel.lat.toFixed(3)}, ${vessel.lon.toFixed(3)}`} />
      <Row label="Last seen" value={new Date(vessel.updated_ts + 'Z').toLocaleTimeString()} />
    </div>
  )
}
