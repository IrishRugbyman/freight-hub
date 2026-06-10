import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useRoutes,
  type RouteResult,
  type RoutesResponse,
  type ArbMatrixCell,
  type BwetInfo,
} from '@/lib/api'

export const Route = createFileRoute('/routes')({ component: RoutesPage })

const STATUS_COLOR: Record<string, string> = {
  open: '#22c55e',
  near: '#eab308',
  closed: '#78716c',
}

function fmt(v: number | null | undefined, dp = 2): string {
  if (v == null || isNaN(v)) return '-'
  const s = v.toFixed(dp)
  return v > 0 ? `+${s}` : s
}

function fmtu(v: number | null | undefined, dp = 2): string {
  if (v == null || isNaN(v)) return '-'
  return v.toFixed(dp)
}

function ArbHeatmap({ matrix, origins, destinations }: {
  matrix: ArbMatrixCell[]
  origins: string[]
  destinations: string[]
}) {
  if (!origins.length) return null
  const lookup = new Map<string, ArbMatrixCell>()
  for (const cell of matrix) lookup.set(`${cell.origin}|${cell.destination}`, cell)

  function cellBg(cell: ArbMatrixCell | undefined) {
    if (!cell || cell.status === null) return '#111'
    const v = cell.net_margin ?? 0
    if (v >= 0.5) return '#14532d'
    if (v >= 0.1) return '#166534'
    if (v >= -0.5) return '#713f12'
    if (v >= -2) return '#7f1d1d'
    return '#450a0a'
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr>
            <th className="p-2 text-left text-muted-foreground font-normal text-[10px]">Origin → Dest</th>
            {destinations.map(d => (
              <th key={d} className="p-2 text-center text-muted-foreground font-normal text-[10px]">{d}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {origins.map(orig => (
            <tr key={orig}>
              <td className="p-2 text-muted-foreground text-[10px] whitespace-nowrap pr-3">{orig}</td>
              {destinations.map(dest => {
                const cell = orig === dest ? undefined : lookup.get(`${orig}|${dest}`)
                return (
                  <td key={dest} className="p-1 text-center align-middle rounded"
                    style={{ backgroundColor: orig === dest ? 'transparent' : cellBg(cell), color: '#e5e7eb', minWidth: 72 }}
                    title={cell ? `${orig} → ${dest}: ${cell.net_margin != null ? fmt(cell.net_margin) : 'n/a'} $/bbl (${cell.voyage_days}d)` : `${orig} → ${dest}: no route`}
                  >
                    {orig === dest ? (
                      <span className="text-[9px] text-muted-foreground/20">—</span>
                    ) : cell && cell.status !== null ? (
                      <div className="leading-tight">
                        <div className="font-mono font-semibold text-[11px]">{fmt(cell.net_margin)}</div>
                        <div className="text-[9px] opacity-70">{cell.voyage_days}d</div>
                      </div>
                    ) : (
                      <span className="text-[9px] opacity-30">—</span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex gap-4 mt-2 text-[10px] text-muted-foreground">
        <span><span className="inline-block w-3 h-3 rounded mr-1 bg-[#14532d]" />positive (open)</span>
        <span><span className="inline-block w-3 h-3 rounded mr-1 bg-[#7f1d1d]" />negative (closed)</span>
        <span><span className="inline-block w-3 h-3 rounded mr-1 bg-[#111]" />no route</span>
        <span>Cell = net margin $/bbl | days</span>
      </div>
    </div>
  )
}

function BwetBanner({ bwet }: { bwet: BwetInfo }) {
  const level = bwet.scale_factor > 3 ? 'high' : bwet.scale_factor > 1.5 ? 'elevated' : 'normal'
  const color = level === 'high' ? '#ef4444' : level === 'elevated' ? '#eab308' : '#22c55e'
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-center gap-6 flex-wrap">
          <div>
            <p className="text-xs text-muted-foreground">BWET (tanker freight proxy)</p>
            <p className="text-2xl font-mono font-bold" style={{ color }}>${fmtu(bwet.bwet_close ?? 0, 2)}</p>
            <p className="text-xs text-muted-foreground">as of {bwet.bwet_date}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">vs 2023-24 baseline</p>
            <p className="text-xl font-mono font-semibold" style={{ color }}>{bwet.scale_factor.toFixed(1)}x</p>
            <p className="text-xs text-muted-foreground">${fmtu(bwet.bwet_baseline, 1)} baseline</p>
          </div>
          <div className="flex-1 min-w-48">
            <p className="text-xs text-muted-foreground mb-1">
              Freight level: <span style={{ color }}>{level.toUpperCase()}</span>
            </p>
            <p className="text-xs text-muted-foreground leading-relaxed">
              {level === 'high'
                ? `At ${bwet.scale_factor.toFixed(0)}x 2023-24 norms. Most long-haul routes uneconomic.`
                : level === 'elevated'
                ? 'Above historical average. Long-haul margins compressed.'
                : 'Near historical average. Routes valued at baseline economics.'}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function RouteTable({ routes }: { routes: RouteResult[] }) {
  const sorted = [...routes].sort((a, b) => b.net_margin - a.net_margin)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-1 pr-3">Route</th>
            <th className="text-right py-1 pr-3">Vessel</th>
            <th className="text-right py-1 pr-3">Days</th>
            <th className="text-right py-1 pr-3">Orig</th>
            <th className="text-right py-1 pr-3">Dest fwd</th>
            <th className="text-right py-1 pr-3">Freight</th>
            <th className="text-right py-1 pr-3">Net</th>
            <th className="text-right py-1 pr-3">Base</th>
            <th className="text-left py-1">Status</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(r => (
            <tr key={r.id} className="border-b border-border/40 hover:bg-secondary/30">
              <td className="py-1.5 pr-3">
                <span className="text-foreground">{r.origin}</span>
                <span className="text-muted-foreground"> → </span>
                <span className="text-foreground">{r.destination}</span>
                <br />
                <span className="text-muted-foreground text-[10px]">{r.product_class}</span>
              </td>
              <td className="text-right py-1.5 pr-3 text-muted-foreground">{r.vessel_class}</td>
              <td className="text-right py-1.5 pr-3">{r.voyage_days}</td>
              <td className="text-right py-1.5 pr-3">{fmtu(r.origin_price)}</td>
              <td className="text-right py-1.5 pr-3">{fmtu(r.dest_fwd)}</td>
              <td className="text-right py-1.5 pr-3 text-muted-foreground">
                {fmtu(r.freight)}
                {r.freight_bwet_adjusted && <span className="text-[9px] text-yellow-500 ml-0.5">BWET</span>}
              </td>
              <td className="text-right py-1.5 pr-3 font-semibold" style={{ color: STATUS_COLOR[r.status_near] }}>
                {fmt(r.net_margin)}
              </td>
              <td className="text-right py-1.5 pr-3 text-muted-foreground">{fmt(r.net_margin_baseline)}</td>
              <td className="py-1.5">
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                  style={{ background: (STATUS_COLOR[r.status_near] ?? '#888') + '33', color: STATUS_COLOR[r.status_near] }}>
                  {r.status.toUpperCase()}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground mt-2">
        Net = F(dest, T+voyage) - P(origin) - freight - port - finance - insurance. Base = no BWET adj.
      </p>
    </div>
  )
}

function MarginBar({ routes }: { routes: RouteResult[] }) {
  const data = [...routes].sort((a, b) => b.net_margin - a.net_margin).map(r => ({
    name: `${r.origin.slice(0, 8)} → ${r.destination.slice(0, 8)}`,
    net_margin: r.net_margin,
    status: r.status_near,
  }))
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 130, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 10, fill: '#888' }}
          tickFormatter={v => `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}`} />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: '#aaa' }} width={130} />
        <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 11 }}
          formatter={(v) => [`${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(3)} $/bbl`, 'Net margin']} />
        <ReferenceLine x={0} stroke="#666" />
        <Bar dataKey="net_margin">
          {data.map((entry, i) => <Cell key={i} fill={STATUS_COLOR[entry.status] ?? '#888'} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function CostBreakdown({ route }: { route: RouteResult }) {
  const items = [
    { name: 'Freight', value: route.freight },
    { name: 'Port', value: route.port_cost },
    { name: 'Finance', value: route.finance_cost },
    { name: 'Insurance', value: route.insurance_cost },
  ]
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground font-mono">{route.origin} → {route.destination}</p>
      {items.map(d => (
        <div key={d.name} className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground w-20">{d.name}</span>
          <div className="flex-1 bg-secondary/40 rounded h-3 overflow-hidden">
            <div className="h-3 bg-muted-foreground rounded"
              style={{ width: `${Math.min(d.value / route.total_cost * 100, 100)}%` }} />
          </div>
          <span className="text-xs font-mono w-14 text-right">${fmtu(d.value, 3)}</span>
        </div>
      ))}
      <div className="flex justify-between text-xs font-mono border-t border-border pt-1">
        <span className="text-muted-foreground">Total cost</span><span>${fmtu(route.total_cost)}</span>
      </div>
      <div className="flex justify-between text-xs font-mono font-semibold">
        <span>Net margin</span>
        <span style={{ color: STATUS_COLOR[route.status_near] }}>{fmt(route.net_margin)}</span>
      </div>
    </div>
  )
}

function HistChart({ data, routes }: { data: RoutesResponse['hist_series']; routes: RouteResult[] }) {
  if (!data?.length) return null
  const COLORS = ['#f97316', '#6366f1', '#22c55e', '#eab308', '#a855f7', '#ec4899', '#14b8a6', '#f59e0b']
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#888' }}
          tickFormatter={d => String(d).slice(0, 7)} interval={12} />
        <YAxis tick={{ fontSize: 10, fill: '#888' }} width={50} />
        <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 10 }}
          formatter={(v) => [`${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)} $/bbl`]} />
        <Legend wrapperStyle={{ fontSize: 10 }} />
        <ReferenceLine y={0} stroke="#555" strokeDasharray="2 2" />
        {routes.map((r, i) => (
          <Line key={r.id} dataKey={r.id} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.2}
            name={`${r.origin.slice(0, 8)}→${r.destination.slice(0, 8)}`} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

function RoutesPage() {
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null)
  const { data, isLoading, isError } = useRoutes()
  const selected = data?.routes.find(r => r.id === selectedRoute) ?? data?.routes[0] ?? null

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Crude / Products Transport Arbitrage</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Forward-adjusted net margin for 8 origin-destination routes. Uses destination forward price
          at discharge date (not spot), so backwardation appears as a cost, contango as a tailwind.
        </p>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground text-sm py-8">
          <Loader2 size={14} className="animate-spin" /> Loading route matrix…
        </div>
      )}
      {isError && <p className="text-destructive text-sm">Failed to load routes data.</p>}

      {data && (
        <>
          <BwetBanner bwet={data.bwet} />

          {/* Summary strip */}
          <div className="flex gap-4 text-sm font-mono items-center">
            <span className="text-green-400">{data.n_open} open</span>
            <span className="text-yellow-400">{data.n_near} near</span>
            <span className="text-muted-foreground">{data.n_closed} closed</span>
            <span className="text-xs text-muted-foreground ml-2">as of {data.as_of}</span>
            <div className="ml-auto flex gap-4 text-xs text-muted-foreground">
              {Object.entries(data.spots).map(([p, v]) => (
                <span key={p}><span className="text-foreground font-mono">${fmtu(v, 2)}</span> {p}</span>
              ))}
            </div>
          </div>

          {/* O×D heatmap */}
          {(data.matrix_origins?.length ?? 0) > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Origin × Destination matrix (net $/bbl)</CardTitle>
              </CardHeader>
              <CardContent>
                <ArbHeatmap matrix={data.matrix} origins={data.matrix_origins} destinations={data.matrix_destinations} />
              </CardContent>
            </Card>
          )}

          {/* Bar + cost breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Net margin by route ($/bbl)</CardTitle>
              </CardHeader>
              <CardContent>
                <MarginBar routes={data.routes} />
              </CardContent>
            </Card>
            {selected && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Cost breakdown</CardTitle>
                  <div className="flex gap-1 flex-wrap mt-2">
                    {data.routes.map(r => (
                      <button key={r.id} type="button"
                        onClick={() => setSelectedRoute(r.id)}
                        className={`px-2 py-0.5 text-[10px] rounded border transition-colors ${
                          r.id === (selectedRoute ?? data.routes[0]?.id)
                            ? 'border-primary bg-primary/10 text-foreground'
                            : 'border-border text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        {r.origin.slice(0, 6)}→{r.destination.slice(0, 6)}
                      </button>
                    ))}
                  </div>
                </CardHeader>
                <CardContent>
                  <CostBreakdown route={selected} />
                </CardContent>
              </Card>
            )}
          </div>

          {/* Route table */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Route details</CardTitle>
            </CardHeader>
            <CardContent>
              <RouteTable routes={data.routes} />
            </CardContent>
          </Card>

          {/* Historical chart */}
          {(data.hist_series?.length ?? 0) > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Weekly net margin history ($/bbl)</CardTitle>
              </CardHeader>
              <CardContent>
                <HistChart data={data.hist_series} routes={data.routes} />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
