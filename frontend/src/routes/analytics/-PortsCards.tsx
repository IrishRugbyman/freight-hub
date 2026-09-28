// Port and anchorage cards: arrivals forecast, flows, congestion, occupancy, density.
import React, { useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis,
  YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  usePortArrivals, usePortFlow, usePortCongestion, useChokepointCongestion, useAnchorageOccupancy,
  useDensity, useArrivals,
} from '@/lib/api'
import {
  fmt, EmptyState, ChartSkeleton, TOOLTIP_STYLE, LEGEND_STYLE, useGoToTracker,
} from './-analyticsShared'

function congestionBadge(factor: number): string {
  if (factor >= 2.0) return 'CRITICAL'
  if (factor >= 1.3) return 'ELEVATED'
  if (factor >= 0.7) return 'NORMAL'
  return 'LOW'
}

function congestionColor(factor: number): string {
  if (factor >= 2.0) return 'text-red-400'
  if (factor >= 1.3) return 'text-orange-400'
  if (factor >= 0.7) return 'text-yellow-400'
  return 'text-green-400'
}

const LADEN_COLOR: Record<string, string> = {
  laden: 'text-blue-400',
  ballast: 'text-muted-foreground',
  unknown: 'text-muted-foreground/60',
}

const DEFAULT_ZONES = 'singapore_west,rotterdam,port_said,singapore_east,suez_roads'

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

// ---------------------------------------------------------------------------
// Local constants (Ports & Cargo tab only)
// ---------------------------------------------------------------------------
const DENSITY_REGIONS = ['singapore_malacca', 'suez', 'dover_channel', 'panama', 'ara', 'japan_korea', 'us_gulf', 'cape_good_hope']

