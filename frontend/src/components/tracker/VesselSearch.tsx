import { useState, useRef } from 'react'
import { Search } from 'lucide-react'
import type { Vessel } from '@/lib/api'

/** Case-insensitive substring match over name, MMSI string, and destination. */
export function searchVessels(vessels: Vessel[], query: string): Vessel[] {
  const q = query.toLowerCase().trim()
  if (!q) return []
  return vessels
    .filter(
      (v) =>
        (v.name && v.name.toLowerCase().includes(q)) ||
        String(v.mmsi).includes(q) ||
        (v.destination && v.destination.toLowerCase().includes(q)),
    )
    .slice(0, 20)
}

export function VesselSearch({
  vessels,
  onSelect,
}: {
  vessels: Vessel[]
  onSelect: (v: Vessel) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const results = searchVessels(vessels, query)

  function pick(v: Vessel) {
    onSelect(v)
    setQuery('')
    setOpen(false)
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-1.5 rounded-md border border-border bg-background/80 px-2 py-1.5 text-sm backdrop-blur">
        <Search size={13} className="text-muted-foreground" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Search vessel, MMSI, destination..."
          className="w-full bg-transparent outline-none placeholder:text-muted-foreground"
        />
      </div>
      {open && results.length > 0 && (
        <div className="absolute left-0 top-full z-[1100] mt-1 w-full overflow-hidden rounded-md border border-border bg-background shadow-lg">
          {results.map((v) => (
            <button
              key={v.mmsi}
              onMouseDown={() => pick(v)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-muted"
            >
              <span className="font-medium">{v.name ?? `MMSI ${v.mmsi}`}</span>
              <span className="ml-auto text-xs text-muted-foreground">{v.segment}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
