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

## Phase 0 - Collector Capture Upgrade (top priority: do this first, today)

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

- [ ] Add a `_migrate(conn)` helper run in `AISCollector.__init__` after `_SCHEMA`:
      `ALTER TABLE live_positions ADD COLUMN IF NOT EXISTS imo BIGINT` (and draught
      DOUBLE, nav_status INTEGER, eta VARCHAR); same for ais_snapshots (sog DOUBLE,
      nav_status INTEGER, draught DOUBLE, destination VARCHAR). DuckDB supports
      `ADD COLUMN IF NOT EXISTS`. Also update the `_SCHEMA` CREATE statements so fresh
      DBs get the full schema directly.
- [ ] In `_on_message`: capture `NavigationalStatus` from PositionReport into
      `v["nav_status"]`; capture `ImoNumber`, `MaximumStaticDraught`, `Eta` from
      ShipStaticData with the unset/sentinel handling above.
- [ ] Update `_live_rows` and `_write_live` for the new columns. Switch both INSERTs
      to explicit column lists, e.g.
      `INSERT OR REPLACE INTO live_positions (mmsi, name, ...) VALUES (...)`, so column
      order can never silently drift again.
- [ ] Update `_snapshot_rows`/`_write_snapshot` to include sog, nav_status, draught,
      destination.
- [ ] Change `_SNAPSHOT_INTERVAL_MIN` default from "30" to "10".
- [ ] Check `ais-collector.service` and `market-data/.env` for an
      `AIS_SNAPSHOT_INTERVAL_MIN` override that would defeat the new default; remove it
      if present.
- [ ] Confirm `fetchers/ais_dispersion.py` selects snapshot columns BY NAME (not
      `SELECT *` positional unpacking); fix if needed so added columns cannot break it.

#### freight-api (`~/quant/freight/backend`)

- [ ] Add `imo`, `draught`, `nav_status`, `eta` (all optional) to the `Vessel` schema in
      `app/schemas.py` and populate them in the `/api/vessels` handler in `app/main.py`.
- [ ] Update `backend/tests/conftest.py` seed schema to the new column set; extend
      `test_endpoints.py` to assert the new fields round-trip.

#### Deploy & Verify (Definition of Done)

- [ ] `cd ~/quant/shared/market-data && .venv/bin/python -c "from ais.collector import AISCollector"` (import check).
- [ ] `sudo systemctl restart ais-collector && sudo journalctl -u ais-collector -n 20 --no-pager`
      shows "snapshot 10 min" and a successful connect.
- [ ] After ~15 min, a read-only DuckDB query shows non-null `draught` and `nav_status`
      for a meaningful share of `live_positions` rows, and `ais_snapshots` rows arriving
      at 10-min spacing with the new columns.
- [ ] `cd ~/quant/freight/backend && .venv/bin/python -m pytest -q` green.
- [ ] `sudo systemctl restart freight-api`; `curl -s localhost:8003/api/vessels | head -c 500`
      shows the new fields.
- [ ] Commit both repos (market-data and freight) with clear messages.

---

## Phase 1 - Map UX: trails, dead-reckoning, detail panel, search

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

- [ ] Add `TrackPoint` schema and the track endpoint to `app/main.py` using the existing
      `db.query` lock-retry helper. Note `live_positions` and `ais_snapshots` live in the
      same DB file, so no new accessor is needed.
- [ ] pytest: seed a few snapshot rows in the temp DB, assert ordering, hours clamping,
      and the empty case.

#### Frontend: fix the refresh flash (known live bug) + dead-reckoning

- [ ] Fix vessels visibly disappearing for a couple of seconds on every 60s poll.
      Root cause: `VesselLayer.tsx`'s useEffect removes the whole layer group and
      rebuilds all ~5k markers whenever the `vessels` array changes (every refetch);
      with `markerClusterGroup({chunkedLoading: true})` the old layer vanishes
      instantly while the new markers stream in over several frames. Fix by making the
      layer persistent and diffing: keep a `Map<mmsi, marker>` ref, on data change
      `setLatLng`/restyle existing markers, add new ones, remove only vessels no longer
      present. Never tear down the group on a data poll (only on clustering/arrows
      toggle). This diff structure is also the foundation the dead-reckoning loop needs.
