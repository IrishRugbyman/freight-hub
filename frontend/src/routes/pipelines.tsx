import { useMemo, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { usePipelines, type PipelineSegment } from '@/lib/api'

export const Route = createFileRoute('/pipelines')({
  component: PipelinesPage,
})

function stateBg(state: string): string {
  if (state === 'offline') return 'bg-red-500/15 text-red-300'
  if (state === 'reduced') return 'bg-orange-500/15 text-orange-300'
  if (state === 'flowing') return 'bg-emerald-500/15 text-emerald-400'
  return 'bg-muted text-muted-foreground'
}

function commodityColor(c: string): string {
  return c === 'oil' ? 'text-amber-400' : 'text-blue-400'
}

function formatCap(p: PipelineSegment): string {
  if (p.commodity === 'oil' && p.capacity_mbd && p.capacity_mbd > 0) {
    return `${p.capacity_mbd.toFixed(2)} mbd`
  }
  if (p.capacity_bcm_yr && p.capacity_bcm_yr > 0) {
    return `${p.capacity_bcm_yr.toFixed(1)} bcm/yr`
  }
  return '-'
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

type SortKey = 'name' | 'from_country' | 'to_country' | 'capacity' | 'state' | 'commodity' | 'since'
type SortDir = 'asc' | 'desc'

function sortPipelines(rows: PipelineSegment[], key: SortKey, dir: SortDir): PipelineSegment[] {
  const mult = dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    let cmp = 0
    if (key === 'name') cmp = a.name.localeCompare(b.name)
    else if (key === 'from_country') cmp = a.from_country.localeCompare(b.from_country)
    else if (key === 'to_country') cmp = a.to_country.localeCompare(b.to_country)
    else if (key === 'commodity') cmp = a.commodity.localeCompare(b.commodity)
    else if (key === 'state') cmp = a.physical_state.localeCompare(b.physical_state)
    else if (key === 'since') {
      const as = sinceStr(a.disruption_since)
      const bs = sinceStr(b.disruption_since)
      cmp = as.localeCompare(bs)
    } else if (key === 'capacity') {
      const ac = a.commodity === 'oil' ? (a.capacity_mbd ?? 0) : (a.capacity_bcm_yr ?? 0) / 10
      const bc = b.commodity === 'oil' ? (b.capacity_mbd ?? 0) : (b.capacity_bcm_yr ?? 0) / 10
      cmp = ac - bc
    }
    return cmp * mult
  })
}

export default function PipelinesPage() {
  const { data, isLoading } = usePipelines(false)
  const pipelines = data?.pipelines ?? []

  const [q, setQ] = useState('')
  const [stateFilter, setStateFilter] = useState<string>('all')
  const [commodityFilter, setCommodityFilter] = useState<string>('all')
  const [sortKey, setSortKey] = useState<SortKey>('capacity')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expanded, setExpanded] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const qLower = q.toLowerCase()
    let rows = pipelines.filter(p => {
      if (stateFilter !== 'all' && p.physical_state !== stateFilter) return false
      if (commodityFilter !== 'all' && p.commodity !== commodityFilter) return false
      if (qLower && !p.name.toLowerCase().includes(qLower) &&
          !p.from_country.toLowerCase().includes(qLower) &&
          !p.to_country.toLowerCase().includes(qLower)) return false
      return true
    })
    return sortPipelines(rows, sortKey, sortDir)
  }, [pipelines, q, stateFilter, commodityFilter, sortKey, sortDir])

  const offline = pipelines.filter(p => p.physical_state === 'offline')
  const reduced = pipelines.filter(p => p.physical_state === 'reduced')
  const offlineMbd = offline.reduce((s, p) => s + (p.capacity_mbd ?? 0), 0)
  const offlineBcm = offline.reduce((s, p) => s + (p.capacity_bcm_yr ?? 0), 0)
  const reducedMbd = reduced.reduce((s, p) => s + (p.capacity_mbd ?? 0), 0)
  const reducedBcm = reduced.reduce((s, p) => s + (p.capacity_bcm_yr ?? 0), 0)

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
            sub="with coordinates"
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
            color="text-emerald-400"
          />
        </div>
      </div>

      {/* Filter bar */}
      <div className="shrink-0 border-b border-border/60 bg-background/95 px-4 py-2 flex flex-wrap items-center gap-3">
        <input
          className="h-7 w-56 rounded border border-border bg-muted/40 px-2.5 text-xs placeholder:text-muted-foreground focus:border-primary/60 focus:outline-none"
          placeholder="Search by name or country..."
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
                  Capacity<SortIcon k="capacity" />
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
                <th className="px-4 py-2 font-medium">Disruption</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(p => {
                const since = sinceStr(p.disruption_since)
                const isExpanded = expanded === p.id
                return (
                  <>
                    <tr
                      key={p.id}
                      className="cursor-pointer border-b border-border/30 hover:bg-muted/30 transition-colors"
                      onClick={() => setExpanded(isExpanded ? null : p.id)}
                    >
                      <td className="max-w-[220px] px-4 py-2">
                        <span className="block truncate font-medium" title={p.name}>
                          {p.name}
                        </span>
                      </td>
                      <td className="px-4 py-2 font-mono text-muted-foreground">
                        {p.from_country}
                      </td>
                      <td className="px-4 py-2 font-mono text-muted-foreground">
                        {p.to_country}
                      </td>
                      <td className="px-4 py-2">
                        <span className={`font-medium ${commodityColor(p.commodity)}`}>
                          {p.commodity}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {formatCap(p)}
                      </td>
                      <td className="px-4 py-2">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${stateBg(p.physical_state)}`}>
                          {p.physical_state}
                        </span>
                      </td>
                      <td className="px-4 py-2 tabular-nums text-muted-foreground">
                        {since}
                      </td>
                      <td className="max-w-[260px] px-4 py-2">
                        {p.disruption_event_type && (
                          <span className="mr-1.5 rounded bg-muted px-1 py-px text-[9px] uppercase tracking-wide">
                            {p.disruption_event_type}
                          </span>
                        )}
                        {p.disruption_description && (
                          <span
                            className="truncate text-muted-foreground/70"
                            style={{ display: 'block', maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          >
                            {p.disruption_description}
                          </span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && p.disruption_description && (
                      <tr key={`${p.id}-detail`} className="border-b border-border/30 bg-muted/20">
                        <td colSpan={8} className="px-6 py-3">
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
