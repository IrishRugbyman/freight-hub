import type { Filters } from './types'
import { segmentsForKind, type Kind } from '@/lib/segments'

const selectCls =
  'w-full rounded border border-border bg-secondary px-2 py-1 text-sm text-foreground focus:border-primary focus:outline-none'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

/** Cascading kind → segment → region filter. Changing kind resets segment. */
export function FilterControls({
  filters,
  regions,
  onChange,
}: {
  filters: Filters
  regions: string[]
  onChange: (f: Filters) => void
}) {
  const segments = segmentsForKind((filters.kind as Kind) || '')
  return (
    <div className="space-y-2 px-3 py-2">
      <Field label="Vessel type">
        <select
          className={selectCls}
          value={filters.kind ?? ''}
          onChange={(e) => onChange({ ...filters, kind: e.target.value || undefined, segment: undefined })}
        >
          <option value="">All</option>
          <option value="bulk">Bulk carriers</option>
          <option value="tanker">Tankers</option>
        </select>
      </Field>
      <Field label="Segment">
        <select
          className={selectCls}
          value={filters.segment ?? ''}
          onChange={(e) => onChange({ ...filters, segment: e.target.value || undefined })}
        >
          <option value="">All sizes</option>
          {segments.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Region">
        <select
          className={selectCls}
          value={filters.region ?? ''}
          onChange={(e) => onChange({ ...filters, region: e.target.value || undefined })}
        >
          <option value="">Worldwide</option>
          {regions.map((r) => (
            <option key={r} value={r}>
              {r.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </Field>
    </div>
  )
}
