# Freight Hub Changelog

## 2026-06-21 - CER pipeline routes via NRCan ArcGIS FeatureServer (373/618 total)

**Added:** 7 Canadian federally-regulated pipeline routes via a new script
`backend/ingest_cer_pipeline_routes.py` that queries the Canada Energy Regulator
ArcGIS Online FeatureServer (public, no auth).

The CER service at `services5.arcgis.com/.../CER_Pipeline_Systems_WGS84_view/FeatureServer/3`
returns all 28 CER-regulated pipeline systems as GeoJSON MultiLineString features, each
with `PipelineID`, `Pipeline_Name`, `Company`, and `Commodity` fields. A manual
`_CER_TO_WM` mapping converts `PipelineID` to WM IDs.

**Routes added:**
- NGTL (Nova Gas Transmission / NGTL System) - `nova-gas-transmission-ngtl-...` - 55 segs, 3205 km
- Westcoast (Enbridge BC Pipeline) - `bc-gas-pipeline-westcoast-pipeline-ca` - 41 segs, 1743 km
- Foothills System - `foothills-system-gas-pipeline-ca` - 9 segs, 896 km
- TCPL (TC Canadian Mainline) - `canadian-mainline-gas-pipeline-ca` - 59 segs, 4430 km
- Cochin Pipeline - `cochin-pipeline-system-ca` - 3 segs, 976 km
- Enbridge Bakken System - `enbridge-line-65-oil-pipeline-ca` - 1 seg, 152 km
- Wascana Pipeline (Plains Midstream) - `saskatchewan-oil-pipeline-ca` - 1 seg, 171 km

**Implementation notes:** NGTL raw geometry has 1483 paths (entire Alberta gas grid).
`_MIN_PATH_KM` filter (40 km for NGTL) drops gathering laterals, keeping major
transmission corridors. `_EPSILON_OVERRIDE` gives NGTL eps=0.10 deg (~10 km) to
further reduce to 55 renderable segments. Coordinates swapped from GeoJSON
`[lng,lat]` to WM storage convention `[lat,lon]`.

**Remaining unrouted Canadian (9):** Cold Lake, Grand Rapids, Athabasca, Corridor,
Horizon, AEGS, Co-Ed (all intra-provincial Alberta - AER regulated, not CER);
Keystone XL (cancelled, no geometry); Prince Rupert Gas Transmission (not built).

**Artifact:** `backend/ingest_cer_pipeline_routes.py`

---

## 2026-06-21 - OSM Chinese name map, proximity fix, US sub-regions (366/618 total)

**Added:** 23 more WM pipeline routes, bringing the total to **366/618** (was 343 at session
start after WM dataset was updated from 700 to 618 entries; pipeline-count delta is unrelated
to routing work).

**Code changes (commit f6bd9d1):**
- `_FOREIGN_NAME_MAP`: translates 20+ Chinese-character and Cyrillic OSM `name` tags to
  WM-matchable English. Chinese characters reduce to empty ASCII through NFKD normalization,
  so they were silently dropped without this map. Covered: West-East Gas Pipeline 1-4 and
  subsections, China-Russia East Pipeline phases 1-3, Shaan-Jing 1-4, Sino-Myanmar crude
  and gas, Kazakhstan-China Oil, ESPO-China spur.
- Proximity check switched from centroid to nearest-point sampling over 600 evenly-spaced
  points: a 5000 km pipeline's centroid is 2500 km from sub-section WM endpoint pairs,
  causing all sub-section matches to fail the 600 km guard.
- `_norm()` now keeps single-digit tokens: "2", "3", "4" were being filtered by `len > 1`,
  making all numbered pipeline variants (West-East Gas Pipeline 2 vs 3 vs 4) produce
  identical token sets and prevent specific numbered matches.
- Bboxes: split `us_lower48` into 6 sub-regions (northeast, southeast, gulf, midcontinent,
  rockies_north, west) and `canada` into `canada_west` + `canada_east` to avoid Overpass
  timeouts on large bboxes.
