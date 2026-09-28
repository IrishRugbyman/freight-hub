# CLAUDE.md - freight frontend

Layer 5 of the hierarchy. Assumes `~/quant/freight/CLAUDE.md` has been read; this file covers
only what is specific to `frontend/`.

## Stack

React 19 + Vite + TypeScript, TanStack Router (file-based) + TanStack Query, Tailwind v4,
react-leaflet + leaflet.markercluster over a MapLibre GL vector basemap, deck.gl (via
`deck.gl-leaflet`) for the heavy layers,
recharts for analytics charts, lucide-react for icons. `npm`, not bun or pnpm.

```bash
npm install
npm run dev      # :5173, proxies /api -> :8003
npm test         # vitest run
npm run build    # tsc -b && vite build -> dist/ (nginx root, no reload needed)
```

## Routing

`src/routes/` is scanned by the TanStack Router plugin and `routeTree.gen.ts` is **generated** -
never hand-edit it. Files prefixed with `-` (`-OverviewCards.tsx`, `-analyticsShared.tsx`) are
deliberately excluded from routing; that is how the analytics tab modules sit next to the route
they belong to without becoming URLs. Adding a route: create the file, then
`npx vite build --emptyOutDir=false` before `npm run build` to regenerate the tree.

Nav lives in `src/routes/__root.tsx`. Disabled entries render as a "soon" chip via `NavItem`'s
`disabled` prop - keep new seams that way rather than linking to an empty page.

## Data layer

`src/lib/api.ts` is the single API surface: ~79 exported `use*` hooks, all going through one
`getJSON<T>()` helper. Do not call `fetch` from a component and do not put a raw URL in a route
file - add a hook here.

