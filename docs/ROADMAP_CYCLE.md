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

## C5 - Verify the registered observations

The four hand-recorded numbers all came from a secondary summary and are flagged unverified on the
page. Each needs a primary source read, then `verified: true` plus a `verified_note` naming the
source and date.

- [ ] Container SCFI/CCFI - confirm the level and date against the Shanghai Shipping Exchange page.
- [ ] Container orderbook % - find a citable Alphaliner or Linerlytica publication, not a summary.
- [ ] Dry-bulk orderbook % - the recorded figure is from 2026-02 and is the stalest on the board.
- [ ] Tanker orderbook % - recorded 2025-11; check whether the ordering trend has continued.

## C6 - Freshness handling

- [ ] Decide whether the review reminder lives in the hourly analytics build or stays a manual
      pass. No new timer unless the data actually moves - registered observations move quarterly.
- [ ] Consider a compact "stale registry" line on `/analytics` so a stale tile is noticed without
      opening `/cycle`.

## C7 - Candidate additions (only with a real source behind them)

- [ ] Tanker scrapping: currently a published gap. Promote only if a free, citable demolition count
      turns up - not a number lifted from a market commentary.
- [ ] Fleet-age distribution from our own registry data as a scrapping *proxy*, clearly labelled as
      a proxy rather than as scrapping.
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
