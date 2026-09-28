// Ports & Cargo tab: composes the card modules in display order.
import {
  PortArrivalForecastCard, PortFlowCard, ActualArrivalsCard, PortCongestionCard,
  ChokepointCongestionCard, AnchorageOccupancyCard, DensityCard,
} from './-PortsCards'
import {
  TradeLaneMatrixCard, DestinationFlowCard, CargoTransitionsCard, CargoStateChangesCard, LadenCard,
} from './-CargoCards'
import { EuropeanInboundCard, LngIntelligenceCard } from './-EuropeanSupplyCards'
import { EtaAccuracyCard, EtaByTargetCard } from './-EtaCards'

// ---------------------------------------------------------------------------
// Default export: Ports & Cargo tab component
// ---------------------------------------------------------------------------
export default function PortsCargoTab() {
  return (
    <div className="space-y-6">
      <LngIntelligenceCard />
      <EuropeanInboundCard />
      <EtaAccuracyCard />
      <EtaByTargetCard />
      <PortArrivalForecastCard />
      <PortFlowCard />
      <ActualArrivalsCard />
      <PortCongestionCard />
      <ChokepointCongestionCard />
      <AnchorageOccupancyCard />
      <TradeLaneMatrixCard />
      <DestinationFlowCard />
      <CargoStateChangesCard />
      <CargoTransitionsCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <LadenCard />
        <DensityCard />
      </div>
    </div>
  )
}
