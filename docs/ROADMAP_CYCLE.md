# Roadmap - Freight Cycle Board (`/cycle`)

Forward-looking only. What was built is in `docs/CHANGELOG.md` (session 18, 2026-07-26).

Phases C1-C4 (Baltic ingestion, signal registry, API, page) [COMPLETE 2026-07-26]

## The rule this page lives by

Every tile states the threshold that would change the read and the observation that would falsify
it, and every number declares its provenance: `live` (computed from a series we ingest),
`registered` (a disclosed observation entered by hand, with source and as-of, going visibly stale)
or `missing` (no acceptable source, rendered as a published gap). Nothing is interpolated, nothing
is carried forward silently, and a `registered` number stays flagged `verified: false` until
someone has read the primary source.

C5 - verify the registered observations [COMPLETE 2026-07-26]

## C6 - Freshness handling

Verification moved two review intervals to 60 days: the dry-bulk and tanker orderbooks both drifted
far enough in one quarter to change their signal state, so 90 days was too slack for them.

- [ ] Decide whether the review reminder lives in the hourly analytics build or stays a manual
      pass. No new timer unless the data actually moves - but note the orderbooks moved fast enough
      in H1 2026 to invalidate a recorded figure inside a single review window.
- [ ] Consider a compact "stale registry" line on `/analytics` so a stale tile is noticed without
      opening `/cycle`.

## C7 - Candidate additions (only with a real source behind them)

Tanker demolition and the two fleet-age proxies shipped 2026-07-27. Remaining:

- [ ] Re-source `tanker_scrapping` properly. It is on the board but still `verified: false`: the
      annual rate is our own arithmetic on a cumulative 2022-2026 tally, and the publisher blocks
      automated reads. Find a citable per-period crude-tanker demolition count, or fall back to the
      NGO Shipbreaking Platform quarterly totals with the all-vessel-types caveat made explicit.
- [ ] Raise Equasis/MST build-year coverage above ~25% of the tracked fleet, which is what limits
      the fleet-age signals. Until then the level is indicative and only the direction is reliable.
- [ ] Check the fleet-age sample for crawl-order bias against a known-age subset, so the caveat can
      state a measured skew rather than a suspected one.
- [ ] Our own chokepoint transit index rebased to collection start, charted next to (never spliced
      with) the Baltic series.

## Deliberately not building

- **Scraping SSE for SCFI/CCFI.** The weekly composite is subscription-only. Registered
  observations with source links, or nothing.
- **A continuous container-rate history.** It does not exist free and machine-readable, and before
  2003 it does not exist at all. Disclosed points only, never interpolated between them.
- **Equity overlays** (anchor-company margins, shipowner rebasing). This is a freight board; the
  equity leg is a different project and imports survivorship problems.
- **Any vs-2023 transit comparison computed from our own AIS.** Collection began 2026-06-09.
- **Forecasts.** The board states thresholds and falsifiers. It does not predict levels.
