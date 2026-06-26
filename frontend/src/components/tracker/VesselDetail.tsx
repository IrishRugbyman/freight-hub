import { X, Anchor, Navigation, ArrowLeftRight, TrendingUp, TrendingDown, Ship, AlertTriangle } from 'lucide-react'
import { useEquasis, useVesselState, useVoyages, useVesselBehavioralRisk, useVesselEta } from '@/lib/api'
import type { Vessel, VoyageEvent } from '@/lib/api'
import { EtaChip } from '@/components/EtaChip'
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

function MouBadge({ label, value }: { label: string; value?: string }) {
  if (!value) return null
  const color =
    value === 'White'
      ? 'text-emerald-400'
      : value === 'Grey'
        ? 'text-yellow-400'
        : 'text-red-400'
  return (
    <span className="text-[10px]">
      <span className="text-muted-foreground">{label} </span>
      <span className={color}>{value}</span>
    </span>
  )
}

function riskScoreColor(score: number) {
  if (score >= 50) return 'text-red-400'
  if (score >= 25) return 'text-yellow-400'
  return 'text-emerald-400'
}

function VoyageEventRow({ ev }: { ev: VoyageEvent }) {
  const d = new Date(ev.ts + 'Z')
  const time = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
    d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

  if (ev.type === 'port_call') {
    return (
      <div className="flex items-start gap-2 text-[10px]">
        <Anchor size={9} className="mt-0.5 shrink-0 text-muted-foreground/70" />
        <div className="min-w-0">
          <span className="font-medium">{ev.zone ?? 'Anchorage'}</span>
          {ev.dwell_hours != null && (
            <span className="text-muted-foreground ml-1">{ev.dwell_hours.toFixed(0)}h</span>
          )}
          <div className="text-muted-foreground/60">{time}</div>
        </div>
      </div>
    )
  }

  if (ev.type === 'transit') {
    return (
      <div className="flex items-start gap-2 text-[10px]">
        <Navigation size={9} className="mt-0.5 shrink-0 text-primary/70" />
        <div className="min-w-0">
          <span className="font-medium">{ev.zone ?? 'Transit'}</span>
          {ev.direction && <span className="text-muted-foreground ml-1">{ev.direction}</span>}
          {ev.laden != null && (
            <span className={`ml-1.5 rounded px-1 py-px text-[9px] ${ev.laden ? 'bg-blue-500/20 text-blue-300' : 'bg-muted text-muted-foreground'}`}>
              {ev.laden ? 'laden' : 'ballast'}
            </span>
          )}
          <div className="text-muted-foreground/60">{time}</div>
        </div>
      </div>
    )
  }

  if (ev.type === 'reroute') {
    return (
      <div className="flex items-start gap-2 text-[10px]">
        <ArrowLeftRight size={9} className="mt-0.5 shrink-0 text-yellow-400/80" />
        <div className="min-w-0 truncate">
          <span className="text-muted-foreground line-through truncate">{ev.old_destination ?? '?'}</span>
          <span className="mx-1 text-muted-foreground/60">-&gt;</span>
          <span className="font-medium">{ev.new_destination ?? '?'}</span>
          <div className="text-muted-foreground/60">{time}</div>
        </div>
      </div>
    )
  }

  if (ev.type === 'cargo_load') {
    return (
      <div className="flex items-start gap-2 text-[10px]">
        <TrendingUp size={9} className="mt-0.5 shrink-0 text-green-400" />
        <div className="min-w-0">
          <span className="font-medium text-green-400">Loading</span>
          {ev.change_m != null && (
            <span className="text-muted-foreground ml-1">
              {ev.draught_before?.toFixed(1)}m -&gt; {ev.draught_after?.toFixed(1)}m (+{ev.change_m.toFixed(1)}m)
            </span>
          )}
          <div className="text-muted-foreground/60">{time}</div>
        </div>
      </div>
    )
  }

  if (ev.type === 'cargo_discharge') {
    return (
      <div className="flex items-start gap-2 text-[10px]">
        <TrendingDown size={9} className="mt-0.5 shrink-0 text-orange-400" />
        <div className="min-w-0">
          <span className="font-medium text-orange-400">Discharging</span>
          {ev.change_m != null && (
            <span className="text-muted-foreground ml-1">
              {ev.draught_before?.toFixed(1)}m -&gt; {ev.draught_after?.toFixed(1)}m (-{ev.change_m.toFixed(1)}m)
            </span>
          )}
          <div className="text-muted-foreground/60">{time}</div>
        </div>
      </div>
    )
  }

  if (ev.type === 'sts') {
    return (
      <div className="flex items-start gap-2 text-[10px]">
        <Ship size={9} className="mt-0.5 shrink-0 text-purple-400/80" />
        <div className="min-w-0 truncate">
          <span className="font-medium">STS Transfer</span>
          {ev.name2 && <span className="text-muted-foreground ml-1">w/ {ev.name2}</span>}
          <div className="text-muted-foreground/60">{time}</div>
        </div>
      </div>
    )
  }

  return null
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
  const { data: eq, isLoading: eqLoading } = useEquasis(vessel.imo)
  const { data: vesselState } = useVesselState(vessel.mmsi)
  const { data: voyages } = useVoyages(vessel.mmsi, 14)
  const { data: behavioralRisk } = useVesselBehavioralRisk(vessel.mmsi)
  const { data: etaData } = useVesselEta(vessel.mmsi)
  const trueEta = etaData?.predictions?.[0] ?? null  // soonest resolvable target

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
        <div className="text-[11px] font-semibold text-muted-foreground mb-1 flex items-center gap-2">
          <span>Motion</span>
          {vesselState?.laden && (
            <span className={`rounded px-1.5 py-px text-[9px] font-medium uppercase tracking-wide ${
              vesselState.laden === 'laden' ? 'bg-blue-500/20 text-blue-300' :
              vesselState.laden === 'ballast' ? 'bg-muted text-muted-foreground' :
              'bg-muted text-muted-foreground'
            }`}>
              {vesselState.laden}
            </span>
          )}
        </div>
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
        <div className="text-[11px] font-semibold text-muted-foreground mb-1">Voyage</div>
        <Row label="Destination" value={vessel.destination} />
        <Row label="ETA (reported)" value={vessel.eta} />
        {trueEta?.eta_p50_h != null && (
          <div className="flex items-baseline justify-between gap-3 py-0.5">
            <span className="text-xs text-muted-foreground">
              True ETA <span className="text-muted-foreground/60">to {trueEta.target_name ?? trueEta.target_id}</span>
            </span>
            <EtaChip
              vessel={{
                eta_true_h: trueEta.eta_p50_h,
                eta_low_h: trueEta.eta_low_h,
                eta_high_h: trueEta.eta_high_h,
                eta_naive_h: trueEta.eta_naive_h,
                eta_method: trueEta.method,
              }}
              fallbackH={trueEta.eta_naive_h}
            />
          </div>
        )}
        <Row label="Draught" value={vessel.draught != null ? `${vessel.draught.toFixed(1)} m` : null} />
      </Section>

      {/* Voyage history (port calls, transits, reroutes) */}
      {voyages && voyages.events.length > 0 && (
        <Section>
          <div className="text-[11px] font-semibold text-muted-foreground mb-1.5">History (14d)</div>
          <div className="space-y-1">
            {voyages.events.slice(-8).map((ev, i) => (
              <VoyageEventRow key={i} ev={ev} />
            ))}
          </div>
        </Section>
      )}

      {/* Behavioral risk */}
      {behavioralRisk && (behavioralRisk.total_score > 0 || behavioralRisk.sts_count > 0 || behavioralRisk.reroute_count > 0) && (
        <Section>
          <div className="text-[11px] font-semibold text-muted-foreground mb-1 flex items-center gap-1.5">
            <AlertTriangle size={9} className={
              behavioralRisk.risk_level === 'Critical' ? 'text-red-400' :
              behavioralRisk.risk_level === 'High' ? 'text-orange-400' :
              behavioralRisk.risk_level === 'Elevated' ? 'text-yellow-400' :
              'text-muted-foreground'
            } />
            <span>Behavioral Risk</span>
            <span className={`ml-auto text-[9px] font-semibold rounded px-1 py-px ${
              behavioralRisk.risk_level === 'Critical' ? 'bg-red-500/20 text-red-400' :
              behavioralRisk.risk_level === 'High' ? 'bg-orange-500/20 text-orange-400' :
              behavioralRisk.risk_level === 'Elevated' ? 'bg-yellow-500/20 text-yellow-400' :
              'bg-muted text-muted-foreground'
            }`}>
              {behavioralRisk.risk_level}
            </span>
          </div>
          <div className="flex items-center gap-1.5 mb-1.5">
            <div className="h-1 flex-1 rounded-full bg-muted/60 overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  behavioralRisk.total_score >= 75 ? 'bg-red-500' :
                  behavioralRisk.total_score >= 50 ? 'bg-orange-500' :
                  behavioralRisk.total_score >= 25 ? 'bg-yellow-500' :
                  'bg-green-500/60'
                }`}
                style={{ width: `${behavioralRisk.total_score}%` }}
              />
            </div>
            <span className="text-[10px] font-mono tabular-nums text-muted-foreground w-7 text-right">
              {behavioralRisk.total_score}/100
            </span>
          </div>
          <div className="flex gap-3 text-[10px]">
            <span className="text-muted-foreground">
              STS <span className={behavioralRisk.sts_count > 0 ? 'text-orange-400 font-medium' : ''}>{behavioralRisk.sts_count}</span>
            </span>
            <span className="text-muted-foreground">
              Reroutes <span className={behavioralRisk.reroute_count > 0 ? 'text-yellow-400 font-medium' : ''}>{behavioralRisk.reroute_count}</span>
            </span>
            <span className="text-muted-foreground">{behavioralRisk.days}d window</span>
          </div>
        </Section>
      )}

      {/* Identity */}
      <Section>
        <div className="text-[11px] font-semibold text-muted-foreground mb-1">Identity</div>
        <Row label="MMSI" value={vessel.mmsi} />
        {vessel.imo != null && <Row label="IMO" value={vessel.imo} />}
        <Row label="Region" value={vessel.region?.replace(/_/g, ' ')} />
        <Row label="Position" value={`${vessel.lat.toFixed(3)}, ${vessel.lon.toFixed(3)}`} />
        <Row label="Last seen" value={new Date(vessel.updated_ts + 'Z').toLocaleTimeString()} />
      </Section>

      {/* Equasis registry data (only shown when IMO present) */}
      {vessel.imo != null && (
        <Section>
          <div className="text-[11px] font-semibold text-muted-foreground mb-1">
            Registry (Equasis)
          </div>
          {eqLoading && (
            <div className="text-[10px] text-muted-foreground">Loading...</div>
          )}
          {eq && (
            <>
              <Row label="Owner" value={eq.owner} />
              <Row label="ISM Manager" value={eq.ism_manager} />
              <Row label="Class" value={eq.class_society} />
              <Row label="P&I" value={eq.pi_club} />
              <Row label="Flag" value={eq.flag} />
              <Row label="Type" value={eq.ship_type} />
              <Row label="Built" value={eq.year_built} />
              <Row label="GT" value={eq.gross_tonnage} />
              <Row label="DWT" value={eq.dwt} />
              {eq.detention_rate_pct != null && (
                <div className="flex items-baseline justify-between gap-3 py-0.5">
                  <span className="text-xs text-muted-foreground">Detention</span>
                  <span
                    className={`text-right text-xs font-mono ${
                      eq.detention_rate_pct >= 10
                        ? 'text-red-400'
                        : eq.detention_rate_pct >= 5
                          ? 'text-yellow-400'
                          : 'text-emerald-400'
                    }`}
                  >
                    {eq.detention_rate_pct}%
                  </span>
                </div>
              )}
              {(eq.paris_mou || eq.tokyo_mou) && (
                <div className="flex gap-3 py-0.5">
                  <MouBadge label="Paris" value={eq.paris_mou} />
                  <MouBadge label="Tokyo" value={eq.tokyo_mou} />
                </div>
              )}
              {eq.ofac_sanctioned && (
                <div className="mt-1 rounded border border-red-500/50 bg-red-500/15 px-2 py-1 text-[10px] font-semibold text-red-300 uppercase tracking-wide">
                  OFAC SDN Sanctioned
                </div>
              )}
              {eq.risk_score != null && (
                <>
                  <div className="flex items-baseline justify-between gap-3 pt-1.5 pb-0.5">
                    <span className="text-xs text-muted-foreground">Risk score</span>
                    <span className={`text-xs font-mono font-semibold ${riskScoreColor(eq.risk_score)}`}>
                      {eq.risk_score}/100
                    </span>
                  </div>
                  {eq.risk_indicators && eq.risk_indicators.length > 0 && (
                    <div className="mt-0.5 space-y-0.5">
                      {eq.risk_indicators.map((ind, i) => (
                        <div key={i} className="text-[10px] text-muted-foreground pl-2 leading-tight">
                          <span className="mr-1 text-muted-foreground/60">+</span>{ind}
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </Section>
      )}

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
        <span className="text-[11px] font-semibold text-muted-foreground">Trail</span>
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
