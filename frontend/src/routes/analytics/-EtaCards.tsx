// True ETA cards: the accuracy scoreboard and per-target accuracy.
import React from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useEtaAccuracy, useEtaByTarget, useEtaTrend, type EtaByTargetRow, type EtaLeadBasis,
} from '@/lib/api'
import { EmptyState, ChartSkeleton, TOOLTIP_STYLE, LEGEND_STYLE } from './-analyticsShared'

// ---------------------------------------------------------------------------
// True ETA accuracy scoreboard (Phase F) - the credibility centerpiece.
// ---------------------------------------------------------------------------

// Model display + chart color. Baseline -> routed -> physics, cool to warm.
const ETA_MODEL_META: Record<string, { label: string; hex: string }> = {
  naive: { label: 'Naive (gc / SOG)', hex: '#a1a1aa' },        // zinc
  'naive+route': { label: '+ Sea route', hex: '#fbbf24' },     // amber
  physics_v1: { label: 'Physics (+ speed, staging)', hex: '#38bdf8' }, // sky
  ml: { label: 'ML (gated)', hex: '#a78bfa' },                 // violet
}

export function EtaAccuracyCard() {
  const [targetType, setTargetType] = React.useState<'all' | 'port' | 'chokepoint'>('all')
  const [leadBasis, setLeadBasis] = React.useState<EtaLeadBasis>('actual')
  const { data, isLoading } = useEtaAccuracy(targetType, leadBasis)
  const { data: trendData } = useEtaTrend()

  // Sample count trend from the run history (shows data flywheel growing toward ML gate)
  const trendPoints = React.useMemo(() => {
    if (!trendData?.points?.length) return []
    // Deduplicate by date (keep last point per day for a cleaner chart)
    const byDate = new Map<string, typeof trendData.points[0]>()
    for (const p of trendData.points) {
      const day = p.run_ts.slice(0, 10)
      byDate.set(day, p)
    }
    return Array.from(byDate.values()).map(p => ({
      date: p.run_ts.slice(0, 10),
      naive: p.naive_mae != null ? parseFloat(p.naive_mae.toFixed(2)) : null,
      physics: p.physics_mae != null ? parseFloat(p.physics_mae.toFixed(2)) : null,
      n: p.n,
    }))
  }, [trendData])

  const chartData = React.useMemo(() => {
    if (!data) return []
    const buckets = data.lead_order.filter(b => b !== 'all')
    return buckets.map(bucket => {
      const row: Record<string, number | string> = { bucket }
      for (const m of data.models) {
        const cell = data.rows.find(r => r.model === m && r.lead_bucket === bucket)
        if (cell?.med_abs_err_h != null) row[m] = Number(cell.med_abs_err_h.toFixed(2))
      }
      return row
    })
  }, [data])

  const rollup = React.useMemo(
    () => (data?.models ?? []).map(m => ({
      model: m,
      cell: data?.rows.find(r => r.model === m && r.lead_bucket === 'all'),
    })),
    [data],
  )

  // Calibration chart: P10-P90 interval coverage for physics_v1 per lead bucket.
  // Target is 80%. Bars colored by how far they deviate from target.
  const calibrationData = React.useMemo(() => {
    if (!data) return []
    const buckets = data.lead_order.filter(b => b !== 'all')
    return buckets.map(bucket => {
      const row = data.rows.find(
        r => r.model === 'physics_v1' && r.lead_bucket === bucket && r.target_type === targetType
      )
      const cov = row?.interval_coverage != null ? row.interval_coverage * 100 : null
      return { bucket, coverage: cov }
    }).filter(d => d.coverage != null)
  }, [data])

  if (isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm">True ETA Accuracy</CardTitle></CardHeader>
        <CardContent><ChartSkeleton /></CardContent>
      </Card>
    )
  }

  if (!data || data.rows.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm">True ETA Accuracy</CardTitle></CardHeader>
        <CardContent>
          <EmptyState message="No backtest metrics yet - the scoreboard populates once the analytics job has scored arrivals." />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm">True ETA Accuracy</CardTitle>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Leakage-free backtest (voyage-grouped split) over reconstructed AIS arrivals: median absolute
              error per model, bucketed by {leadBasis === 'actual' ? 'actual lead time' : 'the model’s own predicted lead'}.
              Lower is better; physics is the shipping champion.
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            {data.run_ts && (
              <span className="text-[10px] text-muted-foreground/50">
                scored {new Date(data.run_ts).toLocaleDateString()}
              </span>
            )}
            <div className="flex gap-1">
              {(['all', 'port', 'chokepoint'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setTargetType(t)}
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    targetType === t
                      ? 'bg-primary/20 text-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {t === 'all' ? 'All' : t === 'port' ? 'Ports' : 'Chokepoints'}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[9px] uppercase tracking-wide text-muted-foreground/50">bucket by</span>
              {(['actual', 'predicted'] as const).map(b => (
                <button
                  key={b}
                  onClick={() => setLeadBasis(b)}
                  title={
                    b === 'actual'
                      ? 'Bucket each sample by its true remaining time (original framing)'
                      : 'Bucket each sample by the model’s own served ETA (what a user sees at decision time)'
                  }
                  className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                    leadBasis === b
                      ? 'bg-primary/20 text-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {b === 'actual' ? 'Actual lead' : 'Predicted lead'}
                </button>
              ))}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {(data.drift?.length ?? 0) > 0 && (
          <div className="mb-3 space-y-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2">
            <div className="flex items-center gap-1.5 text-xs font-medium text-amber-300">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
              Drift watch: champion accuracy degraded
            </div>
            {data.drift!.map(a => (
              <p key={a.kind} className="text-[11px] text-amber-200/80">
                {a.detail}
              </p>
            ))}
          </div>
        )}
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 8, left: -8, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="bucket" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
              <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))"
                label={{ value: 'median |err| (h)', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: 'hsl(var(--muted-foreground))' } }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(val, name) => [`${val}h`, ETA_MODEL_META[String(name)]?.label ?? String(name)]} />
              <Legend wrapperStyle={LEGEND_STYLE} formatter={name => ETA_MODEL_META[String(name)]?.label ?? String(name)} />
              {data.models.map(m => (
                <Bar key={m} dataKey={m} fill={ETA_MODEL_META[m]?.hex ?? '#888'} radius={[2, 2, 0, 0]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/60">
          {leadBasis === 'actual'
            ? 'Bucketed by actual remaining time: long-lead bias looks large because conditioning a signed error on the true outcome pulls it negative (regression to the mean). Switch to “Predicted lead” for the decision-time view.'
            : 'Bucketed by the model’s own served ETA: this is what a user knows at decision time. The gradient reverses vs “Actual lead” — both are selection effects, so read the unconditional bias in the table below as the headline number.'}
        </p>

        {calibrationData.length > 0 && (
          <div className="mt-4">
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              Physics P10-P90 interval calibration
              <span className="ml-1.5 font-normal text-muted-foreground/70">— target 80%; over-wide = wastes precision, under-narrow = surprises</span>
            </p>
            <div className="h-36">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={calibrationData} margin={{ top: 4, right: 8, left: -8, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                  <XAxis dataKey="bucket" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" />
                  <YAxis
                    tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))"
                    domain={[0, 100]}
                    tickFormatter={v => `${v}%`}
                    width={36}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(val) => [`${Number(val).toFixed(1)}%`, 'Actual coverage']}
                  />
                  <ReferenceLine y={80} stroke="#38bdf8" strokeDasharray="4 2" strokeWidth={1.5} label={{ value: '80% target', fontSize: 9, fill: '#38bdf8', position: 'insideTopRight' }} />
                  <Bar dataKey="coverage" radius={[2, 2, 0, 0]}>
                    {calibrationData.map((d, i) => {
                      const v = d.coverage as number
                      const fill = v < 70 ? '#ef4444' : v > 90 ? '#f59e0b' : '#22c55e'
                      return <Cell key={i} fill={fill} opacity={0.75} />
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-0.5 text-[10px] text-muted-foreground/50">
              Green = well-calibrated (70-90%). Red = under-confident (intervals too narrow). Amber = over-conservative (intervals too wide).
            </p>
          </div>
        )}

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 pr-3 font-medium">Model (overall)</th>
                <th className="py-1 pr-3 text-right font-medium">median |err|</th>
                <th className="py-1 pr-3 text-right font-medium">bias</th>
                <th className="py-1 pr-3 text-right font-medium">P90 |err|</th>
                <th className="py-1 pr-3 text-right font-medium">80% coverage</th>
                <th className="py-1 text-right font-medium">n</th>
              </tr>
            </thead>
            <tbody>
              {rollup.map(({ model, cell }) => (
                <tr key={model} className="border-t border-border/50">
                  <td className="py-1 pr-3">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: ETA_MODEL_META[model]?.hex ?? '#888' }} />
                      {ETA_MODEL_META[model]?.label ?? model}
                    </span>
                  </td>
                  <td className="py-1 pr-3 text-right font-semibold text-foreground/90">{cell?.med_abs_err_h != null ? `${cell.med_abs_err_h.toFixed(2)}h` : '-'}</td>
                  <td className="py-1 pr-3 text-right text-muted-foreground">{cell?.bias_h != null ? `${cell.bias_h > 0 ? '+' : ''}${cell.bias_h.toFixed(1)}h` : '-'}</td>
                  <td className="py-1 pr-3 text-right text-muted-foreground">{cell?.p90_abs_err_h != null ? `${cell.p90_abs_err_h.toFixed(1)}h` : '-'}</td>
                  <td className="py-1 pr-3 text-right text-muted-foreground">{cell?.interval_coverage != null ? `${(cell.interval_coverage * 100).toFixed(0)}%` : '-'}</td>
                  <td className="py-1 text-right text-muted-foreground/60">{cell?.n != null ? cell.n.toLocaleString() : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {trendPoints.length >= 3 && (
          <div className="mt-4">
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              Accuracy trend over time
              <span className="ml-1.5 font-normal text-muted-foreground/70">
                — overall MAE per build run (data flywheel toward ML unlock)
              </span>
            </p>
            <div className="h-32">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trendPoints} margin={{ top: 4, right: 8, left: -8, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 9 }} stroke="hsl(var(--muted-foreground))" />
                  <YAxis tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" tickFormatter={v => `${v}h`} width={34} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: unknown) => [`${Number(v).toFixed(2)}h`, '']} />
                  <Line type="monotone" dataKey="naive" stroke="#475569" strokeWidth={1.5} dot={false} name="Naive" />
                  <Line type="monotone" dataKey="physics" stroke="#38bdf8" strokeWidth={1.5} dot={false} name="Physics" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-0.5 text-[10px] text-muted-foreground/50">
              Each point is one hourly analytics build. Slate = naive baseline, blue = physics v1.
              Stable or declining MAE confirms the model is not degrading as the dataset grows.
            </p>
          </div>
        )}
        {trendPoints.length >= 2 && (() => {
          const firstDate = trendPoints[0].date
          const lastDate = trendPoints[trendPoints.length - 1].date
          const msPerDay = 86400_000
          const daysCollected = Math.round(
            (new Date(lastDate).getTime() - new Date(firstDate).getTime()) / msPerDay
          ) + 1
          const ML_GATE_DAYS = 56
          const pct = Math.min(100, (daysCollected / ML_GATE_DAYS) * 100)
          return (
            <div className="mt-3 rounded border border-border/60 bg-muted/20 px-3 py-2">
              <div className="flex items-center justify-between text-[10px]">
                <span className="font-medium text-muted-foreground">
                  Phase D (ML ETA) unlock progress
                </span>
                <span className="tabular-nums text-muted-foreground/70">
                  {daysCollected} / {ML_GATE_DAYS} days
                </span>
              </div>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-sky-500 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <p className="mt-1 text-[10px] text-muted-foreground/50">
                Physics carries production until ML earns its gate. LightGBM quantile regression
                trains on eta_samples when 8+ weeks of clean arrivals are accumulated.
              </p>
            </div>
          )
        })()}
        <p className="mt-2 text-[10px] text-muted-foreground/50">
          History starts at the collection date and cannot be backfilled; the learned (ML) model is gated until enough clean history accrues, so physics carries production today. A drift watch re-checks the champion every run and flags coverage or median-error regressions here.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// ETA per-target accuracy breakdown
// ---------------------------------------------------------------------------
const TYPE_COLOR: Record<string, string> = {
  chokepoint: '#38bdf8',
  port: '#a78bfa',
}

export function EtaByTargetCard() {
  const { data, isLoading } = useEtaByTarget()
  const [showAll, setShowAll] = React.useState(false)
  const [targetFilter, setTargetFilter] = React.useState<'all' | 'port' | 'chokepoint'>('all')

  const rows = React.useMemo<EtaByTargetRow[]>(() => {
    if (!data?.rows?.length) return []
    const filtered = targetFilter === 'all' ? data.rows : data.rows.filter(r => r.target_type === targetFilter)
    return filtered
  }, [data, targetFilter])

  const displayRows = showAll ? rows : rows.slice(0, 12)

  const chartData = React.useMemo(() => {
    const top = rows.slice(0, 15)
    return top.map(r => ({
      name: r.name.length > 14 ? r.name.slice(0, 13) + '…' : r.name,
      physics: r.med_abs_err_h != null ? parseFloat(r.med_abs_err_h.toFixed(2)) : null,
      naive: r.naive_med_abs_err_h != null ? parseFloat(r.naive_med_abs_err_h.toFixed(2)) : null,
      type: r.target_type,
    }))
  }, [rows])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-semibold">
          Physics ETA accuracy by target
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            physics_v1 vs naive baseline, median absolute error (hours), sorted best to worst
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <ChartSkeleton className="h-48" />
        ) : !data?.rows?.length ? (
          <EmptyState message="Per-target accuracy data not yet available - populates after the next analytics build." />
        ) : (
          <>
            <div className="mb-3 flex gap-2">
              {(['all', 'port', 'chokepoint'] as const).map(f => (
                <button
                  key={f}
                  onClick={() => setTargetFilter(f)}
                  className={`rounded px-2 py-0.5 text-xs ${
                    targetFilter === f
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-muted/80'
                  }`}
                >
                  {f === 'all' ? 'All' : f === 'port' ? 'Ports' : 'Chokepoints'}
                </button>
              ))}
              <span className="ml-auto text-xs text-muted-foreground/60">{rows.length} targets</span>
            </div>

            {chartData.length > 0 && (
              <div className="mb-4 h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 4, right: 8, left: -10, bottom: 40 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                    <XAxis
                      dataKey="name"
                      tick={{ fontSize: 9 }}
                      stroke="hsl(var(--muted-foreground))"
                      angle={-40}
                      textAnchor="end"
                      interval={0}
                    />
                    <YAxis
                      tick={{ fontSize: 10 }}
                      stroke="hsl(var(--muted-foreground))"
                      tickFormatter={v => `${v}h`}
                      width={36}
                    />
                    <Tooltip
                      contentStyle={TOOLTIP_STYLE}
                      formatter={(val: unknown, name: unknown) => [
                        `${Number(val).toFixed(1)}h`,
                        name === 'physics' ? 'Physics MAE' : 'Naive MAE',
                      ]}
                    />
                    <Legend
                      wrapperStyle={LEGEND_STYLE}
                      formatter={(v: string) => v === 'physics' ? 'Physics v1' : 'Naive baseline'}
                    />
                    <Bar dataKey="naive" fill="#475569" opacity={0.45} radius={[1, 1, 0, 0]} name="naive" />
                    <Bar dataKey="physics" radius={[2, 2, 0, 0]} name="physics">
                      {chartData.map((d, i) => (
                        <Cell
                          key={i}
                          fill={TYPE_COLOR[d.type] ?? '#64748b'}
                          opacity={0.8}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="py-1 pr-3 text-left font-medium">Target</th>
                    <th className="py-1 pr-3 text-left font-medium">Type</th>
                    <th className="py-1 pr-3 text-right font-medium">n</th>
                    <th className="py-1 pr-3 text-right font-medium">Physics MAE</th>
                    <th className="py-1 pr-3 text-right font-medium">Naive MAE</th>
                    <th className="py-1 pr-3 text-right font-medium">Improvement</th>
                    <th className="py-1 pr-3 text-right font-medium">Bias</th>
                    <th className="py-1 text-right font-medium">P90</th>
                  </tr>
                </thead>
                <tbody>
                  {displayRows.map((r, i) => {
                    const improvement =
                      r.naive_med_abs_err_h != null && r.med_abs_err_h != null
                        ? ((r.naive_med_abs_err_h - r.med_abs_err_h) / r.naive_med_abs_err_h) * 100
                        : null
                    const impColor =
                      improvement == null ? '' : improvement > 10 ? 'text-emerald-400' : improvement < 0 ? 'text-red-400' : 'text-muted-foreground'
                    return (
                      <tr key={i} className="border-b border-border/40 hover:bg-muted/30">
                        <td className="py-1 pr-3 font-medium" style={{ color: TYPE_COLOR[r.target_type] }}>
                          {r.name}
                        </td>
                        <td className="py-1 pr-3 text-muted-foreground capitalize">{r.target_type}</td>
                        <td className="py-1 pr-3 text-right tabular-nums">{r.n.toLocaleString()}</td>
                        <td className="py-1 pr-3 text-right tabular-nums">
                          {r.med_abs_err_h != null ? `${r.med_abs_err_h.toFixed(1)}h` : '-'}
                        </td>
                        <td className="py-1 pr-3 text-right tabular-nums text-muted-foreground">
                          {r.naive_med_abs_err_h != null ? `${r.naive_med_abs_err_h.toFixed(1)}h` : '-'}
                        </td>
                        <td className={`py-1 pr-3 text-right tabular-nums font-medium ${impColor}`}>
                          {improvement != null ? `${improvement > 0 ? '+' : ''}${improvement.toFixed(0)}%` : '-'}
                        </td>
                        <td className={`py-1 pr-3 text-right tabular-nums ${r.bias_h != null && r.bias_h > 0 ? 'text-amber-400' : r.bias_h != null && r.bias_h < -0.5 ? 'text-sky-400' : ''}`}>
                          {r.bias_h != null ? `${r.bias_h > 0 ? '+' : ''}${r.bias_h.toFixed(1)}h` : '-'}
                        </td>
                        <td className="py-1 text-right tabular-nums text-muted-foreground">
                          {r.p90_abs_err_h != null ? `${r.p90_abs_err_h.toFixed(1)}h` : '-'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {rows.length > 12 && (
              <button
                onClick={() => setShowAll(v => !v)}
                className="mt-2 text-xs text-muted-foreground hover:text-foreground"
              >
                {showAll ? 'Show fewer' : `Show all ${rows.length} targets`}
              </button>
            )}
            <p className="mt-2 text-[10px] text-muted-foreground/50">
              Sorted by physics MAE (ascending). Blue = chokepoint, purple = port.
              Improvement = (naive MAE - physics MAE) / naive MAE. Negative means physics underperforms naive at that target.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
