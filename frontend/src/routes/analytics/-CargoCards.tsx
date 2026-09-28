// Cargo cards: trade lanes, destination flows, laden/ballast state and transitions.
import React, { useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useTradeLaneMatrix, useDestinationFlows, useCargoTransitions, useCargoStateChanges, useLaden,
} from '@/lib/api'
import {
  EmptyState, ChartSkeleton, TOOLTIP_STYLE, LEGEND_STYLE, REGION_LABELS, useGoToTracker,
} from './-analyticsShared'

function cellHeatColor(count: number, maxCount: number): string {
  if (maxCount === 0) return 'transparent'
  const pct = count / maxCount
  if (pct > 0.66) return 'rgba(96,165,250,0.35)'
  if (pct > 0.33) return 'rgba(96,165,250,0.18)'
  if (pct > 0.1)  return 'rgba(96,165,250,0.09)'
  return 'transparent'
}

function riskBadge(score: number | null, ofac: boolean) {
  if (ofac) return <span className="rounded bg-red-500/20 px-1 text-[10px] font-semibold text-red-400">OFAC</span>
  if (score == null) return null
  if (score >= 75) return <span className="rounded bg-red-400/20 px-1 text-[10px] font-semibold text-red-400">{score}</span>
  if (score >= 50) return <span className="rounded bg-orange-400/20 px-1 text-[10px] font-semibold text-orange-400">{score}</span>
  if (score >= 25) return <span className="rounded bg-yellow-400/20 px-1 text-[10px] font-semibold text-yellow-400">{score}</span>
  return <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{score}</span>
}

const ZONE_LABEL_CARGO: Record<string, string> = {
  rotterdam: 'Rotterdam',
  port_said: 'Port Said',
  singapore_west: "S'pore West",
  singapore_east: "S'pore East",
  suez_roads: 'Suez Roads',
  hormuz_anchorage: 'Hormuz Anch.',
  fujairah: 'Fujairah',
  kharg: 'Kharg Is.',
  ras_tanura: 'Ras Tanura',
  bonny: 'Bonny (Nig)',
}

const CARGO_SEGMENTS = [
  { value: '', label: 'All' },
  { value: 'VLCC', label: 'VLCC' },
  { value: 'Suezmax', label: 'Suezmax' },
  { value: 'Aframax', label: 'Aframax' },
  { value: 'Panamax', label: 'Panamax' },
  { value: 'MR', label: 'MR Tanker' },
  { value: 'Capesize', label: 'Capesize' },
]

const REGION_SHORT: Record<string, string> = {
  'Far East': 'Far East',
  'SE Asia': 'SE Asia',
  'South Asia': 'S Asia',
  'Middle East': 'Mid East',
  'NW Europe': 'NW Eur',
  'Med': 'Med',
  'Americas': 'Americas',
  'W Africa': 'W Africa',
  'E Africa': 'E Africa',
  'S Africa': 'S Africa',
  'Oceania': 'Oceania',
  'Russia/CIS': 'Russia',
  'Unknown': '?',
}

