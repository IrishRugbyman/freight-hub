import { useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEvents, type AisEvent } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/events')({ component: EventsPage })

const TYPE_LABELS: Record<string, string> = {
  gap: 'Signal Lost',
  loiter: 'Loitering',
  sts: 'STS Candidate',
  reroute: 'Reroute',
  dark_voyage: 'Dark Voyage',
  spoof: 'Position Jump',
}

const TYPE_COLORS: Record<string, string> = {
  gap: 'bg-red-500/20 text-red-400 border-red-500/30',
  loiter: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  sts: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  reroute: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  dark_voyage: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  spoof: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
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

  const { data, isLoading } = useEvents({ type: typeFilter, days, limit: 200 })
  const navigate = useNavigate()

  function goToTracker(ev: AisEvent) {
    navigate({ to: '/', search: { mmsi: ev.mmsi, lat: ev.lat, lon: ev.lon } as never })
  }

  const types = ['dark_voyage', 'spoof', 'gap', 'loiter', 'sts', 'reroute'] as const

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Intelligence Events</h1>

        <div className="flex gap-2">
          {types.map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(typeFilter === t ? undefined : t)}
              className={`rounded-full border px-3 py-0.5 text-xs font-medium transition-colors
                ${typeFilter === t ? TYPE_COLORS[t] : 'border-border text-muted-foreground hover:text-foreground'}`}
            >
              {TYPE_LABELS[t]}
            </button>
          ))}
        </div>

        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="ml-auto rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
        >
          {[1, 3, 7, 14, 30].map((d) => (
            <option key={d} value={d}>Last {d}d</option>
          ))}
        </select>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            {isLoading ? 'Loading...' : `${data?.total ?? 0} events`}
            <span className="ml-2 text-xs font-normal">
              (collecting since 2026-06-09 - feed fills in as history grows)
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {!isLoading && (!data?.events.length) && (
            <div className="px-4 py-8 text-center text-sm text-muted-foreground">
              No events in this window yet. Gap and loitering detection requires 48h+ of vessel
              history; STS candidates are rare. Check back after a few days.
            </div>
          )}
          {data?.events.map((ev) => (
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

      <div className="shrink-0 text-right text-xs text-muted-foreground">
        <div>{timeAgo(ev.start_ts)}</div>
        <div className="mt-0.5 font-mono text-[10px]">
          {ev.lat.toFixed(2)}, {ev.lon.toFixed(2)}
        </div>
      </div>
    </button>
  )
}
