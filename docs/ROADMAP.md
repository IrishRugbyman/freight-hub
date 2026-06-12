# Freight Hub Roadmap

Forward-looking only. Completed work is in `docs/CHANGELOG.md`.

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

- **Port-call history:** derive arrival/departure events per vessel from anchored episodes + destination changes; per-vessel voyage timeline on the detail panel.
- **Owner/fleet dashboards:** aggregate analytics by owner (which owners' VLCCs are laden vs ballast right now); needs registry join.
- **Email/RSS digest:** daily summary of new high-risk events (gaps, STS, new high-score vessels).

---

## Deliberately Not Building

- **Global AIS coverage:** terrestrial receivers make mid-ocean subscription useless; the 24 curated basins cover every chokepoint that matters.
- **Satellite AIS / paid data:** out of budget and against the free-source ethos.
- **Destination geocoding / ETA prediction ML:** the free-text destination field is garbage-in; revisit only after months of clean history.
- **WebSocket bidirectional streaming:** SSE (Phase 4, optional) is sufficient for a read-only feed.
- **Backfilling history:** impossible by definition; charts honestly start at their collection date.
- **Per-visitor accounts/auth:** public showcase.
- **External sanctions lists ingestion:** risk scoring uses only owned data and public registry facts; matching named sanctioned entities is a legal-grade exercise this project should not pretend to do.
- **Aggressive Equasis crawling:** one request per 6-10s with a per-run cap, full stop. Do not rotate accounts or evade blocks.
