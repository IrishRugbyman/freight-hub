# CLAUDE.md

Guidance for Claude Code when working in the freight hub.

## What this is

Standalone freight/maritime web app, live at **https://freight.lbzgiu.xyz**. First and
currently only page is a **live vessel tracker** (interactive Leaflet map of tankers +
bulk carriers from AIS). Built as a hub designed to later absorb `transport-arb` and
`freight-dispersion` from quant-portfolio (the nav has disabled "Routes"/"Dispersion"
seams). Separate from quant-portfolio because it is a *live* app, not the static
precompute showcase.

## Stack

- **Frontend** (`frontend/`): React 19 + Vite + TypeScript, TanStack Router (file-based
  `src/routes`) + TanStack Query (60s polling), Tailwind v4, react-leaflet + OSM/Carto
  dark tiles + leaflet.markercluster. `npm` toolchain (mirrors quant-portfolio).
- **Backend** (`backend/`): FastAPI `freight-api` on `:8003`, a thin read layer over the
  AIS collector's `live_positions` table. pytest suite (mirrors squiidwiki).

## Data flow

```
market-data/ais/collector.py  →  ais_positions.duckdb (live_positions, upsert ~90s)
                                        │ read-only (lock-retry)
backend/app  →  GET /api/vessels|chokepoints|meta|health  →  frontend polls every 60s
```

The collector (in market-data, NOT here) is the single aisstream consumer. This app
never writes. Vessels older than `FREIGHT_STALE_HOURS` (3) are excluded everywhere.

## Run / build / test

```bash
# backend
cd backend && uv sync --extra dev
.venv/bin/python -m pytest -q                      # seeded-DuckDB endpoint tests
.venv/bin/uvicorn app.main:app --port 8003         # local serve (proxied at /api in dev)

# frontend
cd frontend && npm install
npm run dev        # http://localhost:5173, proxies /api -> :8003
npm test           # vitest (segment color/order logic)
npm run build      # tsc -b && vite build -> dist/ (nginx root)
```

## Deploy

- `backend/freight-api.service` → systemd (`:8003`). `sudo systemctl restart freight-api` after backend changes.
- `nginx-freight.conf` → symlinked into sites-enabled; TLS via certbot (already issued). After a frontend change just `npm run build` (dist is the nginx root, no reload).
- Cloudflare A record `freight.lbzgiu.xyz` → 178.104.244.177 (orange-cloud; certbot HTTP-01 passed through the proxy fine).

## Conventions

- Frontend map markers are rendered imperatively in `components/tracker/VesselLayer.tsx`
  (cheap for ~1500 points); everything else is normal React.
- Segment→color and ordering live in `lib/segments.ts` (pure, unit-tested) — reuse it,
  don't hardcode colors in components.
- The 4 map layers (clustering, heading arrows, counts, chokepoints) are independently
  toggleable; state lives in the page (`routes/index.tsx`).
