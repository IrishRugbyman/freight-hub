// Shared helpers and constants used across multiple analytics tab modules.
// Card-local-only helpers stay co-located in their tab file.

import { Skeleton } from '@/components/ui/skeleton'

export function fmt(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Pulse placeholder shaped like a chart/panel, swapped in while data loads. */
export function ChartSkeleton({ className = 'h-40' }: { className?: string }) {
  return <Skeleton className={`w-full ${className}`} />
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
      {message}
    </div>
  )
}

export const TOOLTIP_STYLE = {
  background: 'var(--card)',
  border: '1px solid var(--border)',
  fontSize: 12,
}

export const LEGEND_STYLE = { fontSize: 11 }

// Used across Ports & Cargo tab (DestinationFlowCard) and Overview tab (RegionMomentumCard)
export const REGION_LABELS: Record<string, string> = {
  ara: 'ARA', singapore_malacca: 'Sing/Mal', hormuz: 'Hormuz',
  suez: 'Suez', japan_korea: 'Japan/Korea', us_gulf: 'US Gulf',
  west_africa: 'W Africa', east_africa: 'E Africa', north_sea: 'N Sea',
  black_sea: 'Black Sea', med: 'Med', us_east_coast: 'US East',
  us_west_coast: 'US West', brazil: 'Brazil', australia: 'Australia',
  saldanha_richards_bay: 'S Africa', unknown: '?',
}