- EIA oil: added `keystone` WM ID to TRANSCANADA Keystone override, added Seminole Red
  Pipeline override for Enterprise and Phillips 66 variants.

**Routes added this session (global_pipeline_routes: 116 -> 137, EIA oil: 45 -> 47):**
- China: WEGP 1/2/3/4 + middle/west subsections, China-Russia East phases 1/2/3, Power of
  Siberia, ESPO-China spur (x2 WM IDs), Sino-Myanmar gas trunk - 14 routes
- India: HVJ (Hazira-Vijaipur-Jagdishpur) Gas Pipeline - 1 route
- US (OSM): Mariner East 2 NGL, Aegis Pipeline, Whistler Pipeline, Atmos Pipeline Texas - 4 routes
- EIA oil: Keystone mainline, Seminole Red Pipeline - 2 routes
- Canada (Enbridge Line 65): +1 route (from canada_east)

**Remaining gaps (252 unrouted):** China domestic (42, need CNPC GIS), US NGL gathering
systems (31, PHMSA NPMS needed), India domestic (25, PNGRB/GAIL), Iran (22, no public GIS),
Canada gas/oil sands (16, NRCan/CER shapefiles), Russia domestic (15).

**Artifacts:** `backend/ingest_osm_named_pipeline_routes.py`, `backend/ingest_eia_oil_routes.py`.

---

## 2026-06-21 - EIA oil manual overrides expanded (374/700 total)

**Added:** 16 more WM pipeline routes by expanding `_MANUAL` in `ingest_eia_oil_routes.py`
from 29 to 45 entries. Key change: converted `_MANUAL` values from `str` to `list[str]`
so one EIA `(opername, pipename)` entry can populate multiple WM IDs sharing the same
physical corridor (aliases, phases, same operator).

New WM IDs covered: `alberta-clipper-oil-pipeline-ca` (Enbridge Line 67/Alberta Clipper),
`diamond-oil-pipeline-us` (Plains All American Patoka-Memphis), `enbridge-line-14-64-oil-pipeline-us`
(North Dakota system), `frontier-oil-pipeline-us` (Holly Energy Big Spring-Denver), `grand-mesa-oil-pipeline-us`
(Magellan DJ Basin-Cushing), `kaw-oil-pipeline-us` (CHS Energy Kansas crude),
`midland-to-echo-pipeline-system-*-us` x2 (Enterprise Midland-ECHO), `ozark-crude-oil-pipeline-patoka-to-lima-expansion-us`,
`permian-express-oil-pipeline-phase-i/ii/iv-us` x3, `seaway-oil-pipeline-system-us`,
`spearhead-oil-pipeline-us`, `teppco-pipeline-us` (Enterprise Gulf Coast-Great Lakes products),
`western-corridor-oil-pipeline-system-glacier-pipeline-bearto-us` (Phillips 66 Glacier).

**Combined total: 374/700 WM pipelines now routed** (from 358 after OSM named-way pass).

---

## 2026-06-21 - OSM named-way pipeline routes (global, 56 new routes; 358/700 total)

**Added:** 56 WM pipeline routes via OSM Overpass named-way assembly, on top of the
existing Dijkstra/IGGIELGN/EIA stack. New script `backend/ingest_osm_named_pipeline_routes.py`
covers 21 global region bboxes, queries `way[man_made=pipeline][name]` per region, groups
way segments by name tag, chains disconnected segments with a greedy nearest-endpoint
algorithm (MAX_CHAIN_GAP_KM=300), fuzzy-matches to WM pipeline IDs by Jaccard score
(threshold=0.38), and stores routes in `global_pipeline_routes`.

Key implementation details:
- Name resolution: prefer `name:en > int_name > alt_name > name` to handle Russian Cyrillic,
  Chinese, and Arabic pipeline names whose bare `name` tag normalises to empty ASCII
- Generic name filter: rejects OSM names with <2 distinctive words (e.g. "Gas Pipeline")
- Centroid distance guard: rejects matches where OSM centroid is >600 km from WM endpoints
- Rate-limit handling: polls Overpass `/api/status` before each query, waits on "Slot
  available after:"; HTML (406) responses get 90s+ exponential backoff
