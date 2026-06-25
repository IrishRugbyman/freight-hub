import React, { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useAnomalyWatchlist, useDestinationChanges, useStsProximity,
  useStsOffenders, useReroutes, useRiskEvents, useEventRateTimeline,
  useShadowFleet,
  type RerouteRiskEvent,
} from '@/lib/api'
import { EmptyState, TOOLTIP_STYLE, LEGEND_STYLE } from './-analyticsShared'

function useGoToTracker() {
  const navigate = useNavigate()
  return (mmsi: number, lat?: number | null, lon?: number | null) => {
    const search: Record<string, unknown> = { mmsi }
    if (lat != null && lon != null) { search.lat = lat; search.lon = lon }
    navigate({ to: '/tracker', search: search as never })
  }
}

// ---------------------------------------------------------------------------
// Local helpers (Intelligence tab only)
// ---------------------------------------------------------------------------
function anomalyBg(level: string) {
  if (level === 'Critical') return 'bg-red-500/10 border-l-2 border-red-500/60'
  if (level === 'High') return 'bg-orange-500/8 border-l-2 border-orange-500/50'
  if (level === 'Elevated') return 'bg-yellow-500/8 border-l-2 border-yellow-500/40'
  return ''
}

function anomalyScoreColor(level: string) {
  if (level === 'Critical') return 'text-red-400 font-bold'
  if (level === 'High') return 'text-orange-400 font-semibold'
  if (level === 'Elevated') return 'text-yellow-400'
  return 'text-muted-foreground'
}

function riskBadge(score: number | null, ofac: boolean) {
  if (ofac) return <span className="rounded bg-red-500/20 px-1 text-[10px] font-semibold text-red-400">OFAC</span>
  if (score == null) return null
  if (score >= 75) return <span className="rounded bg-red-400/20 px-1 text-[10px] font-semibold text-red-400">{score}</span>
  if (score >= 50) return <span className="rounded bg-orange-400/20 px-1 text-[10px] font-semibold text-orange-400">{score}</span>
  if (score >= 25) return <span className="rounded bg-yellow-400/20 px-1 text-[10px] font-semibold text-yellow-400">{score}</span>
  return null
}

function alertColor(eventType: string) {
  if (eventType === 'sts') return 'text-purple-400'
  if (eventType === 'reroute') return 'text-amber-400'
  if (eventType === 'gap') return 'text-red-400'
  if (eventType === 'chokepoint') return 'text-blue-400'
  return 'text-muted-foreground'
}

