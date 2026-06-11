import { Suspense, lazy } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useDispersion, useDispersionLive } from '@/lib/api'

export const Route = createFileRoute('/dispersion')({ component: DispersionPage })

const LiveDispersionChart = lazy(() =>
  import('./DispersionCharts').then((m) => ({ default: m.LiveDispersionChart }))
)
const EquityChart = lazy(() =>
  import('./DispersionCharts').then((m) => ({ default: m.EquityChart }))
)
const DispersionOverlayChart = lazy(() =>
  import('./DispersionCharts').then((m) => ({ default: m.DispersionOverlayChart }))
)

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

function ChartSkeleton({ height = 220 }: { height?: number }) {
  return <div style={{ height }} className="animate-pulse rounded bg-muted/40" />
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

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Live AIS fleet dispersion</CardTitle>
        </CardHeader>
        <CardContent>
          {liveLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
              <Loader2 size={14} className="animate-spin" /> Loading live dispersion...
            </div>
          ) : (
            <Suspense fallback={<ChartSkeleton />}>
              <LiveDispersionChart rows={live ?? []} />
            </Suspense>
          )}
          <p className="text-xs text-muted-foreground mt-2">
            Dispersion = mean great-circle distance (nm) of each vessel from the fleet centroid.
            High = fleet spread out; low = concentrated. Refreshed daily by the ais-collector service.
          </p>
        </CardContent>
      </Card>

      <div>
        <h2 className="text-sm font-semibold mb-3">Capesize 5TC FFA backtest: mean reversion strategy</h2>
        {btLoading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
            <Loader2 size={14} className="animate-spin" /> Loading backtest...
          </div>
        )}
        {btError && <p className="text-destructive text-sm">Failed to load backtest data.</p>}
        {backtest && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard label="Sharpe ratio" value={backtest.stats.sharpe.toFixed(2)} sub={`${backtest.stats.n_years.toFixed(0)}y OOS`} />
              <StatCard label="Ann. return" value={`$${(backtest.stats.ann_return / 1000).toFixed(0)}k/unit-yr`} sub="$/day per unit position" />
              <StatCard label="Max drawdown" value={`$${(backtest.stats.max_drawdown / 1000).toFixed(0)}k`} />
              <StatCard label="Hit rate" value={`${(backtest.stats.hit_rate * 100).toFixed(0)}%`} sub={`${backtest.stats.n_trades} trades`} />
            </div>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Cumulative P&L ($/day-unit, net of costs)</CardTitle>
              </CardHeader>
              <CardContent>
                <Suspense fallback={<ChartSkeleton height={200} />}>
                  <EquityChart equity={backtest.equity} />
                </Suspense>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">5TC FFA price vs fleet dispersion (the traded signal)</CardTitle>
              </CardHeader>
              <CardContent>
                <Suspense fallback={<ChartSkeleton height={200} />}>
                  <DispersionOverlayChart price5tc={backtest.price_5tc} avgDisp={backtest.avg_dispersion} />
                </Suspense>
                <p className="text-xs text-muted-foreground mt-2">
                  Strategy: fade dispersion extremes vs 120d mean (mean reversion).
                  High dispersion = fleet spread, easy to match cargo - bearish. Low = concentrated - bullish.
                </p>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
