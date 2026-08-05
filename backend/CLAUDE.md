# CLAUDE.md - freight backend

Layer 5 of the hierarchy. Assumes `~/quant/freight/CLAUDE.md` (data flow, deploy, crawler
cautions, cycle board rules) has already been read; this file covers only what is specific to
`backend/`.

## Shape of the tree

```
app/          FastAPI service. main.py (~8k lines, 83 GET endpoints) + schemas.py (~1.7k lines
              of pydantic response models) + per-domain modules (cycle, fleet, feed, equasis,
              myshiptracking) + runner_*.py bridges into research/ projects.
analytics/    Hourly batch job (build.py) and the True ETA / destination-predictor stack.
              Writes freight_analytics.duckdb. Not imported by the API except via serving modules.
registry/     Daily crawlers (Equasis, MyShipTracking, ITU MARS, OFAC) + risk scoring.
              Write vessel_registry.duckdb / mst.duckdb / PG vessels.
tests/        pytest, TestClient + seeded temp DuckDB. conftest.py owns every fixture.
ingest_*.py   One-shot pipeline-route ingest scripts (root level, run by hand, not services).
data/         DuckDB files owned by the batch jobs. Never commit, never hand-edit.
```

`app/main.py` is one module by design (single `app`, no routers). New endpoints go at the
bottom of the relevant `# ---` banner section, with the response model added to `schemas.py`.
Do not split it into routers as a drive-by refactor.

## Hard rules

1. **The API never writes.** Every DuckDB handle opened from `app/` is `read_only=True`. The
   sole writers are the collector (market-data), `analytics/build.py`, and `registry/crawl*.py`.
   If an endpoint needs derived data, compute it in the batch job and read the table.
2. **All DB access goes through `app/db.py`.** `query()` for DuckDB (with the collector
   lock-retry), `pg_query()` for PostgreSQL `market_data`. Never `duckdb.connect` or
   `psycopg2.connect` inside an endpoint - `db.py` is what makes the paths env-overridable,
   which is what makes the tests possible.
3. **Market data is read through the market-data `loaders/` package**, not raw SQL against
   PG (quant-wide rule). `from loaders.freight import load_ais_dispersion` is the pattern.
4. **Placeholder parameter styles differ**: DuckDB uses `?`, PostgreSQL uses `%s` and
   `= ANY(%s)` with a Python list. Mixing them is the most common bug here.
5. Both `query()` and `pg_query()` **return an empty DataFrame on failure** rather than
   raising. Endpoints must handle `df.empty` explicitly and return an empty-but-valid
   response body; a missing DB is a normal state (fresh machine, collector not yet run).

## Freshness contract

`db.STALE_HOURS` (3, `FREIGHT_STALE_HOURS`) and `db.VISIBLE_HOURS` (24, `FREIGHT_VISIBLE_HOURS`)
are the two cutoffs. Beyond stale a vessel is "dark" and greyed; beyond visible it disappears.
Use `_fresh_cutoff()` / `_visible_cutoff()` in `main.py` rather than re-deriving a timedelta.

## Middleware and serving conventions

- `slowapi` limiter, 240/minute per IP, 429 JSON body.
- GZip above 2048 bytes (the vessels payload is 1.5 MB+ raw).
- CORS is an explicit allow-list: `https://freight.lbzgiu.xyz` + `http://localhost:5173`, GET only.
- Static-backed endpoints (`/api/routes`, `/api/dispersion`) serve precomputed JSON from
  `app/static/` via `_serve_cached()` and fall back to a live compute when the file is absent.
  Regenerate with `.venv/bin/python precompute_freight.py`.
- Writing any file at runtime uses `_write_atomic()` (tempfile + `os.replace`).

## Batch jobs (systemd, unit files live in this directory)

| Unit | Cadence | Writes |
|---|---|---|
| `freight-api.service` | always on, `:8003` | nothing |
| `freight-analytics.timer` | hourly | `data/freight_analytics.duckdb` |
| `freight-registry.timer` | 04:30 daily | `data/vessel_registry.duckdb` + PG `vessels` |
| `freight-mst.timer` | 05:00 daily | `data/mst.duckdb` |