// ---------------------------------------------------------------------------
// AnomalyWatchlistCard
// ---------------------------------------------------------------------------
export function AnomalyWatchlistCard() {
  const [minScore, setMinScore] = useState(50)
  const [limit, setLimit] = useState(25)
  const { data, isLoading } = useAnomalyWatchlist(minScore, limit)
  const goToTracker = useGoToTracker()
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Anomaly Watchlist</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={minScore} onChange={e => setMinScore(Number(e.target.value))}>
              <option value={25}>Score 25+</option>
              <option value={50}>Score 50+</option>
              <option value={75}>Score 75+</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={limit} onChange={e => setLimit(Number(e.target.value))}>
              <option value={25}>Top 25</option>
              <option value={50}>Top 50</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_flagged} vessels flagged (score {'>='} {data.min_score}) as of{' '}
            {new Date(data.as_of).toLocaleTimeString()}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No vessels above threshold.</p>
        ) : (
          <div className="space-y-1">
            {rows.map((row) => (
              <div key={row.mmsi} className={`cursor-pointer rounded px-2 py-1.5 ${anomalyBg(row.risk_level)}`} onClick={() => goToTracker(row.mmsi, row.lat, row.lon)}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-sm font-medium truncate">{row.name ?? `MMSI ${row.mmsi}`}</span>
                      {row.ofac && <span className="rounded bg-red-500/20 px-1 py-px text-[9px] font-bold text-red-400 uppercase">OFAC</span>}
                      <span className="text-xs text-muted-foreground">{row.segment ?? row.kind}</span>
                      {row.region && <span className="text-xs text-muted-foreground/70">{row.region.replace(/_/g, ' ')}</span>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
                      {row.signals.map((sig, i) => (
                        <span key={i} className="text-[10px] text-muted-foreground">
                          <span className="mr-0.5 opacity-50">+</span>{sig}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className={`shrink-0 text-right text-sm tabular-nums ${anomalyScoreColor(row.risk_level)}`}>
                    {row.total_score}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// DestinationChangesCard
// ---------------------------------------------------------------------------
export function DestinationChangesCard() {
  const [hours, setHours] = useState(72)
  const [kind, setKind] = useState('')
  const { data, isLoading } = useDestinationChanges(hours, kind)
  const goToTracker = useGoToTracker()
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Destination Changes (Rerouting)</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={hours} onChange={e => setHours(Number(e.target.value))}>
              <option value={24}>Last 24h</option>
              <option value={72}>Last 72h</option>
              <option value={168}>Last 7d</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="">All types</option>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_changes} vessels changed destination in the last {data.hours}h. Derived from AIS snapshot history.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No destination changes in window." />
        ) : (
          <div className="space-y-0.5 max-h-72 overflow-y-auto pr-1">
            {rows.map(row => (
              <div key={row.mmsi} className="flex cursor-pointer items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30" onClick={() => goToTracker(row.mmsi, row.lat, row.lon)}>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  {row.region && <span className="ml-1 text-muted-foreground/60">{row.region.replace(/_/g, ' ')}</span>}
                </div>
                <div className="shrink-0 text-right">
                  <span className="text-muted-foreground line-through">{row.from_dest}</span>
                  <span className="mx-1 text-muted-foreground">{'→'}</span>
                  <span className="font-semibold text-amber-400">{row.to_dest}</span>
                  <span className="ml-2 text-muted-foreground/60 tabular-nums">{row.hours_ago}h</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// StsProximityCard
// ---------------------------------------------------------------------------
export function StsProximityCard() {
  const [maxDistM, setMaxDistM] = useState(2000)
  const [maxSog, setMaxSog] = useState(3.0)
  const { data, isLoading } = useStsProximity(maxDistM, maxSog)
  const goToTracker = useGoToTracker()
  const pairs = data?.pairs ?? []
  const riskPairs = pairs.filter(p => p.risk_region)
  const normalPairs = pairs.filter(p => !p.risk_region)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Live STS Proximity Watch</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={maxDistM} onChange={e => setMaxDistM(Number(e.target.value))}>
              <option value={500}>500 m</option>
              <option value={1000}>1 km</option>
              <option value={2000}>2 km</option>
              <option value={5000}>5 km</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={maxSog} onChange={e => setMaxSog(Number(e.target.value))}>
              <option value={1.5}>SOG 1.5 kn</option>
              <option value={3.0}>SOG 3.0 kn</option>
              <option value={5.0}>SOG 5.0 kn</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_pairs} vessel pair{data.total_pairs !== 1 ? 's' : ''} within{' '}
            {(data.max_dist_m / 1000).toFixed(1)} km at SOG {'<='} {data.max_sog} kn
            {riskPairs.length > 0 && <span className="ml-1 font-medium text-orange-400">({riskPairs.length} in high-risk regions)</span>}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : pairs.length === 0 ? (
          <EmptyState message="No vessel pairs within threshold." />
        ) : (
          <div className="space-y-1">
            {[...riskPairs, ...normalPairs].slice(0, 30).map((pair, i) => (
              <div key={i} className={`cursor-pointer rounded px-2 py-1.5 ${pair.risk_region ? 'border-l-2 border-orange-500/60 bg-orange-500/8' : 'bg-muted/20 hover:bg-muted/30'}`} onClick={() => goToTracker(pair.mmsi_a, pair.lat, pair.lon)}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1 text-sm">
                    <span className="font-medium">{pair.name_a ?? `MMSI ${pair.mmsi_a}`}</span>
                    <span className="mx-1 text-muted-foreground text-xs">{pair.segment_a ?? pair.kind_a} {pair.sog_a != null ? `${pair.sog_a} kn` : ''}</span>
                    <span className="text-muted-foreground/50">+</span>
                    <span className="ml-1 font-medium">{pair.name_b ?? `MMSI ${pair.mmsi_b}`}</span>
                    <span className="mx-1 text-muted-foreground text-xs">{pair.segment_b ?? pair.kind_b} {pair.sog_b != null ? `${pair.sog_b} kn` : ''}</span>
                  </div>
                  <div className="shrink-0 text-right">
                    <span className={`text-xs tabular-nums ${pair.risk_region ? 'text-orange-400' : 'text-muted-foreground'}`}>
                      {pair.dist_m < 1000 ? `${Math.round(pair.dist_m)} m` : `${(pair.dist_m / 1000).toFixed(1)} km`}
                    </span>
                    {pair.region && <span className="ml-1 text-[10px] text-muted-foreground/60">{pair.region.replace(/_/g, ' ')}</span>}
                  </div>
                </div>
              </div>
            ))}
            {pairs.length > 30 && <p className="text-center text-xs text-muted-foreground pt-1">+{pairs.length - 30} more pairs</p>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// StsOffendersCard
// ---------------------------------------------------------------------------
export function StsOffendersCard() {
  const [days, setDays] = useState(30)
  const [limit, setLimit] = useState(30)
  const { data, isLoading } = useStsOffenders(days, limit)
  const goToTracker = useGoToTracker()
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Serial STS Participants</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={7}>Last 7d</option>
              <option value={14}>Last 14d</option>
              <option value={30}>Last 30d</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={limit} onChange={e => setLimit(Number(e.target.value))}>
              <option value={20}>Top 20</option>
              <option value={30}>Top 30</option>
              <option value={50}>Top 50</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_vessels} vessels appeared in STS events in the last {data.days}d. Ranked by total event appearances.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No STS events in window." />
        ) : (
          <div className="space-y-0.5">
            {rows.map((row, i) => (
              <div key={row.mmsi} className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs ${row.ofac ? 'bg-red-500/10 border-l-2 border-red-500/50' : 'bg-muted/15 hover:bg-muted/30'}`} onClick={() => goToTracker(row.mmsi, row.lat, row.lon)}>
                <span className="w-5 shrink-0 text-muted-foreground/60 tabular-nums">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.ofac && <span className="ml-1 rounded bg-red-500/20 px-1 text-[9px] font-bold text-red-400 uppercase">OFAC</span>}
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  {row.region && <span className="ml-1 text-muted-foreground/60">{row.region.replace(/_/g, ' ')}</span>}
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-semibold text-orange-400">{row.sts_events}</span>
                  <span className="ml-1 text-muted-foreground/60">({row.as_initiator}+{row.as_counterpart})</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// ReroutesCard
// ---------------------------------------------------------------------------
export function ReroutesCard() {
  const [days, setDays] = useState(7)
  const [minRisk, setMinRisk] = useState(0)
  const { data, isLoading } = useReroutes(days, minRisk)
  const goToTracker = useGoToTracker()
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Reroute Events</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={3}>Last 3d</option>
              <option value={7}>Last 7d</option>
              <option value={14}>Last 14d</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={minRisk} onChange={e => setMinRisk(Number(e.target.value))}>
              <option value={0}>All risk</option>
              <option value={25}>Risk 25+</option>
              <option value={50}>Risk 50+</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_events} reroutes detected in last {data.days}d via AIS destination-field changes.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No reroutes in window." />
        ) : (
          <div className="space-y-0.5 max-h-72 overflow-y-auto pr-1">
            {rows.map((row: RerouteRiskEvent, i: number) => (
              <div key={`${row.mmsi}-${i}`} className="flex cursor-pointer items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30" onClick={() => goToTracker(row.mmsi)}>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  {row.region && <span className="ml-1 text-muted-foreground/60">{row.region.replace(/_/g, ' ')}</span>}
                </div>
                <div className="shrink-0 text-right">
                  {row.old_destination && (
                    <span className="text-muted-foreground line-through">{row.old_destination}</span>
                  )}
                  {row.new_destination && (
                    <>
                      <span className="mx-1 text-muted-foreground">{'→'}</span>
                      <span className="font-semibold text-amber-400">{row.new_destination}</span>
                    </>
                  )}
                  {riskBadge(row.risk_score ?? null, row.ofac)}
                  {row.start_ts && <span className="ml-2 text-muted-foreground/60 tabular-nums">{row.start_ts.slice(5, 16).replace('T', ' ')}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// RiskEventsCard
// ---------------------------------------------------------------------------
const EVENT_TYPE_LABELS: Record<string, string> = {
  sts: 'STS',
  reroute: 'Reroute',
  gap: 'AIS Gap',
  chokepoint: 'Chokepoint',
  anchorage: 'Anchorage',
}

export function RiskEventsCard() {
  const [minRisk, setMinRisk] = useState(25)
  const [days, setDays] = useState(2)
  const { data, isLoading } = useRiskEvents(minRisk, days)
  const goToTracker = useGoToTracker()
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Risk Event Feed</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={minRisk} onChange={e => setMinRisk(Number(e.target.value))}>
              <option value={0}>All scores</option>
              <option value={25}>Score 25+</option>
              <option value={50}>Score 50+</option>
              <option value={75}>Score 75+</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={1}>Last 24h</option>
              <option value={2}>Last 48h</option>
              <option value={7}>Last 7d</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {rows.length} events / {data.total_high_risk_vessels} high-risk vessels active in window.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-48 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No risk events in window." />
        ) : (
          <div className="space-y-0.5 max-h-80 overflow-y-auto pr-1">
            {rows.map(ev => (
              <div key={ev.event_id} className="flex cursor-pointer items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30" onClick={() => goToTracker(ev.mmsi, ev.lat, ev.lon)}>
                <span className={`w-16 shrink-0 text-[10px] font-semibold ${alertColor(ev.event_type)}`}>
                  {EVENT_TYPE_LABELS[ev.event_type] ?? ev.event_type}
                </span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{ev.name ?? `MMSI ${ev.mmsi}`}</span>
                  {ev.segment && <span className="ml-1 text-muted-foreground">{ev.segment}</span>}
                  {ev.region && <span className="ml-1 text-muted-foreground/60">{ev.region.replace(/_/g, ' ')}</span>}
                  {ev.name2 && <span className="ml-1 text-muted-foreground/70">+ {ev.name2}</span>}
                  {ev.old_destination && ev.new_destination && (
                    <span className="ml-1 text-[10px] text-muted-foreground/70">{ev.old_destination} {'→'} {ev.new_destination}</span>
                  )}
                </div>
                <div className="shrink-0 flex items-center gap-1 text-right">
                  {riskBadge(ev.risk_score, ev.ofac)}
                  {ev.risk_score2 != null && riskBadge(ev.risk_score2, ev.ofac2)}
                  <span className="ml-1 text-muted-foreground/60 tabular-nums text-[10px]">
                    {new Date(ev.event_ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// EventRateTimelineCard
// ---------------------------------------------------------------------------
export function EventRateTimelineCard() {
  const [hours, setHours] = useState(72)
  const { data, isLoading } = useEventRateTimeline(hours)
  const points = data?.points ?? []

  const chartData = React.useMemo(
    () => points.map(p => ({
      hour: new Date(p.hour).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' }),
      Reroutes: p.reroute_count,
      STS: p.sts_count,
      Total: p.total_count,
    })),
    [points],
  )

  const totalReroutes = points.reduce((s, p) => s + p.reroute_count, 0)
  const totalSts = points.reduce((s, p) => s + p.sts_count, 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>AIS Anomaly Event Rate</span>
          <select className="rounded border border-border bg-background px-2 py-1 text-xs font-normal" value={hours} onChange={e => setHours(Number(e.target.value))}>
            <option value={24}>Last 24h</option>
            <option value={48}>Last 48h</option>
            <option value={72}>Last 72h</option>
            <option value={168}>Last 7d</option>
          </select>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {totalReroutes} reroutes + {totalSts} STS events in the last {data.hours}h. Trend indicates route disruption intensity.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <EmptyState message="No event data in window." />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={chartData} margin={{ left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="hour" tick={{ fontSize: 9 }} interval={Math.max(0, Math.floor(chartData.length / 10) - 1)} angle={-25} textAnchor="end" height={40} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar dataKey="Reroutes" fill="#f97316" opacity={0.8} />
              <Bar dataKey="STS" fill="#a855f7" opacity={0.8} />
              <Line type="monotone" dataKey="Total" stroke="#facc15" dot={false} strokeWidth={1.5} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// ShadowFleetCard
// ---------------------------------------------------------------------------
export function ShadowFleetCard() {
  const [days, setDays] = useState(7)
  const [limit, setLimit] = useState(50)
  const { data, isLoading } = useShadowFleet(days, limit)
  const goToTracker = useGoToTracker()
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Shadow Fleet Monitor</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={7}>Last 7d</option>
              <option value={14}>Last 14d</option>
              <option value={30}>Last 30d</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={limit} onChange={e => setLimit(Number(e.target.value))}>
              <option value={25}>Top 25</option>
              <option value={50}>Top 50</option>
              <option value={100}>Top 100</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total} vessels matched dark-transfer pattern (STS + gap/spoof) in last {data.days}d. Ranked by risk score then covert event count.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No vessels matched the shadow fleet pattern in this window." />
        ) : (
          <div className="space-y-0.5 max-h-96 overflow-y-auto pr-1">
            {rows.map((row, i) => (
              <div
                key={row.mmsi}
                className={`cursor-pointer rounded px-2 py-1.5 ${row.ofac ? 'border-l-2 border-red-500/60 bg-red-500/10' : 'bg-muted/15 hover:bg-muted/30'}`}
                onClick={() => goToTracker(row.mmsi)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="w-5 shrink-0 text-[10px] text-muted-foreground/60 tabular-nums">{i + 1}</span>
                      <span className="text-sm font-medium truncate">{row.name ?? `MMSI ${row.mmsi}`}</span>
                      {row.ofac && <span className="rounded bg-red-500/20 px-1 py-px text-[9px] font-bold text-red-400 uppercase">OFAC</span>}
                      {row.segment && <span className="text-xs text-muted-foreground">{row.segment}</span>}
                      {row.region && <span className="text-xs text-muted-foreground/60">{row.region.replace(/_/g, ' ')}</span>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-6">
                      <span className="text-[10px]">
                        <span className="text-blue-400 font-semibold tabular-nums">{row.sts_count}</span>
                        <span className="ml-0.5 text-muted-foreground/60">STS</span>
                      </span>
                      <span className="text-[10px]">
                        <span className="text-red-400 font-semibold tabular-nums">{row.gap_count}</span>
                        <span className="ml-0.5 text-muted-foreground/60">gap</span>
                      </span>
                      <span className="text-[10px]">
                        <span className="text-purple-400 font-semibold tabular-nums">{row.spoof_count}</span>
                        <span className="ml-0.5 text-muted-foreground/60">spoof</span>
                      </span>
                      {row.last_event_ts && (
                        <span className="text-[10px] text-muted-foreground/50">
                          {row.last_event_ts.slice(5, 16).replace('T', ' ')}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    {riskBadge(row.risk_score, row.ofac)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Default export: Intelligence tab component
// ---------------------------------------------------------------------------
export default function IntelligenceTab() {
  return (
    <div className="space-y-6">
      <ShadowFleetCard />
      <AnomalyWatchlistCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <DestinationChangesCard />
        <ReroutesCard />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <StsProximityCard />
        <StsOffendersCard />
      </div>
      <RiskEventsCard />
      <EventRateTimelineCard />
    </div>
  )
}