- [ ] New `src/lib/deadReckoning.ts`: pure `projectPosition(lat, lon, sogKn, cogDeg,
      dtSec)` using the equirectangular approximation
      (dLat = d*cos(cog)/60nm, dLon = d*sin(cog)/(60*cos(lat))), with guards: null
      sog/cog -> no movement; cap dtSec at 600; sog < 0.3 kn -> no movement (anchored
      jitter). Vitest: known-answer cases (due north, due east at equator vs 60N, cap,
      null handling).
- [ ] In `VesselLayer.tsx` (imperative marker layer): a single `requestAnimationFrame`
      loop (throttled to ~2 Hz with setTimeout, full 60fps is wasted on 5k DOM/canvas
      markers) that calls `setLatLng` with the projected position based on each vessel's
      `updated_ts`. Pause the loop when `document.hidden`.

#### Frontend: trails + detail panel

- [ ] Extend `VesselDetail.tsx` (exists, 46 lines): show imo, draught, nav status
      (decode int to label: map 0/1/5 and common codes, else "code N"), destination,
      eta, sog/cog. Use the Card component (project preference: Card, not Panel).
- [ ] On vessel select, fetch `/api/vessels/{mmsi}/track` via TanStack Query
      (staleTime 5 min) with a 24h/7d toggle; render a Leaflet polyline, colored by the
      vessel's segment color from `lib/segments.ts` (do not hardcode colors). Clear on
      deselect. Keep layer-toggle state in `routes/index.tsx` like the other layers.

#### Frontend: search

- [ ] Search input in the controls area: case-insensitive substring match over the
      already-loaded vessels array (name, MMSI as string, destination). Dropdown of top
      ~20 hits; selecting one pans/zooms the map (`map.setView`, zoom 9) and opens its
      detail panel. Pure filter function unit-tested in vitest.

#### Definition of Done

- [ ] `npm test` and backend pytest green; `npm run build` clean.
- [ ] On the live site: click a VLCC in the Gulf, see its details and a trail; watch a
      moving vessel drift smoothly between polls; search "EAGLE", get hits, zoom works.
- [ ] Commit, restart freight-api, `npm run build`.

---

## Phase 2 - Analytics: transits, congestion, laden/ballast, density

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

- [ ] `backend/analytics/__init__.py`, `zones.py` (anchorage bboxes + chokepoint axis
      dict), `detect.py` (pure functions: `transit_episodes(df)`,
      `anchored_episodes(df)`, `laden_status(draught, max_seen, segment)`),
      `build.py` (CLI entry: open both DBs, read snapshots > watermark minus 6h overlap,
      run detectors, `INSERT OR REPLACE` events, update vessel_state and fleet_density,
      advance watermark).
- [ ] pytest for `detect.py` with hand-built snapshot fixtures: a clean Hormuz transit
      with direction, an anchored-in-box non-transit, an episode split by a 3h gap, a
      laden->ballast draught flip.
- [ ] `backend/freight-analytics.service` (Type=oneshot, runs
      `.venv/bin/python -m analytics.build`, WorkingDirectory=backend) +
      `freight-analytics.timer` (OnCalendar=hourly, Persistent=true). Install both,
      `sudo systemctl enable --now freight-analytics.timer`.

#### API

- [ ] Generalize `app/db.py`: `query(sql, params, db_path=None)` defaulting to the AIS
      DB; add `ANALYTICS_DB` path (env-overridable for tests).
