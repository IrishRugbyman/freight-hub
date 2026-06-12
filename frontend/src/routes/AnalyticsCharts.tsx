import React, { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
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
import { useCongestion, useDensity, useLaden, useTransits, usePortFlow, useOwnerRisk, useFleetSpeed, useRegionUtil, useFlagRisk, useSpeedTrend, useStsRisk, useReroutes, useTransitRisk, useFleetAge, useAnchorageDwell, useCargoTransitions, useSlowSteamers, useFleetUtilization, useRiskEvents } from '@/lib/api'
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
