import { useMemo, useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ChevronDown, ChevronRight, MapPin } from 'lucide-react'
import { usePipelines, type PipelineSegment } from '@/lib/api'

export const Route = createFileRoute('/pipelines')({
  component: PipelinesPage,
})

const COUNTRY: Record<string, string> = {
  AF: 'Afghanistan', AL: 'Albania', DZ: 'Algeria', AO: 'Angola', AR: 'Argentina',
  AM: 'Armenia', AU: 'Australia', AZ: 'Azerbaijan', BH: 'Bahrain', BD: 'Bangladesh',
  BY: 'Belarus', BE: 'Belgium', BO: 'Bolivia', BA: 'Bosnia', BR: 'Brazil',
  BG: 'Bulgaria', CM: 'Cameroon', CA: 'Canada', CL: 'Chile', CN: 'China',
  CO: 'Colombia', CD: 'DR Congo', CG: 'Congo', CR: 'Costa Rica', HR: 'Croatia',
  CZ: 'Czechia', DK: 'Denmark', EC: 'Ecuador', EG: 'Egypt', ET: 'Ethiopia',
  FI: 'Finland', FR: 'France', GA: 'Gabon', GE: 'Georgia', DE: 'Germany',
  GH: 'Ghana', GR: 'Greece', GN: 'Guinea', HU: 'Hungary', IN: 'India',
  ID: 'Indonesia', IR: 'Iran', IQ: 'Iraq', IE: 'Ireland', IL: 'Israel',
  IT: 'Italy', CI: 'Ivory Coast', JP: 'Japan', JO: 'Jordan', KZ: 'Kazakhstan',
  KE: 'Kenya', KW: 'Kuwait', KG: 'Kyrgyzstan', LV: 'Latvia', LB: 'Lebanon',
  LY: 'Libya', LT: 'Lithuania', MY: 'Malaysia', ML: 'Mali', MX: 'Mexico',
  MD: 'Moldova', MN: 'Mongolia', MA: 'Morocco', MZ: 'Mozambique', MM: 'Myanmar',
  NL: 'Netherlands', NG: 'Nigeria', NO: 'Norway', OM: 'Oman', PK: 'Pakistan',
  PE: 'Peru', PH: 'Philippines', PL: 'Poland', PT: 'Portugal', QA: 'Qatar',
  RO: 'Romania', RU: 'Russia', SA: 'Saudi Arabia', SN: 'Senegal', SK: 'Slovakia',
  ZA: 'South Africa', KR: 'South Korea', SS: 'South Sudan', ES: 'Spain',
  SD: 'Sudan', SE: 'Sweden', CH: 'Switzerland', SY: 'Syria', TW: 'Taiwan',
  TJ: 'Tajikistan', TZ: 'Tanzania', TH: 'Thailand', TN: 'Tunisia', TR: 'Turkey',
  TM: 'Turkmenistan', UG: 'Uganda', UA: 'Ukraine', AE: 'UAE', GB: 'UK',
  US: 'USA', UZ: 'Uzbekistan', VE: 'Venezuela', VN: 'Vietnam', YE: 'Yemen',
  ZM: 'Zambia', ZW: 'Zimbabwe',
}

function countryName(iso2: string): string {
  return COUNTRY[iso2.toUpperCase()] ?? iso2
}

function StateBadge({ state }: { state: string }) {
  if (state === 'offline') {
    return (
      <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-red-500/15 text-red-300">
        Offline
      </span>
    )
  }
  if (state === 'reduced') {
    return (
      <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-orange-500/15 text-orange-300">
        Reduced
      </span>
    )
  }
  return (
    <span className="text-[10px] text-muted-foreground/60 uppercase tracking-wide">
      {state}
    </span>
  )
}

