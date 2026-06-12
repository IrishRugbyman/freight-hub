import { Suspense, lazy } from 'react'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/analytics')({ component: AnalyticsPage })

const ChokepointHeatmapCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.ChokepointHeatmapCard })))
const TradeLaneMatrixCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.TradeLaneMatrixCard })))
const TransitsCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.TransitsCard })))
const CongestionCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.CongestionCard })))
const LadenCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.LadenCard })))
const DensityCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.DensityCard })))
const PortFlowCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.PortFlowCard })))
const OwnerRiskCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.OwnerRiskCard })))
const FleetSpeedCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FleetSpeedCard })))
const RegionUtilCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.RegionUtilCard })))
const FlagRiskCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FlagRiskCard })))
const SpeedTrendCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.SpeedTrendCard })))
const StsRiskCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.StsRiskCard })))
const ReroutesCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.ReroutesCard })))
const TransitRiskCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.TransitRiskCard })))
const FleetAgeCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FleetAgeCard })))
const AnchorageDwellCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.AnchorageDwellCard })))
const CargoTransitionsCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.CargoTransitionsCard })))
const SlowSteamersCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.SlowSteamersCard })))
const FleetUtilizationCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FleetUtilizationCard })))
const RiskEventsCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.RiskEventsCard })))
const PortCongestionCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.PortCongestionCard })))
const DestinationFlowCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.DestinationFlowCard })))
const MarketSummaryCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.MarketSummaryCard })))
const VesselRiskLeaderboardCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.VesselRiskLeaderboardCard })))
const AnomalyWatchlistCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.AnomalyWatchlistCard })))
const StsProximityCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.StsProximityCard })))
const RegionMomentumCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.RegionMomentumCard })))
const EventRateTimelineCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.EventRateTimelineCard })))
const TransitRateTimelineCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.TransitRateTimelineCard })))
const AnchorageOccupancyCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.AnchorageOccupancyCard })))
const StsOffendersCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.StsOffendersCard })))
const FleetAtTimeCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.FleetAtTimeCard })))
const DestinationChangesCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.DestinationChangesCard })))
const OwnerIntelligenceCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.OwnerIntelligenceCard })))
const ChokepointAnomalyCard = lazy(() => import('./AnalyticsCharts').then((m) => ({ default: m.ChokepointAnomalyCard })))

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

        <h2 className="text-base font-semibold text-foreground">Market State</h2>
        <Suspense fallback={<ChartSkeleton />}><MarketSummaryCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><RegionMomentumCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><FleetAtTimeCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Chokepoint Traffic</h2>
        <Suspense fallback={<ChartSkeleton />}><ChokepointAnomalyCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><ChokepointHeatmapCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><TransitRateTimelineCard /></Suspense>
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

        <h2 className="text-base font-semibold text-foreground">Port Congestion</h2>
        <Suspense fallback={<ChartSkeleton />}><AnchorageOccupancyCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><PortCongestionCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Cargo Flows</h2>
        <Suspense fallback={<ChartSkeleton />}><TradeLaneMatrixCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><DestinationFlowCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Fleet Age &amp; Risk Profile</h2>
        <Suspense fallback={<ChartSkeleton />}><FleetAgeCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Owner &amp; Flag Risk</h2>
        <Suspense fallback={<ChartSkeleton />}><OwnerIntelligenceCard /></Suspense>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><OwnerRiskCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><FlagRiskCard /></Suspense>
        </div>

        <h2 className="text-base font-semibold text-foreground">Vessel Risk Leaderboard</h2>
        <Suspense fallback={<ChartSkeleton />}><VesselRiskLeaderboardCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Intelligence Alerts</h2>
        <Suspense fallback={<ChartSkeleton />}><AnomalyWatchlistCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><DestinationChangesCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><StsProximityCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><EventRateTimelineCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><RiskEventsCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Event Intelligence</h2>
        <Suspense fallback={<ChartSkeleton />}><StsOffendersCard /></Suspense>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><TransitRiskCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><AnchorageDwellCard /></Suspense>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><StsRiskCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><ReroutesCard /></Suspense>
        </div>

        <h2 className="text-base font-semibold text-foreground">Cargo Intelligence</h2>
        <Suspense fallback={<ChartSkeleton />}><CargoTransitionsCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Market Signals</h2>
        <Suspense fallback={<ChartSkeleton />}><FleetUtilizationCard /></Suspense>
        <Suspense fallback={<ChartSkeleton />}><SlowSteamersCard /></Suspense>

        <h2 className="text-base font-semibold text-foreground">Fleet Speed &amp; Utilization</h2>
        <Suspense fallback={<ChartSkeleton />}><SpeedTrendCard /></Suspense>
        <div className="grid gap-4 lg:grid-cols-2">
          <Suspense fallback={<ChartSkeleton />}><FleetSpeedCard /></Suspense>
          <Suspense fallback={<ChartSkeleton />}><RegionUtilCard /></Suspense>
        </div>
      </div>
    </div>
  )
}