- [ ] New endpoints (schemas in `app/schemas.py`, group-by SQL over event tables):
      `GET /api/analytics/transits?chokepoint=&days=30` (daily counts by direction and
      kind), `GET /api/analytics/congestion?zone=&days=30` (daily anchored counts +
      median dwell hours from completed episodes), `GET /api/analytics/density?region=&days=30`,
      `GET /api/analytics/laden?kind=tanker` (current fleet split by segment),
      `GET /api/analytics/zones` (zone names + bboxes for the frontend).
- [ ] pytest with a seeded temp analytics DB (mirror conftest pattern).

#### Frontend

- [ ] New route `src/routes/analytics.tsx` (run `npx vite build --emptyOutDir=false` to
      regen routeTree). Enable the nav seam in `__root.tsx`.
- [ ] Cards with recharts: chokepoint selector + daily transit bar chart (stacked by
      direction), congestion line chart per zone with dwell stat, laden/ballast stacked
      bar by segment, density area chart per region. Each card states its window and
      that series begin 2026-06 (honest axis, no fabricated history).
- [ ] Empty/shallow-history states: charts render gracefully with < 7 days of data.

#### Definition of Done

- [ ] Run `analytics.build` manually twice: second run is incremental (no duplicate
      events, watermark advanced). Timer fires (check `systemctl list-timers`).
- [ ] All pytest + vitest green; `/analytics` live with real accumulating data.
- [ ] Spot-check one transit event against the map trail of that MMSI (sanity).
- [ ] Update `~/quant/PROJECTS.md` freight row + `docs/CHANGELOG.md`. Commit.

---

## Phase 3 - Intelligence: AIS gaps, loitering, STS candidates

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

- [ ] `ais_events` table + detectors + dedup (event_id = sha1 of type|mmsi|start_ts
      truncated; INSERT OR REPLACE keeps re-runs idempotent; ongoing events update
      end_ts in place).
- [ ] pytest fixtures: a synthetic-free test is impossible without data, so build
      fixtures by copying real snapshot rows from the live DB into test seeds (real
      data, hand-labelled) - one true gap, one coverage-edge exit (must NOT fire), one
      anchorage dweller (must NOT fire as loiter), one stationary pair.
- [ ] API: `GET /api/events?type=&days=7&limit=200` (newest first, joined with current
      vessel name/segment).
- [ ] Frontend route `/events`: filterable table (type chips, time ago, vessel,
      location, duration); row click navigates to the tracker centered on the event
      lat/lon with the vessel's trail loaded. Event pins overlay (toggleable layer) on
      the tracker map showing last 48h events.
- [ ] Thresholds review: after the first week, eyeball the feed; if gap events fire
      mostly at coverage borders, raise the edge margin. Record tuning in CHANGELOG.

#### Definition of Done

- [ ] Detectors green in pytest; feed shows real events within 24h of deploy; at least
      one STS candidate manually verified by looking at the two trails side by side.
- [ ] Commit, restart services, build frontend, update PROJECTS.md + CHANGELOG.

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

## Build Order Summary

| Phase | Goal | Repo | New tables | New routes | Sessions |
|---|---|---|---|---|---|
| 0 | Capture draught/IMO/nav/ETA, 10-min snapshots | market-data (+freight schemas) | 0 (+8 cols) | 0 | 1 |
| 1 | Trails, dead-reckoning, detail, search | freight | 0 | +1 | 2 |
| 2 | Transits, congestion, laden, density + Analytics page | freight | +5 | +5 | 2-3 |
| 3 | Gaps, loitering, STS + Events feed | freight | +1 | +1 | 2 |
| 4 | deck.gl, heatmap, SSE (optional) | freight | 0 | +1 | 2-3 |

## Schema Evolution Map

| Table | Phase 0 | Phase 2 | Phase 3 |
|---|---|---|---|
| live_positions | +imo, draught, nav_status, eta | | |
| ais_snapshots | +sog, nav_status, draught, destination | | |
| meta_watermark / transit_events / anchored_episodes / fleet_density / vessel_state | | ✓ | |
| ais_events | | | ✓ |

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
