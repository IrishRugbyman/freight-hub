import { useMemo } from 'react'
import type { Vessel } from '@/lib/api'
import { colorFor } from '@/lib/segments'

/** Live tally per (kind, segment), doubling as the map legend. */
export function CountsPanel({ vessels }: { vessels: Vessel[] }) {
  const rows = useMemo(() => {
    const m = new Map<string, { kind: string; segment: string; n: number }>()
    for (const v of vessels) {
      const seg = v.segment ?? 'Unknown'
      const key = `${v.kind}/${seg}`
      const cur = m.get(key) ?? { kind: v.kind, segment: seg, n: 0 }
      cur.n += 1
      m.set(key, cur)
    }
    return [...m.values()].sort((a, b) => b.n - a.n)
  }, [vessels])

  return (
    <div className="max-h-[40vh] overflow-y-auto p-3">
      <div className="mb-2 text-sm font-medium">{vessels.length} vessels</div>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={`${r.kind}/${r.segment}`} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ background: colorFor(r.kind, r.segment) }}
            />
            <span className="flex-1 text-muted-foreground">
              {r.segment} <span className="opacity-50">({r.kind})</span>
            </span>
            <span className="font-mono tabular-nums">{r.n}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
