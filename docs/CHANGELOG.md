# Freight Hub Changelog

## 2026-06-20 - UX: Vessel deep-links + pipeline map-link

**Pipeline label fix:** "Disrupted pipelines" layer toggle renamed to "Pipelines" (it always showed all 618, not just disrupted).

**Pipeline map-link:** MapPin button added to each row in `/pipelines` table. Clicking navigates to `/?pipeline_id=X`, which enables the pipelines layer, highlights the pipeline with a white halo + thicker colored line, calls `map.fitBounds` to the pipeline endpoints (max zoom 6, 80px padding), and opens the popup 600ms later. The `pipeline_id` search param is new in the tracker's `validateSearch`.

**Vessel deep-links from analytics:** Every vessel row in the Intelligence, Ports & Cargo, and Fleet analytics tabs is now clickable and navigates to the tracker map with the vessel selected (and zoomed if lat/lon available). Covers: Anomaly Watchlist, Destination Changes, STS Proximity, STS Offenders, Reroutes, Risk Event Feed, Shadow Fleet Monitor (Intelligence tab); Port Arrivals, Cargo Transitions, Cargo State Changes (Ports & Cargo tab); Speed Anomalies, Slow Steamers (Fleet tab); Vessel Risk Leaderboard (Risk tab). Uses a `useGoToTracker()` hook pattern (`?mmsi=X&lat=Y&lon=Z` when coordinates available, `?mmsi=X` only when not).

**Artifacts:** `frontend/src/components/tracker/{LayerToggles,PipelineLayer,VesselMap}.tsx`, `frontend/src/routes/{index,pipelines}.tsx`, `frontend/src/routes/analytics/{-IntelligenceCards,-RiskCards,-PortsCargoCards,-FleetCards}.tsx`.

---

## 2026-06-20 - Phase 55+56: Owner fleet status card + Pipelines page

**Tried:** Two backlog items: (1) live laden/ballast breakdown per beneficial owner by joining live_positions -> vessel_registry (via IMO) -> vessel_state; (2) dedicated /pipelines page showing all 618 World Monitor pipelines in a searchable/sortable table.

**Found:** Owner fleet status JOIN works cleanly - vessel_state (analytics DB) holds laden/ballast per MMSI, vessel_registry (Equasis registry DB) holds owner per IMO. Only ~30-40% of live vessels have a matched Equasis entry (IMO required), so the card shows a subset of the fleet. Pipeline data (disrupted_only=false) returns 618 rows in ~1s via existing endpoint - fully feasible for client-side filtering with no pagination. Physical states: flowing, offline, reduced, unknown. Disruption descriptions are long-form prose averaging ~80 chars.

**Decision:** Owner fleet card placed first on Fleet analytics tab (most useful daily view for who is moving cargo). Pipelines page added as dedicated nav item between Fleet and Routes - table with KPI bar, state/commodity filters, name search, sortable columns, inline expand for full disruption description. No backend changes needed for pipelines page. 3 new backend tests (326 total passing).

**Artifacts:** `backend/app/main.py` (owner-fleet-status endpoint), `backend/app/schemas.py` (OwnerFleetStatusRow/Response), `backend/tests/test_endpoints.py` (+3 tests), `frontend/src/lib/api.ts` (useOwnerFleetStatus), `frontend/src/routes/analytics/-FleetCards.tsx` (OwnerFleetStatusCard), `frontend/src/routes/pipelines.tsx` (new page), `frontend/src/routes/__root.tsx` (nav link).

---

## 2026-06-19 - Phase 54: Pipeline disruption map layer

**Added:** Toggleable "Disrupted pipelines" layer on the vessel tracker map. Draws the 37
currently offline or reduced global energy pipelines as color-coded Polylines on top of
live vessel positions (offline=red dashed, reduced=orange). Clicking a line opens a popup
with pipeline name, route, capacity, event type, and the disruption description from the
World Monitor database. The layer is off by default and can be toggled via the Controls panel.

Data source: World Monitor (Global Energy Monitor, CC-BY 4.0) - same dataset as the
quant research projects (gas-storage, transport-arb). Current state: 15 pipelines offline
(4.62 mbd / 399 bcm/yr), 22 pipelines reduced. Key offline: Kirkuk-Ceyhan (1.6 mbd, IQ->TR,
Mar 2023), Druzhba North (1.0 mbd, RU->DE, Feb 2023), Nord Stream 1+2 (55 bcm/yr each, sabotage
Sep 2022), Brotherhood/Soyuz Ukraine transit (142 bcm/yr, commercial end Jan 2025).

