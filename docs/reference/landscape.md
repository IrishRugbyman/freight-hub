# Landscape: comparable sites and alternative data sources

Reference only. Nothing here is a data source for this project unless it also appears
in `CLAUDE.md`'s data flow. Recorded so we know who else occupies this space and where
a second AIS feed could come from.

## Comparable sites

### darkships.org

"Tracking the shadow fleet from free AIS & satellite data." The closest thing to a
direct comparable: same free-AIS ingredients, same dark-fleet / sanctions-adjacent
framing, and it surfaced in the aisstream issue tracker as a reference point during
the 2026-08-05 outage ("down again, you can see it here too") - which is itself the
useful fact about it.

**Not a data source, and not scrapable.**

- It is *downstream of the same feeds we use*, not an independent observer. It went
  dark in the same outage, at the same time. Reading it during an outage returns the
  same nothing, with someone else's uptime added to our dependency chain.
- `robots.txt` sets `Content-Signal: search=yes,ai-train=no,use=reference` and
  explicitly `Disallow: /` for `ClaudeBot`, `GPTBot`, `CCBot`, `Google-Extended`,
  `Bytespider`, `Amazonbot`, `Applebot-Extended`, `meta-externalagent` and
  `CloudflareBrowserRenderingCrawler`.
- It is a Cloudflare-fronted SPA that returns only a `<title>` to a plain fetch, so
  harvesting it would require the headless renderer its robots.txt specifically
  refuses. Checked 2026-08-07.

Worth watching for what they build, not for what we can take.

## Alternative AIS sources (redundancy)

The 2026-08-05 aisstream outage put the live tracker at zero for over a day with no
fallback. Single-source risk is now a known, demonstrated weakness rather than a
theoretical one. See ROADMAP backlog for the actual work item.

### AISHub - the leading candidate

Contributor model: you feed your own receiver's AIS data in, and get stream access
out. This is what the aisstream community converged on during the outage
(aisstream/issues#260) as the realistic free second source.

Genuinely independent of aisstream, which is the whole point - a mirror of the same
upstream is worthless as redundancy. The cost is that it needs an actual AIS receiver
(hardware within VHF range of traffic) or a partner willing to contribute on our
behalf; there is no pure-consumer free tier.

**Unverified.** The contributor threshold, licensing, and whether a non-contributor
tier exists at all have not been checked against AISHub's own terms. Do that before
counting on it.

### Also worth checking if this is pursued

National open-data AIS feeds are genuinely independent and free, but each covers only
its own waters, so they are a supplement rather than a replacement for a global feed.
Norway (Kystverket/BarentsWatch) and Denmark (Danish Maritime Authority) both publish
open AIS; neither has been verified here.

## Deliberately not pursued

- **Scraping any comparable site.** Covered above for darkships.org, but the principle
  is general: a scraped mirror is not redundancy, and taking content from a site that
  refuses automated access is not something this project does.
- **Paid/satellite AIS.** Unchanged from the root roadmap: against the free-source
  ethos, and coverage gaps are disclosed rather than bought around.
