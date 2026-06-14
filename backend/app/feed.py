"""Syndication feeds (Atom 1.0 + JSON Feed 1.1) for high-risk maritime events.

Read-only public feeds over the same ``ais_events`` rows that power ``/api/events``.
No accounts, no SMTP: visitors subscribe in any feed reader. The feed surfaces only
high-risk event types by default (reroutes are excluded as low-signal noise).

Both builders take an already-enriched list of event dicts so the endpoint owns all
DB access and this module stays pure and unit-testable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from xml.sax.saxutils import escape

SITE_URL = "https://freight.lbzgiu.xyz"
FEED_TITLE = "Freight Hub - High-Risk Maritime Events"
FEED_SUBTITLE = (
    "Dark voyages, AIS gaps, position jumps, loitering and STS candidates "
    "detected from live AIS by freight.lbzgiu.xyz."
)
AUTHOR_NAME = "Freight Hub"
GENERATOR = "freight-api"

# High-risk types in descending severity. Reroute deliberately excluded (noise).
HIGH_RISK_TYPES: tuple[str, ...] = ("dark_voyage", "spoof", "gap", "loiter", "sts")

_TYPE_LABELS: dict[str, str] = {
    "dark_voyage": "Dark Voyage",
    "spoof": "Position Jump",
    "gap": "Signal Lost",
    "loiter": "Loitering",
    "sts": "STS Candidate",
    "reroute": "Reroute",
}


def _parse_ts(ts: str) -> datetime:
    """Parse a naive-UTC ISO timestamp string into an aware UTC datetime."""
    if not ts:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime.now(UTC)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _rfc3339(ts: str) -> str:
    """RFC 3339 / ISO 8601 with a trailing Z, as required by Atom and JSON Feed."""
    return _parse_ts(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_title(ev: dict) -> str:
    """Human title, e.g. 'Dark Voyage: FRONT ALTAIR near Strait of Hormuz'."""
    label = _TYPE_LABELS.get(ev.get("type", ""), ev.get("type", "Event").title())
    name = (ev.get("vessel_name") or "").strip() or f"MMSI {ev.get('mmsi')}"
    if ev.get("type") == "sts" and (ev.get("vessel2_name") or "").strip():
        name = f"{name} + {ev['vessel2_name'].strip()}"
    region = (ev.get("region") or "").strip().replace("_", " ").title()
    where = f" near {region}" if region else ""
    return f"{label}: {name}{where}"


def entry_summary(ev: dict) -> str:
    """One-line plain-text summary with vessel class and key details."""
    parts: list[str] = []
    seg = (ev.get("segment") or "").strip()
    kind = (ev.get("kind") or "").strip()
    if seg or kind:
        parts.append(" ".join(p for p in (seg, kind) if p))
    details = ev.get("details") or {}
    if isinstance(details, dict):
        if ev.get("type") == "gap" and details.get("gap_hours"):
            parts.append(f"silent for {details['gap_hours']}h")
        if ev.get("type") == "loiter" and details.get("duration_hours"):
            parts.append(f"loitered {details['duration_hours']}h")
        if ev.get("type") == "sts" and details.get("duration_hours"):
            parts.append(f"alongside {details['duration_hours']}h")
        if ev.get("type") == "spoof" and details.get("jump_km"):
            parts.append(f"jumped {details['jump_km']} km")
    lat, lon = ev.get("lat"), ev.get("lon")
    if lat is not None and lon is not None:
        parts.append(f"at {float(lat):.3f}, {float(lon):.3f}")
    return "; ".join(parts) if parts else "High-risk maritime event."


def entry_link(ev: dict) -> str:
    """Deep link to the tracker focused on the vessel."""
    mmsi = ev.get("mmsi")
    return f"{SITE_URL}/?mmsi={mmsi}" if mmsi else SITE_URL


def _entry_updated(ev: dict) -> str:
    return ev.get("end_ts") or ev.get("start_ts") or ""


def build_atom(events: list[dict], self_url: str) -> str:
    """Render an Atom 1.0 feed document from enriched event dicts."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = _rfc3339(_entry_updated(events[0])) if events else now

    lines: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(FEED_TITLE)}</title>",
        f"  <subtitle>{escape(FEED_SUBTITLE)}</subtitle>",
        f'  <link href="{escape(self_url)}" rel="self" type="application/atom+xml"/>',
        f'  <link href="{escape(SITE_URL)}/events" rel="alternate" type="text/html"/>',
        f"  <id>{escape(SITE_URL)}/api/feed.xml</id>",
        f"  <updated>{updated}</updated>",
        f"  <author><name>{escape(AUTHOR_NAME)}</name></author>",
        f'  <generator>{escape(GENERATOR)}</generator>',
    ]

    for ev in events:
        eid = str(ev.get("event_id", ""))
        urn = f"urn:freight-event:{escape(eid)}"
        link = escape(entry_link(ev))
        published = _rfc3339(ev.get("start_ts", ""))
        ent_updated = _rfc3339(_entry_updated(ev))
        lines += [
            "  <entry>",
            f"    <title>{escape(entry_title(ev))}</title>",
            f'    <link href="{link}" rel="alternate" type="text/html"/>',
            f"    <id>{urn}</id>",
            f"    <published>{published}</published>",
            f"    <updated>{ent_updated}</updated>",
            f'    <category term="{escape(str(ev.get("type", "")))}"/>',
            f"    <summary>{escape(entry_summary(ev))}</summary>",
            "  </entry>",
        ]

    lines.append("</feed>")
    return "\n".join(lines) + "\n"


def build_json_feed(events: list[dict], self_url: str) -> str:
    """Render a JSON Feed 1.1 document from enriched event dicts."""
    items = []
    for ev in events:
        items.append(
            {
                "id": str(ev.get("event_id", "")),
                "url": entry_link(ev),
                "title": entry_title(ev),
                "content_text": entry_summary(ev),
                "date_published": _rfc3339(ev.get("start_ts", "")),
                "date_modified": _rfc3339(_entry_updated(ev)),
                "tags": [t for t in (ev.get("type"), ev.get("segment")) if t],
            }
        )
    doc = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": FEED_TITLE,
        "description": FEED_SUBTITLE,
        "home_page_url": f"{SITE_URL}/events",
        "feed_url": self_url,
        "authors": [{"name": AUTHOR_NAME}],
        "items": items,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)
