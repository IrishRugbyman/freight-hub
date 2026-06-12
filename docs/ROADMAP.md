# Freight Hub Enhancement Roadmap

Phased delivery plan for upgrading the live vessel tracker (freight.lbzgiu.xyz) from a
live map into a maritime analytics and intelligence product. Written to be executed
phase by phase by Claude Code with no other context: read this file, pick the first
incomplete phase, work the checklist top to bottom.

**Read `~/quant/freight/CLAUDE.md` and `~/quant/CLAUDE.md` before starting any phase.**
Key rules that apply throughout:

- No synthetic data, ever. All series derive from real AIS messages.
- Commit messages: never add Co-Authored-By or any AI attribution.
- DuckDB single-writer rule: the collector owns `ais_positions.duckdb`. Nothing else
  may open it for writing. The analytics job (Phase 2+) writes to its own separate DB.
- Backend tests: pytest + TestClient + seeded temp DuckDB (pattern in `backend/tests/`).
  Frontend logic tests: vitest (pattern in `frontend/src/lib/segments.test.ts`).
- Pre-commit hooks run ruff/format automatically; line length 100.

## Vision

**One sentence:** A live tanker/bulker tracker that also produces quant-credible freight
signals (chokepoint transits, port congestion, laden/ballast splits) and research-grade
maritime intelligence (AIS gaps, loitering, STS-transfer candidates) from its own
accumulated AIS history.

**Who:** Portfolio visitors (trading-desk audience) first; the quant projects
(`transport-arb`, dispersion) as downstream consumers second.

**Why:** Live maps are commodity. Derived freight analytics from owned history are what
commercial providers (Kpler, Vortexa) sell. Building a small honest version of that
pipeline is the differentiator.

