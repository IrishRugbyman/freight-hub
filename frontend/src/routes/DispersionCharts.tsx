import {
  LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import type { AisDispersionRow } from '@/lib/api'

export function LiveDispersionChart({ rows }: { rows: AisDispersionRow[] }) {
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        No live AIS dispersion data yet. The collector accumulates this daily.
      </p>
    )
  }

  const segments = [...new Set(rows.map((r) => r.segment))].sort()
  type ChartRow = { date: string; [key: string]: string | number }
  const byDate = new Map<string, ChartRow>()
  for (const row of rows) {
    if (!byDate.has(row.date)) byDate.set(row.date, { date: row.date })
    const entry = byDate.get(row.date)!
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
          tickFormatter={(d) => String(d).slice(0, 7)} interval={Math.floor(data.length / 8)} />
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

export function EquityChart({ equity }: { equity: { date: string; value: number }[] }) {
  if (!equity.length) return null
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={equity} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#888' }}
          tickFormatter={(d) => String(d).slice(0, 7)} interval={Math.floor(equity.length / 8)} />
        <YAxis tick={{ fontSize: 10, fill: '#888' }} width={60}
          tickFormatter={(v) => `${Number(v) > 0 ? '+' : ''}${(Number(v) / 1000).toFixed(0)}k`} />
        <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333', fontSize: 10 }}
          formatter={(v) => [`${Number(v) > 0 ? '+' : ''}$${Number(v).toFixed(0)}/day-unit`, 'Cum. P&L']} />
        <ReferenceLine y={0} stroke="#555" strokeDasharray="2 2" />
        <Line dataKey="value" stroke="#6366f1" dot={false} strokeWidth={1.5} name="Equity" />
      </LineChart>
    </ResponsiveContainer>
  )
}

export function DispersionOverlayChart({
  price5tc, avgDisp,
}: {
  price5tc: { date: string; value: number }[]
  avgDisp: { date: string; value: number }[]
}) {
  const priceMap = new Map(price5tc.map((p) => [p.date, p.value]))
  const data = avgDisp.map((d) => ({
    date: d.date,
    dispersion: d.value,
    price_5tc: priceMap.get(d.date) ?? null,
  }))
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 4, right: 40, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#222" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#888' }}
          tickFormatter={(d) => String(d).slice(0, 7)} interval={Math.floor(data.length / 8)} />
        <YAxis yAxisId="left" tick={{ fontSize: 10, fill: '#888' }} width={60}
          tickFormatter={(v) => `${(Number(v) / 1000).toFixed(0)}k`}
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