Backend: New `load_pipelines_for_map(disrupted_only)` loader in `loaders/worldmonitor.py`
(LATERAL JOIN to attach the most recent active disruption per pipeline). `GET /api/pipelines`
endpoint with 1h in-process cache. `PipelineSegment` + `PipelinesResponse` schemas.
3 new tests; 323 total passing.

Frontend: `PipelineLayer.tsx` (imperative L.polyline), `usePipelines()` hook in `api.ts`,
`pipelines` key in `LayerState` and `DEFAULT_LAYERS`, toggle in `LayerToggles`.

**Artifacts:** `backend/app/main.py`, `backend/app/schemas.py`, `backend/tests/test_endpoints.py`,
`frontend/src/components/tracker/PipelineLayer.tsx`, `frontend/src/lib/api.ts`,
`frontend/src/components/tracker/{VesselMap,LayerToggles,types}.tsx`,
`shared/market-data/loaders/worldmonitor.py`.

---

## 2026-06-14 - Phase 53: High-risk events syndication feed (Atom + JSON Feed)

**Added:** Public, no-accounts syndication feeds over the same `ais_events` rows that power
`/api/events`. Closes the last unbuilt backlog item (the "email/RSS digest"), delivered as
feeds rather than email to fit the deliberately no-accounts / no-SMTP public-showcase stance.

- `GET /api/feed.xml` - Atom 1.0 (`application/atom+xml`), well-formed, self/alternate links,
  feed `updated` = newest entry, per-entry stable `urn:freight-event:<event_id>` ids,
  `category`, RFC 3339 timestamps.
- `GET /api/feed.json` - JSON Feed 1.1 (`application/feed+json`).
- Default surfaces only high-risk types (`dark_voyage`, `spoof`, `gap`, `loiter`, `sts`);
  reroutes excluded as noise. Overridable via `?types=`, window via `?days=` (1-30),
  `?limit=` (1-500). Each entry deep-links to the tracker (`/?mmsi=<mmsi>`) and is name-enriched
  from `live_positions` (falls back to `MMSI <n>` when a vessel has aged out).
- New `app/feed.py` pure builders (hand-built Atom via stdlib, JSON Feed as a dict; no new
  deps). Shared `_fetch_events_raw()` read helper in `main.py`.
- Frontend: `SubscribeFeed` RSS popover on the Events page header (copy Atom/JSON URLs, built
  from `window.location.origin` so it works in dev and prod). No new deps.
- 5 new backend tests (Atom well-formedness, high-risk default filter, `types=` override,
  JSON Feed structure, empty-DB valid feed). 324 backend tests passing; frontend build clean.

**Also:** `uv sync --extra dev` added `psycopg2-binary` (env drift after the market-data
loaders migration left the freight venv missing it, which had been erroring the whole suite).

**Artifacts:** `backend/app/feed.py`, `backend/app/main.py` (Response import, feed module
import, `_fetch_events_raw`, `_feed_types`, `feed_atom`, `feed_json`), `backend/tests/test_endpoints.py`,
`frontend/src/components/SubscribeFeed.tsx`, `frontend/src/routes/events.tsx`.

---

## 2026-06-12 - Phase 51: Analytics build crash fixes + fleet trend chart + events UX

**Fixed:** Three production bugs that had been causing every analytics build to crash before the watermark was set (forcing 9-min full rebuilds every hour instead of 30-sec incremental runs):
1. `build.py` gap-recheck loop iterated `numpy.int64` MMSIs directly into DuckDB parameters: `NotImplementedException`. Fixed with `.tolist()` on the numpy unique array.
2. `detect.py _dest_edit_dist()` received float NaN destination values (pandas coerces None to float in object columns): `TypeError: float has no len()`. Added `math.isnan()` guard.
3. Each detection step could crash the entire build. Added per-step try/except so individual failures log a WARNING and continue; watermark still advances. 4 dead-code lines removed from port-arrivals endpoint.

**Added:** `GET /api/analytics/fleet-trend?days=30&region=` endpoint aggregating `fleet_density` daily (laden/ballast/unknown/total). Powers new FleetTrendCard area chart in the Overview analytics tab showing 30-day fleet composition trend. 4 new backend tests.