**Response types are generated, not written.** `export type Vessel = Schemas['Vessel']` aliases
into `src/lib/api-schema.gen.ts`, which `npm run gen:api` builds from `openapi.json` (a snapshot
of the backend's schema). After a backend model changes: `.venv/bin/python
scripts/export_openapi.py` in `backend/`, then `npm run gen:api` here, then `tsc -b` shows every
consumer the change breaks. The backend test `test_openapi_snapshot.py` fails if the snapshot is
stale. Never hand-edit the `.gen.ts` file; only the few types with no backend model (request
params like `VesselFilters`, and `EquasisData`, whose endpoint returns a plain dict) are
hand-written. The generator runs through a pinned `npx` because it declares a TypeScript 5 peer
and this project is on 6; the generated file is committed, so builds never need it.

Cadence constants, matched to how the data is actually produced:

| Constant | Value | Applies to |
|---|---|---|
| `REFETCH_MS` | 60 s | live AIS: vessels, chokepoints, meta, dispersion-live |
| `ANALYTICS_STALE` | 10 min | anything from the hourly analytics job |
| `CYCLE_REFETCH_MS` | 15 min | the cycle board |
| `staleTime: Infinity` | - | precomputed statics (routes, dispersion, zones) |
| 12 h | - | Equasis registry data (effectively static) |

When adding a hook, pick the tier from the data's real refresh rate - polling a table the batch
job rewrites hourly at 60 s is pure waste. Query keys are `['name', ...params]`; include every
parameter that changes the URL or the cache will serve the wrong response.

## Styling

Tailwind v4 with the theme declared in `src/index.css` under `@theme` - a dark, low-chroma
palette (`background`, `card`, `primary`, `muted`, `border`, ...). **Use the semantic tokens**
(`bg-card`, `text-muted-foreground`, `border-border`), never raw hex or `bg-slate-800`. Fonts are
Plus Jakarta Sans (sans) + JetBrains Mono (mono); numeric columns get `tabular-nums`.

`src/components/ui/` holds the small primitives (`Card`, `Panel`, `Skeleton`, `Switch`, `Tabs`),
shadcn-flavoured but hand-rolled. Compose these rather than restyling a `<div>`. `cn()` from
`@/lib/utils` merges classes. `@` aliases `./src`.

The one deliberate flourish is the film-grain `body::after` overlay; leave it alone.

## Pure logic lives in `src/lib/` and is unit-tested

`segments.ts` (segment order + color, keyed by `(kind, segment)` because names repeat across
bulk and tanker), `eta.ts`, `cycle.ts`, `deadReckoning.ts`, `status.ts`. Each `.ts` with logic has
a `.test.ts` beside it (vitest). **Never hardcode a segment color in a component** - call
`colorFor(kind, segment)`. New display logic with a branch or a formula belongs here with a test,
not inline in a 700-line card file.

## Map layers

The basemap is **MapLibre GL vector tiles mounted as a Leaflet layer**
(`components/tracker/VectorBasemap.tsx` + `lib/basemap.ts`), not a `TileLayer`. Three things
about it are load-bearing:

- **It is a layer swap, not a migration.** Every vessel/overlay layer below stays on Leaflet.
  Do not "finish the job" by porting them to MapLibre without a reason.
- **The palette is applied at runtime and the layer ids are pinned.** Water is near-black
  (`#191F24`) and land lighter (`#32414E`) - MarineTraffic's scheme, and the inverse of
  CARTO dark-matter's default, because vessel markers sit on water. Ids are matched exactly,
  never by substring, so an upstream rename fires the drift warning instead of silently
  un-painting the map. `basemap.test.ts` covers this.
- **Raster is gone on purpose.** CARTO began watermarking its keyless raster endpoint in
  late August 2026 and is retiring raster. The watermark arrived as HTTP 200 with a valid
  PNG - no 4xx, no console error - so nothing caught it. If the basemap ever looks wrong,
  suspect a silent upstream change before suspecting this code.

`maplibre-gl` is ~790 kB and gets its own manual chunk (the rule sits *before* the `leaflet`
rule in `vite.config.ts`, because `@maplibre/maplibre-gl-leaflet` matches both and falling
into the leaflet chunk would drag the whole engine onto the critical path).


`components/tracker/` renders the map. `VesselLayer.tsx` builds markers **imperatively** against
the Leaflet API (cheap for ~1500 points, and React reconciliation is not); `DeckGLLayer.tsx`,
`TrailLayer.tsx`, `PipelineLayer.tsx`, `RiskLayer.tsx`, `ChokepointLayer.tsx`,
`EventPinsLayer.tsx` are the other overlays. Everything outside the map is normal declarative
React. Layer visibility state lives in the page (`routes/index.tsx` / `routes/tracker.tsx`), not
in the layer components.

deck.gl integration is fragile and the config is load-bearing: `vite.config.ts` force-resolves
`deck.gl-leaflet` to its ESM build and pre-bundles every `@deck.gl/*` + `@luma.gl/*` package as
one entry set, because otherwise a second luma.gl copy gets initialized and the app dies on
`picking.defaultUniforms`. The comments there explain it. Do not "tidy" `resolve.alias`,
`dedupe`, or `optimizeDeps.include`.

`build.rollupOptions.output.manualChunks` splits router/query/luma/deck/leaflet/recharts/react
so recharts stays off the critical path for the map pages. Keep new heavy deps out of the
initial chunk.

## Conventions worth keeping

- Big analytics tabs are split into `-*Cards.tsx` modules under `routes/analytics/`, with shared
  helpers (`fmt`, `ChartSkeleton`, `EmptyState`, `TOOLTIP_STYLE`, `REGION_LABELS`,
  `useGoToTracker`) in `-analyticsShared.tsx`. Card-local helpers stay co-located. A tab file
  that outgrows ~700 lines becomes a composer over per-area modules, as `-PortsCargoCards.tsx`
  does over `-PortsCards` / `-CargoCards` / `-EuropeanSupplyCards` / `-EtaCards`.
- Every panel handles three states: loading (`Skeleton` / `ChartSkeleton`), empty
  (`EmptyState` with a reason), and data. The backend returns empty-but-valid bodies routinely,
  so the empty state is a normal path, not an error path.
- Recharts tooltips/legends use `TOOLTIP_STYLE` / `LEGEND_STYLE` so charts read as one system.
- `npm run build` runs `tsc -b` first: a type error fails the build. Fix types, do not `any` past
  them.
