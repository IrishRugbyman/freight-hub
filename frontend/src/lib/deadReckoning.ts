const DEG_TO_RAD = Math.PI / 180

/**
 * Project a position forward using speed-over-ground and course-over-ground.
 * Uses equirectangular approximation (fine for <600s at vessel speeds).
 *
 * Returns null when inputs are missing or motion is negligible (anchored).
 */
export function projectPosition(
  lat: number,
  lon: number,
  sogKn: number | null | undefined,
  cogDeg: number | null | undefined,
  dtSec: number,
): { lat: number; lon: number } | null {
  if (sogKn == null || cogDeg == null) return null
  if (sogKn < 0.3) return null // anchored or moored - avoid GPS jitter

  const dt = Math.min(dtSec, 600) // cap at 10 minutes
  const distNm = (sogKn * dt) / 3600
  const cogRad = cogDeg * DEG_TO_RAD

  const dLat = (distNm * Math.cos(cogRad)) / 60
  const dLon = (distNm * Math.sin(cogRad)) / (60 * Math.cos(lat * DEG_TO_RAD))

  return { lat: lat + dLat, lon: lon + dLon }
}
