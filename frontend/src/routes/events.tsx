import { useState, useMemo } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { MapPin } from 'lucide-react'
import { useEvents, type AisEvent } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { SubscribeFeed } from '@/components/SubscribeFeed'

export const Route = createFileRoute('/events')({ component: EventsPage })

const TYPE_LABELS: Record<string, string> = {
  dark_voyage: 'Dark Voyage',
  spoof: 'Position Jump',
  gap: 'Signal Lost',
  loiter: 'Loitering',
  sts: 'STS Candidate',
  reroute: 'Reroute',
}

const TYPE_COLORS: Record<string, string> = {
  gap: 'bg-red-500/20 text-red-400 border-red-500/30',
  loiter: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  sts: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  reroute: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  dark_voyage: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  spoof: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
}

// Higher = more concerning, shown first when unfiltered
const SEVERITY: Record<string, number> = {
  dark_voyage: 6,
  spoof: 5,
  gap: 4,
  loiter: 3,
  sts: 2,
  reroute: 1,
}

const ORDERED_TYPES = ['dark_voyage', 'spoof', 'gap', 'loiter', 'sts', 'reroute'] as const

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts + 'Z').getTime()
  const h = Math.floor(diff / 3_600_000)
  if (h < 1) return 'just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function durationLabel(ev: AisEvent): string {
  const d = ev.details
  if (typeof d.duration_hours === 'number') return `${d.duration_hours}h`
  if (typeof d.silence_hours === 'number') return `${d.silence_hours}h silent`
  return ''
}

export default function EventsPage() {
  const [typeFilter, setTypeFilter] = useState<string | undefined>()
  const [days, setDays] = useState(7)

  // Fetch all events (no type filter in query - filter client-side for counts)
  const { data, isLoading } = useEvents({ days, limit: 500 })
  const navigate = useNavigate()

  function goToTracker(ev: AisEvent) {
    navigate({ to: '/', search: { mmsi: ev.mmsi, lat: ev.lat, lon: ev.lon } as never })
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const ev of data?.events ?? []) {
      c[ev.type] = (c[ev.type] ?? 0) + 1
    }
    return c
  }, [data])

  const sorted = useMemo(() => {
    const evs = data?.events ?? []
    const filtered = typeFilter ? evs.filter(e => e.type === typeFilter) : evs
    return [...filtered].sort((a, b) => {
      const sA = SEVERITY[a.type] ?? 0
      const sB = SEVERITY[b.type] ?? 0
      if (sA !== sB) return sB - sA
      return new Date(b.start_ts).getTime() - new Date(a.start_ts).getTime()
    })
  }, [data, typeFilter])

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Intelligence Events</h1>

        <div className="flex flex-wrap gap-2">
          {ORDERED_TYPES.map((t) => {
            const n = counts[t] ?? 0
            if (!isLoading && n === 0) return null
            return (
              <button
                key={t}
                onClick={() => setTypeFilter(typeFilter === t ? undefined : t)}
                className={`rounded-full border px-3 py-0.5 text-xs font-medium transition-colors
                  ${typeFilter === t ? TYPE_COLORS[t] : 'border-border text-muted-foreground hover:text-foreground'}`}
              >
                {TYPE_LABELS[t]}
                {n > 0 && (
                  <span className={`ml-1.5 rounded-full px-1 text-[10px] ${typeFilter === t ? 'bg-white/20' : 'bg-muted'}`}>
                    {n}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <SubscribeFeed />
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
          >
            {[1, 3, 7, 14, 30].map((d) => (
              <option key={d} value={d}>Last {d}d</option>
            ))}
          </select>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            {isLoading
              ? 'Loading...'
              : typeFilter
                ? `${sorted.length} ${TYPE_LABELS[typeFilter]?.toLowerCase()} events`
                : `${sorted.length} events`}
            {!typeFilter && sorted.length >= 500 && (
              <span className="ml-2 text-xs font-normal">(500 shown, filter by type to see more)</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {!isLoading && sorted.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-muted-foreground">
              No events in this window yet. Gap and loitering detection requires 48h+ of vessel
              history; STS candidates are rare. Check back after a few days.
            </div>
          )}
          {sorted.map((ev) => (
            <EventRow key={ev.event_id} ev={ev} onSelect={goToTracker} />
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

function EventRow({ ev, onSelect }: { ev: AisEvent; onSelect: (ev: AisEvent) => void }) {
  return (
    <button
      className="flex w-full items-start gap-3 border-b border-border px-4 py-3 text-left last:border-0 hover:bg-muted/30"
      onClick={() => onSelect(ev)}
    >
      <span
        className={`mt-0.5 shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${TYPE_COLORS[ev.type]}`}
      >
        {TYPE_LABELS[ev.type]}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-medium">
            {ev.vessel_name || `MMSI ${ev.mmsi}`}
          </span>
          {ev.type === 'sts' && ev.vessel2_name && (
            <span className="text-muted-foreground text-xs">+ {ev.vessel2_name || `MMSI ${ev.mmsi2}`}</span>
          )}
          {ev.segment && (
            <span className="text-xs text-muted-foreground">{ev.segment}</span>
          )}
        </div>
        <div className="text-xs text-muted-foreground">
          {ev.region && <span className="mr-2">{ev.region.replace(/_/g, ' ')}</span>}
          {ev.type === 'reroute' && typeof ev.details.old_destination === 'string' && (
            <span>
              <span className="line-through">{ev.details.old_destination}</span>
              <span className="mx-1">-&gt;</span>
              <span>{ev.details.new_destination as string ?? '?'}</span>
            </span>
          )}
          {ev.type === 'spoof' && typeof ev.details.jump_km === 'number' && (
            <span>{(ev.details.jump_km as number).toFixed(0)} km jump in {(ev.details.dt_minutes as number).toFixed(0)} min</span>
          )}
          {ev.type !== 'reroute' && ev.type !== 'spoof' && durationLabel(ev) && <span>{durationLabel(ev)}</span>}
        </div>
      </div>

      <div className="shrink-0 flex flex-col items-end gap-1 text-xs text-muted-foreground">
        <div>{timeAgo(ev.start_ts)}</div>
        <MapPin size={11} className="text-muted-foreground/40" />
      </div>
    </button>
  )
}