**Improved:** Events page now fetches all events client-side and sorts by severity (dark voyage > position jump > signal lost > loitering > STS > reroute) then time. Per-type counts shown in filter chips; empty-type chips hidden. Limit raised to 500.

**Added:** Events nav badge showing 24h event count (updates every 5 min via `useRecentEventCount` hook). Refreshes automatically.

**Artifacts:** `backend/analytics/build.py` (per-step isolation, numpy fix), `backend/analytics/detect.py` (NaN guard, numpy fix), `backend/app/main.py` (fleet-trend endpoint), `backend/app/schemas.py` (FleetTrendDay, FleetTrendResponse), `backend/tests/test_endpoints.py` (4 new tests), `frontend/src/routes/events.tsx` (severity sort, type counts), `frontend/src/routes/__root.tsx` (event badge), `frontend/src/lib/api.ts` (useRecentEventCount, useFleetTrend, FleetTrendResponse), `frontend/src/routes/analytics/-OverviewCards.tsx` (FleetTrendCard).

---

## 2026-06-12 - Phase 50: Zero-downtime analytics build + vectorized zone detection

**Tried:** Analytics build held an exclusive DuckDB write lock for the entire 7-10 min build window. All analytics API calls returned empty data during that time. Root cause: `_open_analytics()` opened a write connection at the start and held it until the last line.

**Found:** First full run had 91MB AIS DB with 2,697 transit events, 2,723 anchored episodes, 5,184 AIS events, 7,413 vessel states. STS `apply(lambda r: _any_zone...)` was the biggest hot-path (Python row iteration on all slow tanker rows = O(n) with interpreter overhead). After the job finished, analytics page showed real data: 1,404 laden tankers, 1,599 ballast, 319 transits/24h.

**Decision:** Analytics build now writes to `freight_analytics.new.duckdb`, atomically renames it to live at completion (`os.replace` = POSIX rename, atomic on same filesystem). Live DB is never locked during the build. Added `_in_any_zone_vec()` vectorized zone check using numpy broadcasting; replaced `apply` in `sts_candidates` and the per-row loop in `loitering_events`. 311 tests still passing.

**Artifacts:** `backend/analytics/build.py` (`_open_analytics_scratch`, `_commit_scratch`, `run` -> `_run_inner` refactor), `backend/analytics/detect.py` (`_in_any_zone_vec`, vectorized STS/loiter zone checks).

---

## 2026-06-12 - Phase 49: SOTA tabbed analytics layout

**Tried:** Restructured the monolithic analytics page (3,624-line `AnalyticsCharts.tsx`, 41 cards, all in one chunk) into a 6-tab production layout inspired by Kpler/Vortexa/MarineTraffic.

**Found:** Build output confirms 6 distinct rollup chunks (FleetCards 12 kB, OverviewCards 14 kB, ChokepointCards 15 kB, IntelligenceCards 19 kB, RiskCards 23 kB, PortsCargoCards 29 kB) plus a 0.7 kB shared analyticsShared chunk. 311 tests passing. TanStack Router `-` prefix convention suppresses non-route file warnings cleanly.

**Decision:** Code splitting works end-to-end. New ChokepointStatusCard added to Chokepoints tab (live transiting/waiting counts per chokepoint). Sticky KPI bar + deep-linkable ?tab= search param replace the old endless scroll. AnalyticsCharts.tsx deleted.

**Artifacts:** `frontend/src/routes/analytics/-{Overview,Chokepoint,PortsCargo,Risk,Intelligence,Fleet}Cards.tsx`, `frontend/src/components/ui/tabs.tsx`, `frontend/src/routes/analytics/-analyticsShared.tsx`, backend `chokepoint-status` endpoint.

---

## 2026-06-12 - Phases 40-46: Intelligence analytics + fix disappearing vessels

**Tried:** Autonomous SOTA-grade feature sprint. Built Phases 40-46 sequentially: STS offenders ranking, fleet historical snapshots, destination change intelligence, owner fleet risk aggregation, chokepoint throughput anomaly detection, cargo loading/discharge detection, and live-fleet speed anomaly detection. Also diagnosed and fixed the disappearing vessels regression.

