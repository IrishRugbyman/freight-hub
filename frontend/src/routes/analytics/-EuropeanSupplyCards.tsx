// European supply cards: crude/products inbound to EU terminals and the LNG board.
import React, { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useEuropeanInbound, useLngInbound, useTransitRateTimeline, type EuropeanInboundVessel,
  type LngVessel,
} from '@/lib/api'
import { EmptyState, ChartSkeleton, TOOLTIP_STYLE, useGoToTracker } from './-analyticsShared'
import { EtaChip } from '@/components/EtaChip'

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
              <EtaChip vessel={v} fallbackH={v.eta_hours} className="justify-end text-[11px]" />
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
    navigate({ to: '/tracker', search: search as never })
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
              ETA is the calibrated physics estimate (sea-route distance + speed + canal staging) where resolvable, else naive; hover for the 80% band and the vs-naive delta.
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
        {isLoading && <ChartSkeleton />}
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
      {v.terminal != null
        ? <EtaChip vessel={v} fallbackH={v.eta_hours} showBand={false} className="shrink-0" />
        : <span className="shrink-0 text-right tabular-nums text-muted-foreground/60">-</span>}
    </button>
  )
}

export function LngIntelligenceCard() {
  const [horizonH, setHorizonH] = useState(72)
  const { data, isLoading } = useLngInbound(horizonH)
  const { data: suezData } = useTransitRateTimeline(360, 'suez')
  const goToTracker = useGoToTracker()

  // Aggregate hourly Suez NB transit data into daily laden counts for the chart
  const suezDailyLaden = React.useMemo(() => {
    if (!suezData) return []
    const byDay: Record<string, number> = {}
    for (const pt of suezData.points) {
      if (pt.chokepoint !== 'suez') continue
      const day = pt.hour.slice(0, 10)
      byDay[day] = (byDay[day] ?? 0) + (pt.laden_count ?? 0)
    }
    return Object.entries(byDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-14)  // last 14 days
      .map(([day, laden]) => ({ day: day.slice(5), laden }))  // MM-DD
  }, [suezData])

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
        {/* Supply chain pipeline: Loading -> Trans-Atlantic -> EU Arrival */}
        {(() => {
          const loading = (data?.us_loading ?? []).filter(v => v.status === 'loading').length
          const departing = (data?.us_loading ?? []).filter(v => v.status === 'departing').length
          const arriving = data?.inbound_to_europe ?? 0
          const deptBcm = (departing * 0.099).toFixed(2)
          return (
            <div className="flex items-center gap-0 overflow-hidden rounded-lg border border-border text-center text-xs">
              <div className="flex-1 bg-amber-500/10 px-2 py-2.5">
                <div className="text-lg font-bold tabular-nums text-amber-400">{loading}</div>
                <div className="text-[10px] text-muted-foreground">loading</div>
                <div className="mt-0.5 text-[9px] text-muted-foreground/60">US terminals</div>
              </div>
              <div className="text-muted-foreground/40 px-1 text-lg">›</div>
              <div className="flex-1 bg-blue-500/10 px-2 py-2.5">
                <div className="text-lg font-bold tabular-nums text-blue-400">{departing}</div>
                <div className="text-[10px] text-muted-foreground">trans-Atlantic</div>
                <div className="mt-0.5 text-[9px] text-muted-foreground/60">{deptBcm} bcm in ~14-18d</div>
              </div>
              <div className="text-muted-foreground/40 px-1 text-lg">›</div>
              <div className="flex-1 bg-green-500/10 px-2 py-2.5">
                <div className="text-lg font-bold tabular-nums text-green-400">{arriving}</div>
                <div className="text-[10px] text-muted-foreground">EU arriving</div>
                <div className="mt-0.5 text-[9px] text-muted-foreground/60">{(arriving * 0.099).toFixed(2)} bcm &lt;{horizonH}h</div>
              </div>
            </div>
          )
        })()}

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

        {/* Suez NB laden daily chart - 14d leading indicator */}
        {suezDailyLaden.length > 0 && (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                Suez NB laden (daily) - ME supply pipeline
              </h4>
              <span className="text-[10px] text-muted-foreground">+16-20d EU arrival lead</span>
            </div>
            <ResponsiveContainer width="100%" height={80}>
              <BarChart data={suezDailyLaden} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <XAxis dataKey="day" tick={{ fontSize: 9 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 9 }} tickLine={false} axisLine={false} allowDecimals={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="laden" fill="#f59e0b" radius={[2, 2, 0, 0]} maxBarSize={20} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        <p className="text-[10px] text-muted-foreground/60">
          * Assumes 160k m³ cargo (standard TFDE LNG carrier = ~0.10 bcm). Origin inferred from
          transit events: Suez NB = Qatar/ME, Gibraltar/Dover E laden = US Gulf, Cape NB = long-haul.
          US loading terminals: within 80nm of Sabine Pass, Calcasieu Pass, Corpus Christi, Freeport, Cove Point.
          Suez chart shows all laden vessel types (includes crude/bulk carriers, not LNG-only).
          AIS coverage varies; some carriers may not broadcast IMO.
        </p>
      </CardContent>
    </Card>
  )
}
