/**
 * Renders pulsing risk rings over high-risk vessels on the Leaflet map.
 * OFAC vessels get a red pulsing circle; non-OFAC high-risk get orange.
 * The rings are CSS-animated divIcons - no GPU required.
 */
import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import type { HighRiskPosition } from '@/lib/api'

function riskIcon(ofac: boolean, score: number): L.DivIcon {
  const color = ofac ? '#ef4444' : score >= 80 ? '#f97316' : '#eab308'
  const size = ofac ? 22 : score >= 80 ? 18 : 14
  const half = size / 2
  return L.divIcon({
    className: '',
    html: `<div style="
      width:${size}px;height:${size}px;
      border-radius:50%;
      border:2px solid ${color};
      background:${color}22;
      animation:risk-pulse 2s ease-out infinite;
      box-shadow:0 0 6px ${color}88;
    "></div>`,
    iconSize: [size, size],
    iconAnchor: [half, half],
  })
}

const _CSS_INJECTED = { done: false }
function injectCSS() {
  if (_CSS_INJECTED.done || typeof document === 'undefined') return
  _CSS_INJECTED.done = true
  const style = document.createElement('style')
  style.textContent = `@keyframes risk-pulse {
    0%   { transform: scale(1);   opacity: 0.9; }
    60%  { transform: scale(1.6); opacity: 0.5; }
    100% { transform: scale(2.2); opacity: 0; }
  }`
  document.head.appendChild(style)
}

export function RiskLayer({ positions, onSelect }: {
  positions: HighRiskPosition[]
  onSelect?: (pos: HighRiskPosition) => void
}) {
  const map = useMap()
  const groupRef = useRef<L.LayerGroup | null>(null)

  injectCSS()

  useEffect(() => {
    if (!groupRef.current) {
      groupRef.current = L.layerGroup()
      map.addLayer(groupRef.current)
    }
    return () => {
      if (groupRef.current) {
        map.removeLayer(groupRef.current)
        groupRef.current = null
      }
    }
  }, [map])

  useEffect(() => {
    const group = groupRef.current
    if (!group) return
    group.clearLayers()
    for (const pos of positions) {
      const m = L.marker([pos.lat, pos.lon], { icon: riskIcon(pos.ofac_sanctioned, pos.risk_score), zIndexOffset: 500 })
      m.bindTooltip(
        `<strong>${pos.name ?? `MMSI ${pos.mmsi}`}</strong><br/>` +
        `Risk: ${pos.risk_score}${pos.ofac_sanctioned ? ' <span style="color:#ef4444">OFAC</span>' : ''}<br/>` +
        `${pos.segment ?? ''} ${pos.kind ?? ''}`.trim(),
        { direction: 'top', offset: [0, -8] },
      )
      if (onSelect) m.on('click', () => onSelect(pos))
      group.addLayer(m)
    }
  }, [positions, onSelect])

  return null
}