**Found:** 300 backend tests passing. Three root causes of vessel disappearing identified: (1) AIS DB write-lock exhaustion causes `db.query` to return empty DataFrame, API returns HTTP 200 `[]`, frontend replaces full vessel list; (2) VesselLayer diff removes all markers on any empty array, even transient ones; (3) SSE stream uses 30-min window but replaces the full cache which is built from a 3-hour window, silently dropping vessels seen 31-180 min ago. Phase 46 had a Pydantic v2 immutability bug - `registry_risk` could not be set post-construction; fixed by building intermediate dicts before constructing Pydantic objects.

**Decision:** All three disappearing-vessel causes fixed: VesselLayer skips diff on empty+existing markers; `useVessels` throws on suspiciously empty response to trigger TanStack Query retry; `useVesselStream` merges updates instead of replacing. Phase 46 endpoint now uses dict-first pattern (build, sort, enrich, then construct Pydantic). `SpeedAnomalyRow` gained `imo` field. MAD-based z-score (factor 1.4826) used for robust segment-peer comparison.

**Artifacts:** New endpoints: `/api/analytics/fleet-at-time`, `/api/analytics/destination-changes`, `/api/analytics/owner-intelligence`, `/api/analytics/chokepoint-anomaly`, `/api/analytics/cargo-state-changes`, `/api/analytics/speed-anomalies`. New frontend cards in Analytics page. 6 new test fixtures added.

## 2026-06-10 - Phase 3: Intelligence events

- AIS gap detection: vessel active for >= 6 fixes in 48h then silent > 6h, last SOG > 2 kn, inside region interior (>0.4 deg from bbox edge). Closes when vessel reappears.
- Loitering detection: >= 12h episode with mean SOG < 1 kn, outside all anchorage zones, > 0.2 deg from region bbox edge.
- STS candidate detection: two tankers within 500m for >= 2h, both SOG < 0.5 kn, outside anchorage zones (0.01-deg grid hash for efficiency).
- `ais_events` table added to `freight_analytics.duckdb`; event_ids stable via sha1 for idempotent re-runs.
- `GET /api/events?type=&days=7&limit=200` endpoint with vessel name enrichment from live_positions.
- Events page at `/events`: type-chip filters, days selector, row click navigates to tracker.
- "Event pins" toggleable layer on the tracker map (last 48h events as color-coded pins).
- 62 backend tests passing (12 new detect unit tests, 6 new endpoint tests).
- 0 events on first run (expected - gap/loiter need 48h+ history, STS is rare).


## 2026-06-10 - Phase 2: Analytics pipeline

- Added hourly analytics batch job (`backend/analytics/`) writing to `freight_analytics.duckdb`.
- Chokepoint transit detection across 9 chokepoints (Suez, Hormuz, Panama, etc.): 419 events detected on first run from 157k snapshot rows.
- Anchored episode detection: 14 curated anchorage zones (Fujairah, Singapore E/W, Rotterdam, Qingdao, etc.).
- Laden/ballast classification per vessel using draught ratio with design-draught fallback by segment.
- Fleet density aggregates per region/kind/segment per snapshot.
- 5 new API endpoints: `/api/analytics/transits`, `/api/analytics/congestion`, `/api/analytics/density`, `/api/analytics/laden`, `/api/analytics/zones`.
- Analytics page at `/analytics` with recharts: transit bar chart, congestion line, laden stacked bar, density chart.
- systemd `freight-analytics.timer` running hourly (Persistent=true).
- 44 backend tests passing; spot-checked transit MMSI 357932000 (Panamax tanker, Cape of Good Hope westbound, lon displacement 0.37 deg over 5 fixes - confirmed correct).

## 2026-06-10 - Phase 1: Map UX

- Vessel trail polyline on click (24h/7d toggle) via `/api/vessels/{mmsi}/track`.
- Dead-reckoning smooth movement between 60s polls (2 Hz, pauses when tab hidden).
- No-flash vessel diff: persistent layer group, markers updated in place not rebuilt.
- Vessel detail panel: IMO, draught, nav status decoded, destination, ETA, SOG/COG.
- Search by name/MMSI/destination with zoom-to-vessel.
- Collapsible controls panel (max-height + scroll, no viewport overflow).

## 2026-06-10 - Phase 0: Collector capture upgrade

- AIS collector now stores draught, IMO, nav status, ETA per vessel.
- Snapshot cadence reduced from 30 min to 10 min.
- freight-api exposes new fields on `/api/vessels`.
