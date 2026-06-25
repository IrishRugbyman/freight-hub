import React, { useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis, Legend,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useChokepointStatus, useChokepointAnomaly, useChokepointHeatmap,
  useTransitRateTimeline, useTransits, useCongestion,
  type ChokepointStatusRow,
} from '@/lib/api'
import { fmt, EmptyState, ChartSkeleton, TOOLTIP_STYLE, LEGEND_STYLE } from './-analyticsShared'

// ---------------------------------------------------------------------------
// Local constants (Chokepoints tab only)
// ---------------------------------------------------------------------------
const CHOKEPOINTS = [
  'singapore_malacca', 'suez', 'hormuz', 'panama', 'gibraltar',
  'bosphorus_dardanelles', 'dover_channel', 'cape_good_hope', 'bab_el_mandeb',
]

const ANCHORAGE_ZONES = [
  'singapore_east', 'singapore_west', 'fujairah', 'suez_roads', 'port_said',
  'rotterdam', 'galveston_ltg', 'arab_gulf_north', 'qingdao', 'port_hedland',
  'richards_bay', 'santos', 'tubarao', 'bab_djibouti',
]

const DIRECTION_COLORS: Record<string, string> = {
  northbound: '#22c55e', southbound: '#f97316',
  eastbound: '#3b82f6', westbound: '#a855f7',
  outbound: '#22c55e', inbound_gulf: '#f97316',
}

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

function cpLabel(cp: string) {
  return cp.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
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

// ---------------------------------------------------------------------------
// ChokepointStatusCard (Phase 49 - new)
// ---------------------------------------------------------------------------
function statusRow(row: ChokepointStatusRow) {
  const transitPct = row.live_total > 0 ? Math.round((row.live_transiting / row.live_total) * 100) : 0
  return (
    <div key={row.chokepoint} className="rounded border border-border/30 bg-muted/10 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{fmt(row.chokepoint)}</span>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-green-400 font-mono">{row.live_transiting} transit</span>
          <span className="text-yellow-400 font-mono">{row.live_waiting} wait</span>
          <span className="text-muted-foreground">/ {row.live_total} live</span>
        </div>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full bg-muted/40 overflow-hidden">
          <div className="h-full rounded-full bg-green-500/60" style={{ width: `${transitPct}%` }} />
        </div>
        <div className="flex gap-3 text-[10px] text-muted-foreground shrink-0">
          {row.avg_transit_h_7d != null && (
            <span>avg {row.avg_transit_h_7d.toFixed(1)}h transit (7d)</span>
          )}
          <span>{row.n_transits_24h} / {row.n_transits_7d} transits (24h/7d)</span>
          {row.pct_fwd_direction != null && (
            <span>{row.pct_fwd_direction.toFixed(0)}% fwd</span>
          )}
        </div>
      </div>
    </div>
  )
}

export function ChokepointStatusCard() {
  const { data, isLoading } = useChokepointStatus()
  const rows = data?.rows ?? []

  return (
    <Card className="bg-card/60 backdrop-blur border-border/40">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Chokepoint Live Status</CardTitle>
        <p className="text-xs text-muted-foreground mt-0.5">
          Live vessel counts per chokepoint: transiting (SOG &gt; 4 kn) vs waiting (SOG {'<='} 0.5 kn).
          {data && ` Updated ${new Date(data.as_of).toLocaleTimeString()}.`}
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && <EmptyState message="No chokepoint data available." />}
        {!isLoading && rows.length > 0 && (
          <div className="space-y-2">
            {rows.map(row => statusRow(row))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// ChokepointAnomalyCard
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// ChokepointHeatmapCard
// ---------------------------------------------------------------------------
export function ChokepointHeatmapCard() {
  const [days, setDays] = useState(30)
  const [kindFilter, setKindFilter] = useState('')
  const { data, isLoading } = useChokepointHeatmap(days, kindFilter)

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
          <p className="py-8 text-center text-sm text-muted-foreground">No transit data yet.</p>
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
              <YAxis tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }} width={32} />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(v, name) => [v, cpLabel(String(name))]}
                labelFormatter={l => `Date: ${l}`}
              />
              <Legend formatter={cpLabel} wrapperStyle={{ fontSize: 11 }} />
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
// TransitRateTimelineCard
// ---------------------------------------------------------------------------
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
// TransitsCard
// ---------------------------------------------------------------------------
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
        {isLoading || !data ? <ChartSkeleton /> : chartData.length === 0 ? (
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

// ---------------------------------------------------------------------------
// CongestionCard (anchorage congestion timeseries)
// ---------------------------------------------------------------------------
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
        {isLoading || !data ? <ChartSkeleton /> : chartData.length === 0 ? (
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

// ---------------------------------------------------------------------------
// Default export: Chokepoints tab component
// ---------------------------------------------------------------------------
export default function ChokepointsTab() {
  return (
    <div className="space-y-6">
      <ChokepointStatusCard />
      <ChokepointAnomalyCard />
      <ChokepointHeatmapCard />
      <TransitRateTimelineCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <TransitsCard />
        <CongestionCard />
      </div>
    </div>
  )
}
