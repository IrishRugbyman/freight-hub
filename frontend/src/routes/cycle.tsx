import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { AlertTriangle, ExternalLink, Loader2 } from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  useCycleSeries,
  useCycleSignals,
  useCycleSubsectors,
  type CycleSignal,
  type CycleSubsector,
} from '@/lib/api'
import {
  distanceLabel,
  formatValue,
  freshnessLabel,
  sortSignals,
  sparkPath,
  stateStyle,
  tierBadge,
} from '@/lib/cycle'

export const Route = createFileRoute('/cycle')({ component: CyclePage })

const SERIES_OPTIONS = [
  { id: 'BDI', label: 'Baltic Dry', threshold: 1500 },
  { id: 'BDTI', label: 'Dirty Tanker', threshold: 1000 },
  { id: 'BCTI', label: 'Clean Tanker', threshold: 800 },
  { id: 'BCI', label: 'Capesize', threshold: null },
  { id: 'BPI', label: 'Panamax', threshold: null },
]

// ---------------------------------------------------------------------------
// Small presentational pieces
// ---------------------------------------------------------------------------

function Badge({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${className}`}
    >
      {children}
    </span>
  )
}

function Spark({ values, state }: { values: number[]; state: string }) {
  const d = sparkPath(values, 120, 28)
  if (!d) return null
  const stroke =
    state === 'breached' ? '#f87171' : state === 'approaching' ? '#fbbf24' : '#34d399'
  return (
    <svg viewBox="0 0 120 28" className="h-7 w-[120px] shrink-0" preserveAspectRatio="none">
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function SignalCard({ signal }: { signal: CycleSignal }) {
  const style = stateStyle(signal.state)
  const tier = tierBadge(signal.tier)
  const fresh = freshnessLabel({ tier: signal.tier, stale: signal.stale, asOf: signal.as_of })
  const isGap = signal.tier === 'missing'

  return (
    <Card className={isGap ? 'border-dashed opacity-90' : undefined}>
      <CardContent className="space-y-3 pt-5 pb-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium leading-snug">{signal.label}</p>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {signal.category}
            </p>
          </div>
          <div className="flex shrink-0 gap-1">
            <Badge className={tier.className}>{tier.label}</Badge>
            <Badge className={style.className}>{style.label}</Badge>
          </div>
        </div>

        <div className="flex items-end justify-between gap-3">
          <div>
            <p className="font-mono text-2xl font-semibold leading-none">
              {formatValue(signal.value, signal.unit)}
            </p>
            {signal.value_note && (
              <p className="mt-1 text-xs text-muted-foreground">{signal.value_note}</p>
            )}
          </div>
          {signal.spark.length > 1 && <Spark values={signal.spark} state={signal.state} />}
        </div>

        {isGap ? (
          <p className="rounded border border-dashed border-border bg-muted/20 p-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Why this is empty: </span>
            {signal.gap_reason}
          </p>
        ) : (
          <div className="space-y-1 text-xs">
            <p className="text-muted-foreground">
              <span className="text-foreground">Threshold: </span>
              {signal.threshold_label}
            </p>
            {signal.distance_pct != null && (
              <p className={style.className.split(' ')[0]}>
                {distanceLabel(signal.distance_pct, signal.direction)}
              </p>
            )}
          </div>
        )}

        <dl className="space-y-1 border-t border-border pt-2 text-xs">
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-muted-foreground">Lag</dt>
            <dd>{signal.expected_lag}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-muted-foreground">Falsifier</dt>
            <dd>{signal.falsifier}</dd>
          </div>
        </dl>

        {signal.caveat && (
          <p className="flex gap-1.5 text-xs text-amber-400/90">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            {signal.caveat}
          </p>
        )}

        {signal.tier === 'registered' && !signal.verified && (
          <p className="flex gap-1.5 text-xs text-amber-400/90">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            Unverified against the primary source. {signal.provenance}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
          <span className={fresh.warn ? 'text-amber-400/90' : undefined}>{fresh.text}</span>
          <span>·</span>
          {signal.source_url ? (
            <a
              href={signal.source_url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1 underline decoration-dotted hover:text-foreground"
            >
              {signal.source_label}
              <ExternalLink size={10} />
            </a>
          ) : (
            <span>{signal.source_label}</span>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function SubsectorCard({ sub }: { sub: CycleSubsector }) {
  const headline = sub.headline
  const orderbook = sub.orderbook
  return (
    <Card>
      <CardContent className="space-y-3 pt-5 pb-4">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold">{sub.name}</h2>
          <span className="text-xs text-muted-foreground">{sub.stage}</span>
        </div>
        <div className="flex gap-6">
          <div>
            <p className="font-mono text-2xl font-semibold leading-none">
              {formatValue(headline?.value, headline?.unit ?? 'index')}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">{headline?.label ?? 'no rate'}</p>
          </div>
          <div>
            <p className="font-mono text-2xl font-semibold leading-none">
              {formatValue(orderbook?.value, orderbook?.unit ?? 'pct')}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">orderbook / fleet</p>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{sub.stage_note}</p>
        {sub.coverage_note && (
          <p className="border-t border-border pt-2 text-[11px] text-muted-foreground">
            {sub.coverage_note}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function SeriesChart() {
  const [selected, setSelected] = useState(SERIES_OPTIONS[0])
  const [years, setYears] = useState(5)
  const { data, isLoading } = useCycleSeries(selected.id, years)

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 pb-2">
        <CardTitle className="text-sm">{data?.label ?? selected.label}</CardTitle>
        <div className="flex flex-wrap gap-1">
          {SERIES_OPTIONS.map((opt) => (
            <button
              key={opt.id}
              onClick={() => setSelected(opt)}
              className={`rounded border px-2 py-0.5 text-xs ${
                opt.id === selected.id
                  ? 'border-foreground/40 bg-muted text-foreground'
                  : 'border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {opt.id}
            </button>
          ))}
          <span className="mx-1 text-muted-foreground">|</span>
          {[1, 5, 20].map((y) => (
            <button
              key={y}
              onClick={() => setYears(y)}
              className={`rounded border px-2 py-0.5 text-xs ${
                y === years
                  ? 'border-foreground/40 bg-muted text-foreground'
                  : 'border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {y}y
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-[260px] items-center gap-2 text-sm text-muted-foreground">
            <Loader2 size={14} className="animate-spin" /> Loading {selected.id}...
          </div>
        ) : !data?.points.length ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            No data for {selected.id}.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={data.points} margin={{ top: 5, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={60} />
              <YAxis tick={{ fontSize: 10 }} width={48} />
              <Tooltip
                contentStyle={{
                  background: 'var(--card)',
                  border: '1px solid var(--border)',
                  fontSize: 12,
                }}
              />
              {selected.threshold != null && (
                <ReferenceLine
                  y={selected.threshold}
                  stroke="#f87171"
                  strokeDasharray="4 4"
                  label={{
                    value: `threshold ${selected.threshold}`,
                    fontSize: 10,
                    fill: '#f87171',
                    position: 'insideTopRight',
                  }}
                />
              )}
              <Line
                type="monotone"
                dataKey="value"
                stroke="#38bdf8"
                strokeWidth={1.4}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
        <p className="mt-2 text-xs text-muted-foreground">{data?.source_label}</p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function CyclePage() {
  const { data: subs, isLoading: subsLoading } = useCycleSubsectors()
  const { data: sig, isLoading: sigLoading, isError } = useCycleSignals()

  const signals = sig?.signals ?? []
  const gaps = signals.filter((s) => s.tier === 'missing')
  const unverified = signals.filter((s) => s.tier === 'registered' && !s.verified)

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Freight Cycle</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Shipping is not one cycle. Container, dry bulk and tankers run on three clocks, and the
          variable that separates them is the orderbook, not the spot rate. Every tile below states
          the threshold that would change the read and the observation that would falsify it.
        </p>
      </div>

      {subsLoading ? (
        <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
          <Loader2 size={14} className="animate-spin" /> Loading the three clocks...
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-3">
          {subs?.subsectors.map((s) => <SubsectorCard key={s.id} sub={s} />)}
        </div>
      )}

      <SeriesChart />

      <div>
        <h2 className="mb-1 text-sm font-semibold">Signals</h2>
        <p className="mb-3 text-xs text-muted-foreground">
          Ordered by how close each one is to changing the read. Badges carry provenance:{' '}
          <span className="text-sky-300">Live</span> is computed from data this site ingests,{' '}
          <span className="text-violet-300">Recorded</span> is a disclosed observation entered by
          hand, <span className="text-muted-foreground">Gap</span> is something we cannot source.
        </p>
        {sigLoading && (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 size={14} className="animate-spin" /> Loading signals...
          </div>
        )}
        {isError && <p className="text-sm text-destructive">Failed to load the signal registry.</p>}
        <div className="grid gap-3 md:grid-cols-2">
          {sortSignals(signals).map((s) => (
            <SignalCard key={s.id} signal={s} />
          ))}
        </div>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">What this board cannot tell you</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs text-muted-foreground">
          <p>
            Published in full rather than buried: {gaps.length} signal
            {gaps.length === 1 ? '' : 's'} with no acceptable source, and {unverified.length}{' '}
            recorded observation{unverified.length === 1 ? '' : 's'} not yet checked against the
            primary.
          </p>
          <ul className="space-y-1">
            {gaps.map((g) => (
              <li key={g.id}>
                <span className="text-foreground">{g.label}:</span> {g.gap_reason}
              </li>
            ))}
            <li>
              <span className="text-foreground">Container rates and every orderbook figure:</span>{' '}
              SCFI, CCFI and Clarksons/Alphaliner orderbook data are sold, not published. They are
              hand-recorded observations, each read off a primary or trade-press source on a stated
              date, and they go visibly stale rather than quietly current.
            </li>
            <li>
              <span className="text-foreground">Fleet age is a proxy, not demolition:</span> we
              cannot see scrappings, only how much of the fleet has reached scrapping age - and only
              across the vessels our enrichment crawlers have reached, which is roughly a quarter of
              what we track and is crawl-order, not a random sample.
            </li>
            <li>
              <span className="text-foreground">Any &quot;versus 2023&quot; transit comparison:</span>{' '}
              our AIS collection began 2026-06-09, so no pre-crisis baseline exists here and none is
              invented. Our transit counts cover tankers and bulk carriers only, never containers.
            </li>
          </ul>
          {sig?.registry_updated && (
            <p className="border-t border-border pt-2">
              Signal registry last edited {sig.registry_updated}. Thresholds and falsifiers live in
              version control, not in this page.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
