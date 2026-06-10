import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Panel({
  children,
  className,
  title,
}: {
  children: ReactNode
  className?: string
  title?: string
}) {
  return (
    <div
      className={cn(
        'rounded-md border border-border bg-card/90 backdrop-blur-sm shadow-lg',
        className,
      )}
    >
      {title && (
        <div className="border-b border-border px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </div>
      )}
      {children}
    </div>
  )
}
