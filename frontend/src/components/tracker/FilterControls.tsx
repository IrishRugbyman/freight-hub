import type { Filters } from './types'
import { segmentsForKind, type Kind } from '@/lib/segments'

const selectCls =
  'w-full rounded border border-border bg-secondary px-2 py-1 text-sm text-foreground focus:border-primary focus:outline-none'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

/** Cascading kind → segment → region filter. Changing kind resets segment. */
export function FilterControls({
  filters,
  regions,
  flags = [],
  onChange,
}: {
  filters: Filters
  regions: string[]
  flags?: { code: string; name: string }[]
  onChange: (f: Filters) => void
}) {
  const segments = segmentsForKind((filters.kind as Kind) || '')
  // "Flag class" select maps to the foc/shadow booleans.
  const flagClass = filters.shadow ? 'shadow' : filters.foc ? 'foc' : ''
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
      <Field label="Flag class">
        <select
          className={selectCls}
          value={flagClass}
          onChange={(e) => {
            const v = e.target.value
            onChange({ ...filters, foc: v === 'foc' || undefined, shadow: v === 'shadow' || undefined })
          }}
        >
          <option value="">All flags</option>
          <option value="foc">Flags of convenience</option>
          <option value="shadow">Shadow-fleet flags</option>
        </select>
      </Field>
      {flags.length > 0 && (
        <Field label="Flag state">
          <select
            className={selectCls}
            value={filters.flag ?? ''}
            onChange={(e) => onChange({ ...filters, flag: e.target.value || undefined })}
          >
            <option value="">All states</option>
            {flags.map((f) => (
              <option key={f.code} value={f.code}>
                {f.name}
              </option>
            ))}
          </select>
        </Field>
      )}
    </div>
  )
}
