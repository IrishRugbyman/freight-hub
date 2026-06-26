// EtaChip: renders a vessel's arrival ETA - the physics P50 with its calibrated
// [P10, P90] band and a method badge when a true estimate resolved, gracefully
// falling back to the naive estimate otherwise. Colors come from lib/eta.ts
// (urgency ramp + method badge), never hardcoded here.

import { etaMethodBadgeClass, etaUrgencyClass, resolveEta, type TrueEta } from '../lib/eta'

interface EtaChipProps {
  vessel: TrueEta
  /** The card's own naive ETA (eta_hours), shown when no true estimate resolved. */
  fallbackH: number | null | undefined
  /** Show the [P10, P90] band line under the value (default true). */
  showBand?: boolean
  /** Show the method badge (default true). */
  showBadge?: boolean
  className?: string
}

export function EtaChip({ vessel, fallbackH, showBand = true, showBadge = true, className = '' }: EtaChipProps) {
  const d = resolveEta(vessel, fallbackH)

  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`} title={d.tooltip}>
      <span className={`font-bold tabular-nums ${etaUrgencyClass(d.primaryH)}`}>{d.primaryLabel}</span>
      {showBand && d.bandLabel && (
        <span className="text-[10px] text-muted-foreground tabular-nums">{d.bandLabel}</span>
      )}
      {showBadge && d.method && (
        <span
          className={`rounded border px-1 py-px text-[9px] font-semibold uppercase leading-none ${etaMethodBadgeClass(d.method)}`}
        >
          {d.method}
        </span>
      )}
    </span>
  )
}