- RDP simplification at epsilon=0.02 deg (~2 km)
- `--region` flag supports multiple invocations for targeted reruns

Regional breakdown (routes stored):
- Middle East West: 7 (East-West Saudi, Greater Nile, Iraq Strategic x3)
- Central Asia: 6 (Kazakhstan-China Oil Pipeline variants)
- Russia Central: 6 (Aleksandrovskoye-Anzhero, Vankor-Purpe, Omsk-Irkutsk)
- Mexico/CA/US: 25 (Wink-to-Webster, Gulf Coast Express, Sur de Texas-Tuxpan, Sand Hills,
  Sistema Nacional de Gasoductos MX, Energia Mayakan, Black Lake, Flanagan South, DAPL,
  North System, Eastern Gas Transmission, Pony Express, Ozark Crude, Red River, etc.)
- Canada: 15 (Enbridge Lines 1/2/3/4/5/6/9/61/78, Trans Mountain, Norman Wells, Minnesota)
- Oceania: 5 (Moomba-Sydney, South West Queensland, Moomba-Adelaide, Dampier-Bunbury)
- South Asia: 5 (Salaya-Mathura, Myanmar-China crude+gas, Dabhol-Bangalore)
- Africa: 2 (Chad-Cameroon, Escravos-Lagos)
- LatAm: 3 (OCP Ecuador, Puerto Rosales-La Plata, Gasoducto al Altiplano)
- SE Asia: 1 (Amadeus Gas)
- China West: 1 (Sebei-Golmud)

