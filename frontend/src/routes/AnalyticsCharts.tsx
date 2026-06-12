import React, { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useCongestion, useDensity, useLaden, useTransits, usePortFlow, useOwnerRisk, useFleetSpeed, useRegionUtil, useFlagRisk, useSpeedTrend, useStsRisk, useReroutes, useTransitRisk, useFleetAge, useAnchorageDwell, useCargoTransitions, useSlowSteamers, useFleetUtilization, useRiskEvents, usePortCongestion, useDestinationFlows, useMarketSummary, useVesselRiskScores, useChokepointHeatmap, useTradeLaneMatrix, useAnomalyWatchlist, useStsProximity, useRegionMomentum, useEventRateTimeline, useTransitRateTimeline, useAnchorageOccupancy, useStsOffenders, useFleetAtTime, useDestinationChanges, useOwnerIntelligence, useChokepointAnomaly } from '@/lib/api'
import type { RiskEventItem } from '@/lib/api'

const CHOKEPOINTS = [
  'singapore_malacca', 'suez', 'hormuz', 'panama', 'gibraltar',
  'bosphorus_dardanelles', 'dover_channel', 'cape_good_hope', 'bab_el_mandeb',
]

const ANCHORAGE_ZONES = [
  'singapore_east', 'singapore_west', 'fujairah', 'suez_roads', 'port_said',
  'rotterdam', 'galveston_ltg', 'arab_gulf_north', 'qingdao', 'port_hedland',
  'richards_bay', 'santos', 'tubarao', 'bab_djibouti',
]

const DENSITY_REGIONS = ['hormuz', 'singapore_malacca', 'suez', 'panama', 'dover_channel']

const DIRECTION_COLORS: Record<string, string> = {
  northbound: '#22c55e', southbound: '#f97316',
  eastbound: '#3b82f6', westbound: '#a855f7',
  outbound: '#22c55e', inbound_gulf: '#f97316',
}

function fmt(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
      {message}
    </div>
  )
}

const TOOLTIP_STYLE = { background: 'var(--card)', border: '1px solid var(--border)', fontSize: 12 }
const LEGEND_STYLE = { fontSize: 11 }

// ---------------------------------------------------------------------------
// Phase 29: Market Summary KPI Card
// ---------------------------------------------------------------------------

