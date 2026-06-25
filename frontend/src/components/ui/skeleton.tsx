import * as React from 'react'
import { cn } from '@/lib/utils'

/**
 * Pulse placeholder. Replaces bare "Loading..." text so the loading frame
 * matches the shape of the content that lands (Section 4.5, taste-skill).
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden className={cn('animate-pulse rounded bg-muted/60', className)} {...props} />
}

// Deterministic, varied bar widths so skeleton rows don't read as a uniform
// block. Cycles per column index (no Math.random, stable across renders).
const CELL_WIDTHS = ['72%', '56%', '64%', '40%', '48%', '60%', '44%', '52%', '36%', '58%']

/**
 * `rows` × `cols` of skeleton `<tr>`s for a table `<tbody>`. Drop straight in:
 * `{isLoading && <SkeletonTableRows rows={12} cols={COLUMNS.length} />}`.
 */
export function SkeletonTableRows({ rows = 12, cols }: { rows?: number; cols: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-b border-border/40">
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c} className="px-2.5 py-1.5">
              <Skeleton className="h-3" style={{ width: CELL_WIDTHS[(r + c) % CELL_WIDTHS.length] }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

/** A grid of KPI-card skeletons matching the Market State / fleet snapshot tiles. */
export function SkeletonKpis({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-6 w-1/2" />
          <Skeleton className="h-2.5 w-3/4" />
        </div>
      ))}
    </div>
  )
}

/** Stacked skeleton list rows for feeds / side panels (events, pipeline list). */
export function SkeletonListRows({ rows = 8 }: { rows?: number }) {
  return (
    <div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-start gap-3 border-b border-border px-4 py-3 last:border-0">
          <Skeleton className="mt-0.5 h-4 w-16 shrink-0" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3.5" style={{ width: CELL_WIDTHS[i % CELL_WIDTHS.length] }} />
            <Skeleton className="h-2.5 w-2/5" />
          </div>
          <Skeleton className="h-2.5 w-10 shrink-0" />
        </div>
      ))}
    </div>
  )
}