// ---------------------------------------------------------------------------
// TradeLaneMatrixCard
// ---------------------------------------------------------------------------
export function TradeLaneMatrixCard() {
  const [kindFilter, setKindFilter] = useState('')
  const [ladenOnly, setLadenOnly] = useState(true)
  const { data, isLoading } = useTradeLaneMatrix(kindFilter, ladenOnly)

  const cellMap = React.useMemo(() => {
    if (!data?.cells.length) return new Map<string, { vessel_count: number; high_risk_count: number }>()
    const m = new Map<string, { vessel_count: number; high_risk_count: number }>()
    for (const c of data.cells) {
      m.set(`${c.origin_region}|${c.dest_region}`, {
        vessel_count: c.vessel_count,
        high_risk_count: c.high_risk_count,
      })
    }
    return m
  }, [data])

  const maxCount = React.useMemo(() => {
    if (!data?.cells.length) return 1
    return Math.max(...data.cells.map(c => c.vessel_count), 1)
  }, [data])

  const origins = data?.origin_regions ?? []
  const dests = data?.dest_regions ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Trade Lane Matrix</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={kindFilter}
              onChange={e => setKindFilter(e.target.value)}
            >
              <option value="">All types</option>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
            </select>
            <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer">
              <input type="checkbox" checked={ladenOnly} onChange={e => setLadenOnly(e.target.checked)} className="h-3 w-3" />
              Laden only
            </label>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            Origin AIS region to destination macro-region. Cell intensity = vessel count. Red tint = high-risk vessels.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-48 animate-pulse rounded bg-muted/40" />
        ) : origins.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No data yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr>
                  <th className="py-1 pr-2 text-left text-muted-foreground font-normal">Origin</th>
                  {dests.map(dest => (
                    <th key={dest} className="py-1 px-1 text-center text-muted-foreground font-normal min-w-[52px]">
                      {REGION_SHORT[dest] ?? dest}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {origins.map(origin => (
                  <tr key={origin} className="border-t border-border/20">
                    <td className="py-1 pr-2 text-muted-foreground whitespace-nowrap">
                      {origin.replace(/_/g, ' ')}
                    </td>
                    {dests.map(dest => {
                      const cell = cellMap.get(`${origin}|${dest}`)
                      if (!cell) return <td key={dest} className="py-1 px-1 text-center text-muted-foreground/20">-</td>
                      const bg = cellHeatColor(cell.vessel_count, maxCount)
                      const hasRisk = cell.high_risk_count > 0
                      return (
                        <td
                          key={dest}
                          className="py-1 px-1 text-center tabular-nums"
                          style={{ background: bg }}
                          title={`${origin} -> ${dest}: ${cell.vessel_count} vessels${hasRisk ? `, ${cell.high_risk_count} high-risk` : ''}`}
                        >
                          <span className="font-medium">{cell.vessel_count}</span>
                          {hasRisk && (
                            <span className="ml-0.5 text-red-400 text-[9px]">{'↑'}{cell.high_risk_count}</span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// DestinationFlowCard
// ---------------------------------------------------------------------------
export function DestinationFlowCard() {
  const [kindFilter, setKindFilter] = React.useState<'' | 'tanker' | 'bulk'>('')
  const [ladenOnly, setLadenOnly] = React.useState(true)
  const { data, isLoading } = useDestinationFlows(kindFilter, '', '', ladenOnly)
  const rows = data?.rows ?? []

  return (
    <Card className="bg-card/60 backdrop-blur border-border/40">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-sm font-medium">Cargo Destination Flows</CardTitle>
          <div className="flex gap-2">
            <div className="flex gap-1">
              {(['', 'tanker', 'bulk'] as const).map(k => (
                <button key={k || 'all'} onClick={() => setKindFilter(k)}
                  className={`rounded px-2 py-0.5 text-xs ${kindFilter === k ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                  {k || 'All'}
                </button>
              ))}
            </div>
            <button
              onClick={() => setLadenOnly(!ladenOnly)}
              className={`rounded px-2 py-0.5 text-xs ${ladenOnly ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
              Laden only
            </button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Top destination flows by origin region
          {data && ladenOnly && ` — ${data.total_laden.toLocaleString()} laden vessels`}
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <ChartSkeleton className="h-24" />}
        {!isLoading && rows.length === 0 && (
          <p className="text-xs text-muted-foreground">No destination data available.</p>
        )}
        {rows.length > 0 && (
          <div className="overflow-auto max-h-[400px]">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border/40 text-muted-foreground">
                  <th className="text-left py-1 pr-3 font-medium">Origin</th>
                  <th className="text-left py-1 pr-3 font-medium">Destination</th>
                  <th className="text-left py-1 pr-3 font-medium">Segment</th>
                  <th className="text-right py-1 font-medium">Vessels</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-border/20 hover:bg-muted/20">
                    <td className="py-1.5 pr-3 text-muted-foreground">
                      {REGION_LABELS[row.origin_region] ?? row.origin_region.replace(/_/g, ' ')}
                    </td>
                    <td className="py-1.5 pr-3 font-mono font-medium text-foreground/90">{row.destination}</td>
                    <td className="pr-3 text-muted-foreground">{row.segment ?? row.kind ?? '-'}</td>
                    <td className="text-right tabular-nums font-semibold text-foreground/80">{row.vessel_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// CargoTransitionsCard
// ---------------------------------------------------------------------------
export function CargoTransitionsCard() {
  const [days, setDays] = React.useState(7)
  const [seg, setSeg] = React.useState('')
  const { data, isLoading } = useCargoTransitions(days, 2.0, seg)
  const rows = data?.rows ?? []
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-sm font-medium">Cargo Transitions</CardTitle>
          <div className="flex gap-1 flex-wrap">
            {[3, 7, 14].map((d) => (
              <button key={d} onClick={() => setDays(d)}
                className={`rounded px-2 py-0.5 text-xs ${days === d ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                {d}d
              </button>
            ))}
            <select
              value={seg}
              onChange={(e) => setSeg(e.target.value)}
              className="rounded border border-border bg-background px-1.5 py-0.5 text-xs text-foreground ml-1"
            >
              {CARGO_SEGMENTS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Loading and discharge events inferred from draught step-changes (6h median buckets)
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <ChartSkeleton className="h-24" />}
        {!isLoading && rows.length === 0 && (
          <p className="text-xs text-muted-foreground">No transitions detected for selected filters.</p>
        )}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-2 font-normal">Vessel</th>
                  <th className="pb-1 pr-2 font-normal">Seg</th>
                  <th className="pb-1 pr-2 font-normal">Region</th>
                  <th className="pb-1 pr-2 font-normal">Direction</th>
                  <th className="pb-1 pr-2 font-normal">Before</th>
                  <th className="pb-1 pr-2 font-normal">After</th>
                  <th className="pb-1 pr-2 font-normal">Change</th>
                  <th className="pb-1 font-normal">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.mmsi}-${i}`} className="cursor-pointer border-t border-border/30 hover:bg-muted/20" onClick={() => goToTracker(r.mmsi, r.lat, r.lon)}>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{r.name ?? r.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{r.segment ?? r.kind ?? '-'}</td>
                    <td className="max-w-[7rem] truncate py-0.5 pr-2 text-muted-foreground">{r.region ? r.region.replace(/_/g, ' ') : '-'}</td>
                    <td className="py-0.5 pr-2">
                      {r.direction === 'loading' ? (
                        <span className="text-green-400">&#8679; Loading</span>
                      ) : (
                        <span className="text-orange-400">&#8681; Discharging</span>
                      )}
                    </td>
                    <td className="py-0.5 pr-2 tabular-nums">{r.draught_before.toFixed(1)}m</td>
                    <td className="py-0.5 pr-2 tabular-nums">{r.draught_after.toFixed(1)}m</td>
                    <td className="py-0.5 pr-2 tabular-nums font-medium">
                      <span className={r.direction === 'loading' ? 'text-green-400' : 'text-orange-400'}>
                        {r.direction === 'loading' ? '+' : '-'}{r.change_m.toFixed(1)}m
                      </span>
                    </td>
                    <td className="py-0.5">{riskBadge(r.risk_score, r.ofac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// CargoStateChangesCard
// ---------------------------------------------------------------------------
export function CargoStateChangesCard() {
  const [days, setDays] = useState(7)
  const [kind, setKind] = useState('tanker')
  const [minChange, setMinChange] = useState(1.5)
  const { data, isLoading } = useCargoStateChanges(days, kind, minChange)
  const rows = data?.rows ?? []
  const goToTracker = useGoToTracker()
  const loaded = rows.filter(r => r.cargo_state === 'loaded').length
  const discharged = rows.filter(r => r.cargo_state === 'discharged').length

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Cargo Loading / Discharge Events</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={3}>Last 3d</option>
              <option value={7}>Last 7d</option>
              <option value={14}>Last 14d</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
              <option value="">All types</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={minChange} onChange={e => setMinChange(Number(e.target.value))}>
              <option value={1.0}>Min 1.0m</option>
              <option value={1.5}>Min 1.5m</option>
              <option value={2.5}>Min 2.5m</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_events} events in last {data.days}d.{' '}
            <span className="text-green-400">{loaded} loaded</span>,{' '}
            <span className="text-blue-400">{discharged} discharged</span>.
            Detected from draught change ({'>='}{minChange}m) between port entry and exit.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No cargo state changes in window.</p>
        ) : (
          <div className="space-y-0.5 max-h-72 overflow-y-auto pr-1">
            {rows.map((row) => (
              <div
                key={`${row.mmsi}-${row.start_ts}`}
                className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs ${row.cargo_state === 'loaded' ? 'bg-green-500/8 hover:bg-green-500/15' : 'bg-blue-500/8 hover:bg-blue-500/15'}`}
                onClick={() => goToTracker(row.mmsi, row.lat, row.lon)}
              >
                <span className={`w-16 shrink-0 rounded px-1 py-0.5 text-center text-[10px] font-bold ${row.cargo_state === 'loaded' ? 'bg-green-500/20 text-green-400' : 'bg-blue-500/20 text-blue-400'}`}>
                  {row.cargo_state === 'loaded' ? 'LOADED' : 'DISCHRG'}
                </span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  <span className="ml-1 text-muted-foreground/60">{ZONE_LABEL_CARGO[row.zone] ?? row.zone.replace(/_/g, ' ')}</span>
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-semibold">
                    {row.draught_entry?.toFixed(1)}m {'->'} {row.draught_exit?.toFixed(1)}m
                  </span>
                  <span className={`ml-1 font-bold ${(row.draught_change_m ?? 0) > 0 ? 'text-green-400' : 'text-blue-400'}`}>
                    {(row.draught_change_m ?? 0) > 0 ? '+' : ''}{row.draught_change_m?.toFixed(1)}m
                  </span>
                  <span className="ml-1 text-muted-foreground/60">{row.dwell_hours}h</span>
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
// LadenCard
// ---------------------------------------------------------------------------
export function LadenCard() {
  const [kind, setKind] = useState<'tanker' | 'bulk'>('tanker')
  const { data, isLoading } = useLaden(kind)

  const chartData = (data?.segments ?? [])
    .filter((s) => s.laden + s.ballast + s.unknown > 0)
    .map((s) => ({ segment: s.segment, Laden: s.laden, Ballast: s.ballast, Unknown: s.unknown }))

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Laden / Ballast Split</CardTitle>
          <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={kind} onChange={(e) => setKind(e.target.value as 'tanker' | 'bulk')}>
            <option value="tanker">Tankers</option>
            <option value="bulk">Bulkers</option>
          </select>
        </div>
        <p className="text-xs text-muted-foreground">Current fleet status by segment.</p>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? <ChartSkeleton /> : chartData.length === 0 ? (
          <EmptyState message="No draught data yet." />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 24, left: 60, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="segment" tick={{ fontSize: 10 }} width={58} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar dataKey="Laden" stackId="a" fill="#22c55e" />
              <Bar dataKey="Ballast" stackId="a" fill="#3b82f6" />
              <Bar dataKey="Unknown" stackId="a" fill="#94a3b8" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}
