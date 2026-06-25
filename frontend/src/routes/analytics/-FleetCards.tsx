import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useSpeedAnomalies, useSpeedTrend, useFleetSpeed,
  useRegionUtil, useFleetUtilization, useSlowSteamers,
  useOwnerFleetStatus,
} from '@/lib/api'
import { fmt, EmptyState, ChartSkeleton, TOOLTIP_STYLE, LEGEND_STYLE } from './-analyticsShared'

function useGoToTracker() {
  const navigate = useNavigate()
  return (mmsi: number, lat?: number | null, lon?: number | null) => {
    const search: Record<string, unknown> = { mmsi }
    if (lat != null && lon != null) { search.lat = lat; search.lon = lon }
    navigate({ to: '/tracker', search: search as never })
  }
}

// ---------------------------------------------------------------------------
// Local helpers (Fleet tab only)
// ---------------------------------------------------------------------------
const SEG_COLORS: Record<string, string> = {
  VLCC: '#ef4444',
  Suezmax: '#f97316',
  Aframax: '#facc15',
  Panamax: '#22c55e',
  MR2: '#3b82f6',
  MR1: '#8b5cf6',
  Handy: '#ec4899',
  Capesize: '#14b8a6',
  Handysize: '#64748b',
  Supramax: '#06b6d4',
}

function segColor(seg: string): string {
  return SEG_COLORS[seg] ?? '#94a3b8'
}