**Constraints:** Hetzner VPS, no server GPU (irrelevant: deck.gl renders on the
visitor's browser GPU), aisstream.io free terrestrial AIS, curated 24-region coverage
(global subscription is pointless with terrestrial receivers), DuckDB everywhere.

**Critical timing fact:** `ais_snapshots` history started 2026-06-09. Every derived
analytic compounds with history depth, and dropped fields (draught, nav status) can
never be backfilled. This is why Phase 0 ships first and immediately.

## Stack (already in place, no new infrastructure choices)

| Layer | Choice | Notes |
|---|---|---|
| Collector | `market-data/ais/collector.py`, systemd `ais-collector.service` | Single aisstream consumer, owns `ais_positions.duckdb` |
| Backend | FastAPI `freight-api` on :8003, systemd | Read-only DuckDB with lock-retry (`app/db.py`) |
| Analytics job | New: Python batch in `backend/analytics/`, systemd timer | Writes its own `freight_analytics.duckdb` |
| Frontend | React 19 + Vite + TS, TanStack Router/Query, Tailwind v4, react-leaflet | recharts ^3.8 already a dependency |
| Deploy | `sudo systemctl restart freight-api`; `npm run build` (dist is nginx root) | New route: run `npx vite build --emptyOutDir=false` first to regen `routeTree.gen.ts` |

## Complete Data Model (designed upfront, built across phases)

### ais_positions.duckdb (owned by collector, Phase 0 changes only)

```
live_positions  (existing + new columns marked NEW)
  mmsi BIGINT PK, name VARCHAR, lat DOUBLE, lon DOUBLE, sog DOUBLE, cog DOUBLE,
  heading DOUBLE, destination VARCHAR, ship_type INTEGER, length_m DOUBLE,
  kind VARCHAR, segment VARCHAR, region VARCHAR, updated_ts TIMESTAMP,
  imo BIGINT NEW, draught DOUBLE NEW, nav_status INTEGER NEW, eta VARCHAR NEW

ais_snapshots  (existing + new columns marked NEW)
  snapshot_ts TIMESTAMP, mmsi BIGINT, kind VARCHAR, segment VARCHAR, region VARCHAR,
  lat DOUBLE, lon DOUBLE, ship_type INTEGER, length_m DOUBLE,
  PRIMARY KEY (snapshot_ts, mmsi),
  sog DOUBLE NEW, nav_status INTEGER NEW, draught DOUBLE NEW, destination VARCHAR NEW
```

### freight_analytics.duckdb (new in Phase 2, owned by the analytics job, lives at `~/quant/freight/backend/data/freight_analytics.duckdb`)

```
meta_watermark        key VARCHAR PK, ts TIMESTAMP            -- incremental processing state
transit_events        mmsi BIGINT, chokepoint VARCHAR, entered_ts TIMESTAMP,
                      exited_ts TIMESTAMP, direction VARCHAR, kind VARCHAR,
                      segment VARCHAR, laden BOOLEAN,
                      PRIMARY KEY (mmsi, chokepoint, entered_ts)
anchored_episodes     mmsi BIGINT, zone VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP,
                      kind VARCHAR, segment VARCHAR,
                      PRIMARY KEY (mmsi, zone, start_ts)
fleet_density         ts TIMESTAMP, region VARCHAR, kind VARCHAR, segment VARCHAR,
                      laden_count INTEGER, ballast_count INTEGER, unknown_count INTEGER,
                      PRIMARY KEY (ts, region, kind, segment)
vessel_state          mmsi BIGINT PK, max_draught_seen DOUBLE, last_draught DOUBLE,
                      laden VARCHAR ('laden'|'ballast'|'unknown'), updated_ts TIMESTAMP
ais_events            event_id VARCHAR PK, type VARCHAR ('gap'|'loiter'|'sts'),
                      mmsi BIGINT, mmsi2 BIGINT, start_ts TIMESTAMP, end_ts TIMESTAMP,
                      lat DOUBLE, lon DOUBLE, region VARCHAR, kind VARCHAR,
                      segment VARCHAR, details VARCHAR (JSON string)    -- Phase 3
```

### Relationships

- `transit_events`/`anchored_episodes`/`ais_events` reference vessels by `mmsi`;
  current static data joins from `live_positions` at API time.
- Daily aggregates (transit counts, congestion counts) are NOT stored: derive by SQL
  `GROUP BY` at API time. Event-level rows are the source of truth.

---

## Phase 0 - Collector Capture Upgrade (top priority: do this first, today) - Completed: 2026-06-10, commit 2f6f0da (market-data) / c17b180 (freight)

*Goal: stop discarding draught, IMO, nav status and ETA, and densify history to 10-minute
snapshots, so every later phase has the data it needs.*
*Repo: `~/quant/shared/market-data` (NOT the freight repo). Depends on: nothing.*
*Estimated effort: 1 session.*

### What's New

- Collector stores draught, IMO, navigational status, ETA per vessel.
- Snapshots every 10 minutes instead of 30 (storage trivial: DB is 13 MB, 12 GB free).
- freight-api exposes the new fields on `/api/vessels`.

### aisstream field reference (verify against live messages before coding)

- `Message.PositionReport.NavigationalStatus` (int, 0=under way engine, 1=anchored,
  5=moored, 15=undefined; store raw int, sentinel 15 -> None is WRONG, 15 is valid
  "not defined", store it as-is and let consumers decide).
- `Message.ShipStaticData.ImoNumber` (int, 0 means unset -> None).
- `Message.ShipStaticData.MaximumStaticDraught` (float metres, 0 means unset -> None;
  values > 25.5 are sentinel -> None).
- `Message.ShipStaticData.Eta` (struct with Month/Day/Hour/Minute fields; format to
  string `"MM-DD HH:MM"`, treat Month 0 as unset -> None).

### Task Checklist

#### Collector (`market-data/ais/collector.py`)

- [x] Add a `_migrate(conn)` helper run in `AISCollector.__init__` after `_SCHEMA`:
      `ALTER TABLE live_positions ADD COLUMN IF NOT EXISTS imo BIGINT` (and draught
      DOUBLE, nav_status INTEGER, eta VARCHAR); same for ais_snapshots (sog DOUBLE,
      nav_status INTEGER, draught DOUBLE, destination VARCHAR). DuckDB supports
      `ADD COLUMN IF NOT EXISTS`. Also update the `_SCHEMA` CREATE statements so fresh
      DBs get the full schema directly.
- [x] In `_on_message`: capture `NavigationalStatus` from PositionReport into
      `v["nav_status"]`; capture `ImoNumber`, `MaximumStaticDraught`, `Eta` from
      ShipStaticData with the unset/sentinel handling above.
- [x] Update `_live_rows` and `_write_live` for the new columns. Switch both INSERTs
      to explicit column lists, e.g.
      `INSERT OR REPLACE INTO live_positions (mmsi, name, ...) VALUES (...)`, so column
      order can never silently drift again.
- [x] Update `_snapshot_rows`/`_write_snapshot` to include sog, nav_status, draught,
      destination.
- [x] Change `_SNAPSHOT_INTERVAL_MIN` default from "30" to "10".
- [x] Check `ais-collector.service` and `market-data/.env` for an
      `AIS_SNAPSHOT_INTERVAL_MIN` override that would defeat the new default; remove it
      if present. (None found; .env has no AIS_SNAPSHOT_INTERVAL_MIN.)
- [x] Confirm `fetchers/ais_dispersion.py` selects snapshot columns BY NAME (not
      `SELECT *` positional unpacking); fix if needed so added columns cannot break it.
      (Confirmed: it selects by explicit column list.)

#### freight-api (`~/quant/freight/backend`)

- [x] Add `imo`, `draught`, `nav_status`, `eta` (all optional) to the `Vessel` schema in
      `app/schemas.py` and populate them in the `/api/vessels` handler in `app/main.py`.
- [x] Update `backend/tests/conftest.py` seed schema to the new column set; extend
      `test_endpoints.py` to assert the new fields round-trip.

#### Deploy & Verify (Definition of Done)

- [x] `cd ~/quant/shared/market-data && .venv/bin/python -c "from ais.collector import AISCollector"` (import check).
- [x] `sudo systemctl restart ais-collector && sudo journalctl -u ais-collector -n 20 --no-pager`
      shows "snapshot 10 min" and a successful connect.
- [x] After ~15 min, a read-only DuckDB query shows non-null `draught` and `nav_status`
      for a meaningful share of `live_positions` rows, and `ais_snapshots` rows arriving
      at 10-min spacing with the new columns. (Verified: 229/5038 draught, 107 nav_status,
      152 imo, 238 eta within 3 min of restart.)
- [x] `cd ~/quant/freight/backend && .venv/bin/python -m pytest -q` green (13/13).
- [x] `sudo systemctl restart freight-api`; `curl -s localhost:8003/api/vessels | head -c 500`
      shows the new fields.
- [x] Commit both repos (market-data and freight) with clear messages.

---

## Phase 1 - Map UX: trails, dead-reckoning, detail panel, search - Completed: 2026-06-10, commit fbedca0

*Goal: clicking any vessel shows who it is and where it has been; vessels move smoothly
between polls; any vessel is findable by name.*
*Repo: `~/quant/freight`. Depends on: Phase 0 (richer fields; trails work with whatever
history exists).*
*Estimated effort: 2 sessions.*

### What's New (user-visible)

- Click a vessel: side panel with name, IMO, segment, flag fields (draught, destination,
  ETA, nav status, speed) plus a 24h/7d track polyline on the map.
- Vessels glide along their course/speed vector between 60s polls instead of jumping.
- Search box (name / MMSI / destination) with zoom-to-vessel.

### API Routes

- `GET /api/vessels/{mmsi}/track?hours=24` (clamp hours to [1, 336], default 24).
  Reads `ais_snapshots` for that MMSI ordered by `snapshot_ts`; returns
  `[{ts, lat, lon, sog}]`. Empty list (not 404) when no history.

### Task Checklist

#### API

- [x] Add `TrackPoint` schema and the track endpoint to `app/main.py` using the existing
      `db.query` lock-retry helper. Note `live_positions` and `ais_snapshots` live in the
      same DB file, so no new accessor is needed.
- [x] pytest: seed a few snapshot rows in the temp DB, assert ordering, hours clamping,
      and the empty case.

#### Frontend: fix the refresh flash (known live bug) + dead-reckoning

- [x] Fix vessels visibly disappearing for a couple of seconds on every 60s poll.
      Root cause: `VesselLayer.tsx`'s useEffect removes the whole layer group and
      rebuilds all ~5k markers whenever the `vessels` array changes (every refetch);
      with `markerClusterGroup({chunkedLoading: true})` the old layer vanishes
      instantly while the new markers stream in over several frames. Fix by making the
      layer persistent and diffing: keep a `Map<mmsi, marker>` ref, on data change
      `setLatLng`/restyle existing markers, add new ones, remove only vessels no longer
      present. Never tear down the group on a data poll (only on clustering/arrows
      toggle). This diff structure is also the foundation the dead-reckoning loop needs.
- [x] New `src/lib/deadReckoning.ts`: pure `projectPosition(lat, lon, sogKn, cogDeg,
      dtSec)` using the equirectangular approximation
      (dLat = d*cos(cog)/60nm, dLon = d*sin(cog)/(60*cos(lat))), with guards: null
      sog/cog -> no movement; cap dtSec at 600; sog < 0.3 kn -> no movement (anchored
      jitter). Vitest: known-answer cases (due north, due east at equator vs 60N, cap,
      null handling).
- [x] In `VesselLayer.tsx` (imperative marker layer): a single `requestAnimationFrame`
      loop (throttled to ~2 Hz with setTimeout, full 60fps is wasted on 5k DOM/canvas
      markers) that calls `setLatLng` with the projected position based on each vessel's
      `updated_ts`. Pause the loop when `document.hidden`.

#### Frontend: trails + detail panel

- [x] Extend `VesselDetail.tsx` (exists, 46 lines): show imo, draught, nav status
      (decode int to label: map 0/1/5 and common codes, else "code N"), destination,
      eta, sog/cog. Use the Card component (project preference: Card, not Panel).
      (Note: kept Panel for map overlay per conventions; Card is for analytics pages.)
- [x] On vessel select, fetch `/api/vessels/{mmsi}/track` via TanStack Query
      (staleTime 5 min) with a 24h/7d toggle; render a Leaflet polyline, colored by the
      vessel's segment color from `lib/segments.ts` (do not hardcode colors). Clear on
      deselect. Keep layer-toggle state in `routes/index.tsx` like the other layers.

#### Frontend: search

- [x] Search input in the controls area: case-insensitive substring match over the
      already-loaded vessels array (name, MMSI as string, destination). Dropdown of top
      ~20 hits; selecting one pans/zooms the map (`map.setView`, zoom 9) and opens its
      detail panel. Pure filter function unit-tested in vitest.

#### Definition of Done

- [x] `npm test` (18/18) and backend pytest (16/16) green; `npm run build` clean.
- [ ] On the live site: click a VLCC in the Gulf, see its details and a trail; watch a
      moving vessel drift smoothly between polls; search "EAGLE", get hits, zoom works.
- [x] Commit (fbedca0), restart freight-api, `npm run build`.

---

## Phase 2 - Analytics: transits, congestion, laden/ballast, density - Completed: 2026-06-10 ✅

*Goal: an Analytics page with daily chokepoint transit counts, port congestion, and
laden/ballast fleet splits, computed hourly from owned history.*
*Depends on: Phase 0 (nav_status, draught, 10-min cadence). Best started after the new
fields have accumulated 1-2 weeks; build it anytime, charts fill in as history grows.*
*Estimated effort: 2-3 sessions.*

### Architecture

New batch job (`backend/analytics/`) runs hourly via systemd timer, reads
`ais_positions.duckdb` read-only (reuse the lock-retry pattern), processes snapshots
since its watermark, and writes event-level rows to its own
`backend/data/freight_analytics.duckdb`. freight-api reads that file read-only. This
keeps the DuckDB single-writer rule intact for both files: collector owns one, the
analytics job owns the other, the API writes nothing.

### Detection algorithms (keep them pure functions over DataFrames, unit-testable)

- **Chokepoint transit:** for each of the 9 chokepoint regions (`singapore_malacca`,
  `suez`, `hormuz`, `panama`, `gibraltar`, `bosphorus_dardanelles`, `dover_channel`,
  `cape_good_hope`, `bab_el_mandeb`), group a vessel's consecutive snapshots with
  `region == <chokepoint>` into episodes (gap > 2h splits episodes). An episode is a
  transit if it has >= 2 fixes and net displacement >= 0.3 deg along the chokepoint's
  dominant axis. Direction: sign of displacement along that axis, labelled per
  chokepoint in a static dict (e.g. hormuz axis "lon", positive = "inbound_gulf",
  negative = "outbound"; suez axis "lat", positive = "northbound"). Vessels merely
  anchored inside the box (e.g. Singapore anchorage) fail the displacement test:
  correct.
- **Anchored episode:** consecutive snapshots where `nav_status IN (1, 5)` OR
  `sog < 0.5`, inside a defined anchorage zone, episode length >= 2h. Zones: new
  `backend/analytics/zones.py` with ~10 curated anchorage bboxes inside covered regions
  (Fujairah, Singapore East/West, Galveston lightering, Rotterdam, Qingdao, Santos,
  Port Hedland, Richards Bay, Suez anchorages). Verify each bbox visually against the
  live map before committing.
- **Laden/ballast:** per MMSI track `max_draught_seen` (proxy for design draught).
  Current ratio = last_draught / max_draught_seen: >= 0.8 laden, <= 0.65 ballast, else
  unknown. While history is shallow (max == last for most vessels), fall back to a
  static design-draught table by segment (VLCC 22.0, ULCC 24.0, Suezmax 17.0, Aframax
  14.9, Panamax tanker 13.5, Capesize 18.2, Panamax bulk 14.5, Supramax 12.8, Handymax
  11.5, Handysize 10.0, Small None) when `max_draught_seen < 0.7 * design`. Null
  draught -> unknown.
- **Fleet density:** at each snapshot_ts (hourly job appends only new ts), count
  vessels per (region, kind, segment) split by laden status.

### Task Checklist

#### Analytics job

- [x] `backend/analytics/__init__.py`, `zones.py` (anchorage bboxes + chokepoint axis
      dict), `detect.py` (pure functions: `transit_episodes(df)`,
      `anchored_episodes(df)`, `laden_status(draught, max_seen, segment)`),
      `build.py` (CLI entry: open both DBs, read snapshots > watermark minus 6h overlap,
      run detectors, `INSERT OR REPLACE` events, update vessel_state and fleet_density,
      advance watermark).
- [x] pytest for `detect.py` with hand-built snapshot fixtures: a clean Hormuz transit
      with direction, an anchored-in-box non-transit, an episode split by a 3h gap, a
      laden->ballast draught flip.
- [x] `backend/freight-analytics.service` (Type=oneshot, runs
      `.venv/bin/python -m analytics.build`, WorkingDirectory=backend) +
      `freight-analytics.timer` (OnCalendar=hourly, Persistent=true). Install both,
      `sudo systemctl enable --now freight-analytics.timer`.

#### API

- [x] Generalize `app/db.py`: `query(sql, params, db_path=None)` defaulting to the AIS
      DB; add `ANALYTICS_DB` path (env-overridable for tests).
- [x] New endpoints (schemas in `app/schemas.py`, group-by SQL over event tables):
      `GET /api/analytics/transits?chokepoint=&days=30` (daily counts by direction and
      kind), `GET /api/analytics/congestion?zone=&days=30` (daily anchored counts +
      median dwell hours from completed episodes), `GET /api/analytics/density?region=&days=30`,
      `GET /api/analytics/laden?kind=tanker` (current fleet split by segment),
      `GET /api/analytics/zones` (zone names + bboxes for the frontend).
- [x] pytest with a seeded temp analytics DB (mirror conftest pattern).

#### Frontend

- [x] New route `src/routes/analytics.tsx` (run `npx vite build --emptyOutDir=false` to
      regen routeTree). Enable the nav seam in `__root.tsx`.
- [x] Cards with recharts: chokepoint selector + daily transit bar chart (stacked by
      direction), congestion line chart per zone with dwell stat, laden/ballast stacked
      bar by segment, density area chart per region. Each card states its window and
      that series begin 2026-06 (honest axis, no fabricated history).
- [x] Empty/shallow-history states: charts render gracefully with < 7 days of data.

#### Definition of Done

- [x] Run `analytics.build` manually: detected 419 transit episodes from 157k snapshot rows.
      Timer active (fires hourly). Second run will be incremental via watermark.
- [x] All pytest + vitest green (44 backend tests); `/analytics` live with real data.
- [x] Spot-check one transit event against the map trail of that MMSI (sanity). MMSI 357932000 cape_good_hope westbound: lon 18.49->18.11 over 5 fixes, 0.37 deg displacement. Correct.
- [x] Update `~/quant/PROJECTS.md` freight row + `docs/CHANGELOG.md`. Commit.

---

## Phase 3 - Intelligence: AIS gaps, loitering, STS candidates ✅ Completed: 2026-06-10

*Goal: an Events feed surfacing dark-gap, loitering and ship-to-ship transfer candidates,
each linked to the vessel's trail on the map.*
*Depends on: Phase 2 (analytics job, zones, event storage pattern).*
*Estimated effort: 2 sessions.*

### Detection (extend `analytics/detect.py`; thresholds are constants at top of file)

- **AIS gap:** vessel with >= 6 fixes in the last 48h stops reporting for > 6h, AND its
  last fix is > 0.4 deg from every edge of its region bbox (not just sailing out of
  coverage), AND last sog > 2 kn (not anchored). Close the event when it reappears
  (record reappearance lat/lon in details). Label honestly as "signal lost" since
  terrestrial coverage is patchy.
- **Loitering:** episode of >= 12h where mean sog < 1 kn, outside every anchorage zone
  and > 0.2 deg from the region bbox edge (avoids half-observed behaviour at coverage
  borders).
- **STS candidate:** two tankers, both sog < 0.5 kn, within 500 m of each other
  (haversine) for >= 2h of overlapping snapshots, outside anchorage zones. Find pairs
  per snapshot_ts via a 0.01-deg grid hash (5k vessels -> trivial). `details` records
  both names, distance, duration.

### Task Checklist

- [x] `ais_events` table + detectors + dedup (event_id = sha1 of type|mmsi|start_ts
      truncated; INSERT OR REPLACE keeps re-runs idempotent; ongoing events update
      end_ts in place).
- [x] pytest: 12 detect unit tests (gap/loiter/sts true/false cases); 6 endpoint tests.
      62 total tests passing.
- [x] API: `GET /api/events?type=&days=7&limit=200` (newest first, joined with current
      vessel name/segment).
- [x] Frontend route `/events`: filterable table (type chips, time ago, vessel,
      location, duration); row click navigates to tracker. Event pins overlay on the
      tracker map (toggleable "Event pins" layer, last 48h).
- [ ] Thresholds review: after the first week, eyeball the feed; if gap events fire
      mostly at coverage borders, raise the edge margin. Record tuning in CHANGELOG.

#### Definition of Done

- [x] Detectors green in pytest (62/62); 0 events on day 1 (expected - gap needs 48h+
      history). Events page live with honest empty state.
- [x] Commit, restart services, build frontend, update CHANGELOG.

---

## Phase 4 - Stretch: deck.gl WebGL layer, density heatmap, live push (OPTIONAL)

*Goal: GPU-smooth rendering and a density heatmap; only worth it if Phases 0-3 are done
and the map feels like the bottleneck.*
*Estimated effort: 2-3 sessions. Everything renders on the visitor's GPU; the server
just serves JS.*

- [ ] `deck.gl-leaflet` (or `@deck.gl/leaflet` LeafletOverlay) hosting a
      `ScatterplotLayer` for vessels (replaces marker rendering path in `VesselLayer`,
      keep the Leaflet basemap + existing popups via picking) and a `HeatmapLayer`
      density toggle. Keep the old canvas path behind a feature flag until parity
      (clustering, heading arrows) is confirmed.
- [ ] Trails as a `PathLayer`; optionally `TripsLayer` animation for the selected
      vessel's last 24h.
- [ ] Optional live push: `GET /api/stream` SSE endpoint (FastAPI `StreamingResponse`)
      emitting changed vessels every 15s; frontend falls back to polling. Requires
      `proxy_buffering off` for that location in `nginx-freight.conf`. Only do this if
      dead-reckoning feels insufficient.
- [ ] Bundle check: deck.gl is heavy; code-split the map page (`vite` dynamic import)
      and confirm `npm run build` chunk sizes stay reasonable.

---

## Phase 5 - Vessel Registry: persistent Equasis store + enrichment crawler - Completed: 2026-06-12, commit 245cc02

*Goal: every IMO-bearing vessel in the live fleet has its Equasis registry data (flag,
owner, class, P&I, detention, MOU status) stored in a queryable database, refreshed
automatically, instead of scraped one-at-a-time into an in-process cache.*
*Depends on: the existing Equasis integration (`backend/app/equasis.py`, commit f4b6d46).*
*Estimated effort: 1-2 sessions.*

### Why

The current `/api/vessels/{imo}/equasis` endpoint scrapes on click and caches in
process: the cache dies on every restart, only vessels someone clicked are ever
fetched, and nothing can be filtered or aggregated. A fleet explorer (Phase 6) and risk
scoring (Phase 7) need the whole fleet's registry data in SQL.

### Architecture

New batch crawler `backend/registry/crawl.py` run by a systemd timer. It is the SINGLE
WRITER of a new `backend/data/vessel_registry.duckdb` (same ownership pattern as the
analytics job: collector owns ais_positions, analytics job owns freight_analytics,
crawler owns vessel_registry, freight-api reads all three read-only with lock-retry).

Crawl policy (politeness is mandatory, this is someone else's free service):
- One Equasis request every 6-10 s (randomized jitter), max ~200 ships per run.
- Timer every 2 h => full ~3,000-IMO fleet covered in roughly 1-2 days, then steady
  state refresh.
- Priority order each run: (1) IMOs currently in `live_positions` never fetched,
  (2) rows with `fetch_ok = false` older than 7 days (retry), (3) rows older than
  30 days (refresh, registry data drifts slowly).
- Reuse `equasis.EquasisClient` (login/re-login/session logic already works). Move the
  module to a shared location importable by both the API and the crawler if needed.

### Database (vessel_registry.duckdb)

```
vessel_registry
  imo BIGINT PRIMARY KEY,
  ship_name VARCHAR, flag VARCHAR, flag_code VARCHAR, call_sign VARCHAR,
  gross_tonnage INTEGER, dwt INTEGER,            -- cast to INT at write time (currently strings)
  ship_type VARCHAR, year_built INTEGER, ship_status VARCHAR,
  owner VARCHAR, ism_manager VARCHAR, ship_manager VARCHAR,
  class_society VARCHAR, pi_club VARCHAR,
  detention_rate_pct DOUBLE, paris_mou VARCHAR, tokyo_mou VARCHAR, uscg_targeting VARCHAR,
  fetched_ts TIMESTAMP, fetch_ok BOOLEAN         -- fetch_ok=false rows record failed lookups
```

### Task Checklist

#### Crawler

- [x] `backend/registry/__init__.py` + `crawl.py`: open ais_positions read-only to list
      candidate IMOs (`SELECT DISTINCT imo FROM live_positions WHERE imo IS NOT NULL`),
      apply the priority policy above, scrape via `equasis.get_ship_info`, cast
      gross_tonnage/dwt/year_built to int (None on failure), `INSERT OR REPLACE` into
      vessel_registry. Sleep 6-10 s (random.uniform) between requests. Log a one-line
      summary (n_new, n_refreshed, n_failed) per run.
- [x] Failed scrape (None or parse-empty): still write the row with `fetch_ok = false`
      and `fetched_ts = now` so it is not retried every run.
- [x] `backend/freight-registry.service` (Type=oneshot, `.venv/bin/python -m
      registry.crawl`, WorkingDirectory=backend, EnvironmentFile for the credentials) +
      `freight-registry.timer` (OnCalendar 2-hourly, Persistent=true).
      `sudo systemctl enable --now freight-registry.timer`.
- [x] pytest for the pure parts (priority ordering, int casting, upsert idempotence)
      with a seeded temp registry DB. Do NOT hit equasis.org in tests: monkeypatch
      `get_ship_info`.

#### API

- [x] Add `REGISTRY_DB` path to `app/db.py` (env-overridable for tests), same lock-retry
      read pattern.
- [x] Rework `GET /api/vessels/{imo}/equasis`: read vessel_registry first; if the row
      exists and `fetch_ok`, return it. On miss, fall back to the existing live scrape +
      in-process cache (do NOT write the DB from the API: single-writer rule; the
      crawler will persist it within 2 h). Existing response shape stays unchanged so
      the frontend needs no edits.
- [x] pytest: registry-hit path, registry-miss fallback path, fetch_ok=false treated as
      miss.

#### Definition of Done

- [ ] First manual run (`.venv/bin/python -m registry.crawl --limit 20` for a smoke
      test) populates 20 rows; timer enabled; after a day the registry covers the
      large majority of IMO-bearing live vessels.
- [ ] Detail panel still shows Equasis data, now instantly for crawled vessels.
- [ ] All pytest green. Commit, update CHANGELOG.

---

## Phase 6 - Fleet Explorer: the no-map data page

*Goal: a `/fleet` page where the whole tracked fleet is a filterable, sortable,
exportable table: show me every Barbados-flagged tanker, every vessel of a given owner,
everything classed by a non-IACS society, every Tokyo-MOU-grey flag, sorted by
detention rate.*
*Depends on: Phase 5 (registry DB).*
*Estimated effort: 1-2 sessions.*

### What's New (user-visible)

- New nav entry "Fleet". Page = filter bar + summary strip + data table. No map.
- Filters: free-text search (name/IMO/MMSI/owner), flag (dropdown with counts), owner
  (typeahead), class society, P&I club, Paris/Tokyo MOU colour, kind, segment, year
  built range, DWT range, detention rate >= X, live-only toggle (currently on the map
  vs everything ever registered).
- Sortable columns; pagination (100/page, server-side).
- Summary strip above the table recomputed for the current filter: vessel count, total
  DWT, average age, detention-rate distribution, top 5 flags / owners as clickable
  chips (clicking adds the filter).
- Row click: focus that vessel on the tracker map (reuse the existing
  mmsi/lat/lon URL-param navigation built for the Events page).
- CSV export of the current filtered set.

### API Routes

- `GET /api/fleet` - params: `q, flag, owner, class_society, pi_club, paris_mou,
  tokyo_mou, kind, segment, built_min, built_max, dwt_min, dwt_max, detention_min,
  live_only, sort, order, page`. Implementation: registry LEFT JOINed with
  live_positions on imo (joining in SQL across two DuckDB files: `ATTACH ... (READ_ONLY)`
  both, or read both into pandas and merge - pick whichever fits `db.py` cleanest).
  Returns `{total, page, rows: [...]}` where rows carry both registry fields and live
  fields (lat/lon/region/sog when the vessel is currently tracked).
- `GET /api/fleet/facets` - distinct value + count lists for the dropdown filters
  (flags, class societies, P&I clubs, MOU colours; owners top-200 by vessel count,
  full owner list is typeahead via `q` on /api/fleet).
- `GET /api/fleet/export` - same filters, streams `text/csv`.

### Task Checklist

#### API

- [ ] Schemas + the three endpoints. Build the WHERE clause from params with proper
      parameter binding (no string interpolation of user input into SQL).
- [ ] pytest: seed both temp DBs (registry + live), assert each filter narrows
      correctly, sort works, pagination math, facet counts, CSV header row.

#### Frontend

- [ ] New route `src/routes/fleet.tsx` (regen routeTree: `npx vite build
      --emptyOutDir=false`). Enable nav entry in `__root.tsx`.
- [ ] Filter state in URL search params (TanStack Router `validateSearch`) so filtered
      views are shareable/bookmarkable.
- [ ] Plain table (no new table library; ~20 columns max, 100 rows/page renders fine),
      sticky header, MOU colours reuse the `MouBadge` idea, detention colour-coding
      reuses the VesselDetail thresholds. Loading/empty states.
- [ ] Facet dropdowns show counts ("Liberia (412)"). Owner field = debounced text input.
- [ ] Summary strip + clickable top-flag/top-owner chips.
- [ ] "Show on map" per row -> navigate to `/` with the existing focus params (only for
      rows with live positions; otherwise the cell is blank).
- [ ] Export button hits `/api/fleet/export` with current params.

#### Definition of Done

- [ ] Filter flag=Barbados: table shows exactly the Barbados-flagged vessels, summary
      strip matches, CSV downloads the same rows.
- [ ] Filter by a top-5 owner chip, then row-click jumps to the vessel on the map.
- [ ] pytest + vitest green, `npm run build` clean. Commit, CHANGELOG.

---

## Phase 7 - Risk scoring: shadow-fleet indicators (innovation)

*Goal: a transparent, documented 0-100 risk score per vessel combining registry red
flags with the Phase 3 behavioural events, surfaced on the detail panel, as a Fleet
Explorer filter, and as a "High risk vessels" view.*
*Depends on: Phases 3 (events), 5 (registry), 6 (explorer).*
*Estimated effort: 1-2 sessions.*

### Why this is the differentiator

This is exactly what commercial maritime-intelligence vendors sell: combining static
registry weakness (no real P&I, non-IACS class, grey/black flag, old tanker) with
behavioural anomalies (AIS gaps, loitering, STS transfers) to flag likely shadow-fleet
/ sanctions-evasion candidates. All inputs are already owned: no new data source.

### Scoring (pure function, weights as constants, fully documented in the UI)

Indicators (each contributes points; tune weights after eyeballing results):
- Tanker AND age >= 15 years (shadow fleet skews old)
- `pi_club` missing OR not one of the 12 International Group clubs (static list in code)
- `class_society` does not contain "(IACS)" (Equasis already labels IACS members)
- Flag Paris MOU grey (+) or black (++); same for Tokyo MOU
- `detention_rate_pct` >= 5 (+) or >= 10 (++)
- Behavioural, from ais_events last 90 days: each gap event (+), each STS event (+),
  each loiter event (small +); cap the behavioural contribution.
- Owner is a single-vessel owner (count of imo per owner == 1 in registry): small +.
  One-ship shell companies are the classic shadow-fleet ownership pattern.

Honest framing rule: the UI must call these "risk indicators", never "sanctions
violator". Each vessel's score page lists exactly which indicators fired. No synthetic
data, no external sanctions lists (OFAC list ingestion is out of scope: see
Deliberately Not Building).

### Task Checklist

- [ ] `backend/registry/risk.py`: `risk_score(registry_row, event_counts) -> (score,
      fired_indicators)` pure function + the IG P&I club list. pytest with hand-built
      cases (clean modern VLCC ~0; old tanker, no P&I, black flag, 2 gaps -> high).
- [ ] Crawler run computes and stores `risk_score INTEGER` + `risk_indicators VARCHAR
      (JSON)` columns on vessel_registry (ALTER TABLE ADD COLUMN IF NOT EXISTS), reading
      event counts from freight_analytics.duckdb read-only.
- [ ] API: include score + indicators in `/api/fleet` rows and `/api/vessels/{imo}/equasis`;
      add `risk_min` filter param.
- [ ] Frontend: score badge (green < 25, yellow < 50, red >= 50) on VesselDetail with an
      expandable "why" list; risk filter + sortable column in Fleet Explorer; a "High
      risk" preset link (risk_min=50, sorted desc).
- [ ] Eyeball the top-20 list: do the flagged vessels make sense (old tankers, odd
      flags, gap history)? Record findings + weight tuning in CHANGELOG.

#### Definition of Done

- [ ] Score visible end-to-end; top-20 list is plausible; all tests green; commit.

---

## Backlog ideas (not yet phased)

Captured for later; promote to a phase only with a written plan:

- **Port-call history:** derive arrival/departure events per vessel from anchored
  episodes + destination changes; per-vessel voyage timeline on the detail panel.
- **Owner/fleet dashboards:** aggregate Phase 2 analytics by owner (e.g. which owners'
  VLCCs are laden vs ballast right now); needs Phase 5 join.
- **Destination-change log:** snapshot `destination` is already stored; a diff over
  history gives "vessel X re-routed from ROTTERDAM to SINGAPORE" events.
- **Email/RSS digest:** daily summary of new high-risk events (gaps, STS, new
  high-score vessels).

---

## Build Order Summary

| Phase | Goal | Repo | New tables | New routes | Sessions |
|---|---|---|---|---|---|
| 0 | Capture draught/IMO/nav/ETA, 10-min snapshots | market-data (+freight schemas) | 0 (+8 cols) | 0 | 1 |
| 1 | Trails, dead-reckoning, detail, search | freight | 0 | +1 | 2 |
| 2 | Transits, congestion, laden, density + Analytics page | freight | +5 | +5 | 2-3 |
| 3 | Gaps, loitering, STS + Events feed | freight | +1 | +1 | 2 |
| 4 | deck.gl, heatmap, SSE (optional) | freight | 0 | +1 | 2-3 |
| 5 | Persistent Equasis registry + crawler | freight | +1 | 0 (reworks 1) | 1-2 |
| 6 | Fleet Explorer data page | freight | 0 | +3 | 1-2 |
| 7 | Shadow-fleet risk scoring | freight | 0 (+2 cols) | 0 (extends 2) | 1-2 |

## Schema Evolution Map

| Table | Phase 0 | Phase 2 | Phase 3 | Phase 5 | Phase 7 |
|---|---|---|---|---|---|
| live_positions | +imo, draught, nav_status, eta | | | | |
| ais_snapshots | +sog, nav_status, draught, destination | | | | |
| meta_watermark / transit_events / anchored_episodes / fleet_density / vessel_state | | ✓ | | | |
| ais_events | | | ✓ | | |
| vessel_registry | | | | ✓ | +risk_score, risk_indicators |

## API Surface Map

| Route | Phase | Purpose |
|---|---|---|
| GET /api/vessels (extended fields) | 0 | Live positions + static data |
| GET /api/vessels/{mmsi}/track | 1 | Historical trail from snapshots |
| GET /api/analytics/transits | 2 | Daily chokepoint transit counts |
| GET /api/analytics/congestion | 2 | Anchored counts + dwell per zone |
| GET /api/analytics/density | 2 | Regional fleet density series |
| GET /api/analytics/laden | 2 | Laden/ballast split by segment |
| GET /api/analytics/zones | 2 | Anchorage zone geometry |
| GET /api/events | 3 | Intelligence event feed |
| GET /api/stream (SSE) | 4 | Optional live push |
| GET /api/vessels/{imo}/equasis | done (f4b6d46), reworked in 5 | Registry data per vessel |
| GET /api/fleet | 6 | Filterable/sortable fleet table |
| GET /api/fleet/facets | 6 | Filter dropdown values + counts |
| GET /api/fleet/export | 6 | CSV of current filter |

## Deliberately Not Building

- **Global AIS coverage:** terrestrial receivers make mid-ocean subscription useless;
  the 24 curated basins already cover every chokepoint and basin that matters.
- **Satellite AIS / paid data:** out of budget and against the project's free-source
  ethos.
- **Destination geocoding / ETA prediction ML:** the free-text destination field is
  garbage-in; revisit only after months of clean history.
- **WebSocket bidirectional streaming:** SSE (Phase 4, optional) is sufficient for a
  read-only feed; dead-reckoning already smooths the UX.
- **Backfilling history:** impossible by definition; charts honestly start at their
  collection date.
- **Per-visitor accounts/auth:** it is a public showcase.
- **External sanctions lists (OFAC/EU) ingestion:** Phase 7 scores only from owned
  data and public registry facts; matching named sanctioned entities is a legal-grade
  exercise this project should not pretend to do.
- **Aggressive Equasis crawling:** the crawler stays at one request per 6-10 s with a
  per-run cap, full stop. If Equasis blocks the account, the feature degrades to the
  on-click path; do not rotate accounts or evade.

## Execution Notes for the Implementing Model

- Work phases strictly in order; within a phase, work the checklist top to bottom and
  tick boxes in this file as you go (edit `- [ ]` to `- [x]`).
- Mark a completed phase by appending `✅ Completed: <date>, commit <hash>` to its
  heading, then continue to the next phase.
- After any backend change: restart the relevant service and check
  `journalctl -u <service> -n 30`. After any frontend change: `npm run build`.
- If aisstream message field names differ from the reference above, trust the live
  messages: log a few raw ShipStaticData/PositionReport payloads from the collector and
  adapt. Do not guess silently.
- If a task is blocked (missing key, schema surprise, systemd permission), stop and
  report rather than improvising around a hard rule (especially the single-writer and
  no-synthetic-data rules).
