import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useVesselRiskScores, useOwnerIntelligence, useOwnerRisk,
  useFlagRisk, useFleetAge, useTransitRisk, useAnchorageDwell, useStsRisk,
} from '@/lib/api'
import { fmt, EmptyState, ChartSkeleton, TOOLTIP_STYLE, LEGEND_STYLE } from './-analyticsShared'

// ---------------------------------------------------------------------------
// Local helpers (Risk tab only)
// ---------------------------------------------------------------------------
function useGoToTracker() {
  const navigate = useNavigate()
  return (mmsi: number, lat?: number | null, lon?: number | null) => {
    const search: Record<string, unknown> = { mmsi }
    if (lat != null && lon != null) { search.lat = lat; search.lon = lon }
    navigate({ to: '/tracker', search: search as never })
  }
}

function riskColor(score: number): string {
  if (score >= 70) return 'text-red-400'
  if (score >= 50) return 'text-orange-400'
  if (score >= 30) return 'text-yellow-400'
  return 'text-green-400'
}

function riskBadge(score: number | null, ofac: boolean) {
  if (ofac) return <span className="rounded bg-red-500/20 px-1 text-[10px] font-semibold text-red-400">OFAC</span>
  if (score == null) return null
  if (score >= 75) return <span className="rounded bg-red-400/20 px-1 text-[10px] font-semibold text-red-400">{score}</span>
  if (score >= 50) return <span className="rounded bg-orange-400/20 px-1 text-[10px] font-semibold text-orange-400">{score}</span>
  if (score >= 25) return <span className="rounded bg-yellow-400/20 px-1 text-[10px] font-semibold text-yellow-400">{score}</span>
  return <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{score}</span>
}

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

const MOU_COLORS: Record<string, string> = {
  Black: 'text-red-400',
  Grey: 'text-yellow-400',
  White: 'text-green-400',
}

function _ownerRiskColor(score: number | null): string {
  if (score == null) return 'text-muted-foreground'
  if (score >= 50) return 'text-red-400'
  if (score >= 25) return 'text-yellow-400'
  return 'text-green-400'
}

const RISK_CHOKEPOINTS = [
  'hormuz', 'suez', 'singapore_malacca', 'bab_el_mandeb',
  'dover_channel', 'cape_good_hope', 'bosphorus_dardanelles',
]

const ANCHORAGE_DWELL_ZONES = [
  'singapore_west', 'singapore_east', 'fujairah', 'suez_roads', 'port_said',
  'rotterdam', 'galveston_ltg', 'arab_gulf_north',
]