// ---------------------------------------------------------------------------
// SpeedAnomaliesCard
// ---------------------------------------------------------------------------
export function SpeedAnomaliesCard() {
  const [kind, setKind] = useState('tanker')
  const [minZ, setMinZ] = useState(2.5)
  const { data, isLoading } = useSpeedAnomalies(kind, minZ, 50)
  const rows = data?.rows ?? []
  const fast = rows.filter(r => r.anomaly_type === 'fast').length
  const slow = rows.filter(r => r.anomaly_type === 'slow').length
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>Speed Anomaly Detection</span>
          <div className="flex gap-2">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
              <option value="">All types</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={minZ} onChange={e => setMinZ(Number(e.target.value))}>
              <option value={2.0}>z {'>='} 2.0</option>
              <option value={2.5}>z {'>='} 2.5</option>
              <option value={3.0}>z {'>='} 3.0</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.anomaly_count} anomalies from {data.total_vessels_checked} vessels checked.{' '}
            <span className="text-orange-400">{fast} fast</span>,{' '}
            <span className="text-blue-400">{slow} slow</span>.
            MAD-based z-score vs segment peers.
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No speed anomalies in current fleet." />
        ) : (
          <div className="space-y-0.5 max-h-80 overflow-y-auto pr-1">
            {rows.map(row => (
              <div key={row.mmsi} className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs ${row.anomaly_type === 'fast' ? 'bg-orange-500/8 hover:bg-orange-500/15' : 'bg-blue-500/8 hover:bg-blue-500/15'}`} onClick={() => goToTracker(row.mmsi, row.lat, row.lon)}>
                <span className={`w-12 shrink-0 rounded px-1 py-0.5 text-center text-[10px] font-bold ${row.anomaly_type === 'fast' ? 'bg-orange-500/20 text-orange-400' : 'bg-blue-500/20 text-blue-400'}`}>
                  {row.anomaly_type === 'fast' ? 'FAST' : 'SLOW'}
                </span>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  {row.region && <span className="ml-1 text-muted-foreground/60">{row.region}</span>}
                  {row.registry_risk != null && (
                    <span className={`ml-1 rounded px-1 text-[10px] ${row.registry_risk >= 70 ? 'bg-red-500/20 text-red-400' : row.registry_risk >= 40 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                      risk {row.registry_risk}
                    </span>
                  )}
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-semibold">{row.sog.toFixed(1)} kn</span>
                  <span className="ml-1 text-muted-foreground">(med {row.segment_median_sog.toFixed(1)})</span>
                  <span className={`ml-2 font-bold ${row.anomaly_type === 'fast' ? 'text-orange-400' : 'text-blue-400'}`}>
                    z={row.z_score > 0 ? '+' : ''}{row.z_score.toFixed(1)}
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
// SpeedTrendCard
// ---------------------------------------------------------------------------
export function SpeedTrendCard() {
  const [kind, setKind] = useState('tanker')
  const [days, setDays] = useState(14)
  const { data, isLoading } = useSpeedTrend(kind, undefined, days)

  const chartData = (data?.series ?? []).map(p => ({
    date: p.date.slice(5),
    'Avg SOG': p.avg_sog,
    Underway: p.underway_count,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Fleet Speed Trend</span>
          <div className="flex gap-2">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={7}>7d</option>
              <option value={14}>14d</option>
              <option value={30}>30d</option>
            </select>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-48 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <EmptyState message="No speed trend data." />
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData} margin={{ left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis yAxisId="sog" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <YAxis yAxisId="cnt" orientation="right" tick={{ fontSize: 10 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Line yAxisId="sog" type="monotone" dataKey="Avg SOG" stroke="#3b82f6" dot={false} strokeWidth={2} connectNulls />
              <Line yAxisId="cnt" type="monotone" dataKey="Underway" stroke="#22c55e" dot={false} strokeWidth={1.5} opacity={0.7} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// FleetSpeedCard
// ---------------------------------------------------------------------------
export function FleetSpeedCard() {
  const { data, isLoading } = useFleetSpeed()
  const rows = data?.rows ?? []

  const chartData = rows.filter(r => r.avg_sog_underway != null).map(r => ({
    seg: r.segment,
    'Avg SOG': r.avg_sog_underway,
    'P50 SOG': r.p50_sog,
    '% Underway': r.pct_underway,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Current Fleet Speed
          {data && <span className="ml-2 text-xs font-normal text-muted-foreground">{data.total_vessels.toLocaleString()} vessels</span>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-48 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <EmptyState message="No speed data." />
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData} margin={{ left: -16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="seg" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} unit=" kn" />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar dataKey="Avg SOG" radius={[2, 2, 0, 0]}>
                {chartData.map((entry, i) => <Cell key={i} fill={segColor(String(entry.seg))} />)}
              </Bar>
              <Bar dataKey="P50 SOG" fill="#64748b" radius={[2, 2, 0, 0]} opacity={0.6} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// RegionUtilCard
// ---------------------------------------------------------------------------
export function RegionUtilCard() {
  const { data, isLoading } = useRegionUtil()
  const rows = (data?.rows ?? []).slice(0, 12)

  const chartData = rows.map(r => ({
    region: r.region.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).slice(0, 14),
    Underway: r.underway,
    Anchored: r.anchored,
    Moored: r.moored,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Region Fleet Utilization</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-48 animate-pulse rounded bg-muted/40" />
        ) : chartData.length === 0 ? (
          <EmptyState message="No region data." />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} layout="vertical" margin={{ left: 90, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="region" tick={{ fontSize: 10 }} width={88} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend wrapperStyle={LEGEND_STYLE} />
              <Bar dataKey="Underway" stackId="a" fill="#22c55e" />
              <Bar dataKey="Anchored" stackId="a" fill="#f97316" />
              <Bar dataKey="Moored" stackId="a" fill="#64748b" radius={[0, 2, 2, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// FleetUtilizationCard
// ---------------------------------------------------------------------------
export function FleetUtilizationCard() {
  const { data, isLoading } = useFleetUtilization()
  const rows = data?.rows ?? []

  const chartData = rows.map(r => ({
    seg: r.segment,
    '% Underway': r.underway_pct,
    '% Idle': r.idle_pct,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Fleet Utilization by Segment
          {data && <span className="ml-2 text-xs font-normal text-muted-foreground">{data.total_fleet.toLocaleString()} vessels</span>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No utilization data." />
        ) : (
          <>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={chartData} margin={{ left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="seg" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} unit="%" domain={[0, 100]} />
                <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => [`${Number(v).toFixed(1)}%`]} />
                <Legend wrapperStyle={LEGEND_STYLE} />
                <Bar dataKey="% Underway" fill="#22c55e" radius={[2, 2, 0, 0]} />
                <Bar dataKey="% Idle" fill="#64748b" radius={[2, 2, 0, 0]} opacity={0.7} />
              </BarChart>
            </ResponsiveContainer>
            <div className="mt-2 space-y-1 border-t border-border pt-2">
              {rows.map(r => (
                <div key={`${r.kind}-${r.segment}`} className="flex items-center gap-2 text-xs">
                  <span className="w-24 shrink-0 font-medium">{r.segment}</span>
                  <span className="text-muted-foreground w-8 tabular-nums">{r.total}</span>
                  <div className="flex-1 h-1.5 rounded-full bg-muted/40 overflow-hidden">
                    <div className="h-full rounded-full bg-green-500/70" style={{ width: `${r.underway_pct}%` }} />
                  </div>
                  <span className="w-10 text-right tabular-nums text-muted-foreground">{r.underway_pct.toFixed(0)}%</span>
                  {r.avg_sog_underway != null && (
                    <span className="w-14 text-right tabular-nums text-muted-foreground/60">{r.avg_sog_underway.toFixed(1)} kn</span>
                  )}
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
// SlowSteamersCard
// ---------------------------------------------------------------------------
export function SlowSteamersCard() {
  const [kind, setKind] = useState('')
  const { data, isLoading } = useSlowSteamers(kind)
  const rows = data?.rows ?? []
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>Slow Steamers</span>
          <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">All types</option>
            <option value="tanker">Tankers</option>
            <option value="bulk">Bulkers</option>
          </select>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {rows.length} vessels operating significantly below segment median.
            {data.total_fleet_underway > 0 && ` Out of ${data.total_fleet_underway.toLocaleString()} underway.`}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-40 animate-pulse rounded bg-muted/40" />
        ) : rows.length === 0 ? (
          <EmptyState message="No slow steaming detected." />
        ) : (
          <div className="space-y-0.5 max-h-80 overflow-y-auto pr-1">
            {rows.map(row => (
              <div key={row.mmsi} className="flex cursor-pointer items-center gap-2 rounded bg-muted/15 px-2 py-1 text-xs hover:bg-muted/30" onClick={() => goToTracker(row.mmsi)}>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.name ?? `MMSI ${row.mmsi}`}</span>
                  {row.segment && <span className="ml-1 text-muted-foreground">{row.segment}</span>}
                  {row.region && <span className="ml-1 text-muted-foreground/60">{fmt(row.region)}</span>}
                  {row.risk_score != null && row.risk_score >= 25 && (
                    <span className={`ml-1 rounded px-1 text-[10px] ${row.risk_score >= 70 ? 'bg-red-500/20 text-red-400' : 'bg-yellow-500/20 text-yellow-400'}`}>
                      risk {row.risk_score}
                    </span>
                  )}
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-semibold text-blue-400">{row.sog.toFixed(1)} kn</span>
                  <span className="ml-1 text-muted-foreground">(med {row.segment_median_sog.toFixed(1)})</span>
                  <span className="ml-2 text-muted-foreground/60 text-[10px]">{row.pct_of_median.toFixed(0)}%</span>
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
// OwnerFleetStatusCard
// ---------------------------------------------------------------------------

function riskColor(score: number | null): string {
  if (score == null) return 'text-muted-foreground'
  if (score >= 50) return 'text-red-400'
  if (score >= 25) return 'text-yellow-400'
  return 'text-emerald-400'
}

export function OwnerFleetStatusCard() {
  const [kind, setKind] = useState<string>('tanker')
  const { data, isLoading } = useOwnerFleetStatus(kind, 1, 30)
  const rows = data?.rows ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Owner Fleet Status</span>
          <div className="flex items-center gap-2">
            <select
              className="rounded border border-border bg-background px-2 py-1 text-xs"
              value={kind}
              onChange={e => setKind(e.target.value)}
            >
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulk carriers</option>
              <option value="">All vessels</option>
            </select>
          </div>
        </CardTitle>
        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total_owners} owners with live positions
          </p>
        )}
      </CardHeader>
      <CardContent>
        {isLoading && <ChartSkeleton />}
        {!isLoading && rows.length === 0 && <EmptyState message="No registry-matched vessels" />}
        {rows.length > 0 && (
          <div className="space-y-2">
            {rows.map(row => {
              const ladenPct = row.live_count > 0 ? (row.laden / row.live_count) * 100 : 0
              const ballastPct = row.live_count > 0 ? (row.ballast / row.live_count) * 100 : 0
              return (
                <div key={row.owner} className="space-y-0.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <span
                      className="truncate text-xs font-medium max-w-[55%]"
                      title={row.owner}
                    >
                      {row.owner}
                    </span>
                    <div className="flex shrink-0 items-center gap-2 text-[10px] tabular-nums text-muted-foreground">
                      {row.top_segment && (
                        <span className="rounded bg-muted px-1 py-px text-[9px] uppercase tracking-wide">
                          {row.top_segment}
                        </span>
                      )}
                      <span className="text-blue-400 font-medium">{row.laden}L</span>
                      <span>{row.ballast}B</span>
                      {row.unknown > 0 && <span>{row.unknown}?</span>}
                      {row.avg_risk != null && (
                        <span className={riskColor(row.avg_risk)}>
                          r{row.avg_risk.toFixed(0)}
                        </span>
                      )}
                      <span className="text-muted-foreground/60">{row.live_count}</span>
                    </div>
                  </div>
                  <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
                    <div
                      className="h-full bg-blue-500/70 transition-all"
                      style={{ width: `${ladenPct}%` }}
                    />
                    <div
                      className="h-full bg-slate-500/60 transition-all"
                      style={{ width: `${ballastPct}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        )}
        <div className="mt-3 flex items-center gap-4 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-blue-500/70" />
            Laden
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-slate-500/60" />
            Ballast
          </span>
          <span className="ml-auto">L=laden B=ballast r=avg risk</span>
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Default export: Fleet tab component
// ---------------------------------------------------------------------------
export default function FleetTab() {
  return (
    <div className="space-y-6">
      <OwnerFleetStatusCard />
      <SpeedAnomaliesCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <SpeedTrendCard />
        <FleetSpeedCard />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <RegionUtilCard />
        <FleetUtilizationCard />
      </div>
      <SlowSteamersCard />
    </div>
  )
}