export function MarketSummaryCard() {
  const { data, isLoading } = useMarketSummary()

  const kpis = data ? [
    { label: 'Laden vessels', value: data.total_laden.toLocaleString(), sub: `${data.laden_pct}% of fleet` },
    { label: 'Transits 24h', value: data.transits_24h.toLocaleString(), sub: 'chokepoint crossings' },
    { label: 'Reroutes 24h', value: data.reroutes_24h.toLocaleString(), sub: 'destination changes' },
    { label: 'STS 24h', value: data.sts_24h.toLocaleString(), sub: 'ship-to-ship transfers' },
  ] : []

  const topSegments = (data?.by_segment ?? []).slice(0, 6)

  return (
    <Card className="bg-card/60 backdrop-blur border-border/40">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Market State</CardTitle>
        <p className="text-xs text-muted-foreground mt-0.5">
          Live fleet snapshot{data && ` — ${data.total_fleet.toLocaleString()} vessels tracked`}
        </p>
      </CardHeader>
      <CardContent className="pt-0 space-y-4">
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
        {!isLoading && data && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {kpis.map(kpi => (
                <div key={kpi.label} className="rounded border border-border/30 bg-muted/20 px-3 py-2">
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wide">{kpi.label}</p>
                  <p className="text-xl font-semibold tabular-nums">{kpi.value}</p>
                  <p className="text-[10px] text-muted-foreground">{kpi.sub}</p>
                </div>
              ))}
            </div>
            {topSegments.length > 0 && (
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border/40 text-muted-foreground">
                    <th className="text-left py-1 pr-3 font-medium">Segment</th>
                    <th className="text-right pr-3 font-medium">Fleet</th>
                    <th className="text-right pr-3 font-medium">Laden%</th>
                    <th className="text-right font-medium">Underway%</th>
                  </tr>
                </thead>
                <tbody>
                  {topSegments.map(s => (
                    <tr key={`${s.segment}-${s.kind}`} className="border-b border-border/20">
                      <td className="py-1 pr-3">
                        <span className="font-medium">{s.segment}</span>
                        <span className="ml-1 text-muted-foreground/60 text-[10px]">{s.kind}</span>
                      </td>
                      <td className="text-right pr-3 tabular-nums text-muted-foreground">{s.total}</td>
                      <td className={`text-right pr-3 tabular-nums font-medium ${s.laden_pct >= 60 ? 'text-green-400' : s.laden_pct >= 40 ? 'text-yellow-400' : 'text-muted-foreground'}`}>
                        {s.laden_pct.toFixed(0)}%
                      </td>
                      <td className={`text-right tabular-nums ${s.underway_pct >= 70 ? 'text-green-400' : s.underway_pct >= 50 ? 'text-yellow-400' : 'text-muted-foreground'}`}>
                        {s.underway_pct.toFixed(0)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function TransitsCard() {
  const [chokepoint, setChokepoint] = useState('suez')
  const [days, setDays] = useState(30)
  const { data, isLoading } = useTransits(chokepoint, days)

  const chartData: Record<string, string | number>[] = []
  if (data?.series) {
    const byDate: Record<string, Record<string, number>> = {}
    for (const row of data.series) {
      if (!byDate[row.date]) byDate[row.date] = {}
      byDate[row.date][row.direction] = (byDate[row.date][row.direction] ?? 0) + row.count
    }
    for (const [date, dirs] of Object.entries(byDate).sort()) {
      chartData.push({ date: date.slice(5), ...dirs })
    }
  }
  const directions = [...new Set(data?.series.map((r) => r.direction) ?? [])]

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Chokepoint Transits</CardTitle>
          <div className="flex gap-2">
            <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={chokepoint} onChange={(e) => setChokepoint(e.target.value)}>
              {CHOKEPOINTS.map((cp) => <option key={cp} value={cp}>{fmt(cp)}</option>)}
            </select>
            <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 14, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">Daily transits. Data from 2026-06.</p>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? <EmptyState message="Loading..." /> : chartData.length === 0 ? (
          <EmptyState message="No transit events yet. Data accumulates as history grows." />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              {directions.map((dir) => (
                <Bar key={dir} dataKey={dir} stackId="a" fill={DIRECTION_COLORS[dir] ?? '#94a3b8'} name={fmt(dir)} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

export function CongestionCard() {
  const [zone, setZone] = useState('singapore_east')
  const [days, setDays] = useState(30)
  const { data, isLoading } = useCongestion(zone, days)

  const chartData = (data?.series ?? []).map((r) => ({
    date: r.date.slice(5),
    vessels: r.vessel_count,
    dwell: r.median_dwell_hours ?? 0,
  }))

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Port Congestion</CardTitle>
          <div className="flex gap-2">
            <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={zone} onChange={(e) => setZone(e.target.value)}>
              {ANCHORAGE_ZONES.map((z) => <option key={z} value={z}>{fmt(z)}</option>)}
            </select>
            <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">Daily anchored vessels and median dwell (hours). Data from 2026-06.</p>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? <EmptyState message="Loading..." /> : chartData.length === 0 ? (
          <EmptyState message="No anchored episodes yet." />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis yAxisId="left" tick={{ fontSize: 10 }} allowDecimals={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Line yAxisId="left" type="monotone" dataKey="vessels" stroke="#3b82f6" name="Vessels anchored" dot={false} />
              <Line yAxisId="right" type="monotone" dataKey="dwell" stroke="#f59e0b" name="Median dwell (h)" dot={false} strokeDasharray="4 2" />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

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
        {isLoading || !data ? <EmptyState message="Loading..." /> : chartData.length === 0 ? (
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

export function DensityCard() {
  const [region, setRegion] = useState('hormuz')
  const [days, setDays] = useState(30)
  const { data, isLoading } = useDensity(region, days)

  const byDate: Record<string, { laden: number; ballast: number; unknown: number }> = {}
  for (const row of data?.series ?? []) {
    const d = row.date.slice(5)
    if (!byDate[d]) byDate[d] = { laden: 0, ballast: 0, unknown: 0 }
    byDate[d].laden += row.laden_count
    byDate[d].ballast += row.ballast_count
    byDate[d].unknown += row.unknown_count
  }
  const chartData = Object.entries(byDate).sort().map(([date, v]) => ({ date, ...v }))

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Fleet Density</CardTitle>
          <div className="flex gap-2">
            <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={region} onChange={(e) => setRegion(e.target.value)}>
              {DENSITY_REGIONS.map((r) => <option key={r} value={r}>{fmt(r)}</option>)}
            </select>
            <select className="rounded border border-border bg-secondary px-2 py-1 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">Daily vessels in region by laden status.</p>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? <EmptyState message="Loading..." /> : chartData.length === 0 ? (
          <EmptyState message="No density data yet." />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar dataKey="laden" stackId="a" fill="#22c55e" name="Laden" />
              <Bar dataKey="ballast" stackId="a" fill="#3b82f6" name="Ballast" />
              <Bar dataKey="unknown" stackId="a" fill="#94a3b8" name="Unknown" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

export function PortFlowCard() {
  const [kind, setKind] = useState<string | undefined>()
  const { data, isLoading } = usePortFlow(kind, 20)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Live Destination Distribution</CardTitle>
        <div className="flex gap-1">
          {([undefined, 'tanker', 'bulk'] as const).map((k) => (
            <button
              key={k ?? 'all'}
              onClick={() => setKind(k)}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                kind === k ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {k ?? 'All'}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <EmptyState message="Loading..." />
        ) : data.ports.length === 0 ? (
          <EmptyState message="No destination data yet." />
        ) : (
          <>
            <div className="mb-2 text-[10px] text-muted-foreground">
              {data.total_with_dest} vessels with recorded destination
            </div>
            <div className="space-y-1">
              {data.ports.map((p) => {
                const pct = data.total_with_dest > 0 ? (p.count / data.total_with_dest) * 100 : 0
                return (
                  <div key={p.destination} className="flex items-center gap-2 text-xs">
                    <div className="w-28 shrink-0 truncate font-mono text-[10px]">{p.destination}</div>
                    <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary/60"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="w-8 shrink-0 text-right text-muted-foreground">{p.count}</div>
                    <div className="w-14 shrink-0 text-right text-[10px] text-muted-foreground/60">
                      {p.tankers > 0 && `${p.tankers}T`}{p.tankers > 0 && p.bulkers > 0 && ' '}{p.bulkers > 0 && `${p.bulkers}B`}
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function riskColor(score: number): string {
  if (score >= 70) return 'text-red-400'
  if (score >= 50) return 'text-orange-400'
  if (score >= 30) return 'text-yellow-400'
  return 'text-green-400'
}

export function OwnerRiskCard() {
  const [minVessels, setMinVessels] = useState(2)
  const { data, isLoading } = useOwnerRisk(minVessels, 25)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Owner Risk Concentration</CardTitle>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span>Min vessels:</span>
          {[1, 2, 3, 5].map((n) => (
            <button
              key={n}
              onClick={() => setMinVessels(n)}
              className={`rounded px-1.5 py-0.5 font-medium transition-colors ${
                minVessels === n ? 'bg-primary/20 text-primary' : 'hover:text-foreground'
              }`}
            >
              {n}+
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <EmptyState message="Loading..." />
        ) : data.rows.length === 0 ? (
          <EmptyState message="No owner data available. Registry crawl may not have run yet." />
        ) : (
          <div className="space-y-1">
            <div className="grid grid-cols-[1fr_3rem_3rem_3rem_3rem] gap-1 pb-1 text-[10px] font-medium text-muted-foreground border-b border-border">
              <span>Owner</span>
              <span className="text-right">Ships</span>
              <span className="text-right">Avg</span>
              <span className="text-right">Max</span>
              <span className="text-right">High</span>
            </div>
            {data.rows.map((row) => (
              <div
                key={row.owner}
                className="grid grid-cols-[1fr_3rem_3rem_3rem_3rem] gap-1 items-center text-xs"
              >
                <div className="truncate font-medium" title={row.owner}>
                  {row.owner}
                  {row.ofac_count > 0 && (
                    <span className="ml-1 rounded bg-red-500/15 px-1 py-0.5 text-[9px] font-semibold text-red-400">
                      OFAC
                    </span>
                  )}
                  {row.flags.length > 0 && (
                    <span className="ml-1 text-[9px] text-muted-foreground/60">
                      {row.flags.slice(0, 2).join(', ')}
                    </span>
                  )}
                </div>
                <span className="text-right text-muted-foreground">{row.vessel_count}</span>
                <span className={`text-right font-mono font-semibold ${riskColor(row.avg_risk_score)}`}>
                  {row.avg_risk_score.toFixed(0)}
                </span>
                <span className={`text-right font-mono text-[10px] ${riskColor(row.max_risk_score)}`}>
                  {row.max_risk_score}
                </span>
                <span className="text-right text-[10px] text-muted-foreground">
                  {row.high_risk_count > 0 ? (
                    <span className="text-orange-400">{row.high_risk_count}</span>
                  ) : '0'}
                </span>
              </div>
            ))}
            <div className="pt-1 text-[10px] text-muted-foreground/50">
              Avg/Max: risk score 0-100. High: vessels with score &gt;= 50.
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

const SEG_COLORS: Record<string, string> = {
  VLCC: '#ef4444',
  Suezmax: '#f97316',
  Aframax: '#eab308',
  Panamax: '#22c55e',
  Capesize: '#3b82f6',
  Supramax: '#8b5cf6',
  Handymax: '#06b6d4',
  Handysize: '#64748b',
  Small: '#94a3b8',
  ULCC: '#be123c',
}

export function FleetSpeedCard() {
  const [kindFilter, setKindFilter] = useState<string | undefined>()
  const { data, isLoading } = useFleetSpeed()

  const rows = (data?.rows ?? []).filter((r) => !kindFilter || r.kind === kindFilter)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">Fleet Speed by Segment</CardTitle>
        <div className="flex gap-1">
          {([undefined, 'tanker', 'bulk'] as const).map((k) => (
            <button
              key={k ?? 'all'}
              onClick={() => setKindFilter(k)}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                kindFilter === k ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {k ?? 'All'}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <EmptyState message="Loading..." />
        ) : rows.length === 0 ? (
          <EmptyState message="No speed data available." />
        ) : (
          <>
            <div className="mb-1 text-[10px] text-muted-foreground">
              {data.total_vessels.toLocaleString()} vessels tracked. Avg SOG = underway (nav=0, SOG &gt; 0.2 kn).
            </div>
            <div className="space-y-1.5">
              <div className="grid grid-cols-[1fr_3rem_3.5rem_3.5rem_4rem] gap-1 text-[10px] font-medium text-muted-foreground pb-1 border-b border-border">
                <span>Segment</span>
                <span className="text-right">Ships</span>
                <span className="text-right">%Underway</span>
                <span className="text-right">Avg kn</span>
                <div className="text-right">Status</div>
              </div>
              {rows.map((r) => (
                <div key={`${r.kind}-${r.segment}`} className="grid grid-cols-[1fr_3rem_3.5rem_3.5rem_4rem] gap-1 items-center text-xs">
                  <div className="flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: SEG_COLORS[r.segment] ?? '#94a3b8' }}
                    />
                    <span className="font-medium">{r.segment}</span>
                    <span className="text-[10px] text-muted-foreground/60">{r.kind[0].toUpperCase()}</span>
                  </div>
                  <span className="text-right text-muted-foreground">{r.total}</span>
                  <span className={`text-right font-mono font-semibold ${r.pct_underway >= 60 ? 'text-green-400' : r.pct_underway >= 40 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {r.pct_underway.toFixed(0)}%
                  </span>
                  <span className="text-right font-mono text-muted-foreground">
                    {r.avg_sog_underway != null ? r.avg_sog_underway.toFixed(1) : '-'}
                  </span>
                  <div className="flex gap-0.5 justify-end">
                    {r.underway > 0 && (
                      <span className="rounded bg-green-500/10 px-1 py-0.5 text-[9px] text-green-400">
                        {r.underway}U
                      </span>
                    )}
                    {r.anchored > 0 && (
                      <span className="rounded bg-yellow-500/10 px-1 py-0.5 text-[9px] text-yellow-400">
                        {r.anchored}A
                      </span>
                    )}
                    {r.moored > 0 && (
                      <span className="rounded bg-blue-500/10 px-1 py-0.5 text-[9px] text-blue-400">
                        {r.moored}M
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function RegionUtilCard() {
  const { data, isLoading } = useRegionUtil()

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Regional Utilization</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <EmptyState message="Loading..." />
        ) : data.rows.length === 0 ? (
          <EmptyState message="No region data." />
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart
              data={data.rows.map((r) => ({
                region: r.region.replace(/_/g, ' '),
                underway: r.underway,
                anchored: r.anchored,
                moored: r.moored,
                other: r.total - r.underway - r.anchored - r.moored,
              }))}
              layout="vertical"
              margin={{ top: 0, right: 8, left: 80, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis type="number" tick={{ fontSize: 9 }} allowDecimals={false} />
              <YAxis type="category" dataKey="region" tick={{ fontSize: 9 }} width={80} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar dataKey="underway" stackId="a" fill="#22c55e" name="Underway" />
              <Bar dataKey="anchored" stackId="a" fill="#eab308" name="Anchored" />
              <Bar dataKey="moored" stackId="a" fill="#3b82f6" name="Moored" />
              <Bar dataKey="other" stackId="a" fill="#475569" name="Other" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

const MOU_COLORS: Record<string, string> = {
  Black: 'text-red-400',
  Grey: 'text-yellow-400',
  White: 'text-green-400',
}

export function FlagRiskCard() {
  const { data, isLoading } = useFlagRisk(25)

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Flag State Risk</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <EmptyState message="Loading..." />
        ) : data.rows.length === 0 ? (
          <EmptyState message="No flag data yet. Registry crawl populates this." />
        ) : (
          <div className="space-y-1">
            <div className="grid grid-cols-[1fr_3rem_3rem_3rem_5rem] gap-1 pb-1 text-[10px] font-medium text-muted-foreground border-b border-border">
              <span>Flag</span>
              <span className="text-right">Ships</span>
              <span className="text-right">Avg</span>
              <span className="text-right">High</span>
              <span className="text-right">MOU (P/T)</span>
            </div>
            {data.rows.map((row) => (
              <div
                key={row.flag}
                className="grid grid-cols-[1fr_3rem_3rem_3rem_5rem] gap-1 items-center text-xs"
              >
                <div className="truncate font-medium" title={row.flag}>
                  {row.flag_code && (
                    <span className="mr-1 text-[9px] text-muted-foreground/60 font-mono">{row.flag_code}</span>
                  )}
                  {row.flag}
                  {row.ofac_count > 0 && (
                    <span className="ml-1 rounded bg-red-500/15 px-1 py-0.5 text-[9px] font-semibold text-red-400">OFAC</span>
                  )}
                </div>
                <span className="text-right text-muted-foreground">{row.vessel_count}</span>
                <span className={`text-right font-mono font-semibold ${riskColor(row.avg_risk_score)}`}>
                  {row.avg_risk_score.toFixed(0)}
                </span>
                <span className="text-right text-[10px] text-muted-foreground">
                  {row.high_risk_count > 0 ? <span className="text-orange-400">{row.high_risk_count}</span> : '0'}
                </span>
                <div className="flex gap-1 justify-end text-[9px]">
                  {row.paris_mou && (
                    <span className={MOU_COLORS[row.paris_mou] ?? 'text-muted-foreground'}>
                      P:{row.paris_mou[0]}
                    </span>
                  )}
                  {row.tokyo_mou && (
                    <span className={MOU_COLORS[row.tokyo_mou] ?? 'text-muted-foreground'}>
                      T:{row.tokyo_mou[0]}
                    </span>
                  )}
                </div>
              </div>
            ))}
            <div className="pt-1 text-[10px] text-muted-foreground/50">
              P/T: Paris/Tokyo MOU (B=Black, G=Grey, W=White). Grows as registry is crawled.
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

const SPEED_SEGMENTS = [
  { kind: 'tanker', segment: 'VLCC', label: 'VLCC' },
  { kind: 'tanker', segment: 'Suezmax', label: 'Suezmax' },
  { kind: 'tanker', segment: 'Aframax', label: 'Aframax' },
  { kind: 'bulk', segment: 'Capesize', label: 'Capesize' },
  { kind: 'bulk', segment: 'Supramax', label: 'Supramax' },
] as const

export function SpeedTrendCard() {
  const [selected, setSelected] = useState<{ kind: string; segment: string }>({ kind: 'tanker', segment: 'VLCC' })
  const [days, setDays] = useState(14)
  const { data, isLoading } = useSpeedTrend(selected.kind, selected.segment, days)

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm font-medium">Fleet Speed Trend</CardTitle>
          <div className="flex flex-wrap gap-1">
            {SPEED_SEGMENTS.map((s) => (
              <button
                key={`${s.kind}-${s.segment}`}
                onClick={() => setSelected({ kind: s.kind, segment: s.segment })}
                className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  selected.segment === s.segment && selected.kind === s.kind
                    ? 'bg-primary/20 text-primary'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="ml-auto rounded border border-border bg-card px-1.5 py-0.5 text-[10px] text-foreground"
          >
            {[7, 14, 30].map((d) => <option key={d} value={d}>Last {d}d</option>)}
          </select>
        </div>
        <p className="text-[10px] text-muted-foreground">
          Daily avg SOG (kn) for underway vessels. Rising speed = tighter freight market.
        </p>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <EmptyState message="Loading..." />
        ) : data.series.length < 2 ? (
          <EmptyState message="Trend builds as snapshot history accumulates. Check back in a few days." />
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data.series} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 9 }}
                tickFormatter={(d: string) => d.slice(5)}
              />
              <YAxis tick={{ fontSize: 9 }} domain={['auto', 'auto']} unit=" kn" />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Line
                type="monotone"
                dataKey="avg_sog"
                stroke={SEG_COLORS[selected.segment] ?? '#3b82f6'}
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

function riskBadge(score: number | null, ofac: boolean) {
  if (ofac) return <span className="rounded bg-red-500/20 px-1 text-[10px] font-semibold text-red-400">OFAC</span>
  if (score == null) return null
  if (score >= 75) return <span className="rounded bg-red-400/20 px-1 text-[10px] font-semibold text-red-400">{score}</span>
  if (score >= 50) return <span className="rounded bg-orange-400/20 px-1 text-[10px] font-semibold text-orange-400">{score}</span>
  if (score >= 25) return <span className="rounded bg-yellow-400/20 px-1 text-[10px] font-semibold text-yellow-400">{score}</span>
  return <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{score}</span>
}

export function StsRiskCard() {
  const { data, isLoading } = useStsRisk(30, 0)
  const rows = data?.rows ?? []
  const showing = rows.slice(0, 15)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          STS Events
          {data && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {data.total_events.toLocaleString()} events / 30d
              {data.enriched_events > 0 && ` - ${data.enriched_events} risk-scored`}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && <EmptyState message="No STS events in last 30 days" />}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-2 font-normal">Time</th>
                  <th className="pb-1 pr-2 font-normal">Region</th>
                  <th className="pb-1 pr-2 font-normal">Vessel 1</th>
                  <th className="pb-1 pr-2 font-normal">Vessel 2</th>
                  <th className="pb-1 pr-2 font-normal">Dur.</th>
                  <th className="pb-1 font-normal">Risk</th>
                </tr>
              </thead>
              <tbody>
                {showing.map((ev) => (
                  <tr key={ev.event_id} className="border-t border-border/30">
                    <td className="py-0.5 pr-2 text-muted-foreground">{ev.start_ts.slice(5, 16).replace('T', ' ')}</td>
                    <td className="py-0.5 pr-2">{ev.region ? fmt(ev.region) : '-'}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{ev.name ?? ev.mmsi}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{ev.name2 ?? (ev.mmsi2 ?? '-')}</td>
                    <td className="py-0.5 pr-2 tabular-nums text-muted-foreground">
                      {ev.duration_hours != null ? `${ev.duration_hours.toFixed(1)}h` : '-'}
                    </td>
                    <td className="py-0.5">
                      <div className="flex gap-1">
                        {riskBadge(ev.risk_score, ev.ofac)}
                        {riskBadge(ev.risk_score2, ev.ofac2)}
                        {ev.max_risk === 0 && ev.risk_score == null && ev.risk_score2 == null && (
                          <span className="text-muted-foreground/40">-</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 15 && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                Showing 15 of {rows.length.toLocaleString()} events (sorted by max risk)
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function ReroutesCard() {
  const [days, setDays] = useState(7)
  const { data, isLoading } = useReroutes(days, 0)
  const rows = data?.rows ?? []
  const showing = rows.slice(0, 15)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          Destination Changes
          <div className="ml-auto flex gap-1">
            {[3, 7, 14].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded px-1.5 py-0.5 text-[10px] ${days === d ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {d}d
              </button>
            ))}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && <EmptyState message={`No destination changes in last ${days} days`} />}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <p className="mb-1 text-[10px] text-muted-foreground">
              {data?.total_events.toLocaleString()} changes total - sorted by risk
            </p>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-2 font-normal">Time</th>
                  <th className="pb-1 pr-2 font-normal">Vessel</th>
                  <th className="pb-1 pr-2 font-normal">From</th>
                  <th className="pb-1 pr-2 font-normal">To</th>
                  <th className="pb-1 font-normal">Risk</th>
                </tr>
              </thead>
              <tbody>
                {showing.map((ev) => (
                  <tr key={ev.event_id} className="border-t border-border/30">
                    <td className="py-0.5 pr-2 text-muted-foreground">{ev.start_ts.slice(5, 16).replace('T', ' ')}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{ev.name ?? ev.mmsi}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2 text-muted-foreground">{ev.old_destination ?? '-'}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{ev.new_destination ?? '-'}</td>
                    <td className="py-0.5">{riskBadge(ev.risk_score, ev.ofac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 15 && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                Showing 15 of {rows.length.toLocaleString()} (risk-sorted)
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

const RISK_CHOKEPOINTS = [
  'hormuz', 'suez', 'singapore_malacca', 'bab_el_mandeb',
  'dover_channel', 'cape_good_hope', 'bosphorus_dardanelles',
]

export function TransitRiskCard() {
  const [chokepoint, setChokepoint] = useState('hormuz')
  const [days, setDays] = useState(30)
  const { data, isLoading } = useTransitRisk(chokepoint, days, 0)
  const rows = data?.rows ?? []
  const showing = rows.slice(0, 15)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex flex-wrap items-center gap-2">
          Chokepoint Transits
          <select
            value={chokepoint}
            onChange={(e) => setChokepoint(e.target.value)}
            className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] focus:outline-none"
          >
            {RISK_CHOKEPOINTS.map((cp) => (
              <option key={cp} value={cp}>{fmt(cp)}</option>
            ))}
          </select>
          <div className="flex gap-1">
            {[7, 14, 30].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded px-1.5 py-0.5 text-[10px] ${days === d ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {d}d
              </button>
            ))}
          </div>
          {data && (
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              {data.total_transits.toLocaleString()} transits
              {data.enriched > 0 && ` - ${data.enriched} risk-scored`}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && (
          <EmptyState message={`No ${fmt(chokepoint)} transits in last ${days} days`} />
        )}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-2 font-normal">Time</th>
                  <th className="pb-1 pr-2 font-normal">Vessel</th>
                  <th className="pb-1 pr-2 font-normal">Segment</th>
                  <th className="pb-1 pr-2 font-normal">Dir.</th>
                  <th className="pb-1 pr-2 font-normal">Laden</th>
                  <th className="pb-1 font-normal">Risk</th>
                </tr>
              </thead>
              <tbody>
                {showing.map((ev, i) => (
                  <tr key={`${ev.mmsi}-${ev.entered_ts}-${i}`} className="border-t border-border/30">
                    <td className="py-0.5 pr-2 text-muted-foreground">{ev.entered_ts.slice(5, 16).replace('T', ' ')}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{ev.name ?? ev.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{ev.segment ?? ev.kind ?? '-'}</td>
                    <td className="py-0.5 pr-2">{ev.direction ? fmt(ev.direction) : '-'}</td>
                    <td className="py-0.5 pr-2">
                      {ev.laden === null ? '-' : ev.laden
                        ? <span className="text-blue-400">L</span>
                        : <span className="text-muted-foreground">B</span>}
                    </td>
                    <td className="py-0.5">{riskBadge(ev.risk_score, ev.ofac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 15 && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                Showing 15 of {rows.length.toLocaleString()} (risk-sorted)
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}


export function FleetAgeCard() {
  const { data, isLoading } = useFleetAge()
  const bands = data?.bands ?? []

  const chartData = bands.map((b) => ({
    band: b.age_band,
    vessels: b.vessel_count,
    avg_risk: b.avg_risk_score,
    high_risk: b.high_risk_count,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Fleet Age Distribution
          {data && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              by 5-year bands (ref {data.reference_year})
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && bands.length === 0 && <EmptyState message="No year_built data in registry yet" />}
        {!isLoading && bands.length > 0 && (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 24, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="band" tick={{ fontSize: 10 }} />
              <YAxis yAxisId="left" tick={{ fontSize: 10 }} allowDecimals={false} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} domain={[0, 100]} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar yAxisId="left" dataKey="vessels" fill="#3b82f6" name="Vessel count" opacity={0.7} />
              <Bar yAxisId="left" dataKey="high_risk" fill="#f97316" name="High risk (>=50)" opacity={0.85} />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="avg_risk"
                stroke="#ef4444"
                strokeWidth={2}
                dot={{ r: 3 }}
                name="Avg risk score"
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

const ANCHORAGE_DWELL_ZONES = [
  'singapore_west', 'singapore_east', 'fujairah', 'suez_roads', 'port_said',
  'rotterdam', 'galveston_ltg', 'arab_gulf_north',
]

export function AnchorageDwellCard() {
  const [zone, setZone] = useState('singapore_west')
  const { data, isLoading } = useAnchorageDwell(zone, 20)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex flex-wrap items-center gap-2">
          Longest Anchored
          <select
            value={zone}
            onChange={(e) => setZone(e.target.value)}
            className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] focus:outline-none"
          >
            {ANCHORAGE_DWELL_ZONES.map((z) => (
              <option key={z} value={z}>{fmt(z)}</option>
            ))}
          </select>
          {data && rows.length > 0 && (
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              {rows.length} open episodes
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && (
          <EmptyState message={`No open anchor episodes at ${fmt(zone)}`} />
        )}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-2 font-normal">Vessel</th>
                  <th className="pb-1 pr-2 font-normal">Segment</th>
                  <th className="pb-1 pr-2 font-normal">Dwell</th>
                  <th className="pb-1 pr-2 font-normal">State</th>
                  <th className="pb-1 font-normal">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((v, i) => (
                  <tr key={`${v.mmsi}-${i}`} className="border-t border-border/30">
                    <td className="max-w-[9rem] truncate py-0.5 pr-2">{v.name ?? v.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{v.segment ?? v.kind ?? '-'}</td>
                    <td className="py-0.5 pr-2 tabular-nums">
                      {v.dwell_hours >= 24
                        ? <span className="text-orange-400">{(v.dwell_hours / 24).toFixed(1)}d</span>
                        : `${v.dwell_hours.toFixed(1)}h`}
                    </td>
                    <td className="py-0.5 pr-2">
                      {v.laden === 'laden'
                        ? <span className="text-blue-400">L</span>
                        : v.laden === 'ballast'
                          ? <span className="text-muted-foreground">B</span>
                          : <span className="text-muted-foreground">?</span>}
                    </td>
                    <td className="py-0.5">{riskBadge(v.risk_score, v.ofac)}</td>
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

const CARGO_SEGMENTS = [
  { value: '', label: 'All' },
  { value: 'VLCC', label: 'VLCC' },
  { value: 'Suezmax', label: 'Suezmax' },
  { value: 'Aframax', label: 'Aframax' },
  { value: 'Panamax', label: 'Panamax' },
  { value: 'MR', label: 'MR Tanker' },
  { value: 'Capesize', label: 'Capesize' },
]

export function CargoTransitionsCard() {
  const [days, setDays] = React.useState(7)
  const [seg, setSeg] = React.useState('')
  const { data, isLoading } = useCargoTransitions(days, 2.0, seg)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-sm font-medium">Cargo Transitions</CardTitle>
          <div className="flex gap-1 flex-wrap">
            {[3, 7, 14].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded px-2 py-0.5 text-xs ${days === d ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}
              >
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
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
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
                  <tr key={`${r.mmsi}-${i}`} className="border-t border-border/30">
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{r.name ?? r.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{r.segment ?? r.kind ?? '-'}</td>
                    <td className="max-w-[7rem] truncate py-0.5 pr-2 text-muted-foreground">
                      {r.region ? r.region.replace(/_/g, ' ') : '-'}
                    </td>
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

export function SlowSteamersCard() {
  const [kind, setKind] = React.useState('')
  const { data, isLoading } = useSlowSteamers(kind)
  const rows = data?.rows ?? []

  const pctColor = (pct: number) => {
    if (pct < 30) return 'text-red-400'
    if (pct < 45) return 'text-orange-400'
    return 'text-yellow-400'
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-sm font-medium">Slow Steamers</CardTitle>
          <div className="flex gap-1">
            {(['', 'tanker', 'bulk'] as const).map((k) => (
              <button
                key={k || 'all'}
                onClick={() => setKind(k)}
                className={`rounded px-2 py-0.5 text-xs ${kind === k ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {k || 'All'}
              </button>
            ))}
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Underway vessels below 60% of segment median SOG
          {data && ` (${data.total_fleet_underway.toLocaleString()} underway tracked)`}
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
        {!isLoading && rows.length === 0 && (
          <p className="text-xs text-muted-foreground">No slow-steaming vessels detected.</p>
        )}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-2 font-normal">Vessel</th>
                  <th className="pb-1 pr-2 font-normal">Seg</th>
                  <th className="pb-1 pr-2 font-normal">Region</th>
                  <th className="pb-1 pr-2 font-normal">SOG</th>
                  <th className="pb-1 pr-2 font-normal">Median</th>
                  <th className="pb-1 pr-2 font-normal">% of Med</th>
                  <th className="pb-1 font-normal">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.mmsi}-${i}`} className="border-t border-border/30">
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{r.name ?? r.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{r.segment ?? r.kind ?? '-'}</td>
                    <td className="max-w-[7rem] truncate py-0.5 pr-2 text-muted-foreground">
                      {r.region ? r.region.replace(/_/g, ' ') : '-'}
                    </td>
                    <td className="py-0.5 pr-2 tabular-nums">{r.sog.toFixed(1)} kn</td>
                    <td className="py-0.5 pr-2 tabular-nums text-muted-foreground">{r.segment_median_sog.toFixed(1)} kn</td>
                    <td className={`py-0.5 pr-2 tabular-nums font-medium ${pctColor(r.pct_of_median)}`}>
                      {r.pct_of_median.toFixed(0)}%
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

export function FleetUtilizationCard() {
  const [kindFilter, setKindFilter] = React.useState<'' | 'tanker' | 'bulk'>('')
  const { data, isLoading } = useFleetUtilization()

  const chartData = (data?.rows ?? [])
    .filter((r) => !kindFilter || r.kind === kindFilter)
    .map((r) => ({
      name: r.segment,
      Underway: r.underway_pct,
      Idle: r.idle_pct,
      Unknown: parseFloat((100 - r.underway_pct - r.idle_pct).toFixed(1)),
      total: r.total,
      avg_sog: r.avg_sog_underway,
    }))
    .sort((a, b) => b.Underway - a.Underway)

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-sm font-medium">Fleet Utilization by Segment</CardTitle>
          <div className="flex gap-1">
            {(['', 'tanker', 'bulk'] as const).map((k) => (
              <button
                key={k || 'all'}
                onClick={() => setKindFilter(k)}
                className={`rounded px-2 py-0.5 text-xs ${kindFilter === k ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {k || 'All'}
              </button>
            ))}
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          % of live fleet underway (SOG &gt;2 kn) vs idle (anchored/moored)
          {data && ` — ${data.total_fleet.toLocaleString()} vessels tracked`}
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
        {!isLoading && chartData.length === 0 && (
          <p className="text-xs text-muted-foreground">No utilization data available.</p>
        )}
        {!isLoading && chartData.length > 0 && (
          <BarChart width={440} height={220} data={chartData} layout="vertical" margin={{ left: 60, right: 40, top: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
            <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fontSize: 10, fill: '#888' }} />
            <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: '#ccc' }} width={58} />
            <Tooltip
              formatter={(val, name) => [`${Number(val).toFixed(1)}%`, String(name)]}
              contentStyle={{ background: '#1a1a2e', border: '1px solid #333', fontSize: 11 }}
            />
            <Bar dataKey="Underway" stackId="a" fill="#22c55e" radius={[0, 0, 0, 0]} />
            <Bar dataKey="Unknown" stackId="a" fill="#6b7280" />
            <Bar dataKey="Idle" stackId="a" fill="#f97316" radius={[0, 4, 4, 0]} />
          </BarChart>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 26: High-Risk Vessel Alert Feed
// ---------------------------------------------------------------------------

function alertColor(score: number): string {
  if (score >= 75) return 'text-red-400'
  if (score >= 50) return 'text-orange-400'
  return 'text-yellow-400'
}

function alertBg(score: number): string {
  if (score >= 75) return 'bg-red-900/40 border-red-700/40'
  if (score >= 50) return 'bg-orange-900/30 border-orange-700/40'
  return 'bg-yellow-900/20 border-yellow-700/30'
}

function RiskRow({ ev }: { ev: RiskEventItem }) {
  const ts = new Date(ev.event_ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  const isReroute = ev.event_type === 'reroute'
  const isSts = ev.event_type === 'sts'

  return (
    <div className={`rounded border px-3 py-2 text-xs space-y-1 ${alertBg(ev.max_risk)}`}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`font-semibold shrink-0 ${alertColor(ev.max_risk)}`}>
            {ev.max_risk}
          </span>
          <span className="font-medium truncate text-foreground/90">
            {ev.name ?? `MMSI ${ev.mmsi}`}
          </span>
          {ev.ofac && (
            <span className="rounded bg-red-700 px-1 py-0.5 text-[10px] font-bold text-white shrink-0">OFAC</span>
          )}
          {ev.segment && <span className="text-muted-foreground shrink-0">{ev.segment}</span>}
        </div>
        <div className="flex items-center gap-2 shrink-0 text-muted-foreground">
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${isReroute ? 'bg-blue-900/50 text-blue-300' : 'bg-purple-900/50 text-purple-300'}`}>
            {isReroute ? 'REROUTE' : 'STS'}
          </span>
          <span>{ts}</span>
        </div>
      </div>

      {isReroute && ev.old_destination && ev.new_destination && (
        <div className="text-muted-foreground">
          <span className="text-foreground/60">{ev.old_destination}</span>
          <span className="mx-1">{'->'}</span>
          <span className="font-medium text-foreground/80">{ev.new_destination}</span>
          {ev.region && <span className="ml-2 text-muted-foreground/60">({ev.region})</span>}
        </div>
      )}

      {isSts && ev.mmsi2 != null && (
        <div className="text-muted-foreground">
          {'with '}
          <span className={`font-medium ${ev.risk_score2 != null ? alertColor(ev.risk_score2) : 'text-foreground/70'}`}>
            {ev.name2 ?? `MMSI ${ev.mmsi2}`}
          </span>
          {ev.risk_score2 != null && (
            <span className="ml-1 text-muted-foreground/70">(score {ev.risk_score2})</span>
          )}
          {ev.ofac2 && (
            <span className="ml-1 rounded bg-red-700 px-1 py-0.5 text-[10px] font-bold text-white">OFAC</span>
          )}
        </div>
      )}
    </div>
  )
}

export function RiskEventsCard() {
  const [minRisk, setMinRisk] = React.useState(25)
  const [days, setDays] = React.useState(2)
  const { data, isLoading } = useRiskEvents(minRisk, days)
  const rows = data?.rows ?? []

  return (
    <Card className="bg-card/60 backdrop-blur border-border/40">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-sm font-medium">High-Risk Vessel Alerts</CardTitle>
          <div className="flex gap-2">
            <div className="flex gap-1">
              {([25, 50, 75] as const).map(r => (
                <button key={r} onClick={() => setMinRisk(r)}
                  className={`rounded px-2 py-0.5 text-xs ${minRisk === r ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                  {r === 25 ? 'Elevated' : r === 50 ? 'High' : 'Critical'}
                </button>
              ))}
            </div>
            <div className="flex gap-1">
              {([1, 2, 7] as const).map(d => (
                <button key={d} onClick={() => setDays(d)}
                  className={`rounded px-2 py-0.5 text-xs ${days === d ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          STS transfers and destination changes involving registry risk score &ge;{minRisk}
          {data && ` — ${data.total_high_risk_vessels} vessels tracked`}
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
        {!isLoading && rows.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No high-risk vessel events in the last {days}d window.
          </p>
        )}
        {rows.length > 0 && (
          <div className="space-y-2 max-h-[480px] overflow-y-auto pr-1">
            {rows.map(ev => <RiskRow key={ev.event_id} ev={ev} />)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 27: Port Congestion Monitor
// ---------------------------------------------------------------------------

function congestionColor(factor: number): string {
  if (factor >= 2.0) return 'text-red-400'
  if (factor >= 1.3) return 'text-orange-400'
  if (factor >= 0.7) return 'text-yellow-400'
  return 'text-green-400'
}

function congestionBadge(factor: number): string {
  if (factor >= 2.0) return 'CRITICAL'
  if (factor >= 1.3) return 'ELEVATED'
  if (factor >= 0.7) return 'NORMAL'
  return 'LOW'
}

export function PortCongestionCard() {
  const [kindFilter, setKindFilter] = React.useState<'' | 'tanker' | 'bulk'>('')
  const [days, setDays] = React.useState(14)
  const { data, isLoading } = usePortCongestion(kindFilter, days)
  const rows = (data?.rows ?? []).filter(r => r.current_vessels > 0 || (r.baseline_avg_vessels ?? 0) > 0)

  return (
    <Card className="bg-card/60 backdrop-blur border-border/40">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-sm font-medium">Port Congestion Monitor</CardTitle>
          <div className="flex gap-2">
            <div className="flex gap-1">
              {(['', 'tanker', 'bulk'] as const).map(k => (
                <button key={k || 'all'} onClick={() => setKindFilter(k)}
                  className={`rounded px-2 py-0.5 text-xs ${kindFilter === k ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                  {k || 'All'}
                </button>
              ))}
            </div>
            <div className="flex gap-1">
              {([7, 14, 30] as const).map(d => (
                <button key={d} onClick={() => setDays(d)}
                  className={`rounded px-2 py-0.5 text-xs ${days === d ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Current anchored vessels vs {days}d baseline - congestion factor = current / avg
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
        {!isLoading && rows.length === 0 && (
          <p className="text-xs text-muted-foreground">No anchored episodes in selected window.</p>
        )}
        {rows.length > 0 && (
          <div className="overflow-auto max-h-[400px]">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border/40 text-muted-foreground">
                  <th className="text-left py-1 pr-3 font-medium">Zone</th>
                  <th className="text-right py-1 pr-3 font-medium">Now</th>
                  <th className="text-right py-1 pr-3 font-medium">Dwell</th>
                  <th className="text-right py-1 pr-3 font-medium">Baseline</th>
                  <th className="text-right py-1 pr-3 font-medium">Factor</th>
                  <th className="text-right py-1 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.zone} className="border-b border-border/20 hover:bg-muted/20">
                    <td className="py-1.5 pr-3 font-medium text-foreground/90">
                      {row.zone.replace(/_/g, ' ')}
                      {row.region && <span className="ml-1 text-muted-foreground/60 text-[10px]">({row.region})</span>}
                    </td>
                    <td className="text-right pr-3 tabular-nums">{row.current_vessels}</td>
                    <td className="text-right pr-3 tabular-nums text-muted-foreground">
                      {row.avg_current_dwell_hours != null ? `${row.avg_current_dwell_hours.toFixed(0)}h` : '-'}
                    </td>
                    <td className="text-right pr-3 tabular-nums text-muted-foreground">
                      {row.baseline_avg_vessels != null ? row.baseline_avg_vessels.toFixed(1) : '-'}
                    </td>
                    <td className={`text-right pr-3 tabular-nums font-semibold ${congestionColor(row.congestion_factor)}`}>
                      {row.congestion_factor.toFixed(2)}x
                    </td>
                    <td className={`text-right text-[10px] font-medium ${congestionColor(row.congestion_factor)}`}>
                      {congestionBadge(row.congestion_factor)}
                    </td>
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
// Phase 28: Destination Flow Intelligence
// ---------------------------------------------------------------------------

const REGION_LABELS: Record<string, string> = {
  ara: 'ARA', singapore_malacca: 'Sing/Mal', hormuz: 'Hormuz',
  suez: 'Suez', japan_korea: 'Japan/Korea', us_gulf: 'US Gulf',
  west_africa: 'W Africa', east_africa: 'E Africa', north_sea: 'N Sea',
  black_sea: 'Black Sea', med: 'Med', us_east_coast: 'US East',
  us_west_coast: 'US West', brazil: 'Brazil', australia: 'Australia',
  saldanha_richards_bay: 'S Africa', unknown: '?',
}

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
        {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
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
                    <td className="py-1.5 pr-3 font-mono font-medium text-foreground/90">
                      {row.destination}
                    </td>
                    <td className="pr-3 text-muted-foreground">
                      {row.segment ?? row.kind ?? '-'}
                    </td>
                    <td className="text-right tabular-nums font-semibold text-foreground/80">
                      {row.vessel_count}
                    </td>
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
// Phase 30: Vessel Behavioral Risk Leaderboard
// ---------------------------------------------------------------------------

function riskScoreBar(score: number) {
  if (score >= 75) return 'bg-red-500'
  if (score >= 50) return 'bg-orange-500'
  if (score >= 25) return 'bg-yellow-500'
  return 'bg-green-500/60'
}

function riskScoreLabel(score: number) {
  if (score >= 75) return { text: 'Critical', cls: 'text-red-400 font-semibold' }
  if (score >= 50) return { text: 'High', cls: 'text-orange-400 font-semibold' }
  if (score >= 25) return { text: 'Elevated', cls: 'text-yellow-400' }
  return { text: 'Low', cls: 'text-green-400/80' }
}

export function VesselRiskLeaderboardCard() {
  const [topN, setTopN] = useState(25)
  const [days, setDays] = useState(30)
  const [kindFilter, setKindFilter] = useState('')
  const { data, isLoading } = useVesselRiskScores(topN, days, '', kindFilter, 5)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Vessel Risk Leaderboard</span>
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
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={days}
              onChange={e => setDays(Number(e.target.value))}
            >
              <option value={7}>7d</option>
              <option value={14}>14d</option>
              <option value={30}>30d</option>
              <option value={60}>60d</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={topN}
              onChange={e => setTopN(Number(e.target.value))}
            >
              <option value={25}>Top 25</option>
              <option value={50}>Top 50</option>
              <option value={100}>Top 100</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_candidates} candidates scored ({data.days}d behavioral window) as of{' '}
            {new Date(data.as_of).toLocaleTimeString()}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-48 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No vessels above minimum risk threshold.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted-foreground">
                  <th className="w-8 text-right py-1 pr-2">#</th>
                  <th className="text-left py-1 pr-3 font-medium">Vessel</th>
                  <th className="text-left py-1 pr-3 font-medium">Segment</th>
                  <th className="text-left py-1 pr-3 font-medium">Region</th>
                  <th className="text-center py-1 pr-2 font-medium">STS</th>
                  <th className="text-center py-1 pr-2 font-medium">Reroutes</th>
                  <th className="text-center py-1 pr-2 font-medium">Registry</th>
                  <th className="text-right py-1 font-medium">Score</th>
                  <th className="text-left py-1 pl-2 font-medium">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => {
                  const label = riskScoreLabel(row.total_score)
                  return (
                    <tr key={row.mmsi} className="border-b border-border/20 hover:bg-muted/20">
                      <td className="py-1.5 pr-2 text-right text-xs text-muted-foreground tabular-nums">
                        {i + 1}
                      </td>
                      <td className="py-1.5 pr-3">
                        <span className="font-medium text-foreground/90">{row.name ?? '—'}</span>
                        {row.ofac && (
                          <span className="ml-1.5 rounded bg-red-500/20 px-1 py-0.5 text-xs text-red-400 font-bold">
                            OFAC
                          </span>
                        )}
                        <div className="text-xs text-muted-foreground">
                          {row.mmsi}{row.imo ? ` / ${row.imo}` : ''}
                        </div>
                      </td>
                      <td className="py-1.5 pr-3 text-muted-foreground text-xs">
                        {row.segment ?? row.kind ?? '—'}
                      </td>
                      <td className="py-1.5 pr-3 text-muted-foreground text-xs">
                        {row.region?.replace(/_/g, ' ') ?? '—'}
                      </td>
                      <td className="py-1.5 pr-2 text-center tabular-nums font-mono text-xs">
                        {row.sts_count > 0 ? (
                          <span className="text-orange-400">{row.sts_count}</span>
                        ) : (
                          <span className="text-muted-foreground/40">0</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-2 text-center tabular-nums font-mono text-xs">
                        {row.reroute_count > 0 ? (
                          <span className="text-yellow-400">{row.reroute_count}</span>
                        ) : (
                          <span className="text-muted-foreground/40">0</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-2 text-center tabular-nums text-xs">
                        {row.registry_risk !== null ? (
                          <span className={row.registry_risk >= 50 ? 'text-red-400' : row.registry_risk >= 25 ? 'text-yellow-400' : 'text-muted-foreground'}>
                            {row.registry_risk}
                          </span>
                        ) : (
                          <span className="text-muted-foreground/30">—</span>
                        )}
                      </td>
                      <td className="py-1.5 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <div className="h-1.5 w-12 rounded-full bg-muted/40 overflow-hidden">
                            <div
                              className={`h-full rounded-full ${riskScoreBar(row.total_score)}`}
                              style={{ width: `${row.total_score}%` }}
                            />
                          </div>
                          <span className="tabular-nums font-mono text-xs font-semibold w-6 text-right">
                            {row.total_score}
                          </span>
                        </div>
                      </td>
                      <td className={`py-1.5 pl-2 text-xs ${label.cls}`}>{label.text}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 31: Chokepoint Traffic Heatmap (multi-line daily trend)
// ---------------------------------------------------------------------------

const CP_COLORS: Record<string, string> = {
  dover_channel: '#60a5fa',
  singapore_malacca: '#34d399',
  cape_good_hope: '#f59e0b',
  suez: '#a78bfa',
  hormuz: '#f87171',
  bosphorus_dardanelles: '#fb923c',
  gibraltar: '#38bdf8',
  malacca: '#4ade80',
}

function cpLabel(cp: string) {
  return cp.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export function ChokepointHeatmapCard() {
  const [days, setDays] = useState(30)
  const [kindFilter, setKindFilter] = useState('')
  const { data, isLoading } = useChokepointHeatmap(days, kindFilter)

  // Pivot cells into [{date, cp1: count, cp2: count, ...}] for recharts
  const chartData = React.useMemo(() => {
    if (!data?.cells.length) return []
    const byDate = new Map<string, Record<string, number | string>>()
    for (const cell of data.cells) {
      if (!byDate.has(cell.date)) byDate.set(cell.date, { date: cell.date })
      byDate.get(cell.date)![cell.chokepoint] = cell.total
    }
    return Array.from(byDate.values()).sort((a, b) => String(a.date) < String(b.date) ? -1 : 1)
  }, [data])

  const chokepoints = data?.chokepoints ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Chokepoint Traffic Trend</span>
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
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={days}
              onChange={e => setDays(Number(e.target.value))}
            >
              <option value={7}>7d</option>
              <option value={14}>14d</option>
              <option value={30}>30d</option>
              <option value={60}>60d</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            Daily vessel transits per chokepoint. Ordered highest to lowest traffic.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No transit data yet.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                tickFormatter={d => d.slice(5)}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                width={32}
              />
              <Tooltip
                contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', fontSize: 12 }}
                formatter={(v, name) => [v, cpLabel(String(name))]}
                labelFormatter={l => `Date: ${l}`}
              />
              <Legend
                formatter={cpLabel}
                wrapperStyle={{ fontSize: 11 }}
              />
              {chokepoints.map(cp => (
                <Line
                  key={cp}
                  type="monotone"
                  dataKey={cp}
                  stroke={CP_COLORS[cp] ?? '#94a3b8'}
                  strokeWidth={1.5}
                  dot={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 32: Trade Lane Risk Matrix
// ---------------------------------------------------------------------------

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

function cellHeatColor(count: number, maxCount: number): string {
  if (maxCount === 0) return 'transparent'
  const pct = count / maxCount
  if (pct > 0.66) return 'rgba(96,165,250,0.35)'
  if (pct > 0.33) return 'rgba(96,165,250,0.18)'
  if (pct > 0.1)  return 'rgba(96,165,250,0.09)'
  return 'transparent'
}

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
              <input
                type="checkbox"
                checked={ladenOnly}
                onChange={e => setLadenOnly(e.target.checked)}
                className="h-3 w-3"
              />
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
                          title={`${origin} → ${dest}: ${cell.vessel_count} vessels${hasRisk ? `, ${cell.high_risk_count} high-risk` : ''}`}
                        >
                          <span className="font-medium">{cell.vessel_count}</span>
                          {hasRisk && (
                            <span className="ml-0.5 text-red-400 text-[9px]">↑{cell.high_risk_count}</span>
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
// Phase 34: Anomaly Watchlist
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

export function AnomalyWatchlistCard() {
  const [minScore, setMinScore] = useState(50)
  const [limit, setLimit] = useState(25)
  const { data, isLoading } = useAnomalyWatchlist(minScore, limit)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Anomaly Watchlist</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={minScore}
              onChange={e => setMinScore(Number(e.target.value))}
            >
              <option value={25}>Score 25+</option>
              <option value={50}>Score 50+</option>
              <option value={75}>Score 75+</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
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
          <p className="py-8 text-center text-sm text-muted-foreground">
            No vessels above threshold.
          </p>
        ) : (
          <div className="space-y-1">
            {rows.map((row) => (
              <div key={row.mmsi} className={`rounded px-2 py-1.5 ${anomalyBg(row.risk_level)}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-sm font-medium truncate">{row.name ?? `MMSI ${row.mmsi}`}</span>
                      {row.ofac && (
                        <span className="rounded bg-red-500/20 px-1 py-px text-[9px] font-bold text-red-400 uppercase">OFAC</span>
                      )}
                      <span className="text-xs text-muted-foreground">{row.segment ?? row.kind}</span>
                      {row.region && (
                        <span className="text-xs text-muted-foreground/70">{row.region.replace(/_/g, ' ')}</span>
                      )}
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
// Phase 35: Live STS Proximity Watch
// ---------------------------------------------------------------------------

export function StsProximityCard() {
  const [maxDistM, setMaxDistM] = useState(2000)
  const [maxSog, setMaxSog] = useState(3.0)
  const { data, isLoading } = useStsProximity(maxDistM, maxSog)
  const pairs = data?.pairs ?? []
  const riskPairs = pairs.filter(p => p.risk_region)
  const normalPairs = pairs.filter(p => !p.risk_region)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Live STS Proximity Watch</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={maxDistM}
              onChange={e => setMaxDistM(Number(e.target.value))}
            >
              <option value={500}>500 m</option>
              <option value={1000}>1 km</option>
              <option value={2000}>2 km</option>
              <option value={5000}>5 km</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={maxSog}
              onChange={e => setMaxSog(Number(e.target.value))}
            >
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
            {riskPairs.length > 0 && (
              <span className="ml-1 font-medium text-orange-400">
                ({riskPairs.length} in high-risk regions)
              </span>
            )}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : pairs.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No vessel pairs within threshold.
          </p>
        ) : (
          <div className="space-y-1">
            {[...riskPairs, ...normalPairs].slice(0, 30).map((pair, i) => (
              <div
                key={i}
                className={`rounded px-2 py-1.5 ${pair.risk_region ? 'border-l-2 border-orange-500/60 bg-orange-500/8' : 'bg-muted/20'}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1 text-sm">
                    <span className="font-medium">{pair.name_a ?? `MMSI ${pair.mmsi_a}`}</span>
                    <span className="mx-1 text-muted-foreground text-xs">
                      {pair.segment_a ?? pair.kind_a} {pair.sog_a != null ? `${pair.sog_a} kn` : ''}
                    </span>
                    <span className="text-muted-foreground/50">+</span>
                    <span className="ml-1 font-medium">{pair.name_b ?? `MMSI ${pair.mmsi_b}`}</span>
                    <span className="mx-1 text-muted-foreground text-xs">
                      {pair.segment_b ?? pair.kind_b} {pair.sog_b != null ? `${pair.sog_b} kn` : ''}
                    </span>
                  </div>
                  <div className="shrink-0 text-right">
                    <span className={`text-xs tabular-nums ${pair.risk_region ? 'text-orange-400' : 'text-muted-foreground'}`}>
                      {pair.dist_m < 1000 ? `${Math.round(pair.dist_m)} m` : `${(pair.dist_m / 1000).toFixed(1)} km`}
                    </span>
                    {pair.region && (
                      <span className="ml-1 text-[10px] text-muted-foreground/60">
                        {pair.region.replace(/_/g, ' ')}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {pairs.length > 30 && (
              <p className="text-center text-xs text-muted-foreground pt-1">
                +{pairs.length - 30} more pairs
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 36: Region Fleet Momentum
// ---------------------------------------------------------------------------

const _MOMENTUM_EXTRA_LABELS: Record<string, string> = {
  panama: 'Panama',
  dover_channel: 'Dover',
  cape_good_hope: 'C. Good Hope',
  bab_el_mandeb: 'Bab el-Mandeb',
  primorsk_baltic: 'Baltic',
  bosphorus_dardanelles: 'Bosphorus',
  us_pacific_nw: 'US Pacific',
  gibson: 'Gibson',
}

function regionLabel(r: string) {
  return { ...REGION_LABELS, ..._MOMENTUM_EXTRA_LABELS }[r] ?? r.replace(/_/g, ' ')
}

export function RegionMomentumCard() {
  const [hoursBack, setHoursBack] = useState(24)
  const { data, isLoading } = useRegionMomentum(hoursBack)
  const rows = data?.rows ?? []

  const chartData = React.useMemo(
    () =>
      rows.slice(0, 12).map(r => ({
        region: regionLabel(r.region),
        delta: r.delta,
        current: r.current_total,
        laden_pct: r.laden_ratio_pct,
        fill: r.delta >= 0 ? '#22c55e' : '#ef4444',
      })),
    [rows],
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Region Fleet Momentum</span>
          <select
            className="rounded border border-border bg-background px-2 py-1 text-xs font-normal"
            value={hoursBack}
            onChange={e => setHoursBack(Number(e.target.value))}
          >
            <option value={12}>vs 12h ago</option>
            <option value={24}>vs 24h ago</option>
            <option value={48}>vs 48h ago</option>
            <option value={72}>vs 72h ago</option>
          </select>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            Net vessel count change per region. Green = fleet building, red = fleet clearing.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No density data available.</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData} layout="vertical" margin={{ left: 80, right: 30 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 10 }}
                  tickFormatter={v => (v >= 0 ? `+${v}` : String(v))}
                  domain={['auto', 'auto']}
                />
                <YAxis type="category" dataKey="region" tick={{ fontSize: 10 }} width={78} />
                <Tooltip
                  formatter={(v, _name) => {
                    const n = Number(v)
                    return [n >= 0 ? `+${n}` : n, `vs ${hoursBack}h ago`]
                  }}
                  labelFormatter={label => String(label)}
                />
                <Bar dataKey="delta" radius={[0, 3, 3, 0]}>
                  {chartData.map((entry, i) => (
                    <Cell key={i} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <div className="mt-2 grid grid-cols-2 gap-1 text-xs text-muted-foreground sm:grid-cols-3">
              {rows.slice(0, 6).map(r => (
                <div key={r.region} className="flex items-center justify-between rounded bg-muted/20 px-2 py-0.5">
                  <span>{regionLabel(r.region)}</span>
                  <span className="tabular-nums">{r.laden_ratio_pct}% laden</span>
                </div>
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 37: AIS Event Rate Timeline
// ---------------------------------------------------------------------------

export function EventRateTimelineCard() {
  const [hours, setHours] = useState(72)
  const { data, isLoading } = useEventRateTimeline(hours)
  const points = data?.points ?? []

  const chartData = React.useMemo(
    () =>
      points.map(p => ({
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
          <select
            className="rounded border border-border bg-background px-2 py-1 text-xs font-normal"
            value={hours}
            onChange={e => setHours(Number(e.target.value))}
          >
            <option value={24}>Last 24h</option>
            <option value={48}>Last 48h</option>
            <option value={72}>Last 72h</option>
            <option value={168}>Last 7d</option>
          </select>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {totalReroutes} reroutes + {totalSts} STS events in the last {data.hours}h.
            Trend indicates route disruption intensity.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No event data in window.</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={chartData} margin={{ left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis
                dataKey="hour"
                tick={{ fontSize: 9 }}
                interval={Math.max(0, Math.floor(chartData.length / 10) - 1)}
                angle={-25}
                textAnchor="end"
                height={40}
              />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
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
// Phase 38: Chokepoint Transit Rate Timeline
// ---------------------------------------------------------------------------

const CP_LINE_COLORS: Record<string, string> = {
  dover_channel: '#3b82f6',
  singapore_malacca: '#22c55e',
  suez: '#f97316',
  hormuz: '#ef4444',
  bosphorus_dardanelles: '#a855f7',
  gibraltar: '#facc15',
  cape_good_hope: '#64748b',
  panama: '#06b6d4',
  bab_el_mandeb: '#f43f5e',
}

const CP_SHORT: Record<string, string> = {
  dover_channel: 'Dover',
  singapore_malacca: 'Sing/Mal',
  suez: 'Suez',
  hormuz: 'Hormuz',
  bosphorus_dardanelles: 'Bosphorus',
  gibraltar: 'Gibraltar',
  cape_good_hope: 'C. Good Hope',
  panama: 'Panama',
  bab_el_mandeb: 'Bab el-Mandeb',
}

export function TransitRateTimelineCard() {
  const [hours, setHours] = useState(72)
  const [selectedCPs, setSelectedCPs] = useState<string[]>(['dover_channel', 'singapore_malacca', 'suez'])
  const { data, isLoading } = useTransitRateTimeline(hours, selectedCPs.join(','))

  const chartData = React.useMemo(() => {
    if (!data?.points.length) return []
    const pts = data.points
    const hours_set: Set<string> = new Set(pts.map(p => p.hour))
    const sorted_hours = [...hours_set].sort()
    return sorted_hours.map(h => {
      const row: Record<string, string | number> = {
        hour: new Date(h).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' }),
      }
      for (const cp of selectedCPs) {
        const pt = pts.find(p => p.hour === h && p.chokepoint === cp)
        row[cp] = pt?.count ?? 0
      }
      return row
    })
  }, [data, selectedCPs])

  const availableCPs = data?.chokepoints ?? Object.keys(CP_SHORT)

  function toggleCP(cp: string) {
    setSelectedCPs(prev => prev.includes(cp) ? prev.filter(c => c !== cp) : [...prev, cp])
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Chokepoint Transit Rate</span>
          <select
            className="rounded border border-border bg-background px-2 py-1 text-xs font-normal"
            value={hours}
            onChange={e => setHours(Number(e.target.value))}
          >
            <option value={24}>Last 24h</option>
            <option value={48}>Last 48h</option>
            <option value={72}>Last 72h</option>
            <option value={168}>Last 7d</option>
          </select>
        </CardTitle>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {availableCPs.map(cp => (
            <button
              key={cp}
              onClick={() => toggleCP(cp)}
              className={`rounded px-2 py-0.5 text-[10px] transition-opacity ${selectedCPs.includes(cp) ? 'opacity-100' : 'opacity-30'}`}
              style={{ backgroundColor: (CP_LINE_COLORS[cp] ?? '#888') + '33', color: CP_LINE_COLORS[cp] ?? '#888', border: `1px solid ${CP_LINE_COLORS[cp] ?? '#888'}55` }}
            >
              {CP_SHORT[cp] ?? cp}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No transit data in window.</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartData} margin={{ left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis
                dataKey="hour"
                tick={{ fontSize: 9 }}
                interval={Math.max(0, Math.floor(chartData.length / 8) - 1)}
                angle={-25}
                textAnchor="end"
                height={40}
              />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 10 }} formatter={cp => CP_SHORT[String(cp)] ?? cp} />
              {selectedCPs.map(cp => (
                <Line
                  key={cp}
                  type="monotone"
                  dataKey={cp}
                  stroke={CP_LINE_COLORS[cp] ?? '#888'}
                  dot={false}
                  strokeWidth={1.5}
                  name={cp}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 39: Anchorage Occupancy Timeline
// ---------------------------------------------------------------------------

const ZONE_COLORS: Record<string, string> = {
  singapore_west: '#22c55e',
  singapore_east: '#4ade80',
  rotterdam: '#3b82f6',
  port_said: '#f97316',
  suez_roads: '#facc15',
  galveston_ltg: '#a855f7',
  richards_bay: '#64748b',
  fujairah: '#ef4444',
}

const ZONE_SHORT: Record<string, string> = {
  singapore_west: 'Sing West',
  singapore_east: 'Sing East',
  rotterdam: 'Rotterdam',
  port_said: 'Port Said',
  suez_roads: 'Suez Roads',
  galveston_ltg: 'Galveston',
  richards_bay: "Richard's Bay",
  fujairah: 'Fujairah',
}

const DEFAULT_ZONES = 'singapore_west,rotterdam,port_said,singapore_east,suez_roads'

export function AnchorageOccupancyCard() {
  const [hours, setHours] = useState(72)
  const [selectedZones, setSelectedZones] = useState<string[]>(['singapore_west', 'rotterdam', 'suez_roads'])
  const { data, isLoading } = useAnchorageOccupancy(hours, selectedZones.join(',') || DEFAULT_ZONES)

  const chartData = React.useMemo(() => {
    if (!data?.points.length) return []
    const hourSet: Set<string> = new Set(data.points.map(p => p.hour))
    const sortedHours = [...hourSet].sort()
    return sortedHours.map(h => {
      const row: Record<string, string | number> = {
        hour: new Date(h).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' }),
      }
      for (const zone of selectedZones) {
        const pt = data.points.find(p => p.hour === h && p.zone === zone)
        row[zone] = pt?.vessel_count ?? 0
      }
      return row
    })
  }, [data, selectedZones])

  const availableZones = data?.zones ?? Object.keys(ZONE_SHORT)

  function toggleZone(z: string) {
    setSelectedZones(prev => prev.includes(z) ? prev.filter(z2 => z2 !== z) : [...prev, z])
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Anchorage Occupancy</span>
          <select
            className="rounded border border-border bg-background px-2 py-1 text-xs font-normal"
            value={hours}
            onChange={e => setHours(Number(e.target.value))}
          >
            <option value={24}>Last 24h</option>
            <option value={48}>Last 48h</option>
            <option value={72}>Last 72h</option>
            <option value={168}>Last 7d</option>
          </select>
        </CardTitle>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {availableZones.map(z => (
            <button
              key={z}
              onClick={() => toggleZone(z)}
              className={`rounded px-2 py-0.5 text-[10px] transition-opacity ${selectedZones.includes(z) ? 'opacity-100' : 'opacity-30'}`}
              style={{ backgroundColor: (ZONE_COLORS[z] ?? '#888') + '33', color: ZONE_COLORS[z] ?? '#888', border: `1px solid ${ZONE_COLORS[z] ?? '#888'}55` }}
            >
              {ZONE_SHORT[z] ?? z.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No anchorage data in window.</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartData} margin={{ left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis
                dataKey="hour"
                tick={{ fontSize: 9 }}
                interval={Math.max(0, Math.floor(chartData.length / 8) - 1)}
                angle={-25}
                textAnchor="end"
                height={40}
              />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 10 }} formatter={z => ZONE_SHORT[String(z)] ?? String(z).replace(/_/g, ' ')} />
              {selectedZones.map(z => (
                <Line
                  key={z}
                  type="monotone"
                  dataKey={z}
                  stroke={ZONE_COLORS[z] ?? '#888'}
                  dot={false}
                  strokeWidth={1.5}
                  name={z}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Phase 40: Serial STS Offenders
// ---------------------------------------------------------------------------

export function StsOffendersCard() {
  const [days, setDays] = useState(30)
  const [limit, setLimit] = useState(30)
  const { data, isLoading } = useStsOffenders(days, limit)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Serial STS Participants</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={days}
              onChange={e => setDays(Number(e.target.value))}
            >
              <option value={7}>Last 7d</option>
              <option value={14}>Last 14d</option>
              <option value={30}>Last 30d</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
              <option value={20}>Top 20</option>
              <option value={30}>Top 30</option>
              <option value={50}>Top 50</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_vessels} vessels appeared in STS events in the last {data.days}d.
            Ranked by total event appearances (both parties combined).
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No STS events in window.</p>
        ) : (
          <div className="space-y-0.5">
            {rows.map((row, i) => (
              <div
                key={row.mmsi}
                className={`flex items-center gap-2 rounded px-2 py-1 text-xs ${row.ofac ? 'bg-red-500/10 border-l-2 border-red-500/50' : 'bg-muted/15 hover:bg-muted/30'}`}
              >
                <span className="w-5 shrink-0 text-muted-foreground/60 tabular-nums">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.ofac && <span className="ml-1 rounded bg-red-500/20 px-1 text-[9px] font-bold text-red-400 uppercase">OFAC</span>}
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  {row.region && <span className="ml-1 text-muted-foreground/60">{row.region.replace(/_/g, ' ')}</span>}
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-semibold text-orange-400">{row.sts_events}</span>
                  <span className="ml-1 text-muted-foreground/60">
                    ({row.as_initiator}+{row.as_counterpart})
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
// Phase 41: Fleet Historical Snapshot (fleet-at-time)
// ---------------------------------------------------------------------------

const SEG_ORDER = [
  'VLCC', 'Suezmax', 'Aframax', 'Panamax', 'MR2', 'MR1', 'Handy',
  'Capesize', 'Panamax', 'Supramax', 'Handysize', 'ULCS', 'VLCS',
  'Large', 'Medium', 'Small',
]

function segSort(a: string, b: string): number {
  const ia = SEG_ORDER.indexOf(a)
  const ib = SEG_ORDER.indexOf(b)
  if (ia === -1 && ib === -1) return a.localeCompare(b)
  if (ia === -1) return 1
  if (ib === -1) return -1
  return ia - ib
}

export function FleetAtTimeCard() {
  const [inputTs, setInputTs] = useState('')
  const [region, setRegion] = useState('')
  const [submittedTs, setSubmittedTs] = useState('')
  const [submittedRegion, setSubmittedRegion] = useState('')
  const { data, isLoading, isFetching } = useFleetAtTime(submittedTs, submittedRegion)

  const handleQuery = () => {
    setSubmittedTs(inputTs)
    setSubmittedRegion(region)
  }

  const tankers = (data?.segments ?? []).filter(r => r.kind === 'tanker').sort((a, b) => segSort(a.segment, b.segment))
  const bulkers = (data?.segments ?? []).filter(r => r.kind === 'bulk').sort((a, b) => segSort(a.segment, b.segment))
  const others  = (data?.segments ?? []).filter(r => r.kind !== 'tanker' && r.kind !== 'bulk').sort((a, b) => a.kind.localeCompare(b.kind) || segSort(a.segment, b.segment))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Fleet Snapshot at Time</CardTitle>
        <p className="text-xs text-muted-foreground">
          Query the vessel composition at any point in the last 30 days.
          Finds the nearest 30-min snapshot window.
        </p>
        <div className="flex flex-wrap gap-2 pt-1">
          <input
            type="datetime-local"
            className="rounded border border-border bg-background px-2 py-1 text-xs"
            value={inputTs}
            onChange={e => setInputTs(e.target.value)}
            placeholder="e.g. 2026-06-10T12:00"
          />
          <select
            className="rounded border border-border bg-background px-2 py-1 text-xs"
            value={region}
            onChange={e => setRegion(e.target.value)}
          >
            <option value="">All regions</option>
            <option value="hormuz">Strait of Hormuz</option>
            <option value="malacca">Malacca</option>
            <option value="suez">Suez</option>
            <option value="bab_el_mandeb">Bab el-Mandeb</option>
            <option value="singapore">Singapore</option>
            <option value="taiwan_strait">Taiwan Strait</option>
            <option value="danish_straits">Danish Straits</option>
            <option value="dover">Dover</option>
            <option value="ara">ARA</option>
            <option value="med">Mediterranean</option>
            <option value="black_sea">Black Sea</option>
          </select>
          <button
            className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground"
            onClick={handleQuery}
            disabled={isFetching}
          >
            {isFetching ? 'Loading...' : 'Query'}
          </button>
          {!submittedTs && !submittedRegion && (
            <span className="text-xs text-muted-foreground self-center">Showing 24h ago by default</span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || isFetching ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : !data ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Select a timestamp and click Query.</p>
        ) : data.total_vessels === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No snapshot data near that timestamp.</p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">Queried: </span>
                <span className="font-medium">{new Date(data.queried_ts).toLocaleString()}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Snapshot: </span>
                <span className="font-medium">{new Date(data.actual_ts).toLocaleString()}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Vessels: </span>
                <span className="font-semibold text-primary">{data.total_vessels.toLocaleString()}</span>
                {data.region && <span className="ml-1 text-muted-foreground">in {data.region.replace(/_/g, ' ')}</span>}
              </div>
            </div>
            {[{ label: 'Tankers', rows: tankers }, { label: 'Bulkers', rows: bulkers }, { label: 'Other', rows: others }]
              .filter(g => g.rows.length > 0)
              .map(group => (
                <div key={group.label}>
                  <div className="mb-1 text-xs font-semibold text-muted-foreground uppercase tracking-wide">{group.label}</div>
                  <div className="grid gap-0.5">
                    {group.rows.map(row => (
                      <div key={`${row.kind}-${row.segment}`} className="flex items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30">
                        <span className="w-28 shrink-0 font-medium">{row.segment}</span>
                        <span className="w-12 shrink-0 tabular-nums text-primary font-semibold">{row.count}</span>
                        <div className="flex min-w-0 gap-2 text-muted-foreground">
                          <span>L: {row.laden}</span>
                          <span>B: {row.ballast}</span>
                          <span>U/W: {row.underway}</span>
                          {row.avg_sog != null && <span>avg {row.avg_sog}kn</span>}
                        </div>
                      </div>
                    ))}
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
// Phase 42: Destination Change Intelligence
// ---------------------------------------------------------------------------

export function DestinationChangesCard() {
  const [hours, setHours] = useState(72)
  const [kind, setKind] = useState('')
  const { data, isLoading } = useDestinationChanges(hours, kind)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Destination Changes (Rerouting)</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={hours}
              onChange={e => setHours(Number(e.target.value))}
            >
              <option value={24}>Last 24h</option>
              <option value={72}>Last 72h</option>
              <option value={168}>Last 7d</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={kind}
              onChange={e => setKind(e.target.value)}
            >
              <option value="">All types</option>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_changes} vessels changed destination in the last {data.hours}h.
            Derived from AIS snapshot history - requires LOCODEs to differ.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No destination changes in window.</p>
        ) : (
          <div className="space-y-0.5 max-h-72 overflow-y-auto pr-1">
            {rows.map(row => (
              <div
                key={row.mmsi}
                className="flex items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30"
              >
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
// Phase 43: Owner Intelligence
// ---------------------------------------------------------------------------

function _ownerRiskColor(score: number | null): string {
  if (score == null) return 'text-muted-foreground'
  if (score >= 50) return 'text-red-400'
  if (score >= 25) return 'text-yellow-400'
  return 'text-green-400'
}

export function OwnerIntelligenceCard() {
  const [minVessels, setMinVessels] = useState(2)
  const [limit, setLimit] = useState(30)
  const { data, isLoading } = useOwnerIntelligence(minVessels, limit)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Owner Fleet Intelligence</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={minVessels}
              onChange={e => setMinVessels(Number(e.target.value))}
            >
              <option value={1}>All owners</option>
              <option value={2}>2+ vessels</option>
              <option value={3}>3+ vessels</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
              <option value={20}>Top 20</option>
              <option value={30}>Top 30</option>
              <option value={50}>Top 50</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_owners} owners in registry. Sorted by risk-weighted fleet size (sum of risk scores).
            Only Equasis-enriched vessels included.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No owner data in registry.</p>
        ) : (
          <div className="space-y-0.5 max-h-80 overflow-y-auto pr-1">
            {rows.map((row, i) => (
              <div key={row.owner} className="flex items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30">
                <span className="w-5 shrink-0 text-muted-foreground/60 tabular-nums">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.owner}</span>
                  {row.top_segment && <span className="ml-1 text-muted-foreground">{row.top_segment}</span>}
                  {row.flags.length > 0 && (
                    <span className="ml-1 text-muted-foreground/60">{row.flags.slice(0, 2).join(', ')}</span>
                  )}
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-medium">{row.vessel_count}v</span>
                  {row.high_risk_count > 0 && (
                    <span className="ml-1 rounded bg-red-500/20 px-1 text-[9px] font-bold text-red-400">
                      {row.high_risk_count} hi-risk
                    </span>
                  )}
                  {row.avg_risk != null && (
                    <span className={`ml-2 font-semibold ${_ownerRiskColor(row.avg_risk)}`}>
                      avg {row.avg_risk}
                    </span>
                  )}
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
// Phase 44: Chokepoint Throughput Anomaly
// ---------------------------------------------------------------------------

const CP_LABEL: Record<string, string> = {
  suez: 'Suez',
  bosphorus_dardanelles: 'Bosphorus',
  strait_of_hormuz: 'Hormuz',
  singapore_malacca: 'Malacca',
  dover_channel: 'Dover',
  gibraltar: 'Gibraltar',
  cape_good_hope: 'Cape',
  lombok: 'Lombok',
  sunda: 'Sunda',
}

function anomalyBadge(direction: string, zScore: number | null, pctChange: number | null) {
  if (direction === 'high') {
    return (
      <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-red-400">
        +{pctChange != null ? Math.round(pctChange) + '%' : 'HIGH'} z={zScore?.toFixed(1)}
      </span>
    )
  }
  if (direction === 'low') {
    return (
      <span className="rounded bg-blue-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-blue-400">
        {pctChange != null ? Math.round(pctChange) + '%' : 'LOW'} z={zScore?.toFixed(1)}
      </span>
    )
  }
  if (direction === 'no_baseline') {
    return <span className="rounded bg-muted/30 px-1.5 py-0.5 text-[10px] text-muted-foreground">new</span>
  }
  return null
}

export function ChokepointAnomalyCard() {
  const [windowHours, setWindowHours] = useState(6)
  const [baselineHours, setBaselineHours] = useState(48)
  const { data, isLoading } = useChokepointAnomaly(windowHours, baselineHours)
  const rows = data?.rows ?? []
  const anomalies = rows.filter(r => r.direction === 'high' || r.direction === 'low')

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Chokepoint Traffic Anomaly</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={windowHours}
              onChange={e => setWindowHours(Number(e.target.value))}
            >
              <option value={3}>3h window</option>
              <option value={6}>6h window</option>
              <option value={12}>12h window</option>
            </select>
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={baselineHours}
              onChange={e => setBaselineHours(Number(e.target.value))}
            >
              <option value={24}>24h baseline</option>
              <option value={48}>48h baseline</option>
              <option value={168}>7d baseline</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            Last {data.window_hours}h vs {data.baseline_hours}h baseline (Z {'≥'} 2 = anomaly).
            {anomalies.length > 0
              ? ` ${anomalies.length} chokepoint${anomalies.length > 1 ? 's' : ''} outside normal range.`
              : ' All chokepoints within normal range.'}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-32 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No transit data available.</p>
        ) : (
          <div className="space-y-1.5">
            {rows.map(row => {
              const pct = row.baseline_avg != null && row.baseline_avg > 0
                ? row.recent_count / row.baseline_avg
                : null
              const barWidth = pct != null ? Math.min(100, Math.round(pct * 50)) : 0
              return (
                <div key={row.chokepoint} className="grid grid-cols-[100px_1fr_auto] items-center gap-2">
                  <span className="text-xs font-medium">{CP_LABEL[row.chokepoint] ?? row.chokepoint.replace(/_/g, ' ')}</span>
                  <div className="relative h-3 overflow-hidden rounded-full bg-muted/30">
                    <div
                      className={`h-full rounded-full transition-all ${row.direction === 'high' ? 'bg-red-500/60' : row.direction === 'low' ? 'bg-blue-500/40' : 'bg-primary/50'}`}
                      style={{ width: `${barWidth}%` }}
                    />
                    {row.baseline_avg != null && (
                      <div className="absolute inset-y-0 left-1/2 w-px bg-muted-foreground/40" title="baseline avg" />
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5 text-right text-xs tabular-nums">
                    <span className="font-semibold">{row.recent_count}</span>
                    {row.baseline_avg != null && (
                      <span className="text-muted-foreground">/ {row.baseline_avg.toFixed(1)} avg</span>
                    )}
                    {anomalyBadge(row.direction, row.z_score, row.pct_change)}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
