import { useEffect, useRef, useState } from 'react'
import { createFileRoute, useNavigate, useSearch } from '@tanstack/react-router'
import { Download, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react'
import {
  useFleet,
  useFleetFacets,
  fleetExportUrl,
  type FleetParams,
  type FleetRow,
} from '@/lib/api'

export const Route = createFileRoute('/fleet')({
  component: FleetPage,
  validateSearch: (s: Record<string, unknown>): FleetParams => ({
    q: typeof s.q === 'string' ? s.q : undefined,
    flag: typeof s.flag === 'string' ? s.flag : undefined,
    owner: typeof s.owner === 'string' ? s.owner : undefined,
    paris_mou: typeof s.paris_mou === 'string' ? s.paris_mou : undefined,
    tokyo_mou: typeof s.tokyo_mou === 'string' ? s.tokyo_mou : undefined,
    class_society: typeof s.class_society === 'string' ? s.class_society : undefined,
    pi_club: typeof s.pi_club === 'string' ? s.pi_club : undefined,
    kind: typeof s.kind === 'string' ? s.kind : undefined,
    segment: typeof s.segment === 'string' ? s.segment : undefined,
    detention_min: typeof s.detention_min === 'number' ? s.detention_min : undefined,
    risk_min: typeof s.risk_min === 'number' ? s.risk_min : undefined,
    live_only: s.live_only === true || s.live_only === 'true',
    sort: typeof s.sort === 'string' ? s.sort : undefined,
    order: s.order === 'desc' ? 'desc' : s.order === 'asc' ? 'asc' : undefined,
    page: typeof s.page === 'number' ? s.page : undefined,
  }),
})

const MOU_COLORS: Record<string, string> = {
  White: 'text-emerald-400',
  Grey: 'text-yellow-400',
  Black: 'text-red-400',
}

function mouColor(v?: string) {
  return v ? (MOU_COLORS[v] ?? 'text-muted-foreground') : 'text-muted-foreground'
}

function detentionColor(v?: number | null) {
  if (v == null) return ''
  if (v >= 10) return 'text-red-400'
  if (v >= 5) return 'text-yellow-400'
  return 'text-emerald-400'
}

function riskColor(v?: number | null) {
  if (v == null) return 'text-muted-foreground'
  if (v >= 50) return 'text-red-400'
  if (v >= 25) return 'text-yellow-400'
  return 'text-emerald-400'
}

const COLUMNS: { key: string; label: string; sortable?: boolean }[] = [
  { key: 'imo', label: 'IMO' },
  { key: 'ship_name', label: 'Name', sortable: true },
  { key: 'flag', label: 'Flag', sortable: true },
  { key: 'ship_type', label: 'Type', sortable: true },
  { key: 'year_built', label: 'Built', sortable: true },
  { key: 'dwt', label: 'DWT', sortable: true },
  { key: 'owner', label: 'Owner', sortable: true },
  { key: 'class_society', label: 'Class', sortable: true },
  { key: 'pi_club', label: 'P&I' },
  { key: 'detention_rate_pct', label: 'Detention', sortable: true },
  { key: 'paris_mou', label: 'Paris', sortable: true },
  { key: 'tokyo_mou', label: 'Tokyo', sortable: true },
  { key: 'risk_score', label: 'Risk', sortable: true },
  { key: 'segment', label: 'Segment', sortable: true },
  { key: 'region', label: 'Region', sortable: true },
  { key: 'live', label: 'Live' },
]

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export default function FleetPage() {
  const search = useSearch({ from: '/fleet' })
  const navigate = useNavigate({ from: '/fleet' })

  const { data: facets } = useFleetFacets()

  // Local text-input state with debounce (avoids firing a request on every keystroke)
  const [qInput, setQInput] = useState(search.q ?? '')
  const [ownerInput, setOwnerInput] = useState(search.owner ?? '')
  const debouncedQ = useDebounce(qInput, 350)
  const debouncedOwner = useDebounce(ownerInput, 350)
  const firstRender = useRef(true)

  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return }
    navigate({
      search: (prev) => ({ ...prev, q: debouncedQ || undefined, page: undefined }),
      replace: true,
    })
  }, [debouncedQ])

  useEffect(() => {
    if (firstRender.current) return
    navigate({
      search: (prev) => ({ ...prev, owner: debouncedOwner || undefined, page: undefined }),
      replace: true,
    })
  }, [debouncedOwner])

  const params: FleetParams = {
    ...search,
    q: debouncedQ || undefined,
    owner: debouncedOwner || undefined,
  }

  const { data, isFetching } = useFleet(params)
  const rows = data?.rows ?? []
  const summary = data?.summary
  const total = data?.total ?? 0
  const page = data?.page ?? 1
  const pageSize = data?.page_size ?? 100
  const totalPages = Math.ceil(total / pageSize)

  function set(patch: Partial<FleetParams>) {
    navigate({ search: (prev) => ({ ...prev, ...patch, page: undefined }), replace: true })
  }

  function setPage(p: number) {
    navigate({ search: (prev) => ({ ...prev, page: p }), replace: true })
  }

  function toggleSort(col: string) {
    const cur = search.sort
    const curOrd = search.order ?? 'asc'
    if (cur === col) {
      set({ sort: col, order: curOrd === 'asc' ? 'desc' : 'asc' })
    } else {
      set({ sort: col, order: 'asc' })
    }
  }

  function clearFilters() {
    setQInput('')
    setOwnerInput('')
    navigate({ search: {}, replace: true })
  }

  const hasFilters = !!(
    search.q || search.flag || search.owner || search.paris_mou ||
    search.tokyo_mou || search.class_society || search.pi_club ||
    search.kind || search.segment || search.detention_min != null ||
    search.risk_min != null || search.live_only
  )

  function SortIcon({ col }: { col: string }) {
    if (search.sort !== col) return <ArrowUpDown size={11} className="text-muted-foreground/50" />
    if (search.order === 'desc') return <ArrowDown size={11} className="text-primary" />
    return <ArrowUp size={11} className="text-primary" />
  }

  function goToMap(row: FleetRow) {
    if (row.mmsi == null || row.lat == null || row.lon == null) return
    navigate({ to: '/', search: { mmsi: row.mmsi, lat: row.lat, lon: row.lon } as never })
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Filter bar */}
      <div className="shrink-0 border-b border-border bg-background px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="h-7 w-48 rounded border border-border bg-muted px-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            placeholder="Name, IMO, MMSI..."
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
          />
          <input
            className="h-7 w-40 rounded border border-border bg-muted px-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            placeholder="Owner..."
            value={ownerInput}
            onChange={(e) => setOwnerInput(e.target.value)}
          />

          {/* Flag dropdown */}
          <select
            className="h-7 rounded border border-border bg-muted px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
            value={search.flag ?? ''}
            onChange={(e) => set({ flag: e.target.value || undefined })}
          >
            <option value="">All flags</option>
            {facets?.flags.map((f) => (
              <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
            ))}
          </select>

          {/* Paris MOU */}
          <select
            className="h-7 rounded border border-border bg-muted px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
            value={search.paris_mou ?? ''}
            onChange={(e) => set({ paris_mou: e.target.value || undefined })}
          >
            <option value="">Paris MOU</option>
            {facets?.paris_mou.map((f) => (
              <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
            ))}
          </select>

          {/* Tokyo MOU */}
          <select
            className="h-7 rounded border border-border bg-muted px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
            value={search.tokyo_mou ?? ''}
            onChange={(e) => set({ tokyo_mou: e.target.value || undefined })}
          >
            <option value="">Tokyo MOU</option>
            {facets?.tokyo_mou.map((f) => (
              <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
            ))}
          </select>

          {/* Detention >= */}
          <select
            className="h-7 rounded border border-border bg-muted px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
            value={search.detention_min ?? ''}
            onChange={(e) => set({ detention_min: e.target.value ? Number(e.target.value) : undefined })}
          >
            <option value="">Detention</option>
            <option value="5">≥ 5%</option>
            <option value="10">≥ 10%</option>
            <option value="15">≥ 15%</option>
            <option value="20">≥ 20%</option>
          </select>

          {/* Risk >= */}
          <select
            className="h-7 rounded border border-border bg-muted px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
            value={search.risk_min ?? ''}
            onChange={(e) => set({ risk_min: e.target.value ? Number(e.target.value) : undefined, sort: e.target.value ? 'risk_score' : search.sort, order: e.target.value ? 'desc' : search.order })}
          >
            <option value="">Risk score</option>
            <option value="25">≥ 25 (elevated)</option>
            <option value="50">≥ 50 (high)</option>
            <option value="75">≥ 75 (critical)</option>
          </select>

          {/* Live only toggle */}
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              className="accent-primary"
              checked={search.live_only === true}
              onChange={(e) => set({ live_only: e.target.checked || undefined as never })}
            />
            Live only
          </label>

          {/* High risk preset */}
          <button
            onClick={() => set({ risk_min: 50, sort: 'risk_score', order: 'desc' })}
            className={`h-7 rounded border px-2 text-xs transition-colors ${
              search.risk_min === 50
                ? 'border-red-400/40 bg-red-400/10 text-red-400'
                : 'border-border text-muted-foreground hover:text-foreground'
            }`}
          >
            High risk
          </button>

          {hasFilters && (
            <button
              onClick={clearFilters}
              className="h-7 rounded border border-border px-2 text-xs text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          )}

          {/* Export */}
          <a
            href={fleetExportUrl(params)}
            download="fleet.csv"
            className="ml-auto flex h-7 items-center gap-1.5 rounded border border-border px-2.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <Download size={12} /> CSV
          </a>
        </div>
      </div>

      {/* Summary strip */}
      {summary && summary.total > 0 && (
        <div className="shrink-0 border-b border-border/60 bg-muted/20 px-4 py-1.5">
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="font-mono text-foreground">{summary.total.toLocaleString()} vessels</span>
            {summary.total_dwt != null && (
              <span>DWT {(summary.total_dwt / 1_000_000).toFixed(1)}M</span>
            )}
            {summary.avg_age_years != null && (
              <span>avg age {summary.avg_age_years}y</span>
            )}
            <span className="text-border">|</span>
            {summary.top_flags.map((f) => (
              <button
                key={f.value}
                onClick={() => set({ flag: search.flag === f.value ? undefined : f.value })}
                className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                  search.flag === f.value
                    ? 'bg-primary/20 text-primary'
                    : 'bg-muted/60 hover:bg-muted'
                }`}
              >
                {f.value} {f.count}
              </button>
            ))}
            {summary.top_owners.slice(0, 3).map((o) => (
              <button
                key={o.value}
                onClick={() => {
                  set({ owner: search.owner === o.value ? undefined : o.value })
                  setOwnerInput(search.owner === o.value ? '' : o.value)
                }}
                className={`max-w-[16ch] truncate rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                  search.owner === o.value
                    ? 'bg-primary/20 text-primary'
                    : 'bg-muted/60 hover:bg-muted'
                }`}
              >
                {o.value} {o.count}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Table */}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full min-w-[900px] text-xs">
          <thead className="sticky top-0 z-10 bg-background border-b border-border">
            <tr>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className={`px-2.5 py-2 text-left text-[10px] uppercase tracking-wide text-muted-foreground font-medium whitespace-nowrap
                    ${col.sortable ? 'cursor-pointer select-none hover:text-foreground' : ''}`}
                  onClick={col.sortable ? () => toggleSort(col.key) : undefined}
                >
                  <span className="flex items-center gap-1">
                    {col.label}
                    {col.sortable && <SortIcon col={col.key} />}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isFetching && rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-muted-foreground">
                  Loading...
                </td>
              </tr>
            )}
            {!isFetching && rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-muted-foreground">
                  {hasFilters
                    ? 'No vessels match the current filters.'
                    : 'Registry is still being populated by the crawler. Check back soon.'}
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr
                key={row.imo}
                onClick={() => goToMap(row)}
                className={`border-b border-border/40 transition-colors
                  ${row.mmsi != null ? 'cursor-pointer hover:bg-muted/40' : 'hover:bg-muted/20'}`}
              >
                <td className="px-2.5 py-1.5 font-mono text-muted-foreground">{row.imo}</td>
                <td className="px-2.5 py-1.5 font-medium">
                  {row.ship_name ?? row.live_name ?? (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
                <td className="px-2.5 py-1.5">
                  <button
                    className="hover:text-primary"
                    onClick={(e) => {
                      e.stopPropagation()
                      set({ flag: search.flag === row.flag ? undefined : row.flag })
                    }}
                  >
                    {row.flag ?? '—'}
                  </button>
                </td>
                <td className="px-2.5 py-1.5 text-muted-foreground">{row.ship_type ?? '—'}</td>
                <td className="px-2.5 py-1.5 font-mono">{row.year_built ?? '—'}</td>
                <td className="px-2.5 py-1.5 font-mono">
                  {row.dwt != null ? row.dwt.toLocaleString() : '—'}
                </td>
                <td className="max-w-[16ch] truncate px-2.5 py-1.5">
                  <button
                    className="hover:text-primary truncate text-left"
                    onClick={(e) => {
                      e.stopPropagation()
                      set({ owner: search.owner === row.owner ? undefined : row.owner })
                      setOwnerInput(search.owner === row.owner ? '' : (row.owner ?? ''))
                    }}
                  >
                    {row.owner ?? '—'}
                  </button>
                </td>
                <td className="max-w-[14ch] truncate px-2.5 py-1.5 text-muted-foreground">
                  {row.class_society ?? '—'}
                </td>
                <td className="max-w-[14ch] truncate px-2.5 py-1.5 text-muted-foreground">
                  {row.pi_club ?? '—'}
                </td>
                <td className={`px-2.5 py-1.5 font-mono ${detentionColor(row.detention_rate_pct)}`}>
                  {row.detention_rate_pct != null ? `${row.detention_rate_pct}%` : '—'}
                </td>
                <td className={`px-2.5 py-1.5 ${mouColor(row.paris_mou)}`}>
                  {row.paris_mou ?? '—'}
                </td>
                <td className={`px-2.5 py-1.5 ${mouColor(row.tokyo_mou)}`}>
                  {row.tokyo_mou ?? '—'}
                </td>
                <td className={`px-2.5 py-1.5 font-mono ${riskColor(row.risk_score)}`}
                    title={row.risk_indicators?.join('\n') ?? ''}>
                  {row.risk_score != null ? row.risk_score : '—'}
                </td>
                <td className="px-2.5 py-1.5 text-muted-foreground">{row.segment ?? '—'}</td>
                <td className="px-2.5 py-1.5 text-muted-foreground">
                  {row.region?.replace(/_/g, ' ') ?? '—'}
                </td>
                <td className="px-2.5 py-1.5">
                  {row.mmsi != null ? (
                    <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-400">
                      Live
                    </span>
                  ) : (
                    <span className="text-muted-foreground/40 text-[10px]">offline</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="shrink-0 border-t border-border px-4 py-2 flex items-center justify-between text-xs text-muted-foreground">
          <span>
            Page {page} of {totalPages} ({total.toLocaleString()} vessels)
          </span>
          <div className="flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="rounded border border-border px-2.5 py-1 disabled:opacity-40 hover:text-foreground"
            >
              Previous
            </button>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
              className="rounded border border-border px-2.5 py-1 disabled:opacity-40 hover:text-foreground"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
