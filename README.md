# Freight Hub

Live maritime freight intelligence platform. [freight.lbzgiu.xyz](https://freight.lbzgiu.xyz)

---

## What it does

A real-time vessel tracking and analytics application built entirely on free, public AIS data. The tracker shows ~4,000 tankers and bulk carriers across 15 geographic basins with active terrestrial coverage. The analytics pipeline runs hourly and detects behavioural events from raw AIS history: chokepoint transits, anchorage episodes, ship-to-ship transfer candidates, AIS gaps, dark voyages, GPS spoofing anomalies, loitering, and destination changes.

On top of that, a risk scoring layer combines observed AIS behaviour with vessel registry facts (flag, P&I club, classification society, age) and OFAC SDN screening to produce a transparent 0-100 shadow-fleet risk score for each vessel. Scores are indicators, not accusations - the methodology is documented in `backend/registry/risk.py`.

Fleet intelligence uses MMSI-derived flag states (ITU Maritime Identification Digits) to classify every vessel as FOC, shadow, or standard regardless of Equasis coverage, and surfaces flag mismatches between MMSI-implied nationality and the registered flag.

There is also a physics-based ETA module (trailing 6h speed blended with segment cruise priors, calibrated uncertainty intervals) and a pipeline disruption map overlaying energy infrastructure on live vessel positions.

---

## Data pipeline

```
aisstream.io (free terrestrial AIS)
    │
    ▼  24/7 WebSocket collector  (market-data/ais/collector.py, separate repo)
ais_positions.duckdb             single writer: collector; API reads with lock-retry
    │
    ▼  hourly analytics job      (analytics/build.py, systemd timer)
freight_analytics.duckdb         single writer: analytics job; atomic rename on completion
    │
    ▼  FastAPI :8003             read-only; no DB writes from the API
    │
    ▼  React frontend            60s polling
```

The collector is the sole writer to `ais_positions.duckdb`. The analytics job is the sole writer to `freight_analytics.duckdb`. It writes to a scratch file and atomically renames it over the live database at the end of each run, so the API never reads a partially-written file.

Vessels older than 3 hours are excluded from all responses.

**Coverage note:** 24 basins are subscribed via aisstream.io. Nine of them (Hormuz, Arab Gulf, Bab-el-Mandeb, and others) consistently return zero positions because aisstream.io's free terrestrial network has no receivers there. The application discloses this explicitly rather than hiding empty zones - chokepoints without coverage are shown dashed on the map with a label. The detection is self-healing: a basin lights up automatically if a receiver ever comes online.

---

## Analytics and detection

The hourly analytics job (`analytics/`) runs the following detectors over raw AIS history:

| Signal | Method |
|---|---|
| Chokepoint transits | Axis-crossing inside curated bounding boxes (Suez, Singapore, Malacca, Dover, Gibraltar, Bosphorus, Danish Straits, Cape of Good Hope, Cape Horn, Panama) |
| Anchorage episodes | Speed + position clustering inside 16 named anchorage zones (ARA, Suez roads, Fujairah, Singapore, Qingdao, Port Hedland, Richards Bay, Santos, Galveston, others) |
| STS candidates | Vessel pairs within 0.5 nm, both near-stationary, neither at a known anchorage |
| AIS gaps | Intervals > 6 hours without a position update from a vessel previously active in a covered region |
| Dark voyages | AIS gap events that bridge two observed positions with implausible displacement (vessel re-appeared far from where it disappeared) |
| GPS spoofing | Sudden large displacement inconsistent with vessel speed history |
| Loitering | Extended low-speed operation outside anchorage zones |
| Destination changes | Free-text destination field edits detected via edit-distance comparison |

Anchorage dwell and port congestion monitoring merge overlapping episode fragments into continuous spans before computing current vessel counts and dwell-time baselines. This is non-trivial because the sliding-window job stores each incremental pass as a closed fragment; raw `end_ts IS NULL` queries would always return zero.

---

## Vessel registry and risk scoring

The Equasis crawler (`registry/crawl.py`) enriches vessels with owner, P&I club, classification society, and flag from Equasis. It runs once daily at 04:30 UTC, processes 100 vessels per run, and aborts immediately on account lock detection rather than continuing to hammer a locked session. Current coverage is ~13% of live IMOs (sparse by design - aggressive crawling is explicitly out of scope).

Risk scoring (`registry/risk.py`) combines:

- OFAC SDN screening (US Treasury public XML list, no auth required)
- Flag: FOC list, high-shadow-activity flag set
- P&I: absence of an International Group club
- Classification society: presence on known sub-standard registries
- Age: vessels over 20 years weighted upward
- AIS behaviour: observed gap frequency, STS event count

All inputs are public facts or owned observations. The score weights are constants at the top of `risk.py` and are tunable.

---

## Fleet intelligence

Flag state is derived from the vessel's MMSI using the ITU Maritime Identification Digit table, giving 100% coverage independent of registry enrichment. The derivation lives in `quant_lib.freight.flags` (shared library). This supports:

- FOC classification against the ITF flags-of-convenience list
- Shadow-flag classification against a curated high-risk set
- Flag mismatch detection (MMSI-implied nationality vs Equasis registry flag, ISO-normalised)

---

## Stack

**Backend** (`backend/`)
- FastAPI, Python 3.11+, uv for dependency management
- DuckDB (read-only access; no ORM)
- pandas for analytics computation
- slowapi rate limiting
- pytest + TestClient + seeded temporary DuckDB for tests

**Frontend** (`frontend/`)
- React 19 + Vite + TypeScript (strict)
- TanStack Router (file-based) + TanStack Query (60s polling)
- Tailwind CSS v4
- react-leaflet + leaflet.markercluster (OSM/CartoDB dark tiles)

**Infrastructure**
- systemd services: `freight-api` (FastAPI), `ais-collector` (WebSocket client), `freight-analytics.timer` (hourly analytics job)
- nginx reverse proxy + TLS (Certbot)
- DuckDB files on local disk (no Postgres dependency)

---

## Running locally

```bash
# Backend
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --port 8003 --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # http://localhost:5173, proxies /api -> :8003

# Tests
cd backend && uv run pytest -q
cd frontend && npm test
```

The backend requires `AIS_POSITIONS_DB` to point to a populated `ais_positions.duckdb`. Without it the live vessel endpoints return empty results but the application starts cleanly.

```bash
# Build for production
cd frontend && npm run build   # tsc -b + vite build -> dist/ (nginx serves this directly)
```

---

## Research subprojects

Two backtest projects live in `research/` and are wired to forthcoming tabs in the frontend:

- **`research/transport-arb/`** - Forward-adjusted crude/products transport arbitrage matrix across 8 routes. Runner: `backend/app/runner_routes.py`. Target tab: `/routes`.
- **`research/freight-dispersion/`** - Capesize 5TC FFA vs fleet geographic dispersion backtest. Runner: `backend/app/runner_dispersion.py`. Target tab: `/dispersion`.

Each has its own uv venv symlinked from `~/data/`.

---

## Deliberate non-goals

- **Satellite AIS** - out of budget. Terrestrial coverage is honest about its gaps.
- **Global ocean coverage** - mid-ocean vessels are outside the scope of a terrestrial feed.
- **Destination-string geocoding** - the AIS destination field is free text, garbage-in by nature. ETA is computed only to resolved chokepoint and port targets, not raw destination strings.
- **Sanctions matching by vessel name** - legal-grade entity matching is not something this project should pretend to do. The OFAC screen matches only on IMO number against the SDN XML.
- **Aggressive Equasis crawling** - one request per 6-10 seconds, 100 vessels per day, full stop. The account lock mechanism is respected, not circumvented.
- **Per-visitor auth** - public showcase.