function commodityDot(c: string) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${c === 'oil' ? 'text-amber-400' : 'text-sky-400'}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${c === 'oil' ? 'bg-amber-400' : 'bg-sky-400'}`} />
      {c}
    </span>
  )
}

function formatCap(p: PipelineSegment): string {
  const parts: string[] = []
  if (p.commodity === 'gas') {
    if (p.capacity_bcfd && p.capacity_bcfd > 0) parts.push(`${p.capacity_bcfd.toFixed(2)} Bcf/d`)
    else if (p.capacity_bcm_yr && p.capacity_bcm_yr > 0) parts.push(`${p.capacity_bcm_yr.toFixed(1)} bcm/yr`)
  } else if (p.capacity_mbd && p.capacity_mbd > 0) {
    parts.push(`${p.capacity_mbd.toFixed(1)} mbd`)
  }
  if (p.length_miles && p.length_miles > 0) parts.push(`${p.length_miles.toLocaleString()} mi`)
  return parts.join(' · ') || '-'
}

function sinceStr(s: string | null): string {
  if (!s || s === 'NaT' || s === 'null') return ''
  return s.slice(0, 10)
}

function KpiTile({
  label,
  value,
  sub,
  color,
}: {
  label: string
  value: string
  sub?: string
  color?: string
}) {
  return (
    <div className="flex min-w-[8rem] flex-col rounded-lg border border-border bg-muted/30 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-xl font-semibold tabular-nums ${color ?? 'text-foreground'}`}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </div>
  )
}

type SortKey = 'name' | 'owner' | 'from_country' | 'to_country' | 'capacity' | 'state' | 'commodity' | 'since'
type SortDir = 'asc' | 'desc'

function sortPipelines(rows: PipelineSegment[], key: SortKey, dir: SortDir): PipelineSegment[] {
  const mult = dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    let cmp = 0
    if (key === 'name') cmp = a.name.localeCompare(b.name)
    else if (key === 'owner') cmp = (a.owner ?? '').localeCompare(b.owner ?? '')
    else if (key === 'from_country') cmp = a.from_country.localeCompare(b.from_country)
    else if (key === 'to_country') cmp = a.to_country.localeCompare(b.to_country)
    else if (key === 'commodity') cmp = a.commodity.localeCompare(b.commodity)
    else if (key === 'state') cmp = a.physical_state.localeCompare(b.physical_state)
    else if (key === 'since') {
      cmp = sinceStr(a.disruption_since).localeCompare(sinceStr(b.disruption_since))
    } else if (key === 'capacity') {
      const ac = a.capacity_bcfd
        ? a.capacity_bcfd * 10
        : a.commodity === 'oil'
          ? (a.capacity_mbd ?? 0)
          : (a.capacity_bcm_yr ?? 0)
      const bc = b.capacity_bcfd
        ? b.capacity_bcfd * 10
        : b.commodity === 'oil'
          ? (b.capacity_mbd ?? 0)
          : (b.capacity_bcm_yr ?? 0)
      cmp = ac - bc
    }
    return cmp * mult
  })
}