// ---------------------------------------------------------------------------
// PortArrivalForecastCard
// ---------------------------------------------------------------------------
export function PortArrivalForecastCard() {
  const [kind, setKind] = useState<string>('tanker')
  const [horizonH, setHorizonH] = useState<number>(48)
  const [expandedPort, setExpandedPort] = useState<string | null>(null)
  const { data, isLoading } = usePortArrivals(kind, horizonH)
  const ports = data?.ports ?? []
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>Port Arrival Forecast</span>
          <div className="flex gap-2">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
              <option value="">All types</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={horizonH} onChange={e => setHorizonH(Number(e.target.value))}>
              <option value={24}>24h</option>
              <option value={48}>48h</option>
              <option value={72}>72h</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_inbound} vessels inbound to {ports.length} ports within {horizonH}h.
            ETA computed from live position + SOG + great-circle distance.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : ports.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No inbound vessels matched.</p>
        ) : (
          <div className="space-y-1">
            {ports.map((port) => (
              <div key={port.port} className="rounded border border-border/50 overflow-hidden">
                <button
                  className="w-full flex items-center gap-3 px-3 py-2 text-left text-xs hover:bg-muted/30"
                  onClick={() => setExpandedPort(expandedPort === port.port ? null : port.port)}
                >
                  <span className="font-semibold flex-1">{port.port}</span>
                  <span className="text-muted-foreground">
                    <span className="font-bold text-foreground">{port.arrivals_24h}</span> in 24h
                    {' / '}
                    <span className="font-bold text-foreground">{port.arrivals_48h}</span> in {horizonH}h
                  </span>
                  <span className="text-muted-foreground/60">{expandedPort === port.port ? '▲' : '▼'}</span>
                </button>
                {expandedPort === port.port && (
                  <div className="border-t border-border/30 divide-y divide-border/20">
                    {port.vessels.map((v) => (
                      <div key={v.mmsi} className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted/20" onClick={() => goToTracker(v.mmsi)}>
                        <div className="min-w-0 flex-1">
                          <span className="font-medium">{v.name ?? `MMSI ${v.mmsi}`}</span>
                          {v.segment && <span className="ml-1 text-muted-foreground">{v.segment}</span>}
                          {v.laden && (
                            <span className={`ml-1 font-medium ${LADEN_COLOR[v.laden] ?? 'text-muted-foreground'}`}>{v.laden}</span>
                          )}
                          {v.registry_risk != null && (
                            <span className={`ml-1 rounded px-1 text-[10px] ${v.registry_risk >= 70 ? 'bg-red-500/20 text-red-400' : v.registry_risk >= 40 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                              risk {v.registry_risk}
                            </span>
                          )}
                        </div>
                        <div className="shrink-0 text-right tabular-nums">
                          <span className={`font-bold ${v.eta_hours <= 6 ? 'text-orange-400' : v.eta_hours <= 24 ? 'text-yellow-400' : 'text-muted-foreground'}`}>
                            ETA {v.eta_hours < 1 ? `${Math.round(v.eta_hours * 60)}m` : `${v.eta_hours.toFixed(1)}h`}
                          </span>
                          <span className="ml-1.5 text-muted-foreground">{v.distance_nm.toFixed(0)} nm @ {v.sog}kn</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// PortFlowCard
// ---------------------------------------------------------------------------
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
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${kind === k ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
            >
              {k ?? 'All'}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <ChartSkeleton />
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
                      <div className="h-full rounded-full bg-primary/60" style={{ width: `${pct}%` }} />
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

// ---------------------------------------------------------------------------
// ActualArrivalsCard - ground-truth arrivals mined from AIS closest-approach,
// the honest counterpart to the stated-destination distribution above.
// ---------------------------------------------------------------------------
function prettyTargetName(name: string): string {
  return name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function ActualArrivalsCard() {
  const [targetType, setTargetType] = useState<'all' | 'port' | 'chokepoint'>('all')
  const [days, setDays] = useState(14)
  const { data, isLoading } = useArrivals(days, targetType, 15)
  const rows = data?.rows ?? []
  const maxArrivals = rows.length > 0 ? rows[0].arrivals : 0

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
        <div>
          <CardTitle className="text-sm font-medium">Actual Arrivals (Ground Truth)</CardTitle>
          <div className="mt-0.5 text-[10px] text-muted-foreground/70">
            Where vessels actually arrived, mined from AIS, not the self-reported destination
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <div className="flex gap-1">
            {(['all', 'port', 'chokepoint'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTargetType(t)}
                className={`rounded px-2 py-0.5 text-[10px] font-medium capitalize transition-colors ${targetType === t ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {t === 'all' ? 'All' : t === 'port' ? 'Ports' : 'Chokepoints'}
              </button>
            ))}
          </div>
          <div className="flex gap-1">
            {([7, 14, 30] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${days === d ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <ChartSkeleton />
        ) : rows.length === 0 ? (
          <EmptyState message="No arrivals mined yet for this window." />
        ) : (
          <>
            <div className="mb-2 text-[10px] text-muted-foreground">
              {data.total_arrivals.toLocaleString()} arrivals from {data.total_vessels.toLocaleString()} distinct vessels over {data.window_days}d
            </div>
            <div className="space-y-1">
              {rows.map((r) => {
                const pct = maxArrivals > 0 ? (r.arrivals / maxArrivals) * 100 : 0
                const ladenPct = r.laden_share != null ? Math.round(r.laden_share * 100) : null
                return (
                  <div key={r.target_id} className="flex items-center gap-2 text-xs">
                    <div className="flex w-32 shrink-0 items-center gap-1 truncate">
                      <span
                        className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${r.target_type === 'chokepoint' ? 'bg-amber-400' : 'bg-sky-400'}`}
                        title={r.target_type}
                      />
                      <span className="truncate font-medium">{prettyTargetName(r.name)}</span>
                    </div>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                      <div
                        className={`h-full rounded-full ${r.target_type === 'chokepoint' ? 'bg-amber-400/60' : 'bg-sky-400/60'}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">{r.arrivals.toLocaleString()}</div>
                    <div className="w-12 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground/60" title="distinct vessels">
                      {r.vessels.toLocaleString()}v
                    </div>
                    <div className="w-12 shrink-0 text-right text-[10px] tabular-nums" title="laden share">
                      {ladenPct != null ? (
                        <span className={ladenPct >= 70 ? 'text-emerald-400' : 'text-muted-foreground/60'}>{ladenPct}%L</span>
                      ) : (
                        <span className="text-muted-foreground/30">-</span>
                      )}
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

// ---------------------------------------------------------------------------
// PortCongestionCard
// ---------------------------------------------------------------------------
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
        {isLoading && <ChartSkeleton className="h-24" />}
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
// ChokepointCongestionCard
// ---------------------------------------------------------------------------
export function ChokepointCongestionCard() {
  const [kindFilter, setKindFilter] = React.useState<'' | 'tanker' | 'bulk'>('')
  const [days, setDays] = React.useState(14)
  const { data, isLoading } = useChokepointCongestion(kindFilter, days)
  const rows = (data?.rows ?? []).filter(r => r.current_vessels > 0 || (r.baseline_avg_vessels ?? 0) > 0)

  return (
    <Card className="bg-card/60 backdrop-blur border-border/40">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-sm font-medium">Chokepoint Congestion Monitor</CardTitle>
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
          Vessels currently transiting vs {days}d baseline - congestion factor = current / avg concurrent
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading && <ChartSkeleton className="h-24" />}
        {!isLoading && rows.length === 0 && (
          <p className="text-xs text-muted-foreground">No transit episodes in selected window.</p>
        )}
        {rows.length > 0 && (
          <div className="overflow-auto max-h-[400px]">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border/40 text-muted-foreground">
                  <th className="text-left py-1 pr-3 font-medium">Chokepoint</th>
                  <th className="text-right py-1 pr-3 font-medium">Now</th>
                  <th className="text-right py-1 pr-3 font-medium">Dwell</th>
                  <th className="text-right py-1 pr-3 font-medium">Baseline</th>
                  <th className="text-right py-1 pr-3 font-medium">Factor</th>
                  <th className="text-right py-1 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.chokepoint} className="border-b border-border/20 hover:bg-muted/20">
                    <td className="py-1.5 pr-3 font-medium text-foreground/90">
                      {row.chokepoint.replace(/_/g, ' ')}
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
// AnchorageOccupancyCard
// ---------------------------------------------------------------------------
export function AnchorageOccupancyCard() {
  const [hours, setHours] = useState(72)
  const [selectedZones, setSelectedZones] = useState<string[]>(['singapore_west', 'rotterdam', 'suez_roads'])
  const { data, isLoading } = useAnchorageOccupancy(hours, selectedZones.join(',') || DEFAULT_ZONES)

  const chartData = React.useMemo(() => {
    if (!data?.points.length) return []
    const hourSet: Set<string> = new Set(data.points.map(p => p.hour))
    // hour is an ISO timestamp, so lexicographic order is chronological order.
    const sortedHours = [...hourSet].sort((a, b) => a.localeCompare(b))
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
// DensityCard
// ---------------------------------------------------------------------------
export function DensityCard() {
  const [region, setRegion] = useState('singapore_malacca')
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
  const chartData = Object.entries(byDate)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, v]) => ({ date, ...v }))

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
        {isLoading || !data ? <ChartSkeleton /> : chartData.length === 0 ? (
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
