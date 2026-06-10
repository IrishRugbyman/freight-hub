import { X } from 'lucide-react'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'

const NAV_STATUS: Record<number, string> = {
  0: 'Underway (engine)',
  1: 'At anchor',
  2: 'Not under command',
  3: 'Restricted manoeuvrability',
  4: 'Constrained by draught',
  5: 'Moored',
  6: 'Aground',
  7: 'Fishing',
  8: 'Underway (sail)',
  15: 'Not defined',
}

function navLabel(code: number | null | undefined): string | null {
  if (code == null) return null
  return NAV_STATUS[code] ?? `code ${code}`
}

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono tabular-nums">{value}</span>
    </div>
  )
}

/** Slide-over detail for the selected vessel, with trail window toggle. */
export function VesselDetail({
  vessel,
  trailHours,
  onTrailHoursChange,
  onClose,
}: {
  vessel: Vessel
  trailHours: 24 | 168
  onTrailHoursChange: (h: 24 | 168) => void
  onClose: () => void
}) {
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
              {vessel.segment ?? 'Unknown'} · {vessel.kind}
            </div>
          </div>
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
          <X size={16} />
        </button>
      </div>

      <Row label="MMSI" value={vessel.mmsi} />
      {vessel.imo != null && <Row label="IMO" value={vessel.imo} />}
      <Row label="Speed" value={vessel.sog != null ? `${vessel.sog.toFixed(1)} kn` : null} />
      <Row label="Course" value={vessel.cog != null ? `${Math.round(vessel.cog)}°` : null} />
      <Row label="Heading" value={vessel.heading != null ? `${Math.round(vessel.heading)}°` : null} />
      <Row label="Draught" value={vessel.draught != null ? `${vessel.draught.toFixed(1)} m` : null} />
      <Row label="Nav status" value={navLabel(vessel.nav_status)} />
      <Row label="Destination" value={vessel.destination} />
      <Row label="ETA" value={vessel.eta} />
      <Row label="Region" value={vessel.region?.replace(/_/g, ' ')} />
      <Row label="Position" value={`${vessel.lat.toFixed(3)}, ${vessel.lon.toFixed(3)}`} />
      <Row label="Last seen" value={new Date(vessel.updated_ts + 'Z').toLocaleTimeString()} />

      {/* Trail window toggle */}
      <div className="mt-2 flex items-center gap-2 border-t pt-2 text-xs text-muted-foreground">
        <span>Trail:</span>
        {([24, 168] as const).map((h) => (
          <button
            key={h}
            onClick={() => onTrailHoursChange(h)}
            className={`rounded px-2 py-0.5 ${trailHours === h ? 'bg-primary/20 text-primary' : 'hover:text-foreground'}`}
          >
            {h === 24 ? '24h' : '7d'}
          </button>
        ))}
      </div>
    </div>
  )
}
