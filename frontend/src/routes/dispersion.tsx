import { createFileRoute } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import {
  LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useDispersion, useDispersionLive, type AisDispersionRow } from '@/lib/api'

export const Route = createFileRoute('/dispersion')({ component: DispersionPage })

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-5 pb-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-xl font-mono font-semibold mt-0.5">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  )
}

function LiveDispersionChart({ rows }: { rows: AisDispersionRow[] }) {
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        No live AIS dispersion data yet. The collector accumulates this daily.
      </p>
    )
  }

  // Build a date-keyed object per segment for recharts
  const segments = [...new Set(rows.map(r => r.segment))].sort()
  type ChartRow = { date: string; [key: string]: string | number }
  const byDate = new Map<string, ChartRow>()
  for (const row of rows) {
    const d = row.date
    if (!byDate.has(d)) byDate.set(d, { date: d })
    const entry = byDate.get(d)!
    entry[row.segment] = row.dispersion_nm
    entry[`${row.segment}_count`] = row.vessel_count
  }
  const data = [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))

  const COLORS = ['#6366f1', '#f97316', '#22c55e', '#eab308']

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#888' }}
          tickFormatter={d => String(d).slice(0, 7)} interval={Math.floor(data.length / 8)} />
        <YAxis tick={{ fontSize: 10, fill: '#888' }} width={55}
          label={{ value: 'nm', angle: -90, position: 'insideLeft', style: { fontSize: 9, fill: '#666' } }} />
        <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 10 }}
          formatter={(v, name) => [`${Number(v).toFixed(0)} nm`, name]} />
        <Legend wrapperStyle={{ fontSize: 10 }} />
        {segments.map((seg, i) => (
          <Line key={seg} dataKey={seg} stroke={COLORS[i % COLORS.length]}
            dot={false} strokeWidth={1.5} connectNulls name={seg} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

function EquityChart({ equity }: { equity: { date: string; value: number }[] }) {
  if (!equity.length) return null
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={equity} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#888' }}
          tickFormatter={d => String(d).slice(0, 7)} interval={Math.floor(equity.length / 8)} />
        <YAxis tick={{ fontSize: 10, fill: '#888' }} width={60}
          tickFormatter={v => `${Number(v) > 0 ? '+' : ''}${(Number(v) / 1000).toFixed(0)}k`} />
        <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 10 }}
          formatter={(v) => [`${Number(v) > 0 ? '+' : ''}$${Number(v).toFixed(0)}/day-unit`, 'Cum. P&L']} />
        <ReferenceLine y={0} stroke="#555" strokeDasharray="2 2" />
        <Line dataKey="value" stroke="#6366f1" dot={false} strokeWidth={1.5} name="Equity" />
      </LineChart>
    </ResponsiveContainer>
  )
}

function DispersionOverlayChart({
  price5tc, avgDisp,
}: {
  price5tc: { date: string; value: number }[]
  avgDisp: { date: string; value: number }[]
}) {
  const priceMap = new Map(price5tc.map(p => [p.date, p.value]))
  const data = avgDisp.map(d => ({
    date: d.date,
    dispersion: d.value,
    price_5tc: priceMap.get(d.date) ?? null,
  }))
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 4, right: 40, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#888' }}
          tickFormatter={d => String(d).slice(0, 7)} interval={Math.floor(data.length / 8)} />
        <YAxis yAxisId="left" tick={{ fontSize: 10, fill: '#888' }} width={60}
          tickFormatter={v => `${(Number(v) / 1000).toFixed(0)}k`}
          label={{ value: '$/day', angle: -90, position: 'insideLeft', style: { fontSize: 9, fill: '#666' } }} />
        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: '#888' }} width={50}
          label={{ value: 'nm', angle: 90, position: 'insideRight', style: { fontSize: 9, fill: '#666' } }} />
        <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 10 }} />
        <Legend wrapperStyle={{ fontSize: 10 }} />
        <Line yAxisId="left" dataKey="price_5tc" stroke="#f97316" dot={false} strokeWidth={1.2} name="5TC FFA $/day" connectNulls />
        <Line yAxisId="right" dataKey="dispersion" stroke="#22c55e" dot={false} strokeWidth={1.2} name="Avg dispersion (nm)" connectNulls />
      </LineChart>
    </ResponsiveContainer>
  )
}

function DispersionPage() {
  const { data: live, isLoading: liveLoading } = useDispersionLive()
  const { data: backtest, isLoading: btLoading, isError: btError } = useDispersion()

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Fleet Dispersion</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Geographic dispersion of the tracked fleet (mean nm from fleet centroid) and a
          Capesize 5TC FFA backtest that trades freight rates against fleet concentration signals.
        </p>
      </div>

      {/* Live AIS section */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Live AIS fleet dispersion</CardTitle>
        </CardHeader>
        <CardContent>
          {liveLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
              <Loader2 size={14} className="animate-spin" /> Loading live dispersion…
            </div>
          ) : (
            <LiveDispersionChart rows={live ?? []} />
          )}
          <p className="text-xs text-muted-foreground mt-2">
            Dispersion = mean great-circle distance (nm) of each vessel from the fleet centroid.
            High = fleet spread out; low = concentrated. Self-consistent AIS measure, not a paid feed.
            Refreshed daily by the ais-collector service.
          </p>
        </CardContent>
      </Card>

      {/* Backtest section */}
      <div>
        <h2 className="text-sm font-semibold mb-3">Capesize 5TC FFA backtest: mean reversion strategy</h2>
        {btLoading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
            <Loader2 size={14} className="animate-spin" /> Loading backtest…
          </div>
        )}
        {btError && <p className="text-destructive text-sm">Failed to load backtest data.</p>}
        {backtest && (
          <div className="space-y-4">
            {/* Stat cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard label="Sharpe ratio" value={backtest.stats.sharpe.toFixed(2)} sub={`${backtest.stats.n_years.toFixed(0)}y OOS`} />
              <StatCard label="Ann. return" value={`$${(backtest.stats.ann_return / 1000).toFixed(0)}k/unit-yr`} sub="$/day per unit position" />
              <StatCard label="Max drawdown" value={`$${(backtest.stats.max_drawdown / 1000).toFixed(0)}k`} />
              <StatCard label="Hit rate" value={`${(backtest.stats.hit_rate * 100).toFixed(0)}%`} sub={`${backtest.stats.n_trades} trades`} />
            </div>

            {/* Equity curve */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Cumulative P&L ($/day-unit, net of costs)</CardTitle>
              </CardHeader>
              <CardContent>
                <EquityChart equity={backtest.equity} />
              </CardContent>
            </Card>

            {/* Dispersion vs price overlay */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">5TC FFA price vs fleet dispersion (the traded signal)</CardTitle>
              </CardHeader>
              <CardContent>
                <DispersionOverlayChart price5tc={backtest.price_5tc} avgDisp={backtest.avg_dispersion} />
                <p className="text-xs text-muted-foreground mt-2">
                  Strategy: fade dispersion extremes vs 120d mean (mean reversion).
                  High dispersion = fleet spread, easy to match cargo → bearish. Low = concentrated → bullish.
                  Data: Capesize 5TC C+1MON FFA + historical fleet dispersion (Capesize+VLOC, seeded one-time from paid feed).
                </p>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
