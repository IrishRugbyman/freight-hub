import { useMemo } from 'react'
import type { Vessel } from '@/lib/api'
import { colorFor, SEGMENTS_BY_KIND } from '@/lib/segments'

type KindGroup = { kind: string; label: string; total: number; rows: { segment: string; n: number }[] }

/** Live vessel count legend grouped by kind, ordered by segment size. */
export function CountsPanel({ vessels }: { vessels: Vessel[] }) {
  const groups = useMemo((): KindGroup[] => {
    const counts = new Map<string, number>()
    for (const v of vessels) {
      const key = `${v.kind}/${v.segment ?? 'Unknown'}`
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }

    return (['tanker', 'bulk'] as const).map((kind) => {
      const segOrder = SEGMENTS_BY_KIND[kind]
      const rows = segOrder
        .map((seg) => ({ segment: seg, n: counts.get(`${kind}/${seg}`) ?? 0 }))
        .filter((r) => r.n > 0)
      return { kind, label: kind === 'tanker' ? 'Tankers' : 'Bulk carriers', total: rows.reduce((s, r) => s + r.n, 0), rows }
    }).filter((g) => g.total > 0)
  }, [vessels])

  if (!groups.length) return <div className="p-3 text-xs text-muted-foreground">No vessels</div>

  return (
    <div className="max-h-[45vh] overflow-y-auto py-2">
      {groups.map((g, gi) => (
        <div key={g.kind} className={gi > 0 ? 'border-t border-border pt-2 mt-1' : ''}>
          <div className="flex items-center justify-between px-3 pb-1">
            <span className="text-xs font-medium text-muted-foreground">{g.label}</span>
            <span className="text-xs font-mono tabular-nums text-muted-foreground">{g.total}</span>
          </div>
          <ul>
            {g.rows.map((r) => (
              <li key={r.segment} className="flex items-center gap-2 px-3 py-0.5 text-xs hover:bg-muted/40">
                <span
                  className="inline-block h-2 w-2 shrink-0 rounded-sm"
                  style={{ background: colorFor(g.kind, r.segment) }}
                />
                <span className="flex-1 text-foreground/80">{r.segment}</span>
                <span className="font-mono tabular-nums">{r.n}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <div className="mt-2 border-t border-border px-3 pt-2 text-xs font-medium tabular-nums">
        {vessels.length} total
      </div>
    </div>
  )
}
