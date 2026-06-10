import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { Ship } from 'lucide-react'
import { cn } from '@/lib/utils'

function NavItem({ to, children, disabled }: { to?: string; children: string; disabled?: boolean }) {
  if (disabled) {
    return (
      <span className="cursor-not-allowed px-3 py-1.5 text-sm text-muted-foreground/50">
        {children} <span className="text-[10px] uppercase">soon</span>
      </span>
    )
  }
  return (
    <Link
      to={to}
      className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground [&.active]:text-foreground [&.active]:font-medium"
      activeProps={{ className: 'active' }}
    >
      {children}
    </Link>
  )
}

export const Route = createRootRoute({
  component: () => (
    <div className="flex h-full flex-col">
      <header className={cn('flex items-center gap-4 border-b border-border px-4 py-2.5')}>
        <div className="flex items-center gap-2 font-semibold">
          <Ship size={18} className="text-primary" />
          Freight Hub
        </div>
        <nav className="flex items-center">
          <NavItem to="/">Tracker</NavItem>
          <NavItem to="/routes">Routes</NavItem>
          <NavItem to="/dispersion">Dispersion</NavItem>
          <NavItem to="/analytics">Analytics</NavItem>
        </nav>
        <a
          href="https://quant.lbzgiu.xyz"
          className="ml-auto text-sm text-muted-foreground hover:text-foreground"
        >
          ← quant portfolio
        </a>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  ),
})
