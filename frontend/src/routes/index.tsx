import { createFileRoute, Link } from '@tanstack/react-router'
import {
  Radar,
  BarChart3,
  Workflow,
  Anchor,
  Bell,
  Route as RouteIcon,
  Network,
  ArrowRight,
} from 'lucide-react'
import { useMeta } from '@/lib/api'

export const Route = createFileRoute('/')({
  component: LandingPage,
})

type DashRoute =
  | '/tracker' | '/analytics' | '/pipelines' | '/fleet'
  | '/events' | '/routes' | '/dispersion'

interface Dashboard {
  to: DashRoute
  label: string
  icon: React.ComponentType<{ className?: string }>
  description: string
  featured: boolean
}

const DASHBOARDS: Dashboard[] = [
  {
    to: '/tracker',
    label: 'Live Tracker',
    icon: Radar,
    description:
      'Real-time AIS map of tankers and bulk carriers, with vessel trails, chokepoint overlays and supply-chain pipelines.',
    featured: true,
  },
  {
    to: '/analytics',
    label: 'Analytics',
    icon: BarChart3,
    description:
      'Port congestion, cargo flows, sanctions risk and fleet-mix intelligence derived from the live AIS feed.',
    featured: true,
  },
  {
    to: '/pipelines',
    label: 'Pipelines',
    icon: Workflow,
    description:
      'Commodity supply chains traced vessel-by-vessel: loading, transit and arriving cargo across key trade lanes.',
    featured: true,
  },
  {
    to: '/fleet',
    label: 'Fleet',
    icon: Anchor,
    description: 'Searchable register of every tracked vessel: segment, DWT, flag, destination and risk flags.',
    featured: false,
  },
  {
    to: '/events',
    label: 'Events',
    icon: Bell,
    description: 'Live feed of chokepoint transits, anomalies and notable position changes.',
    featured: false,
  },
  {
    to: '/routes',
    label: 'Routes',
    icon: RouteIcon,
    description: 'Forward-adjusted crude and products transport arbitrage across 8 routes, BWET-scaled.',
    featured: false,
  },
  {
    to: '/dispersion',
    label: 'Dispersion',
    icon: Network,
    description: 'Fleet geographic dispersion signal and a Capesize 5TC FFA backtest.',
    featured: false,
  },
]

const SOURCES = ['AIS (aisstream.io)', 'EIA forwards', 'Baltic / BWET', 'Equasis registry']

function LandingPage() {
  const { data: meta } = useMeta()

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] mx-auto px-6 md:px-10 lg:px-14">

        {/* Hero */}
        <section className="pt-12 pb-10">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/[0.06] px-3 py-1">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
            </span>
            <span className="text-[11px] font-medium tracking-wide text-primary/90 tabular-nums">
              {meta?.total_tracked != null
                ? `${meta.total_tracked.toLocaleString()} vessels tracked live`
                : 'Live AIS feed'}
            </span>
          </div>
          <h1 className="text-4xl md:text-5xl lg:text-[3.25rem] font-semibold tracking-tight leading-[1.06] text-foreground mb-4">
            Maritime Freight<br className="hidden sm:block" /> Intelligence
          </h1>
          <p className="text-base text-muted-foreground leading-relaxed mb-7 max-w-[52ch]">
            A live view of the tanker and dry-bulk fleet: vessel positions, port congestion, commodity
            supply chains and freight-rate arbitrage, all built on a 24/7 AIS feed.
          </p>
          <Link
            to="/tracker"
            search={{ mmsi: undefined, lat: undefined, lon: undefined, pipeline_id: undefined }}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground rounded font-medium text-sm hover:bg-primary/85 transition-colors"
          >
            Open the tracker
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </section>

        {/* Dashboard grid */}
        <section className="pb-8">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
            {/* Row 1: Tracker (2 col), Analytics, Pipelines */}
            <DashCard d={DASHBOARDS[0]} extraClass="lg:col-span-2" />
            <DashCard d={DASHBOARDS[1]} />
            <DashCard d={DASHBOARDS[2]} />
            {/* Row 2: Fleet, Events, Routes, Dispersion */}
            <DashCard d={DASHBOARDS[3]} />
            <DashCard d={DASHBOARDS[4]} />
            <DashCard d={DASHBOARDS[5]} />
            <DashCard d={DASHBOARDS[6]} />
          </div>
        </section>

        {/* Sources strip */}
        <section className="pb-8">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <span className="text-[10px] font-mono uppercase tracking-[0.14em] text-muted-foreground/35 mr-1">
              Sources
            </span>
            {SOURCES.map((s) => (
              <span key={s} className="text-[11px] text-muted-foreground/45">{s}</span>
            ))}
          </div>
        </section>

      </div>
    </div>
  )
}

function DashCard({ d, extraClass = '' }: { d: Dashboard; extraClass?: string }) {
  const { to, label, icon: Icon, description, featured } = d
  // `to` is loosened to a plain string so the Link does not demand the
  // tracker route's (all-optional) search params on every card.
  const linkTo: string = to
  return (
    <Link
      to={linkTo}
      className={[
        'group flex flex-col justify-between gap-4 p-4 rounded-lg border transition-colors min-h-[108px]',
        featured
          ? 'bg-primary/[0.055] border-primary/[0.16] hover:bg-primary/[0.09] hover:border-primary/[0.28]'
          : 'bg-card border-border hover:bg-secondary/50 hover:border-border',
        extraClass,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <div className="flex items-start justify-between gap-2">
        <div
          className={[
            'p-1.5 rounded shrink-0',
            featured
              ? 'bg-primary/15 text-primary'
              : 'bg-secondary text-muted-foreground group-hover:text-foreground transition-colors',
          ].join(' ')}
        >
          <Icon className="w-3.5 h-3.5" />
        </div>
        <ArrowRight className="w-3 h-3 text-muted-foreground/25 group-hover:text-muted-foreground/60 transition-colors mt-0.5 shrink-0" />
      </div>
      <div>
        <p className="text-sm font-medium text-foreground mb-1">{label}</p>
        <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
      </div>
    </Link>
  )
}
