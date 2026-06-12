import { Suspense, lazy } from 'react'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/analytics')({ component: AnalyticsPage })

const TransitsCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.TransitsCard })))
const CongestionCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.CongestionCard })))
const LadenCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.LadenCard })))
const DensityCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.DensityCard })))
const PortFlowCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.PortFlowCard })))
const OwnerRiskCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.OwnerRiskCard })))
const FleetSpeedCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FleetSpeedCard })))
const RegionUtilCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.RegionUtilCard })))
const FlagRiskCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FlagRiskCard })))

function ChartSkeleton() {
  return <div className="h-[300px] animate-pulse rounded-lg bg-muted/40" />
}

function AnalyticsPage() {
  return (
    <div className="overflow-auto p-4">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-xl font-semibold">Freight Analytics</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Derived signals from accumulated AIS history. Collection started 2026-06-09.
            Charts fill in as history grows.
          </p>
        </div>

        <h2 className="text-base font-semibold text-foreground">Chokepoint Traffic</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><TransitsCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><CongestionCard /></Suspense>
        </div>

        <h2 className="text-base font-semibold text-foreground">Fleet State</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><LadenCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><DensityCard /></Suspense>
        </div>

        <h2 className="text-base font-semibold text-foreground">Port Flow</h2>
        <Suspense fallback={<ChartSkeleton />}><PortFlowCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Owner &amp; Flag Risk</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><OwnerRiskCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><FlagRiskCard /></Suspense>
        </div>

        <h2 className="text-base font-semibold text-foreground">Fleet Speed &amp; Utilization</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><FleetSpeedCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><RegionUtilCard /></Suspense>
        </div>
      </div>
    </div>
  )
}
