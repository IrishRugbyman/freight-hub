import React, { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  usePortArrivals, usePortFlow, usePortCongestion, useAnchorageOccupancy,
  useTradeLaneMatrix, useDestinationFlows, useCargoTransitions,
  useCargoStateChanges, useLaden, useDensity, useEuropeanInbound,
  useLngInbound, type EuropeanInboundVessel, type LngVessel,
} from '@/lib/api'
import { fmt, EmptyState, TOOLTIP_STYLE, LEGEND_STYLE, REGION_LABELS } from './-analyticsShared'

function useGoToTracker() {
  const navigate = useNavigate()
  return (mmsi: number, lat?: number | null, lon?: number | null) => {
    const search: Record<string, unknown> = { mmsi }
    if (lat != null && lon != null) { search.lat = lat; search.lon = lon }
    navigate({ to: '/', search: search as never })
  }
}

// ---------------------------------------------------------------------------
// Local constants (Ports & Cargo tab only)
// ---------------------------------------------------------------------------
const DENSITY_REGIONS = ['hormuz', 'singapore_malacca', 'suez', 'panama', 'dover_channel']

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

const CARGO_SEGMENTS = [
  { value: '', label: 'All' },
  { value: 'VLCC', label: 'VLCC' },
  { value: 'Suezmax', label: 'Suezmax' },
  { value: 'Aframax', label: 'Aframax' },
  { value: 'Panamax', label: 'Panamax' },
  { value: 'MR', label: 'MR Tanker' },
  { value: 'Capesize', label: 'Capesize' },
]

