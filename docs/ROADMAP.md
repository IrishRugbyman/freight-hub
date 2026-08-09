# Freight Hub Roadmap

Forward-looking only. Completed work is in `docs/CHANGELOG.md`.

**Active initiative:** True ETA - see [`docs/ROADMAP_TRUE_ETA.md`](ROADMAP_TRUE_ETA.md) for the full phased blueprint (sea-route distance, physics ETA, history-gated ML, calibrated intervals, accuracy scoreboard).

**Rules that apply to every phase:**
- No synthetic data. All series from real AIS messages.
- No `Co-Authored-By` or AI attribution in commits.
- DuckDB single-writer: collector owns `ais_positions.duckdb`, analytics job owns `freight_analytics.duckdb`, crawler owns `vessel_registry.duckdb`. The API writes nothing.
- Backend tests: pytest + TestClient + seeded temp DuckDB. Frontend logic: vitest.
- Pre-commit hooks run ruff/format automatically.

---

## Phase 4 - Stretch: deck.gl WebGL layer, density heatmap, live push (OPTIONAL)

Only worth doing if the Leaflet map feels like the performance bottleneck. Everything renders on the visitor's GPU.

- [ ] `deck.gl-leaflet` `ScatterplotLayer` replacing the marker rendering path in `VesselLayer.tsx` (keep Leaflet basemap + popups via picking). `HeatmapLayer` density toggle. Keep the old canvas path behind a flag until clustering + heading arrows reach parity.
- [ ] Trails as a `PathLayer`; optionally `TripsLayer` animation for the selected vessel's last 24h.
- [ ] Optional SSE: `GET /api/stream` (`StreamingResponse`, changed vessels every 15s). `proxy_buffering off` in `nginx-freight.conf`. Only if dead-reckoning feels insufficient.
- [ ] Bundle check: deck.gl is heavy - confirm `npm run build` chunk sizes stay reasonable.

---

## Backlog (not yet phased)

Promote to a phase only with a written plan:

- **Owner/fleet dashboards:** aggregate analytics by owner (which owners' VLCCs are laden vs ballast right now); needs the registry join. Constraint: Equasis owner data is currently filled for only ~1,925 of 15,235 IMOs (~13%), so an owner view would be sparse until the crawler covers more of the live fleet.

- **A second AIS source (redundancy):** aisstream is a single point of failure and it
  has now failed twice - zero vessels from 2026-08-05 13:31 UTC for over a day, then
  again from 2026-08-06 02:28 UTC for several days. The collector no longer
  *fabricates* data during an outage (CHANGELOG 2026-08-07) and the site now says so
  out loud (2026-08-09), but it still has nothing to fall back on.

  **Both free candidates are now ruled out, verified against their own terms**
  (2026-08-09, detail in [`docs/reference/landscape.md`](reference/landscape.md)):
  AISHub is contributor-only with no consumer tier and explicitly bans re-feeding
  public sources, and a receiver is not viable from Geneva anyway; Data Docked is
  credit-metered REST at roughly 42k credits/day for our basin coverage against a
  20-credit free tier, and does not return vessel type inline with positions.

  The requirement that eliminates most providers, and the first thing to check on any
  new one: **the feed must carry `ship_type` and dimensions**, or `classify()` cannot
  produce a segment and every segment-keyed surface breaks.

  What remains, in order of realism: (a) scope redundancy to the 13 chokepoints only
  and price a fixed-area plan (VesselFinder LiveData), rather than replacing a global
  firehose like-for-like; (b) a receiver hosted by someone within VHF range of real
  traffic, which unlocks AISHub legitimately for ~EUR 60 plus a standing favour;
  (c) national open feeds (Norway, Denmark, Finland) as an honest partial - about 3% of
  our rows, so a supplement and nothing more.

---

## Deliberately Not Building

- **Global AIS coverage:** terrestrial receivers make mid-ocean subscription useless; the 24 curated basins cover every chokepoint that matters.
- **Satellite AIS / paid data:** out of budget and against the free-source ethos.
- **Destination-string geocoding:** the free-text destination field is garbage-in and stays an untrusted ETA target. (ETA *itself* is now a planned initiative, computed only to resolved chokepoint/port targets - see `docs/ROADMAP_TRUE_ETA.md`. ML gated on accumulated clean history; physics ships first.)
- **WebSocket bidirectional streaming:** SSE (Phase 4, optional) is sufficient for a read-only feed.
- **Backfilling history:** impossible by definition; charts honestly start at their collection date.
- **Per-visitor accounts/auth:** public showcase.
- **External sanctions lists ingestion:** risk scoring uses only owned data and public registry facts; matching named sanctioned entities is a legal-grade exercise this project should not pretend to do.
- **Aggressive Equasis crawling:** one request per 6-10s with a per-run cap, full stop. Do not rotate accounts or evade blocks.
