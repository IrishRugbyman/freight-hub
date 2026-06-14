import { useState, useRef, useEffect } from 'react'

/** Builds an absolute feed URL from the current origin so it works in dev and prod. */
function feedUrl(path: string): string {
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://freight.lbzgiu.xyz'
  return `${origin}${path}`
}

const FEEDS = [
  { label: 'Atom / RSS', path: '/api/feed.xml' },
  { label: 'JSON Feed', path: '/api/feed.json' },
] as const

/** RSS icon + popover exposing the high-risk-events Atom and JSON feed URLs. */
export function SubscribeFeed() {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  async function copy(path: string) {
    const url = feedUrl(path)
    try {
      await navigator.clipboard.writeText(url)
      setCopied(path)
      setTimeout(() => setCopied((c) => (c === path ? null : c)), 1500)
    } catch {
      window.open(url, '_blank', 'noopener')
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Subscribe to high-risk events feed"
        aria-expanded={open}
        title="Subscribe (RSS / JSON Feed)"
        className={`flex items-center gap-1.5 rounded-full border px-3 py-0.5 text-xs font-medium transition-colors
          ${open ? 'border-orange-500/40 bg-orange-500/10 text-orange-300' : 'border-border text-muted-foreground hover:text-foreground'}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="6.18" cy="17.82" r="2.18" />
          <path d="M4 4.44v2.83c7.03 0 12.73 5.7 12.73 12.73h2.83C19.56 11.4 12.6 4.44 4 4.44z" />
          <path d="M4 10.1v2.83c3.9 0 7.07 3.17 7.07 7.07h2.83C13.9 14.47 9.43 10.1 4 10.1z" />
        </svg>
        Subscribe
      </button>

      {open && (
        <div className="absolute right-0 z-[1000] mt-2 w-72 rounded-lg border border-border bg-card p-3 shadow-xl">
          <div className="mb-2 text-xs font-medium text-foreground">High-risk events feed</div>
          <p className="mb-3 text-[11px] leading-snug text-muted-foreground">
            Dark voyages, AIS gaps, position jumps, loitering and STS candidates. Add a URL to any
            feed reader.
          </p>
          <div className="space-y-1.5">
            {FEEDS.map((f) => (
              <div key={f.path} className="flex items-center gap-2">
                <a
                  href={feedUrl(f.path)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex-1 truncate rounded border border-border bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground hover:text-foreground"
                  title={feedUrl(f.path)}
                >
                  {f.path}
                </a>
                <button
                  onClick={() => copy(f.path)}
                  className="shrink-0 rounded border border-border px-2 py-1 text-[10px] font-medium text-muted-foreground hover:text-foreground"
                >
                  {copied === f.path ? 'Copied' : 'Copy'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
