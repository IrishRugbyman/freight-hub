import { useMemo } from 'react'
import { AlertTriangle, RadioTower } from 'lucide-react'
import { useMeta } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * States the upstream AIS feed's condition when it is not healthy.
 *
 * The failure this exists for: every vessel query filters to VISIBLE_HOURS, so
 * an outage lasting more than a day empties the result set and the map renders
 * blank with nothing to explain it. A visitor reads a blank map as a broken
 * site. Saying "the upstream feed stopped at <time>" costs one line and is the
 * difference between looking broken and being transparent.
 *
 * Nothing is rendered while the feed is healthy: a permanent status strip is
 * noise, and noise is what makes a real warning invisible.
 */

/** "3 days" / "7 hours" / "45 minutes" - coarse on purpose, this is not a clock. */
function humanAge(minutes: number): string {
  if (minutes < 90) return `${Math.round(minutes)} minutes`
  const hours = minutes / 60
  if (hours < 36) return `${Math.round(hours)} hours`
  return `${Math.round(hours / 24)} days`
}

export function FeedBanner() {
  const { data: meta } = useMeta()
  const feed = meta?.feed

  const content = useMemo(() => {
    if (!feed || feed.state === 'live') return null

    // timeZone is pinned to UTC because the label says UTC. Formatting in the
    // viewer's zone and calling it UTC would be wrong for everyone outside it,
    // and quietly so - it only looks right when you happen to test from UTC.
    // AIS timestamps are UTC throughout the rest of the hub, so this matches.
    const since = feed.last_seen
      ? new Date(feed.last_seen + 'Z').toLocaleString(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short',
          timeZone: 'UTC',
        })
      : null
    const age = feed.age_minutes != null ? humanAge(feed.age_minutes) : null

    if (feed.state === 'unknown') {
      return {
        tone: 'warn' as const,
        headline: 'No AIS positions have been collected yet.',
        detail: 'The live map will populate once the collector receives its first messages.',
      }
    }
    if (feed.state === 'stale') {
      return {
        tone: 'warn' as const,
        headline: `Live AIS feed is lagging: no new positions for ${age}.`,
        detail: since
          ? `Last message ${since} UTC. Positions older than ${feed.stale_hours}h are shown greyed.`
          : undefined,
      }
    }
    return {
      tone: 'down' as const,
      headline: `Live AIS feed is down: no new positions for ${age}.`,
      detail: since
        ? `Last message ${since} UTC. The upstream provider (aisstream.io) is accepting connections but ` +
          `sending no data; the map is empty for that reason, not because there are no ships. ` +
          `Historical analytics are unaffected.`
        : undefined,
    }
  }, [feed])

  if (!content) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'flex items-start gap-2.5 border-b px-4 py-2 text-xs leading-relaxed',
        content.tone === 'down'
          ? 'border-destructive/30 bg-destructive/10 text-destructive'
          : 'border-amber-500/30 bg-amber-500/10 text-amber-400',
      )}
    >
      {content.tone === 'down' ? (
        <AlertTriangle size={14} className="mt-px shrink-0" />
      ) : (
        <RadioTower size={14} className="mt-px shrink-0" />
      )}
      <p>
        <span className="font-medium">{content.headline}</span>
        {content.detail && <span className="ml-1.5 opacity-80">{content.detail}</span>}
      </p>
    </div>
  )
}
