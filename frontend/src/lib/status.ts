// Semantic status colors: single source of truth for risk severity, pipeline
// flow state / commodity, and intelligence event types. Pure, no React.
//
// This is the STATUS domain. Vessel-class colors (ULCC, Capesize, ...) live in
// segments.ts, a separate domain encoding ship *size*, not status. The two
// domains intentionally share some hues (e.g. orange) but never render on the
// same surface, so meaning stays unambiguous within any one view. Keep all
// status hues here so a severity color is defined once and reused everywhere.

// --- Risk severity ramp -----------------------------------------------------
// low -> elevated -> high -> critical. Used by Fleet KPIs, risk columns, and
// the Paris/Tokyo MoU detention/deficiency cells.

export type RiskLevel = 'low' | 'elevated' | 'high' | 'critical'

export const RISK_TEXT: Record<RiskLevel, string> = {
  low: 'text-emerald-400',
  elevated: 'text-yellow-400',
  high: 'text-orange-400',
  critical: 'text-red-400',
}

/** Overall vessel risk score (0-100) -> severity text class. */
export function riskScoreClass(score: number): string {
  if (score >= 50) return RISK_TEXT.high
  if (score >= 25) return RISK_TEXT.elevated
  return RISK_TEXT.low
}

/** Detention rate (%) -> severity text class. */
export function detentionClass(pct: number): string {
  if (pct >= 10) return RISK_TEXT.critical
  if (pct >= 5) return RISK_TEXT.elevated
  return RISK_TEXT.low
}

/** Deficiency count -> severity text class. */
export function deficiencyClass(count: number): string {
  if (count >= 50) return RISK_TEXT.critical
  if (count >= 25) return RISK_TEXT.elevated
  return RISK_TEXT.low
}

// --- Pipeline flow state ----------------------------------------------------

export type PipelineState = 'offline' | 'reduced' | 'flowing'

export const PIPELINE_STATE: Record<PipelineState, { hex: string; text: string; dot: string; badge: string }> = {
  offline: { hex: '#ef4444', text: 'text-red-400', dot: 'bg-red-400', badge: 'bg-red-500/20 text-red-300' },
  reduced: { hex: '#f97316', text: 'text-orange-400', dot: 'bg-orange-400', badge: 'bg-orange-500/20 text-orange-300' },
  flowing: { hex: '#38bdf8', text: 'text-sky-400', dot: 'bg-sky-400', badge: 'bg-sky-500/20 text-sky-300' },
}

// Commodity carried by a pipeline (drives the map line color when flowing).
export const COMMODITY: Record<'oil' | 'gas', { hex: string; text: string; dot: string }> = {
  oil: { hex: '#fbbf24', text: 'text-amber-400', dot: 'bg-amber-400' },
  gas: { hex: '#38bdf8', text: 'text-sky-400', dot: 'bg-sky-400' },
}

// --- Intelligence event types -----------------------------------------------
// Badge classes for the events feed. Severity-ordered (high concern first).

export const EVENT_TYPE_COLORS: Record<string, string> = {
  dark_voyage: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  spoof: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
  gap: 'bg-red-500/20 text-red-400 border-red-500/30',
  loiter: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  sts: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  reroute: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
}
