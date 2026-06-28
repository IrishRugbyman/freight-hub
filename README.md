# Freight Hub

Live maritime freight tracker and analytics dashboard - [freight.lbzgiu.xyz](https://freight.lbzgiu.xyz)

## What it is

A real-time vessel tracking and freight analytics web app built on live AIS data. Currently covers:

- **Live tracker** - interactive map of tankers and bulk carriers with AIS position updates
- **Analytics** - fleet statistics, STS operations, ARA anchorage activity
- **Pipeline disruption map** - overlays key energy infrastructure on vessel positions

Research subprojects (`research/`) wire into forthcoming Routes and Dispersion tabs:
- **transport-arb** - forward-adjusted crude/products transport arbitrage matrix (8 routes)
- **freight-dispersion** - Capesize 5TC FFA vs fleet geographic dispersion backtest

## Stack

- **Frontend**: React 19 + Vite + TypeScript, TanStack Router + Query, Tailwind v4, react-leaflet
- **Backend**: FastAPI on `:8003`, reads from a 24/7 AIS collector (`ais_positions.duckdb`)
- **Data**: [aisstream.io](https://aisstream.io) live terrestrial AIS feed

## Running locally

```bash
# Backend
cd backend
uv sync
uv run uvicorn app.main:app --port 8003 --reload

# Frontend
cd frontend
npm install
npm run dev
```
