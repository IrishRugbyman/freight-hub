import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useCongestion, useDensity, useLaden, useTransits, usePortFlow, useOwnerRisk, useFleetSpeed, useRegionUtil, useFlagRisk, useSpeedTrend } from '@/lib/api'

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