const LADEN_COLOR: Record<string, string> = {
  laden: 'text-blue-400',
  ballast: 'text-muted-foreground',
  unknown: 'text-muted-foreground/60',
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

function riskBadge(score: number | null, ofac: boolean) {
  if (ofac) return <span className="rounded bg-red-500/20 px-1 text-[10px] font-semibold text-red-400">OFAC</span>
  if (score == null) return null
  if (score >= 75) return <span className="rounded bg-red-400/20 px-1 text-[10px] font-semibold text-red-400">{score}</span>
  if (score >= 50) return <span className="rounded bg-orange-400/20 px-1 text-[10px] font-semibold text-orange-400">{score}</span>
  if (score >= 25) return <span className="rounded bg-yellow-400/20 px-1 text-[10px] font-semibold text-yellow-400">{score}</span>
  return <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{score}</span>
}

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

function cellHeatColor(count: number, maxCount: number): string {
  if (maxCount === 0) return 'transparent'
  const pct = count / maxCount
  if (pct > 0.66) return 'rgba(96,165,250,0.35)'
  if (pct > 0.33) return 'rgba(96,165,250,0.18)'
  if (pct > 0.1)  return 'rgba(96,165,250,0.09)'
  return 'transparent'
}

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
// AnchorageOccupancyCard
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// DensityCard
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// European Supply Intelligence card (Phase 54)
// ---------------------------------------------------------------------------

const ORIGIN_COLORS: Record<string, string> = {
  'Middle East':        'bg-amber-500/20 text-amber-300 border-amber-500/30',
  'Black Sea':          'bg-purple-500/20 text-purple-300 border-purple-500/30',
  'West Africa':        'bg-green-500/20 text-green-300 border-green-500/30',
  'Americas':           'bg-blue-500/20 text-blue-300 border-blue-500/30',
  'Atlantic / Americas':'bg-blue-500/20 text-blue-300 border-blue-500/30',
  'Asia Pacific':       'bg-teal-500/20 text-teal-300 border-teal-500/30',
  'East / Long-haul':   'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
}
const ORIGIN_DEFAULT = 'bg-muted text-muted-foreground border-border'

function OriginBadge({ origin }: { origin: string | null }) {
  if (!origin) return null
  const cls = ORIGIN_COLORS[origin] ?? ORIGIN_DEFAULT
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${cls}`}>
      {origin}
    </span>
  )
}

const LADEN_COLORS: Record<string, string> = {
  laden:   'text-emerald-400',
  ballast: 'text-muted-foreground',
  unknown: 'text-muted-foreground/50',
}

const ETA_BUCKET_ORDER = ['0-6h', '6-12h', '12-24h', '24-48h']

function EtaBucketGroup({
  bucket,
  vessels,
  onSelect,
}: {
  bucket: string
  vessels: EuropeanInboundVessel[]
  onSelect: (mmsi: number) => void
}) {
  if (vessels.length === 0) return null
  return (
    <div>
      <div className="sticky top-0 bg-background/90 px-0 py-1 text-[10px] font-semibold text-muted-foreground/60 uppercase tracking-widest">
        {bucket}
        <span className="ml-1.5 rounded bg-muted px-1 py-px text-[9px] font-normal">
          {vessels.length}
        </span>
      </div>
      <div className="space-y-px">
        {vessels.map(v => (
          <button
            key={v.mmsi}
            onClick={() => onSelect(v.mmsi)}
            className="flex w-full items-center gap-2 rounded px-1.5 py-1.5 text-left hover:bg-muted/30 transition-colors"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 truncate">
                <span className="truncate text-xs font-medium">{v.name ?? `MMSI ${v.mmsi}`}</span>
                <span className="shrink-0 rounded bg-muted px-1 py-px text-[9px] text-muted-foreground">
                  {v.segment}
                </span>
                {v.inferred_origin && <OriginBadge origin={v.inferred_origin} />}
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
                <span className="font-medium text-foreground/70">{v.port}</span>
                <span className="text-muted-foreground/40">|</span>
                <span className={LADEN_COLORS[v.laden ?? 'unknown'] ?? 'text-muted-foreground'}>
                  {v.laden ?? 'unknown'}
                </span>
                {v.dwt_estimate != null && v.laden === 'laden' && (
                  <>
                    <span className="text-muted-foreground/40">|</span>
                    <span>{(v.dwt_estimate / 1000).toFixed(0)}k DWT</span>
                  </>
                )}
                {v.inferred_via && (
                  <>
                    <span className="text-muted-foreground/40">|</span>
                    <span className="text-muted-foreground/60">via {v.inferred_via}</span>
                  </>
                )}
              </div>
            </div>
            <div className="shrink-0 text-right text-[10px] tabular-nums">
              <span className="font-semibold text-foreground/80">{v.eta_hours.toFixed(1)}h</span>
              <div className="text-muted-foreground/50">{v.sog.toFixed(1)} kn</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function OriginBreakdown({ byOrigin }: { byOrigin: Record<string, number> }) {
  const total = Object.values(byOrigin).reduce((a, b) => a + b, 0)
  if (total === 0) return null
  const entries = Object.entries(byOrigin).filter(([, n]) => n > 0)
  return (
    <div className="flex flex-col gap-0.5">
      {entries.map(([origin, count]) => {
        const pct = Math.round((count / total) * 100)
        const cls = ORIGIN_COLORS[origin] ? ORIGIN_COLORS[origin].split(' ')[1] : 'text-muted-foreground'
        return (
          <div key={origin} className="flex items-center gap-2 text-[10px]">
            <div className="flex-1 min-w-0">
              <div className="flex justify-between mb-0.5">
                <span className="truncate text-muted-foreground">{origin}</span>
                <span className={`font-mono ${cls}`}>{count}</span>
              </div>
              <div className="h-1 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-current"
                  style={{ width: `${pct}%`, color: ORIGIN_COLORS[origin]?.split(' ')[1]?.replace('text-', '') ?? '#6b7280' }}
                />
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function EuropeanInboundCard() {
  const [horizonH, setHorizonH] = useState(48)
  const [ladenOnly, setLadenOnly] = useState(false)
  const { data, isLoading } = useEuropeanInbound(horizonH, ladenOnly)
  const navigate = useNavigate()

  function goToTracker(mmsi: number) {
    const v = data?.vessels.find(x => x.mmsi === mmsi)
    const search: Record<string, unknown> = { mmsi }
    if (v?.eta_hours != null) {
      // Not on the map yet (approaching), just navigate to tracker with mmsi pre-selected
    }
    navigate({ to: '/', search: search as never })
  }

  // Group vessels by ETA bucket
  const bucketMap: Record<string, EuropeanInboundVessel[]> = {}
  for (const v of data?.vessels ?? []) {
    const b = v.eta_hours <= 6 ? '0-6h' : v.eta_hours <= 12 ? '6-12h' : v.eta_hours <= 24 ? '12-24h' : '24-48h'
    if (!bucketMap[b]) bucketMap[b] = []
    bucketMap[b].push(v)
  }

  const totalDwtK = data ? Math.round(data.total_dwt_laden / 1000) : 0
  const knownOriginCount = data
    ? Object.entries(data.by_origin).filter(([k]) => k !== 'Unknown').reduce((a, [, v]) => a + v, 0)
    : 0

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm">European Supply Intelligence</CardTitle>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Inbound vessel arrivals at European import terminals with cargo origin inference from transit history.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setLadenOnly(v => !v)}
              className={`rounded border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                ladenOnly
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400'
                  : 'border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              Laden only
            </button>
            <div className="flex rounded border border-border overflow-hidden text-[10px]">
              {([24, 48, 72] as const).map(h => (
                <button
                  key={h}
                  onClick={() => setHorizonH(h)}
                  className={`px-2 py-1 transition-colors ${
                    horizonH === h ? 'bg-primary/20 text-primary font-medium' : 'text-muted-foreground hover:bg-muted/40'
                  }`}
                >
                  {h}h
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* KPI bar */}
        {data && (
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-border/40 pt-2">
            <div className="text-xs">
              <span className="font-semibold tabular-nums text-foreground">{data.total_vessels}</span>
              <span className="ml-1 text-muted-foreground">inbound</span>
            </div>
            <div className="text-xs">
              <span className="font-semibold tabular-nums text-emerald-400">{data.total_laden}</span>
              <span className="ml-1 text-muted-foreground">laden</span>
            </div>
            <div className="text-xs">
              <span className="font-semibold tabular-nums text-foreground">{totalDwtK.toLocaleString()}k</span>
              <span className="ml-1 text-muted-foreground">DWT laden</span>
            </div>
            {knownOriginCount > 0 && (
              <div className="text-xs">
                <span className="font-semibold tabular-nums text-foreground">{knownOriginCount}</span>
                <span className="ml-1 text-muted-foreground">origins traced</span>
              </div>
            )}
          </div>
        )}
      </CardHeader>

      <CardContent className="pb-3">
        {isLoading && <EmptyState message="Loading..." />}
        {!isLoading && (!data || data.total_vessels === 0) && (
          <EmptyState message="No inbound vessels in this window." />
        )}
        {data && data.total_vessels > 0 && (
          <div className="flex gap-4">
            {/* Vessel timeline */}
            <div className="min-w-0 flex-1 max-h-[480px] overflow-y-auto space-y-3 pr-1">
              {ETA_BUCKET_ORDER.map(bucket => (
                <EtaBucketGroup
                  key={bucket}
                  bucket={bucket}
                  vessels={bucketMap[bucket] ?? []}
                  onSelect={goToTracker}
                />
              ))}
            </div>

            {/* Right sidebar: origin breakdown + top ports */}
            <div className="w-36 shrink-0 space-y-4">
              <div>
                <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Origin
                </div>
                <OriginBreakdown byOrigin={data.by_origin} />
              </div>
              <div>
                <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Port
                </div>
                <div className="space-y-0.5">
                  {Object.entries(data.by_port).slice(0, 8).map(([port, count]) => (
                    <div key={port} className="flex items-center justify-between text-[10px]">
                      <span className="truncate text-muted-foreground">{port}</span>
                      <span className="ml-1 font-mono tabular-nums">{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// LNG Intelligence Card (Phase 55)
// ---------------------------------------------------------------------------
const LNG_ORIGIN_COLORS: Record<string, string> = {
  'Qatar / ME':      '#f59e0b',   // amber
  'US Gulf LNG':     '#3b82f6',   // blue
  'Atlantic LNG':    '#10b981',   // green
  'Asia Pacific LNG':'#8b5cf6',   // purple
  'Norway / Russia LNG': '#6b7280', // gray
}

const COUNTRY_FLAG: Record<string, string> = {
  Netherlands: 'NL', Belgium: 'BE', France: 'FR', UK: 'GB',
  Spain: 'ES', Italy: 'IT', Poland: 'PL', Greece: 'GR',
  Croatia: 'HR', Lithuania: 'LT', Sweden: 'SE', Finland: 'FI',
}

function LngOriginDot({ origin }: { origin: string | null }) {
  const color = origin ? (LNG_ORIGIN_COLORS[origin] ?? '#6b7280') : '#6b7280'
  return (
    <span
      className="inline-block h-2 w-2 flex-shrink-0 rounded-full"
      style={{ backgroundColor: color }}
    />
  )
}

function LngOriginBar({ byOrigin }: { byOrigin: Record<string, number> }) {
  const total = Object.values(byOrigin).reduce((a, b) => a + b, 0)
  if (total === 0) return null
  const entries = Object.entries(byOrigin).sort((a, b) => b[1] - a[1])
  return (
    <div className="space-y-1">
      {entries.map(([origin, count]) => (
        <div key={origin} className="flex items-center gap-2 text-xs">
          <LngOriginDot origin={origin} />
          <span className="w-36 truncate text-muted-foreground">{origin}</span>
          <div className="flex-1 overflow-hidden rounded-full bg-muted h-1.5">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${(count / total) * 100}%`,
                backgroundColor: LNG_ORIGIN_COLORS[origin] ?? '#6b7280',
              }}
            />
          </div>
          <span className="w-4 text-right font-mono text-foreground">{count}</span>
        </div>
      ))}
    </div>
  )
}

function LngVesselRow({ v, onNavigate }: { v: LngVessel; onNavigate: (mmsi: number, lat: number, lon: number) => void }) {
  const etaLabel = v.eta_hours != null
    ? v.eta_hours < 1 ? '<1h' : `${Math.round(v.eta_hours)}h`
    : '-'

  return (
    <button
      type="button"
      className="flex w-full items-center gap-2 rounded px-1 py-1 text-left text-xs hover:bg-muted/60 transition-colors"
      onClick={() => onNavigate(v.mmsi, v.lat, v.lon)}
    >
      <LngOriginDot origin={v.inferred_origin} />
      <span className="w-36 truncate font-medium text-foreground">{v.name || `MMSI ${v.mmsi}`}</span>
      <span className="flex-1 truncate text-muted-foreground">{v.terminal ?? v.region ?? '-'}</span>
      {v.terminal_country && (
        <span className="text-[10px] text-muted-foreground/70">{COUNTRY_FLAG[v.terminal_country] ?? v.terminal_country}</span>
      )}
      <span className="w-8 text-right tabular-nums text-foreground/80">{etaLabel}</span>
    </button>
  )
}

export function LngIntelligenceCard() {
  const [horizonH, setHorizonH] = useState(72)
  const { data, isLoading } = useLngInbound(horizonH)
  const goToTracker = useGoToTracker()

  if (isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">LNG Intelligence</CardTitle></CardHeader>
        <CardContent><div className="h-48 animate-pulse rounded bg-muted/40" /></CardContent>
      </Card>
    )
  }

  const inboundVessels = (data?.vessels ?? []).filter(v => v.terminal != null)
  const otherVessels = (data?.vessels ?? []).filter(v => v.terminal == null)

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">LNG Intelligence</CardTitle>
            <p className="mt-0.5 text-xs text-muted-foreground">
              LNG carriers visible via AIS - European regas terminal ETAs and origin inference
            </p>
          </div>
          <select
            value={horizonH}
            onChange={e => setHorizonH(Number(e.target.value))}
            className="rounded border border-border bg-background px-2 py-1 text-xs"
          >
            {[48, 72, 120].map(h => (
              <option key={h} value={h}>{h}h window</option>
            ))}
          </select>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* KPI bar */}
        <div className="grid grid-cols-3 gap-3 rounded-lg bg-muted/30 px-4 py-3 text-center">
          <div>
            <div className="text-xl font-bold tabular-nums text-foreground">
              {data?.total_lng_visible ?? '-'}
            </div>
            <div className="text-[10px] text-muted-foreground">LNG in AIS</div>
          </div>
          <div>
            <div className="text-xl font-bold tabular-nums text-amber-400">
              {data?.inbound_to_europe ?? '-'}
            </div>
            <div className="text-[10px] text-muted-foreground">EU inbound</div>
          </div>
          <div>
            <div className="text-xl font-bold tabular-nums text-blue-400">
              {data?.bcm_inbound != null ? `${data.bcm_inbound.toFixed(2)}` : '-'}
            </div>
            <div className="text-[10px] text-muted-foreground">bcm inbound*</div>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          {/* Inbound list */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                EU terminal arrivals
              </h4>
              <span className="text-[10px] text-muted-foreground">within {horizonH}h</span>
            </div>
            {inboundVessels.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">No vessels matched EU terminal in window</p>
            ) : (
              <div className="space-y-0.5">
                {inboundVessels.map(v => (
                  <LngVesselRow key={v.mmsi} v={v} onNavigate={goToTracker} />
                ))}
              </div>
            )}
          </div>

          {/* Origin breakdown + terminal breakdown */}
          <div className="space-y-4">
            {Object.keys(data?.by_origin ?? {}).length > 0 && (
              <div>
                <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Origin breakdown
                </h4>
                <LngOriginBar byOrigin={data?.by_origin ?? {}} />
              </div>
            )}

            {Object.keys(data?.by_terminal ?? {}).length > 0 && (
              <div>
                <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Terminals receiving
                </h4>
                <div className="space-y-0.5">
                  {Object.entries(data?.by_terminal ?? {})
                    .sort((a, b) => Number(b[1]) - Number(a[1]))
                    .map(([terminal, count]) => (
                      <div key={terminal} className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground truncate">{terminal}</span>
                        <span className="font-mono text-foreground">{count}</span>
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* US LNG loading terminal activity */}
        {(data?.us_loading ?? []).length > 0 && (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                US loading terminals
              </h4>
              <span className="text-[10px] text-muted-foreground">leading indicator +14-18d</span>
            </div>
            <div className="space-y-0.5">
              {(data?.us_loading ?? []).map(v => (
                <button
                  key={v.mmsi}
                  type="button"
                  className="flex w-full items-center gap-2 rounded px-1 py-1 text-left text-xs hover:bg-muted/60 transition-colors"
                  onClick={() => goToTracker(v.mmsi, v.lat, v.lon)}
                >
                  <span
                    className={`inline-block h-2 w-2 flex-shrink-0 rounded-full ${v.status === 'loading' ? 'bg-amber-400' : 'bg-blue-400'}`}
                  />
                  <span className="w-36 truncate font-medium text-foreground">{v.name || `MMSI ${v.mmsi}`}</span>
                  <span className="flex-1 truncate text-muted-foreground text-[10px]">{v.terminal_name}</span>
                  <span className={`text-[10px] font-medium ${v.status === 'loading' ? 'text-amber-400' : 'text-blue-400'}`}>
                    {v.status === 'loading' ? 'loading' : `dep. - EU ~${v.eu_terminal_eta_days}d`}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Fleet in transit (not EU-bound, not US loading) */}
        {otherVessels.length > 0 && (
          <div>
            <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Fleet in transit / at terminal
            </h4>
            <div className="max-h-28 overflow-y-auto space-y-0.5">
              {otherVessels.map(v => (
                <LngVesselRow key={v.mmsi} v={v} onNavigate={goToTracker} />
              ))}
            </div>
          </div>
        )}

        <p className="text-[10px] text-muted-foreground/60">
          * Assumes 160k m³ cargo (standard TFDE LNG carrier = ~0.10 bcm). Origin inferred from
          transit events: Suez NB = Qatar/ME, Gibraltar/Dover E laden = US Gulf, Cape NB = long-haul.
          US loading terminals: within 80nm of Sabine Pass, Calcasieu Pass, Corpus Christi, Freeport, Cove Point.
          AIS coverage varies; some carriers may not broadcast IMO.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Default export: Ports & Cargo tab component
// ---------------------------------------------------------------------------
export default function PortsCargoTab() {
  return (
    <div className="space-y-6">
      <LngIntelligenceCard />
      <EuropeanInboundCard />
      <PortArrivalForecastCard />
      <PortFlowCard />
      <PortCongestionCard />
      <AnchorageOccupancyCard />
      <TradeLaneMatrixCard />
      <DestinationFlowCard />
      <CargoStateChangesCard />
      <CargoTransitionsCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <LadenCard />
        <DensityCard />
      </div>
    </div>
  )
}
