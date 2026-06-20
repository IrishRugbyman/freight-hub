import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { Ship } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useRecentEventCount } from '@/lib/api'

function NavItem({ to, children, disabled }: { to?: string; children: React.ReactNode; disabled?: boolean }) {
  if (disabled) {
    return (
      <span className="cursor-not-allowed px-3 py-1.5 text-sm text-muted-foreground/40">
        {children}{' '}
        <span className="rounded bg-muted px-1 py-px text-[9px] font-medium tracking-wide text-muted-foreground/60">
          soon
        </span>
      </span>
    )
  }
  return (
    <Link
      to={to}
      className="rounded px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground [&.active]:bg-primary/10 [&.active]:font-medium [&.active]:text-primary"
      activeProps={{ className: 'active' }}
    >
      {children}
    </Link>
  )
}

function RootLayout() {
  const { data: eventCount } = useRecentEventCount()

  return (
    <div className="flex h-full flex-col">
      <header className={cn('flex items-center gap-5 border-b border-border bg-card/40 px-4 py-2')}>
        <div className="flex items-center gap-2">
          <Ship size={16} className="text-primary" strokeWidth={2.5} />
          <span className="text-sm font-semibold tracking-tight">Freight Hub</span>
        </div>
        <nav className="flex items-center gap-0.5">
          <NavItem to="/">Tracker</NavItem>
          <NavItem to="/fleet">Fleet</NavItem>
          <NavItem to="/pipelines">Pipelines</NavItem>
          <span className="mx-1.5 h-3.5 w-px bg-border" />
          <NavItem to="/analytics">Analytics</NavItem>
          <NavItem to="/events">
            <span className="flex items-center gap-1.5">
              Events
              {eventCount != null && eventCount > 0 && (
                <span className="rounded-full bg-primary/20 px-1.5 py-px text-[10px] font-semibold tabular-nums text-primary">
                  {eventCount > 99 ? '99+' : eventCount}
                </span>
              )}
            </span>
          </NavItem>
          <span className="mx-1.5 h-3.5 w-px bg-border" />
          <NavItem to="/routes">Routes</NavItem>
          <NavItem to="/dispersion">Dispersion</NavItem>
        </nav>
        <a
          href="https://quant.lbzgiu.xyz"
          className="ml-auto text-xs text-muted-foreground/60 transition-colors hover:text-muted-foreground"
        >
          quant portfolio
        </a>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}

export const Route = createRootRoute({
  component: RootLayout,
})
