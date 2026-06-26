// True ETA presentation logic (pure, no React, unit-tested).
//
// The backend serves a physics ETA (P50) with a calibrated [P10, P90] band and a
// method label, plus the honest naive (great-circle / SOG) baseline. This module
// turns those raw fields into the strings + flags the EtaChip renders, and owns
// the urgency color ramp so it is defined once.

export interface TrueEta {
  eta_true_h: number | null
  eta_low_h: number | null
  eta_high_h: number | null
  eta_naive_h: number | null
  eta_method: string | null
}

/** Format an hour count compactly: minutes < 1h, hours < 2d, else days. */
export function formatEtaHours(h: number | null | undefined): string {
  if (h == null || !Number.isFinite(h)) return '-'
  if (h < 0) return '-'
  if (h < 1) return `${Math.round(h * 60)}m`
  if (h < 48) return `${h.toFixed(1)}h`
  return `${(h / 24).toFixed(1)}d`
}

/** Urgency text class for an ETA in hours (sooner = hotter). */
export function etaUrgencyClass(h: number | null | undefined): string {
  if (h == null || !Number.isFinite(h)) return 'text-muted-foreground'
  if (h <= 6) return 'text-orange-400'
  if (h <= 24) return 'text-yellow-400'
  return 'text-muted-foreground'
}

// Method badge styling. 'physics' is the shipping champion; 'ml' is reserved for
// the gated learned model; 'naive' marks a degraded fallback (no calibrated band).
export const ETA_METHOD_BADGE: Record<string, string> = {
  ml: 'bg-violet-500/20 text-violet-300 border-violet-500/30',
  physics: 'bg-sky-500/20 text-sky-300 border-sky-500/30',
  naive: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30',
}

export function etaMethodBadgeClass(method: string | null | undefined): string {
  return ETA_METHOD_BADGE[method ?? ''] ?? ETA_METHOD_BADGE.naive
}

export interface EtaDisplay {
  /** Primary value to show (true ETA when present, else naive). */
  primaryH: number | null
  primaryLabel: string
  /** True when a physics/ml estimate with a calibrated band is available. */
  hasTrue: boolean
  bandLabel: string | null // e.g. "8.2-14.1h", null when no band
  method: string | null
  /** Signed true-minus-naive delta in hours (null when not comparable). */
  deltaH: number | null
  /** Hover tooltip explaining the estimate vs the naive baseline. */
  tooltip: string
}

/**
 * Resolve the raw true-ETA fields into everything the chip needs. `fallbackH`
 * is the card's own naive ETA (e.g. `eta_hours`) used when no true estimate
 * resolved, so the chip always renders a value.
 */
export function resolveEta(v: TrueEta, fallbackH: number | null | undefined): EtaDisplay {
  const trueH = v.eta_true_h
  const naiveH = v.eta_naive_h ?? (fallbackH ?? null)
  const hasTrue = trueH != null && Number.isFinite(trueH)
  const primaryH = hasTrue ? trueH : (fallbackH ?? null)

  let bandLabel: string | null = null
  if (hasTrue && v.eta_low_h != null && v.eta_high_h != null) {
    bandLabel = `${formatEtaHours(v.eta_low_h)}-${formatEtaHours(v.eta_high_h)}`
  }

  const deltaH = hasTrue && naiveH != null ? (trueH as number) - naiveH : null

  let tooltip: string
  if (hasTrue) {
    const parts = [`${v.eta_method ?? 'physics'} ETA ${formatEtaHours(trueH)}`]
    if (bandLabel) parts.push(`(${bandLabel} 80% band)`)
    if (naiveH != null) {
      const sign = (deltaH as number) >= 0 ? '+' : ''
      parts.push(`vs naive ${formatEtaHours(naiveH)} (${sign}${(deltaH as number).toFixed(1)}h)`)
    }
    tooltip = parts.join(' ')
  } else {
    tooltip = naiveH != null ? `naive ETA ${formatEtaHours(naiveH)} (no resolved route)` : 'ETA unavailable'
  }

  return {
    primaryH,
    primaryLabel: formatEtaHours(primaryH),
    hasTrue,
    bandLabel,
    method: hasTrue ? (v.eta_method ?? 'physics') : null,
    deltaH,
    tooltip,
  }
}