`analytics/build.py` is incremental off a `meta_watermark` row with a 6 h overlap, idempotent
(`INSERT OR REPLACE`), and writes to `freight_analytics.new.duckdb` before an atomic rename, so
the API keeps reading the live file through the whole ~5-10 min build. `--reset` wipes the
watermark and reprocesses all history. Preserve all three of those properties in any change.

**The snapshot window is loaded into one DataFrame**, so window width is the job's memory
driver. It was OOM-killed three times in Jul 2026 (4.0-5.0 GB on a 7.6 GB box) and could not
self-recover: a dead run stops advancing the watermark, so the next run reads a larger window
and dies sooner. Recover a backlog with bounded passes, then one full run:

```bash
.venv/bin/python -m analytics.build --max-window-hours 48 --skip-derived   # repeat until clear
.venv/bin/python -m analytics.build                                        # final, with 7b-7e
```

`--skip-derived` skips `_run_derived_stages()` (7b-7e: ETA labels/samples/serving, destination,
drift). Those read the **full AIS history** regardless of the watermark, so they dominate the
footprint and grow daily; only their final state matters, so intermediate passes skip them.
The unit carries `MemoryMax=5G` to keep an overrun a contained cgroup OOM rather than a global
one that could pick postgres or the collector as its victim. Do not raise it without fixing the
underlying footprint.

Per-account quotas on the crawlers are not tuning knobs: raising the Equasis rate locks the
account for 7 days, and MyShipTracking is anonymous but IP-rate-limited. See the root CLAUDE.md.

## The True ETA / destination stack

`analytics/eta_*.py` is a lettered pipeline and the letters are load-bearing (see
`docs/ROADMAP_TRUE_ETA.md`): `eta_labels` (A, ground-truth arrivals) -> `eta_routing` (B,
searoute distance with a persistent cache) + `eta_samples` (B, training table) -> `eta_physics`
(C, deterministic ETA + intervals) -> `eta_ml` (D, LightGBM quantiles) -> `eta_serving` (E, live
scorer) -> `eta_backtest` (scoring harness) and `eta_drift` (G, drift watch). Trained artifacts
and the champion map are committed under `analytics/models/`; `eta_champion_map.json` decides
per-target which model serves, so a model swap is a data change, not a code change. Baselines in
`analytics/baselines/*.csv` exist to be beaten - regenerate them only deliberately.

The destination predictor mirrors this: `destination_labels` (transition graph) ->
`destination_features` (candidates) -> `destination_predict` (heuristic + LightGBM reranker) ->
`destination_serving`, with `destination_resolver` mapping free-text AIS destination strings to
UN/LOCODE seaports.

## Tests

```bash
cd backend && uv sync --extra dev
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_eta.py -q          # single module
```

Fixtures all live in `tests/conftest.py`; add to it rather than seeding a DuckDB inside a test:

- `client` - temp `ais_positions.duckdb` with 5 known vessels (1001-1005, one deliberately stale)
- `empty_client` - points at a missing DB, for the "collector never ran" path
- `client_with_snaps` - adds `ais_snapshots` history
- `analytics_client` - adds a seeded `freight_analytics.duckdb` (transits, anchorages, density,
  vessel_state, events, eta targets/arrivals)
- `static_routes_json` / `static_dispersion_json` - seed and restore the static JSON
- `setup_pg_vessels(monkeypatch, rows)` - serves registry reads from a PG **temp table** named
  `vessels`, which shadows `public.vessels` via search_path precedence

**Every new endpoint gets a test** (quant-wide convention: FastAPI services always get
integration coverage). Assert the empty-DB path too, not just the happy path. Pure logic in
`analytics/` (detectors, physics, resolvers) gets ordinary unit tests - that is what
`test_detect.py`, `test_eta.py`, `test_destination_*.py` already are.

The seeded fixtures make the suite real without touching live data; the no-synthetic-data rule
governs production analysis inputs, not test fixtures.

## Gotchas

- `ingest_*.py` at the repo root are one-shot scripts, not part of the service. They are run by
  hand and some hit slow external sources.
- `app/freight_api.egg-info/` is build output from the editable install; ignore it.
- `data/*.duckdb` and `.env` are local state. Nothing in `data/` is reproducible from git alone -
  it comes back by running the batch jobs.
- `runner_*.py` reach into `research/` projects through `project_paths.project_dir(slug)`, which
  validates the slug against a fixed set. Add new research projects to `_SLUGS` there.
