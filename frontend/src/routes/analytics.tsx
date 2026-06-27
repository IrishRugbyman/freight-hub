import { Suspense, lazy } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMarketSummary, useCrudeOnWater } from '@/lib/api'
import { Tabs } from '@/components/ui/tabs'

// ---------------------------------------------------------------------------
// Route: typed search param (deep-linkable tab)
// ---------------------------------------------------------------------------
const VALID_TABS = ['overview', 'chokepoints', 'ports', 'risk', 'intelligence', 'fleet'] as const
type TabId = (typeof VALID_TABS)[number]

export const Route = createFileRoute('/analytics')({
  component: AnalyticsPage,
  validateSearch: (s: Record<string, unknown>): { tab: TabId } => ({
    tab: VALID_TABS.includes(s.tab as TabId) ? (s.tab as TabId) : 'overview',
  }),
})

// ---------------------------------------------------------------------------
// Per-tab lazy chunks (6 distinct specifiers = 6 distinct rollup chunks)
// ---------------------------------------------------------------------------
const OverviewTab    = lazy(() => import('./analytics/-OverviewCards'))
const ChokepointsTab = lazy(() => import('./analytics/-ChokepointCards'))
const PortsCargoTab  = lazy(() => import('./analytics/-PortsCargoCards'))
const RiskTab        = lazy(() => import('./analytics/-RiskCards'))
const IntelligenceTab = lazy(() => import('./analytics/-IntelligenceCards'))
const FleetTab       = lazy(() => import('./analytics/-FleetCards'))

// ---------------------------------------------------------------------------
// Tab definitions (with card counts as badges)
// ---------------------------------------------------------------------------
const TABS = [
  { id: 'overview'      as const, label: 'Overview',       count: 4  },
  { id: 'chokepoints'   as const, label: 'Chokepoints',    count: 6  },
  { id: 'ports'         as const, label: 'Ports & Cargo',  count: 14 },
  { id: 'risk'          as const, label: 'Risk',           count: 8  },
  { id: 'intelligence'  as const, label: 'Intelligence',   count: 8  },
  { id: 'fleet'         as const, label: 'Fleet',          count: 7  },
]

// ---------------------------------------------------------------------------
// KPI command-bar (always visible, never unmounts on tab switch)
// ---------------------------------------------------------------------------
function KpiBar() {
  const { data: summary } = useMarketSummary()
  const { data: crude }   = useCrudeOnWater()

  const kpis = [
    {
      label: 'Vessels tracked',
      value: summary?.total_fleet != null ? summary.total_fleet.toLocaleString() : '-',
    },
    {
      label: 'Laden tankers',
      value: summary?.total_laden != null ? summary.total_laden.toLocaleString() : '-',
      cls: 'text-orange-400',
    },
    {
      label: 'MB on water',
      value: crude?.estimated_mb_on_water != null ? crude.estimated_mb_on_water.toFixed(0) : '-',
    },
    {
      label: 'Transits 24h',
      value: summary?.transits_24h != null ? summary.transits_24h.toLocaleString() : '-',
    },
    {
      label: 'Reroutes 24h',
      value: summary?.reroutes_24h != null ? summary.reroutes_24h.toLocaleString() : '-',
    },
    {
      label: 'STS 24h',
      value: summary?.sts_24h != null ? summary.sts_24h.toLocaleString() : '-',
      cls: summary?.sts_24h && summary.sts_24h > 50 ? 'text-orange-400' : undefined,
    },
  ]

  return (
    <div className="border-b border-border bg-muted/30 px-4 py-2">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-1">
        {kpis.map(kpi => (
          <div key={kpi.label} className="flex items-baseline gap-1.5">
            <span className={`text-sm font-semibold tabular-nums ${kpi.cls ?? 'text-foreground'}`}>
              {kpi.value}
            </span>
            <span className="text-[11px] text-muted-foreground">{kpi.label}</span>
          </div>
        ))}
        {summary?.as_of && (
          <span className="ml-auto text-[10px] text-muted-foreground/50">
            {new Date(summary.as_of).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Tab skeleton (shown while lazy chunk loads)
// ---------------------------------------------------------------------------
function TabSkeleton() {
  return (
    <div className="space-y-4 p-4">
      {[300, 240, 200].map(h => (
        <div key={h} className="animate-pulse rounded-lg bg-muted/40" style={{ height: h }} />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
function AnalyticsPage() {
  const { tab } = Route.useSearch()
  const navigate = useNavigate({ from: '/analytics' })

  function setTab(id: TabId) {
    void navigate({ search: { tab: id }, replace: true })
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <KpiBar />
      <Tabs tabs={TABS} value={tab} onChange={t => setTab(t as TabId)} />

      <main
        id={`tabpanel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        className="mx-auto max-w-5xl px-4 py-6"
      >
        <Suspense fallback={<TabSkeleton />}>
          {tab === 'overview'     && <OverviewTab />}
          {tab === 'chokepoints'  && <ChokepointsTab />}
          {tab === 'ports'        && <PortsCargoTab />}
          {tab === 'risk'         && <RiskTab />}
          {tab === 'intelligence' && <IntelligenceTab />}
          {tab === 'fleet'        && <FleetTab />}
        </Suspense>
      </main>
    </div>
  )
}
