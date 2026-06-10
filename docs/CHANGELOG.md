# Freight Hub Changelog

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
