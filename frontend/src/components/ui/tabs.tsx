import { useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'

export interface TabItem {
  id: string
  label: string
  count?: number
}

interface TabsProps {
  tabs: TabItem[]
  value: string
  onChange: (id: string) => void
  className?: string
}

export function Tabs({ tabs, value, onChange, className }: TabsProps) {
  const listRef = useRef<HTMLDivElement>(null)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
      let next = idx
      if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length
      else if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length
      else if (e.key === 'Home') next = 0
      else if (e.key === 'End') next = tabs.length - 1
      else return
      e.preventDefault()
      onChange(tabs[next].id)
      const btns = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      btns?.[next]?.focus()
    },
    [tabs, onChange],
  )

  return (
    <div
      className={cn(
        'sticky top-0 z-10 bg-background/90 backdrop-blur border-b border-border',
        className,
      )}
    >
      <div
        ref={listRef}
        role="tablist"
        aria-label="Analytics tabs"
        className="mx-auto flex max-w-5xl gap-1 overflow-x-auto px-4 py-2 scrollbar-none"
      >
        {tabs.map((tab, idx) => {
          const active = tab.id === value
          return (
            <button
              key={tab.id}
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={active}
              aria-controls={`tabpanel-${tab.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(tab.id)}
              onKeyDown={(e) => handleKeyDown(e, idx)}
              className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-0.5 text-xs font-medium transition-colors whitespace-nowrap',
                active
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/30',
              )}
            >
              {tab.label}
              {tab.count != null && (
                <span
                  className={cn(
                    'rounded-full px-1.5 py-px text-[10px] tabular-nums',
                    active ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-muted text-muted-foreground',
                  )}
                >
                  {tab.count}
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
