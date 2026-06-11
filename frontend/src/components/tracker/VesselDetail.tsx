import { X, Anchor, Navigation } from 'lucide-react'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'

const NAV_LABELS: Record<number, string> = {
  0: 'Underway',
  1: 'At anchor',
  2: 'Not under command',
  3: 'Restricted manoeuvrability',
  4: 'Constrained by draught',
  5: 'Moored',
  6: 'Aground',
  7: 'Fishing',
  8: 'Underway (sail)',
  15: 'Unknown',
}

function navLabel(code: number | null | undefined): string {
  if (code == null) return 'Unknown'
  return NAV_LABELS[code] ?? `Status ${code}`
}

function isAnchored(v: Vessel): boolean {
  return v.nav_status === 1 || v.nav_status === 5 || (v.sog != null && v.sog < 0.3)
}

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-right text-xs font-mono">{value}</span>
    </div>
  )
}

function Section({ children }: { children: React.ReactNode }) {
  return <div className="border-t border-border/60 px-3 py-2 space-y-0.5">{children}</div>
}

/** MarineTraffic-inspired vessel detail panel. */
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
  const color = colorFor(vessel.kind, vessel.segment)
  const anchored = isAnchored(vessel)

  return (
    <div className="w-64">
      {/* Header */}
      <div className="flex items-start gap-2 px-3 pt-3 pb-2">
        <span
          className="mt-0.5 h-3 w-3 shrink-0 rounded-sm"
          style={{ background: color }}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold leading-tight text-sm">
            {vessel.name ?? `MMSI ${vessel.mmsi}`}
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            {anchored ? (
              <Anchor size={10} className="text-muted-foreground" />
            ) : (
              <Navigation size={10} className="text-muted-foreground" />
            )}
            <span className="text-xs text-muted-foreground">{navLabel(vessel.nav_status)}</span>
          </div>
        </div>
        <button onClick={onClose} className="shrink-0 text-muted-foreground hover:text-foreground" aria-label="Close">
          <X size={15} />
        </button>
      </div>

      {/* Type + segment */}
      <div className="flex gap-2 px-3 pb-2">
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          {vessel.segment ?? 'Unknown'}
        </span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
          {vessel.kind}
        </span>
      </div>

      {/* Motion */}
      <Section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Motion</div>
        <Row label="Speed" value={vessel.sog != null ? `${vessel.sog.toFixed(1)} kn` : null} />
        <Row label="Course" value={vessel.cog != null ? `${Math.round(vessel.cog)}°` : null} />
        <Row label="Heading" value={vessel.heading != null ? `${Math.round(vessel.heading)}°` : null} />
        {vessel.sog != null && vessel.sog > 0.3 && (
          <div className="mt-1.5 h-1 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${Math.min(100, (vessel.sog / 20) * 100)}%`, background: color }}
            />
          </div>
        )}
      </Section>

      {/* Voyage */}
      <Section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Voyage</div>
        <Row label="Destination" value={vessel.destination} />
        <Row label="ETA" value={vessel.eta} />
        <Row label="Draught" value={vessel.draught != null ? `${vessel.draught.toFixed(1)} m` : null} />
      </Section>

      {/* Identity */}
      <Section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Identity</div>
        <Row label="MMSI" value={vessel.mmsi} />
        {vessel.imo != null && <Row label="IMO" value={vessel.imo} />}
        <Row label="Region" value={vessel.region?.replace(/_/g, ' ')} />
        <Row label="Position" value={`${vessel.lat.toFixed(3)}, ${vessel.lon.toFixed(3)}`} />
        <Row label="Last seen" value={new Date(vessel.updated_ts + 'Z').toLocaleTimeString()} />
      </Section>

      {/* External links */}
      <div className="flex gap-2 border-t border-border/60 px-3 py-2">
        <a
          href={`https://www.marinetraffic.com/en/ais/details/ships/mmsi:${vessel.mmsi}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] text-primary hover:underline"
        >
          MarineTraffic
        </a>
        <a
          href={`https://www.vesselfinder.com/?mmsi=${vessel.mmsi}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] text-primary hover:underline"
        >
          VesselFinder
        </a>
      </div>

      {/* Trail window toggle */}
      <div className="flex items-center gap-2 border-t border-border/60 px-3 py-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Trail</span>
        <div className="flex gap-1 ml-auto">
          {([24, 168] as const).map((h) => (
            <button
              key={h}
              onClick={() => onTrailHoursChange(h)}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                trailHours === h
                  ? 'bg-primary/20 text-primary'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {h === 24 ? '24h' : '7d'}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