export default function PipelinesPage() {
  const { data, isLoading } = usePipelines(false, true)
  const pipelines = data?.pipelines ?? []
  const navigate = useNavigate({ from: '/pipelines' })

  const [q, setQ] = useState('')
  const [stateFilter, setStateFilter] = useState<string>('all')
  const [commodityFilter, setCommodityFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<string>('all')
  const [sortKey, setSortKey] = useState<SortKey>('capacity')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expanded, setExpanded] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const qLower = q.toLowerCase()
    let rows = pipelines.filter(p => {
      if (stateFilter !== 'all' && p.physical_state !== stateFilter) return false
      if (commodityFilter !== 'all' && p.commodity !== commodityFilter) return false
      if (sourceFilter !== 'all' && p.data_source !== sourceFilter) return false
      if (qLower && !p.name.toLowerCase().includes(qLower) &&
          !p.from_country.toLowerCase().includes(qLower) &&
          !p.to_country.toLowerCase().includes(qLower) &&
          !(p.owner ?? '').toLowerCase().includes(qLower) &&
          !(p.states_served ?? '').toLowerCase().includes(qLower)) return false
      return true
    })
    return sortPipelines(rows, sortKey, sortDir)
  }, [pipelines, q, stateFilter, commodityFilter, sourceFilter, sortKey, sortDir])

  const offline = pipelines.filter(p => p.physical_state === 'offline')
  const reduced = pipelines.filter(p => p.physical_state === 'reduced')
  const offlineMbd = offline.reduce((s, p) => s + (p.capacity_mbd ?? 0), 0)
  const offlineBcm = offline.reduce((s, p) => s + (p.capacity_bcm_yr ?? 0), 0)
  const reducedMbd = reduced.reduce((s, p) => s + (p.capacity_mbd ?? 0), 0)
  const reducedBcm = reduced.reduce((s, p) => s + (p.capacity_bcm_yr ?? 0), 0)
  const rextagCount = pipelines.filter(p => p.data_source === 'rextag').length

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  function SortIcon({ k }: { k: SortKey }) {
    if (sortKey !== k) return <span className="text-muted-foreground/40 ml-1">-</span>
    return <span className="ml-1 text-primary">{sortDir === 'desc' ? '↓' : '↑'}</span>
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* KPI bar */}
      <div className="shrink-0 border-b border-border/60 bg-background/80 px-4 py-2.5">
        <div className="flex flex-wrap gap-2">
          <KpiTile
            label="Total pipelines"
            value={pipelines.length.toLocaleString()}
            sub={rextagCount > 0 ? `incl. ${rextagCount} US FERC` : 'with coordinates'}
          />
          <KpiTile
            label="Offline"
            value={offline.length.toString()}
            sub={`${offlineMbd.toFixed(1)} mbd / ${offlineBcm.toFixed(0)} bcm/yr`}
            color="text-red-400"
          />
          <KpiTile
            label="Reduced"
            value={reduced.length.toString()}
            sub={`${reducedMbd.toFixed(1)} mbd / ${reducedBcm.toFixed(0)} bcm/yr`}
            color="text-orange-400"
          />
          <KpiTile
            label="Flowing"
            value={pipelines.filter(p => p.physical_state === 'flowing').length.toString()}
          />
        </div>
      </div>

      {/* Filter bar */}
      <div className="shrink-0 border-b border-border/60 bg-background/95 px-4 py-2 flex flex-wrap items-center gap-3">
        <input
          className="h-7 w-56 rounded border border-border bg-muted/40 px-2.5 text-xs placeholder:text-muted-foreground focus:border-primary/60 focus:outline-none"
          placeholder="Search name, owner, states..."
          value={q}
          onChange={e => setQ(e.target.value)}
        />
        <select
          className="h-7 rounded border border-border bg-background px-2 text-xs"
          value={stateFilter}
          onChange={e => setStateFilter(e.target.value)}
        >
          <option value="all">All states</option>
          <option value="offline">Offline</option>
          <option value="reduced">Reduced</option>
          <option value="flowing">Flowing</option>
          <option value="unknown">Unknown</option>
        </select>
        <select
          className="h-7 rounded border border-border bg-background px-2 text-xs"
          value={commodityFilter}
          onChange={e => setCommodityFilter(e.target.value)}
        >
          <option value="all">All commodities</option>
          <option value="oil">Oil</option>
          <option value="gas">Gas</option>
        </select>
        <select
          className="h-7 rounded border border-border bg-background px-2 text-xs"
          value={sourceFilter}
          onChange={e => setSourceFilter(e.target.value)}
        >
          <option value="all">All sources</option>
          <option value="worldmonitor">World Monitor</option>
          <option value="rextag">RexTag US</option>
        </select>
        <span className="ml-auto text-xs text-muted-foreground">
          {filtered.length} of {pipelines.length}
        </span>
      </div>

      {/* Table */}
      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading && (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            Loading...
          </div>
        )}
        {!isLoading && filtered.length === 0 && (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            No pipelines match the filter.
          </div>
        )}
        {!isLoading && filtered.length > 0 && (
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10 bg-background/95 backdrop-blur">
              <tr className="border-b border-border/60 text-left text-muted-foreground">
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('name')}
                >
                  Name<SortIcon k="name" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('owner')}
                >
                  Owner<SortIcon k="owner" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('from_country')}
                >
                  From<SortIcon k="from_country" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('to_country')}
                >
                  To<SortIcon k="to_country" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('commodity')}
                >
                  Commodity<SortIcon k="commodity" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground text-right"
                  onClick={() => toggleSort('capacity')}
                >
                  Capacity / Length<SortIcon k="capacity" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('state')}
                >
                  State<SortIcon k="state" />
                </th>
                <th
                  className="cursor-pointer px-4 py-2 font-medium hover:text-foreground"
                  onClick={() => toggleSort('since')}
                >
                  Since<SortIcon k="since" />
                </th>
                <th className="px-4 py-2 font-medium">States</th>
                <th className="px-4 py-2 font-medium">Disruption</th>
                <th className="w-8 px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(p => {
                const since = sinceStr(p.disruption_since)
                const isExpanded = expanded === p.id
                const disrupted = p.physical_state === 'offline' || p.physical_state === 'reduced'
                const hasMap = (p.start_lat != null && p.start_lon != null) || (p.route_coords != null && p.route_coords.length > 0)
                return (
                  <>
                    <tr
                      key={p.id}
                      className={`border-b border-border/20 transition-colors ${hasMap ? 'cursor-pointer' : ''} ${disrupted ? 'hover:bg-muted/40' : 'hover:bg-muted/20'}`}
                      onClick={() => hasMap && navigate({ to: '/', search: { pipeline_id: p.id } as never })}
                    >
                      <td className="px-4 py-2.5" style={{ minWidth: '200px', maxWidth: '300px' }}>
                        <span
                          className={`block font-medium leading-snug ${disrupted ? '' : 'text-foreground/70'}`}
                          title={p.name}
                          style={{ overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
                        >
                          {p.name}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground" style={{ maxWidth: '200px' }}>
                        {p.owner && (
                          <span
                            title={p.owner}
                            style={{ overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
                          >
                            {p.owner}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground">
                        {countryName(p.from_country)}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground">
                        {countryName(p.to_country)}
                      </td>
                      <td className="px-4 py-2.5">
                        {commodityDot(p.commodity)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">
                        {formatCap(p)}
                      </td>
                      <td className="px-4 py-2.5">
                        <StateBadge state={p.physical_state} />
                      </td>
                      <td className="px-4 py-2.5 tabular-nums text-muted-foreground/70 text-[11px]">
                        {since}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground/70 text-[11px]" style={{ maxWidth: '180px' }}>
                        {p.states_served && (
                          <span title={p.states_served}>
                            {p.states_served.split(',').slice(0, 4).join(', ')}{p.states_served.split(',').length > 4 ? ` +${p.states_served.split(',').length - 4}` : ''}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5" style={{ maxWidth: '280px' }}>
                        {p.disruption_event_type && (
                          <span className="mr-1.5 rounded bg-muted/80 px-1 py-px text-[9px] uppercase tracking-wide text-muted-foreground">
                            {p.disruption_event_type}
                          </span>
                        )}
                        {p.disruption_description && (
                          <span
                            className="text-muted-foreground/60 text-[11px]"
                            style={{ overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
                          >
                            {p.disruption_description}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2.5">
                        <div className="flex items-center gap-1">
                          {hasMap ? (
                            <button
                              title="View on map"
                              className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground/50 transition-colors hover:bg-muted hover:text-primary"
                              onClick={(e) => {
                                e.stopPropagation()
                                navigate({ to: '/', search: { pipeline_id: p.id } as never })
                              }}
                            >
                              <MapPin size={13} />
                            </button>
                          ) : (
                            <span
                              className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground/20"
                              title="US domestic (no map coordinates)"
                            >
                              <MapPin size={13} />
                            </span>
                          )}
                          {p.disruption_description && (
                            <button
                              title={isExpanded ? 'Collapse' : 'Show disruption detail'}
                              className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground/50 transition-colors hover:bg-muted hover:text-foreground"
                              onClick={(e) => {
                                e.stopPropagation()
                                setExpanded(isExpanded ? null : p.id)
                              }}
                            >
                              {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isExpanded && p.disruption_description && (
                      <tr key={`${p.id}-detail`} className="border-b border-border/30 bg-muted/20">
                        <td colSpan={11} className="px-6 py-3">
                          <div className="max-w-2xl">
                            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                              {p.disruption_event_type ?? 'Disruption'} details
                            </div>
                            <p className="text-xs leading-relaxed text-foreground/80">
                              {p.disruption_description}
                            </p>
                            {since && (
                              <div className="mt-1.5 text-[10px] text-muted-foreground">
                                Since {since}
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