// ---------------------------------------------------------------------------
// VesselRiskLeaderboardCard
// ---------------------------------------------------------------------------
export function VesselRiskLeaderboardCard() {
  const [topN, setTopN] = useState(25)
  const [days, setDays] = useState(30)
  const [kindFilter, setKindFilter] = useState('')
  const { data, isLoading } = useVesselRiskScores(topN, days, '', kindFilter, 5)
  const rows = data?.rows ?? []
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Vessel Risk Leaderboard</span>
          <div className="flex flex-wrap gap-2 text-sm font-normal">
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={kindFilter} onChange={e => setKindFilter(e.target.value)}>
              <option value="">All types</option>
              <option value="tanker">Tankers</option>
              <option value="bulk">Bulkers</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={days} onChange={e => setDays(Number(e.target.value))}>
              <option value={7}>7d</option>
              <option value={14}>14d</option>
              <option value={30}>30d</option>
              <option value={60}>60d</option>
            </select>
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={topN} onChange={e => setTopN(Number(e.target.value))}>
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
          <p className="py-8 text-center text-sm text-muted-foreground">No vessels above minimum risk threshold.</p>
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
                    <tr key={row.mmsi} className="cursor-pointer border-b border-border/20 hover:bg-muted/20" onClick={() => goToTracker(row.mmsi, row.lat, row.lon)}>
                      <td className="py-1.5 pr-2 text-right text-xs text-muted-foreground tabular-nums">{i + 1}</td>
                      <td className="py-1.5 pr-3">
                        <span className="font-medium text-foreground/90">{row.name ?? '-'}</span>
                        {row.ofac && (
                          <span className="ml-1.5 rounded bg-red-500/20 px-1 py-0.5 text-xs text-red-400 font-bold">OFAC</span>
                        )}
                        <div className="text-xs text-muted-foreground">
                          {row.mmsi}{row.imo ? ` / ${row.imo}` : ''}
                        </div>
                      </td>
                      <td className="py-1.5 pr-3 text-muted-foreground text-xs">{row.segment ?? row.kind ?? '-'}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground text-xs">{row.region?.replace(/_/g, ' ') ?? '-'}</td>
                      <td className="py-1.5 pr-2 text-center tabular-nums font-mono text-xs">
                        {row.sts_count > 0 ? <span className="text-orange-400">{row.sts_count}</span> : <span className="text-muted-foreground/40">0</span>}
                      </td>
                      <td className="py-1.5 pr-2 text-center tabular-nums font-mono text-xs">
                        {row.reroute_count > 0 ? <span className="text-yellow-400">{row.reroute_count}</span> : <span className="text-muted-foreground/40">0</span>}
                      </td>
                      <td className="py-1.5 pr-2 text-center tabular-nums text-xs">
                        {row.registry_risk !== null ? (
                          <span className={row.registry_risk >= 50 ? 'text-red-400' : row.registry_risk >= 25 ? 'text-yellow-400' : 'text-muted-foreground'}>
                            {row.registry_risk}
                          </span>
                        ) : <span className="text-muted-foreground/30">-</span>}
                      </td>
                      <td className="py-1.5 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <div className="h-1.5 w-12 rounded-full bg-muted/40 overflow-hidden">
                            <div className={`h-full rounded-full ${riskScoreBar(row.total_score)}`} style={{ width: `${row.total_score}%` }} />
                          </div>
                          <span className="tabular-nums font-mono text-xs font-semibold w-6 text-right">{row.total_score}</span>
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
// OwnerIntelligenceCard
// ---------------------------------------------------------------------------
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
            <select className="rounded border border-border bg-background px-2 py-1 text-xs" value={minVessels} onChange={e => setMinVessels(Number(e.target.value))}>
              <option value={1}>All owners</option>
              <option value={2}>2+ vessels</option>
              <option value={3}>3+ vessels</option>
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
            {data.total_owners} owners in registry. Sorted by risk-weighted fleet size.
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
                  {row.flags.length > 0 && <span className="ml-1 text-muted-foreground/60">{row.flags.slice(0, 2).join(', ')}</span>}
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <span className="font-medium">{row.vessel_count}v</span>
                  {row.high_risk_count > 0 && (
                    <span className="ml-1 rounded bg-red-500/20 px-1 text-[9px] font-bold text-red-400">{row.high_risk_count} hi-risk</span>
                  )}
                  {row.avg_risk != null && (
                    <span className={`ml-2 font-semibold ${_ownerRiskColor(row.avg_risk)}`}>avg {row.avg_risk}</span>
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
// OwnerRiskCard
// ---------------------------------------------------------------------------
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
            <button key={n} onClick={() => setMinVessels(n)}
              className={`rounded px-1.5 py-0.5 font-medium transition-colors ${minVessels === n ? 'bg-primary/20 text-primary' : 'hover:text-foreground'}`}>
              {n}+
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <ChartSkeleton />
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
              <div key={row.owner} className="grid grid-cols-[1fr_3rem_3rem_3rem_3rem] gap-1 items-center text-xs">
                <div className="truncate font-medium" title={row.owner}>
                  {row.owner}
                  {row.ofac_count > 0 && <span className="ml-1 rounded bg-red-500/15 px-1 py-0.5 text-[9px] font-semibold text-red-400">OFAC</span>}
                  {row.flags.length > 0 && <span className="ml-1 text-[9px] text-muted-foreground/60">{row.flags.slice(0, 2).join(', ')}</span>}
                </div>
                <span className="text-right text-muted-foreground">{row.vessel_count}</span>
                <span className={`text-right font-mono font-semibold ${riskColor(row.avg_risk_score)}`}>{row.avg_risk_score.toFixed(0)}</span>
                <span className={`text-right font-mono text-[10px] ${riskColor(row.max_risk_score)}`}>{row.max_risk_score}</span>
                <span className="text-right text-[10px] text-muted-foreground">
                  {row.high_risk_count > 0 ? <span className="text-orange-400">{row.high_risk_count}</span> : '0'}
                </span>
              </div>
            ))}
            <div className="pt-1 text-[10px] text-muted-foreground/50">
              Avg/Max: risk score 0-100. High: vessels with score {'>='} 50.
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// FlagRiskCard
// ---------------------------------------------------------------------------
export function FlagRiskCard() {
  const { data, isLoading } = useFlagRisk(25)

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Flag State Risk</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <ChartSkeleton />
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
              <div key={row.flag} className="grid grid-cols-[1fr_3rem_3rem_3rem_5rem] gap-1 items-center text-xs">
                <div className="truncate font-medium" title={row.flag}>
                  {row.flag_code && <span className="mr-1 text-[9px] text-muted-foreground/60 font-mono">{row.flag_code}</span>}
                  {row.flag}
                  {row.ofac_count > 0 && <span className="ml-1 rounded bg-red-500/15 px-1 py-0.5 text-[9px] font-semibold text-red-400">OFAC</span>}
                </div>
                <span className="text-right text-muted-foreground">{row.vessel_count}</span>
                <span className={`text-right font-mono font-semibold ${riskColor(row.avg_risk_score)}`}>{row.avg_risk_score.toFixed(0)}</span>
                <span className="text-right text-[10px] text-muted-foreground">
                  {row.high_risk_count > 0 ? <span className="text-orange-400">{row.high_risk_count}</span> : '0'}
                </span>
                <div className="flex gap-1 justify-end text-[9px]">
                  {row.paris_mou && <span className={MOU_COLORS[row.paris_mou] ?? 'text-muted-foreground'}>P:{row.paris_mou[0]}</span>}
                  {row.tokyo_mou && <span className={MOU_COLORS[row.tokyo_mou] ?? 'text-muted-foreground'}>T:{row.tokyo_mou[0]}</span>}
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

// ---------------------------------------------------------------------------
// FleetAgeCard
// ---------------------------------------------------------------------------
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
          {data && <span className="ml-2 text-xs font-normal text-muted-foreground">by 5-year bands (ref {data.reference_year})</span>}
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
              <Line yAxisId="right" type="monotone" dataKey="avg_risk" stroke="#ef4444" strokeWidth={2} dot={{ r: 3 }} name="Avg risk score" connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// TransitRiskCard
// ---------------------------------------------------------------------------
export function TransitRiskCard() {
  const [chokepoint, setChokepoint] = useState('hormuz')
  const [days, setDays] = useState(30)
  const { data, isLoading } = useTransitRisk(chokepoint, days, 0)
  const rows = data?.rows ?? []
  const showing = rows.slice(0, 15)
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex flex-wrap items-center gap-2">
          Chokepoint Transits
          <select value={chokepoint} onChange={(e) => setChokepoint(e.target.value)}
            className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] focus:outline-none">
            {RISK_CHOKEPOINTS.map((cp) => <option key={cp} value={cp}>{fmt(cp)}</option>)}
          </select>
          <div className="flex gap-1">
            {[7, 14, 30].map((d) => (
              <button key={d} onClick={() => setDays(d)}
                className={`rounded px-1.5 py-0.5 text-[10px] ${days === d ? 'bg-primary/20 text-primary' : 'text-muted-foreground hover:text-foreground'}`}>
                {d}d
              </button>
            ))}
          </div>
          {data && <span className="ml-auto text-xs font-normal text-muted-foreground">{data.total_transits.toLocaleString()} transits{data.enriched > 0 && ` - ${data.enriched} risk-scored`}</span>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && <EmptyState message={`No ${fmt(chokepoint)} transits in last ${days} days`} />}
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
                  <tr key={`${ev.mmsi}-${ev.entered_ts}-${i}`} className="cursor-pointer border-t border-border/30 hover:bg-muted/20" onClick={() => goToTracker(ev.mmsi)}>
                    <td className="py-0.5 pr-2 text-muted-foreground">{ev.entered_ts.slice(5, 16).replace('T', ' ')}</td>
                    <td className="max-w-[8rem] truncate py-0.5 pr-2">{ev.name ?? ev.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{ev.segment ?? ev.kind ?? '-'}</td>
                    <td className="py-0.5 pr-2">{ev.direction ? fmt(ev.direction) : '-'}</td>
                    <td className="py-0.5 pr-2">
                      {ev.laden === null ? '-' : ev.laden ? <span className="text-blue-400">L</span> : <span className="text-muted-foreground">B</span>}
                    </td>
                    <td className="py-0.5">{riskBadge(ev.risk_score, ev.ofac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 15 && <p className="mt-1 text-[10px] text-muted-foreground">Showing 15 of {rows.length.toLocaleString()} (risk-sorted)</p>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// AnchorageDwellCard
// ---------------------------------------------------------------------------
export function AnchorageDwellCard() {
  const [zone, setZone] = useState('singapore_west')
  const { data, isLoading } = useAnchorageDwell(zone, 20)
  const rows = data?.rows ?? []
  const goToTracker = useGoToTracker()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex flex-wrap items-center gap-2">
          Longest Anchored
          <select value={zone} onChange={(e) => setZone(e.target.value)}
            className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] focus:outline-none">
            {ANCHORAGE_DWELL_ZONES.map((z) => <option key={z} value={z}>{fmt(z)}</option>)}
          </select>
          {data && rows.length > 0 && <span className="ml-auto text-xs font-normal text-muted-foreground">{rows.length} open episodes</span>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <div className="h-48 animate-pulse rounded bg-muted/40" />}
        {!isLoading && rows.length === 0 && <EmptyState message={`No open anchor episodes at ${fmt(zone)}`} />}
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
                  <tr key={`${v.mmsi}-${i}`} className="cursor-pointer border-t border-border/30 hover:bg-muted/20" onClick={() => goToTracker(v.mmsi)}>
                    <td className="max-w-[9rem] truncate py-0.5 pr-2">{v.name ?? v.mmsi}</td>
                    <td className="py-0.5 pr-2 text-muted-foreground">{v.segment ?? v.kind ?? '-'}</td>
                    <td className="py-0.5 pr-2 tabular-nums">
                      {v.dwell_hours >= 24 ? <span className="text-orange-400">{(v.dwell_hours / 24).toFixed(1)}d</span> : `${v.dwell_hours.toFixed(1)}h`}
                    </td>
                    <td className="py-0.5 pr-2">
                      {v.laden === 'laden' ? <span className="text-blue-400">L</span> : v.laden === 'ballast' ? <span className="text-muted-foreground">B</span> : <span className="text-muted-foreground">?</span>}
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

// ---------------------------------------------------------------------------
// StsRiskCard
// ---------------------------------------------------------------------------
export function StsRiskCard() {
  const { data, isLoading } = useStsRisk(30, 0)
  const rows = data?.rows ?? []
  const showing = rows.slice(0, 15)
  const goToTracker = useGoToTracker()

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
                  <tr key={ev.event_id} className="cursor-pointer border-t border-border/30 hover:bg-muted/20" onClick={() => goToTracker(ev.mmsi)}>
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
                        {ev.max_risk === 0 && ev.risk_score == null && ev.risk_score2 == null && <span className="text-muted-foreground/40">-</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 15 && <p className="mt-1 text-[10px] text-muted-foreground">Showing 15 of {rows.length.toLocaleString()} events (sorted by max risk)</p>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Default export: Risk tab component
// ---------------------------------------------------------------------------
export default function RiskTab() {
  return (
    <div className="space-y-6">
      <VesselRiskLeaderboardCard />
      <OwnerIntelligenceCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <OwnerRiskCard />
        <FlagRiskCard />
      </div>
      <FleetAgeCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <TransitRiskCard />
        <AnchorageDwellCard />
      </div>
      <StsRiskCard />
    </div>
  )
}
