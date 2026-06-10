import { cn } from '@/lib/utils'

export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-3 text-sm text-foreground/90 hover:text-foreground"
    >
      <span
        className={cn(
          'relative h-4 w-8 shrink-0 rounded-full transition-colors',
          checked ? 'bg-primary' : 'bg-secondary',
        )}
      >
        <span
          className={cn(
            'absolute left-0.5 top-0.5 h-3 w-3 rounded-full bg-white transition-transform',
            checked ? 'translate-x-4' : 'translate-x-0',
          )}
        />
      </span>
      {label}
    </button>
  )
}
