import React, { useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useMarketSummary, useCrudeOnWater, useRegionMomentum, useFleetAtTime,
} from '@/lib/api'
import { REGION_LABELS } from './-analyticsShared'

// ---------------------------------------------------------------------------
// regionLabel - used only in RegionMomentumCard (Overview tab)
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

// ---------------------------------------------------------------------------
// SEG_ORDER / segSort - used only in FleetAtTimeCard (Overview tab)
// ---------------------------------------------------------------------------
const SEG_ORDER = [
  'VLCC', 'Suezmax', 'Aframax', 'Panamax', 'MR2', 'MR1', 'Handy',
  'Capesize', 'Supramax', 'Handysize', 'ULCS', 'VLCS', 'Large', 'Medium', 'Small',
]

function segSort(a: string, b: string): number {
  const ia = SEG_ORDER.indexOf(a)
  const ib = SEG_ORDER.indexOf(b)
  if (ia === -1 && ib === -1) return a.localeCompare(b)
  if (ia === -1) return 1
  if (ib === -1) return -1
  return ia - ib
}

// ---------------------------------------------------------------------------
// MarketSummaryCard
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

// ---------------------------------------------------------------------------
// RegionMomentumCard
// ---------------------------------------------------------------------------
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
// FleetAtTimeCard
// ---------------------------------------------------------------------------
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
// CrudeOnWaterCard
// ---------------------------------------------------------------------------
export function CrudeOnWaterCard() {
  const { data, isLoading } = useCrudeOnWater()

  if (isLoading || !data) {
    return (
      <Card>
        <CardHeader><CardTitle>Crude Oil on Water</CardTitle></CardHeader>
        <CardContent><div className="h-32 animate-pulse rounded bg-muted/40" /></CardContent>
      </Card>
    )
  }

  const { total_laden_tankers, total_ballast_tankers, estimated_mb_on_water, by_segment, inbound_regions } = data

  const regionData = inbound_regions.slice(0, 8).map((r) => ({
    region: r.region,
    vessels: r.vessel_count,
    mb: r.estimated_mb,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Crude Oil on Water</span>
          <span className="text-xs font-normal text-muted-foreground">laden tankers only</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg bg-muted/30 p-3 text-center">
            <div className="text-2xl font-bold tabular-nums">{estimated_mb_on_water.toFixed(0)}</div>
            <div className="text-xs text-muted-foreground mt-0.5">million barrels on water</div>
          </div>
          <div className="rounded-lg bg-muted/30 p-3 text-center">
            <div className="text-2xl font-bold tabular-nums text-orange-400">{total_laden_tankers.toLocaleString()}</div>
            <div className="text-xs text-muted-foreground mt-0.5">laden tankers</div>
          </div>
          <div className="rounded-lg bg-muted/30 p-3 text-center">
            <div className="text-2xl font-bold tabular-nums text-slate-400">{total_ballast_tankers.toLocaleString()}</div>
            <div className="text-xs text-muted-foreground mt-0.5">ballast tankers</div>
          </div>
        </div>
        <div>
          <div className="mb-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wide">By Segment</div>
          <div className="space-y-1">
            {by_segment.filter((s) => s.laden_count > 0).map((s) => {
              const total = s.laden_count + s.ballast_count + s.unknown_count
              const ladenPct = total > 0 ? Math.round((s.laden_count / total) * 100) : 0
              return (
                <div key={s.segment} className="flex items-center gap-2 text-xs">
                  <span className="w-20 shrink-0 font-medium">{s.segment}</span>
                  <div className="flex-1 h-1.5 rounded-full bg-muted/50 overflow-hidden">
                    <div className="h-full rounded-full bg-orange-500/70" style={{ width: `${ladenPct}%` }} />
                  </div>
                  <span className="w-16 text-right tabular-nums text-muted-foreground">{s.laden_count}v</span>
                  <span className="w-16 text-right tabular-nums text-orange-400">{s.estimated_mb.toFixed(1)} MB</span>
                </div>
              )
            })}
          </div>
        </div>
        {regionData.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Inbound by Destination Region (MB)
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={regionData} margin={{ top: 4, right: 8, left: 0, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="region" tick={{ fontSize: 10, fill: '#888' }} angle={-35} textAnchor="end" interval={0} />
                <YAxis tick={{ fontSize: 10, fill: '#888' }} width={40} />
                <Tooltip
                  contentStyle={{ background: '#1c1c1c', border: '1px solid #333', fontSize: 11 }}
                  formatter={(val, name) => {
                    const n = Number(val)
                    return name === 'mb' ? [`${n.toFixed(1)} MB`, 'Est. MB'] : [n, 'Vessels']
                  }}
                />
                <Bar dataKey="mb" fill="#f97316" radius={[2, 2, 0, 0]} name="mb" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="text-[10px] text-muted-foreground/60">
          Estimates: VLCC 2.0 MB, Suezmax 1.1 MB, Aframax 0.75 MB, Panamax 0.54 MB, Small 0.30 MB per laden vessel. Destination from LOCODE country code.
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Default export: Overview tab component
// ---------------------------------------------------------------------------
export default function OverviewTab() {
  return (
    <div className="space-y-6">
      <MarketSummaryCard />
      <RegionMomentumCard />
      <FleetAtTimeCard />
      <CrudeOnWaterCard />
    </div>
  )
}