Post-ingest cleanup removed 20 routes: 4 false positives (3 unrelated IDs mapped to "Casa
Pipeline System", 1 matched "US Amines Hydrogen Pipeline") and 16 routes with <4 points
(too sparse to render a meaningful line). Final: 104 routes in `global_pipeline_routes`.

**Combined total: 358/700 WM pipelines with full polyline routes** (from 302 at session
start). Route priority: EIA gas (RexTag crosswalk) -> EIA oil -> EU IGGIELGN -> OSM global.

**Artifacts:** `backend/ingest_osm_named_pipeline_routes.py` (new), commits 18ff75a, 570e3f6, c19a077.

---

## 2026-06-21 - EIA crude oil + petroleum product pipeline routes; extended WM-RexTag crosswalk

**Added:** Full polyline geometry for an additional 36 WM pipelines (17 oil + 19 gas)
via two parallel tracks:

**Track 1 - EIA oil shapefile ingest (`ingest_eia_oil_routes.py`):**
Downloads crude oil (231 segments, 40 operators) and petroleum product (329 segments)
pipeline geometries from EIA ArcGIS FeatureServer endpoints. Fuzzy-matches EIA
`(opername, pipename)` pairs to WM pipeline IDs; fuzzy scope restricted to US-endpoint
WM pipelines to prevent false-positive matches to international WM entries. 19 WM oil
pipelines now have full EIA shapefile routes, stored in new `eia_oil_pipeline_routes`
table (wm_id PK). Priority in loader chain: EIA gas -> EIA oil -> EU IGGIELGN -> OSM global.

Notable new oil routes: Enbridge Mainline System (3.15 mbd, 8 segments),
Trans-Alaska Pipeline TAPS (2.1 mbd), Trans Mountain (3 segs), Gray Oak Pipeline,
EPIC Crude Pipeline, Seaway Pipeline, BridgeTex, Southern Lights, Double H Pipeline,
Energy Transfer ETCOP.

**Track 2 - Extended rextag_wm_crosswalk (`ingest_extend_crosswalk.py`):**
23 new WM ID -> RexTag slug mappings for major US/Canada gas pipelines that already
had EIA route geometry but lacked a crosswalk entry. Zero new downloads. New entries
include: ANR, El Paso, Rockies Express, Panhandle Eastern, Kern River, East Tennessee,
NGPL, Alliance, Gulf South, Northwest, Northern Border, Mississippi River Transmission,
WBI/Williston Basin, Enable Oklahoma (EOIT), MountainWest Overthrust, Midcontinent
Express, Gulfstream, Maritimes & Northeast, Mojave, Iroquois, Empire, Ruby, Sabal Trail.

**Combined result:** 286/700 WM pipelines now have full polyline routes (up from 207
before this session), plus 65 RexTag-only US gas pipelines. Oil coverage now includes
TAPS, Enbridge Mainline, Trans Mountain, Gray Oak, and 15 other US/CA crude routes.

**Artifacts:** `backend/ingest_eia_oil_routes.py`, `backend/ingest_extend_crosswalk.py`,
`shared/market-data/loaders/worldmonitor.py` (4th JOIN: eia_oil_pipeline_routes).

---

## 2026-06-20 - Global pipeline route geometry (OSM Overpass Dijkstra)

**Added:** Full polyline geometry for an additional 40 World Monitor pipelines
covering Russia/Central Asia, East Asia, Middle East, Africa, South America,
and Oceania using OSM Overpass API Dijkstra routing. Two-pass ingest:

- Pass 1 (`ingest_global_pipeline_routes.py`): 20 regional bbox Overpass queries,
  per-region DuckDB saves (idempotent on resume), rate-limit auto-retry.
- Pass 2 (`ingest_global_pipeline_routes_pass2.py`): merges sub-region graphs into
  super-regions to handle trans-regional pipelines (e.g. ESPO spans East + Far East
  Russia boxes). Adds a further 4 routes.

Notable new routes: ESPO (4,436 km, 270 pts), West-East Gas Pipeline China
(2,811 km), GASBOL Bolivia (2,428 km), Power of Siberia (634 km), Central
Asia-China Line C (1,838 km), Dampier-Bunbury AU (1,544 km), Express CA
(1,258 km), Chad-Cameroon (1,067 km), Mozambique-SA Gas (858 km).

Combined with EU IGGIELGN (147) and EIA US gas (85 RexTag + 20 WM-linked),
total with full polyline routes: 272/722 (38%). Remaining gaps are mostly US/CA
oil pipelines (TAPS, Enbridge, Keystone, Colonial) where OSM network lacks
connected endpoint topology for Dijkstra routing.

**Loader:** `worldmonitor.py` `load_pipelines_for_map()` now JOINs three route
tables in priority order: EIA -> EU -> global.

**Artifacts:** `backend/ingest_global_pipeline_routes.py`,
`backend/ingest_global_pipeline_routes_pass2.py`,
`shared/market-data/loaders/worldmonitor.py`.

---

## 2026-06-20 - EU + global pipeline full route geometry (IGGIELGN)

**Added:** Full polyline geometry for 147 World Monitor EU/global pipelines using the
SciGRID_gas IGGIELGN dataset (Zenodo CC-BY, 6323 gas network segments covering Europe,
Russia, MENA, and the Caucasus). One-shot ingest script (`backend/ingest_eu_pipeline_routes.py`)
downloads the zip, builds a graph, runs Dijkstra shortest-path routing from each WM
pipeline's start/end coordinates, RDP-simplifies at epsilon=0.02 degrees, and stores
routes in `eu_pipeline_routes` table in `freight_analytics.duckdb`.

Previously only 85 US pipelines had full EIA polylines. Now 232 of 722 total pipelines
have route_coords in the API. Notable routes: Nord Stream 1&2 (Baltic crossing,
52-61N,13-30E), Yamal-Europe (Siberia to Germany, 52-66N), BTC (Azerbaijan through
Georgia to Turkey), Druzhba North/South, TAP (Turkey to Italy via Adriatic submarine),
TANAP, Kirkuk-Ceyhan, Transalpine (TAL), and 140+ others.

**Loader:** `shared/market-data/loaders/worldmonitor.py` updated to LEFT JOIN
`eu_pipeline_routes` in `load_pipelines_for_map()`. EIA US routes take priority;
EU routes fill all others. No frontend changes needed.

**Artifacts:** `backend/ingest_eu_pipeline_routes.py` (new), `shared/market-data/loaders/worldmonitor.py`.

---

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
