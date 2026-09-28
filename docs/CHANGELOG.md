# Freight Hub Changelog

## Archives

- [2026-Q2](changelog/2026-Q2.md) - 53 entries, 2026-06-10 to 2026-06-30

## 2026-09-28 - main.py split into routers; ETA target seeding stops depending on the web app

`backend/app/main.py` had grown to 8,963 lines holding all 83 endpoints plus the
port reference data, the live-positions cache and every shared helper. It is now
a ~60-line app factory (limiter, GZip, CORS, `include_router`), and the code lives in:

- `app/routers/` - one module per page (`tracker`, `vessels`, `fleet`, `events`,
  `pipelines`, `cycle`, `research`) and six `analytics_*` modules split the same way
  as the frontend's `routes/analytics/-*Cards.tsx` (fleet, risk, chokepoints, ports,
  cargo, eta). The largest is `analytics_risk.py` at ~1.9k lines.
- `app/common.py` (freshness cutoffs, coercion, `haversine_nm`, `write_atomic`),
  `app/live.py` (the cached `live_positions` frame and feed status), `app/ports.py`
  (port and terminal reference data plus destination canonicalisation).

The move was done by an AST script, not by hand: each top-level statement moved
verbatim with its comments, imports were recomputed from `symtable` scope analysis
and pruned by ruff, and a shared name became public only where another module uses
it. **Acceptance was an identical `GET /openapi.json`** (paths and components) between
the old and new app, plus the full suite (736 passed). The suite caught two
regressions the OpenAPI comparison cannot see, both fixed: the static-JSON path was
computed from `__file__` and pointed into `app/routers/`, and three function-local
`from .schemas import` lines needed the extra dot. The stale "Phase NN" section
banners were dropped, and `_SEG_TYPICAL_SOG`, a speed-range table nothing read, was
deleted.

**`analytics/eta_labels.py` no longer imports the web app.** It pulled the
terminal dictionaries from `app.main`, which loaded FastAPI and every endpoint into
the batch job, inside a `try/except Exception` that fell back to a four-terminal
"vendored core" on any failure: target seeding would have shrunk from 47 curated
points to 4 with only a log warning. `app/ports.py` has no FastAPI or DB imports,
so this is now a plain import that fails loudly.

### Same day: generated API types, script move, card split

- **Frontend response types are generated from the backend schema.** `api.ts` hand-kept
  150 types mirroring `schemas.py`; they are now aliases into `api-schema.gen.ts`
  (`npm run gen:api`, from a committed `frontend/openapi.json` that
  `test_openapi_snapshot.py` holds to the live schema). `api.ts` went 2,556 -> 1,305 lines.
  `tsc` then found **a shipped bug**: the Fleet-at-time card read `laden` / `ballast` /
  `underway` while the API sends `*_count`, so every row showed blank counts since the card
  shipped. It also found that the fleet table pushed a `null` flag or owner into the URL filter.
  Response models now share an `ApiModel` base marking defaulted fields required in the
  serialisation schema (FastAPI always sends them), and `bbox` is a typed `(lat, lon)` pair.
- **Equasis panel 500'd for 97% of vessels** (6,900 of 7,078): the registry's integer `mmsi`
  and `ais_ship_type` columns came back as `numpy.int64`, which FastAPI cannot encode. Found
  while smoke-testing the router split; it predates it. Fixed, with a regression test.
- **The 13 pipeline ingest scripts moved to `backend/scripts/pipelines/`**, with a README
  listing each one's source and target table. Their `sys.path` inserts all pointed at paths
  that do not exist and were removed; data paths now resolve from `backend/`.
- **`-PortsCargoCards.tsx` (2,207 lines) split** into `-PortsCards`, `-CargoCards`,
  `-EuropeanSupplyCards` and `-EtaCards`, the original file keeping only the tab's
  composition. `useGoToTracker`, byte-identical in four files, now lives once in
  `-analyticsShared.tsx`.
- **The push-to-deploy workflow now has test gates.** A GitHub job type-checks the frontend
  (including the generated API types) and runs vitest; only then does the VPS deploy, which
  runs the full backend suite against the incoming revision in a throwaway worktree before
  pulling into the live checkout (the batch timers import from it, not just `freight-api`).
  Two latent faults went with it: the 10-minute default SSH `command_timeout` had killed the
  2026-08-16 deploy mid-script (now 30m), and the plain `uv sync` uninstalled pytest and four
  other dev packages on every deploy (now `--extra dev`).
- **First-load JavaScript cut from ~476 kB to ~173 kB gz** (the tracker page including its
  basemap: ~700 kB -> 394 kB). Every page was downloading deck.gl (216 kB) and recharts (107 kB).
  Rolldown's `manualChunks` shim captures a group's dependencies recursively, so the deckgl
  group swallowed `leaflet` and Vite's `__vitePreload` helper, and the recharts group swallowed
  `clsx`; the entry needed all three. Replaced with native `codeSplitting.groups` by priority,
  plus router `autoCodeSplitting` so chart-heavy route files leave the entry. WebGL mode still
  loads deck.gl on demand with no luma.gl init errors; all 12 pages checked in a browser.
- **`/api/vessels` returned 500 intermittently from 02:35 UTC.** The ghost-row lookup indexed the
  PG `vessels` master by MMSI, which is not unique there (66 duplicated MMSIs). Now `DISTINCT ON
  (mmsi)` preferring a valid IMO, then a verified row, then the newest.
- **Fleet-trend fixture no longer straddles midnight.** Two density rows seeded at `now - 2h`
  and `now - 1h` fell on different days between 00:00 and 02:00 UTC, failing two tests.

## 2026-09-27 - a second ML target, conformal calibrated on the cells the gate judges, and the served band held to the gate

The weekly ETA retrain log showed ML 4-5x worse than physics at short lead (5.35h vs
1.15h median |err| at 0-6h by actual lead). That turned out to be mostly the known
conditioning artifact of bucketing by the true outcome, but chasing it found three
real problems and one real improvement. Promoted cells went from **3 to 5**, every
cell that was already ML got more accurate, and live ML-served predictions went from
48% to 79% of the fleet.

**The ML learned hours from scratch; a log-ratio to physics does better.** Four
targets measured on the gate's own walk-forward split (P50 head, 1.46M samples):
raw hours, raw + physics as a feature, the additive residual `truth - physics`, and
`log(truth / physics)`. The additive residual was no better than raw (11.30h vs
11.35h overall), which rules out "the model just needed the physics anchor". The
log-ratio was best overall (10.75h) and, with a voyage-grouped bootstrap, beat raw
in 8 of 10 gate cells with 95% CIs excluding zero: port|0-6h 12.22 -> 11.27h
(n=128k), port|12-24h 13.51 -> 12.89, port|24-48h 12.03 -> 11.71. It lost both 48h+
cells by 2-3h, because a relative loss underweights large absolute misses. Neither
dominates, so both are trained and the gate picks per cell among physics and each
target. Quantiles are equivariant under `q -> physics * exp(q)`, so the log-ratio
heads map back to hour quantiles exactly. `raw` keeps its artifact filenames and
the loader reads the old single-target layout, so nothing broke in between.

**Chokepoint intervals were calibrated on ports.** The conformal offset was one per
lead bucket, pooled over target types. Ports are ~80% of rows, so they set it, and
chokepoint cells realised 0.68-0.74 coverage against the [0.75, 0.85] gate: four
cells where ML beat physics on error were refused on coverage alone. Offsets are now
group-conditional (Mondrian) on `target_type x bucket`, falling back to bucket, then
global. A first version keyed the bucket on the model's *own* P50, which is also
serve-time-known but is not the partition the gate judges or serving routes on, so
chokepoint|48h+ still realised 0.47. Keyed on the *physics* bucket, chokepoint|24-48h
went 0.683 -> 0.798.

**The served band had been under-covering, and a pooled number hid it.** The
production refit trains on train+calib, then recalibrated conformal on calib, which
is in-sample. On 2026-09-08 that measured harmless (0.801 vs 0.822 held-out, one
pooled number). Measured per cell on the test window (a hold-out for the refit too),
in-sample offsets under-covered in **all 20** (target, cell) pairs: 0.70-0.76 in
promoted port cells, 0.718 on port|24-48h, i.e. outside the band the promotion had
passed on. The refit now serves the evaluation model's held-out offsets
(`PROD_OFFSETS = "heldout"`), nearer 0.80 in all 20. Every run prints both, so the
choice can be re-checked from any retrain log.

**The gate judged one model and served another.** Even with held-out offsets,
chokepoint|48h+ (raw, 5.66h vs physics 8.20h) passed at 0.759 on the evaluation
model while the refit that would serve it realised 0.659. `demote_uncovered` now
holds the served model's hold-out coverage to the same band and demotes any cell
that misses it, with a warning. It fired on exactly that cell. The ML scoreboard
rows now score the served refit too, matching the hourly scorer.

Result, from the live run through `freight-eta-retrain.service` (2026-09-28 00:34):
chokepoint|12-24h, port|0-6h, port|12-24h, port|24-48h -> logratio; port|48h+ -> raw.
Served coverage in those cells 0.764-0.814. The live scoreboard returned all 24 rows
in each of 41 polls during the run. A read-only `build_predictions` smoke run on the
new artifact produced 2,120 ML rows of 2,699, all ordered, with no NaN and no
negative bounds.

Cost: 40m28s wall and 2.26 GB cgroup peak for the live run (54m13s / 2.31 GB RSS in
a dry run on 8 threads), up from ~25 min and 1.5 GB. The log-ratio P50 head
converges near 8000 rounds. Production refits reuse the evaluation fit's round
counts instead of re-probing, which is what keeps it at ~2x. `MemoryMax` 3G -> 4G
(2.31 GB RSS x ~1.17 accounting is ~2.7 GB), and the timer moved from Sun 02:20 to
01:10 so it still ends well before the 03:20 derived stages. The previous artifact
is kept at `~/data/freight-eta-models-backup-20260927/` for rollback.

Tests: 31 cases in `test_eta_ml.py` (from 16). Each new one was checked against an
independent oracle, and three were mutation-checked to fail on the bug they guard:
target choice under both dict orders, the physics-vs-ML bucket key, and the
cell-level offset lookup.

## 2026-09-10 - both challengers retrained on a schedule, and the retrain stopped blanking the site

Follow-on from the band fix. Three of the four things here were found by measuring
the cost of the work rather than by looking for bugs.

**The retrain was blanking the live analytics endpoints, and the timer shipped the
night before would have done it every Sunday.** Both trainers connected to the live
analytics DuckDB read-WRITE and held that connection for the whole run - 92s for
`eta_ml`, 24m30s for `destination_predict`. DuckDB's write lock excludes new
readers, and `app.db.query` opens a fresh read-only connection per request and
retries a locked file for only ~9s before returning an **empty DataFrame**. So
every analytics endpoint served HTTP 200 with no rows for the length of a retrain.

Confirmed live, not reasoned about: with the destination job holding the lock,
`GET /api/analytics/eta-accuracy` returned `{"run_ts": null, "rows": []}` and the
scoreboard rendered blank; killing the job made the same request return all four
models instantly. Nothing 5xxed, so neither UptimeRobot nor Sentry could see it.

Both `run()` entrypoints are now two-phase: training loads through a READ-ONLY
connection (DuckDB allows concurrent readers, so the API keeps serving), and the
exclusive connection is opened only for the sub-second metric writes. Verified by
polling the live endpoint every 8s through a full retrain - 24 rows throughout,
where before it would have been 0. Separately, exhausting the retry budget in
`app.db.query` now logs a warning instead of silently returning empty: the budget
is there for the collector's brief per-cycle lock, so using all of it means a
writer misbehaved.

That change then broke `destination_predict`, which unlike `eta_ml` routes
distances during its candidate build - `RouteCache.__init__` runs a CREATE, which
a read-only attach refuses outright. It now detects a read-only connection and
degrades to memory-only: same in-run memoization, it just cannot persist
first-time-routed cells. Cheap, because the daily derived build routes the same
cells through a writable connection anyway.

**The round count was the real tuning story, and it was a negative result.** Swept
63/127/255/511 leaves against `min_child_samples` 50/100 on an inner validation
slice carved from `train` (never `test`, which decides the gate). Every
early-stopped config landed between 9.928 and 9.995 median |err| - a 0.7% spread,
noise - while the shipped config sat 6% behind all of them at 10.640. Capacity was
never the bottleneck; `NUM_BOOST_ROUND = 400` was, and it was chosen when history
was ~3 weeks.

Raising it to a new number would just reset the trap for whoever reads this at 16
weeks - which is exactly how the P05/P95 band went wrong the day before. So it is
no longer a constant. Each head is fitted twice: a probe that early-stops on a
forward slice of `train`, then a refit on all of `train` at the round count the
probe found. The heads want budgets no single constant could serve - P50 converges
around 4500-5300 rounds, P90 around 250. Result: **7 promoted cells, up from 6**,
coverage 0.811. Cost 14m55s and 1.5 GB, up from 1m32s.

**The destination reranker had the identical staleness.** `dest_lgbm.txt` was last
trained 2026-07-04 on 40k labelled test groups; ground truth had since grown to
89k and nothing was re-deriving it - the derived build mines its labels and serves
its predictions, but never retrains it. Retrained 2026-09-10 it still beats the
heuristic and promotes:

| | 2026-07-04 (40,030 groups) | 2026-09-10 (88,657 groups) |
|---|--:|--:|
| heuristic top-1 | 0.633 | 0.647 |
| **ml top-1** | **0.678** | **0.691** |
| ml top-3 | 0.955 | 0.959 |

`freight-dest-retrain.{service,timer}` runs it weekly, **Saturday** 02:20 - a
different night from the ETA retrain's Sunday. Both take the same analytics flock
so sharing a night would only serialize them, but this job peaks at 4.35 GB
against the ETA retrain's 1.5 GB, and this box has a documented history of global
OOMs where the kernel picks the largest process and takes postgres or the AIS
collector with it. Separate nights keep the peaks from ever coinciding.
MemoryMax=6G; treat a cgroup OOM there as a signal to chunk
`build_training_candidates`, which materialises the whole 2.49M-row candidate
frame, not to raise the number.

**Two flaky tests, and they were genuinely wrong.** `test_feed_status` read `_NOW`
once at module import while the endpoint computes `age_minutes` against the wall
clock at request time, with an `abs=5` minute tolerance - so the drift was however
long the suite took to reach them. A loaded box pushed a run to 5m16s and two
failed, then passed alone. The clock is now read per fixture. That is the
project's own "never depend on wall-clock time" standard, broken.

**Also:** all four freight batch units now carry
`OnFailure=alert-email@%N.service`, which none of them had despite `~/CLAUDE.md`
saying every batch unit does. Worth knowing:
`freight-mst.{service,timer}` are SYMLINKS from `/etc/systemd/system` into this
repo while the others are copies, so editing the repo file changes production
immediately for mst and does nothing for the rest until copied.

## 2026-09-09 - the ETA gate was punishing the model for getting better

Question that started it: we have more data now, can we do more ML? We do have more
data - `eta_samples` has grown 2.5x since the ML challenger was last trained, from
1.23M rows / 51k voyages on 2026-07-01 to 3.10M / 129k. The challenger had never been
retrained against any of it, because Phase G's retrain timer was the one item of the
True ETA roadmap never built.

**Retraining on the extra data made things worse.** The gated retrain promoted 1 cell
where the frozen July artifact had 6. That is the opposite of what more data should do,
so it was worth understanding before shipping.

**Cause: a small-sample compensation that inverted.** The quantile heads were P05/P95
(nominal 90%) while `TARGET_COVERAGE` is 0.80. That was deliberate and documented: on
the original ~3-week history a P10/P90 head was under-dispersed out-of-time and realised
only ~0.71 coverage - below the [0.75, 0.85] promotion band - while the wider P05/P95
realised ~0.83 and fit inside it. Widening the heads bought coverage the model had not
earned.

On ~8 weeks the under-dispersion is gone, and the compensation became an over-correction.
Raw P05/P95 now realises **0.876** on the walk-forward test window, *above* the band. The
conformal offset is clamped non-negative (it may only widen, never shrink), so nothing
could narrow it back - every bucket's offset clamped to exactly 0.0. The per-cell
diagnostic is unambiguous:

| cell | physics \|err\| | ML \|err\| | ML coverage | verdict |
|---|--:|--:|--:|---|
| chokepoint\|0-6h | 1.37 | 3.66 | 0.882 | reject: error |
| chokepoint\|6-12h | 3.02 | 3.49 | 0.868 | reject: error |
| chokepoint\|12-24h | 9.26 | **8.31** | 0.852 | reject: coverage |
| chokepoint\|24-48h | 10.77 | **5.58** | 0.785 | promote |
| chokepoint\|48h+ | 15.38 | **6.11** | 0.877 | reject: coverage |
| port\|0-6h | 13.73 | **12.68** | 0.880 | reject: coverage |
| port\|6-12h | 12.56 | 12.96 | 0.876 | reject: error |
| port\|12-24h | 14.46 | **13.39** | 0.877 | reject: coverage |
| port\|24-48h | 16.54 | **12.91** | 0.885 | reject: coverage |
| port\|48h+ | 15.70 | **9.81** | 0.854 | reject: coverage |

Seven of ten cells had ML beating physics on median |err|. Six were rejected purely on
coverage, and **every one of them for over-covering - not one for under-covering.** The
gate was reading a better-calibrated model as a failure.

**Fix.** Heads are now P10/P90. That matches `TARGET_COVERAGE`, matches the 80% band
physics already serves (so the two models are finally compared at the same band width),
and matches the `eta_p10_h` / `eta_p90_h` names the API schema and the UI have always
used - the 90% heads made those field names untrue. Conformal is now the only thing that
moves the band, and it genuinely widens: offsets run 0.05h at 0-6h to 0.56h at 24-48h
instead of clamping to zero everywhere.

**Result.** 6 cells promoted, held-out interval coverage 0.876 -> 0.814. The wins are
exactly where physics is structurally optimistic: chokepoint 48h+ median |err| 15.38h ->
6.11h, port 48h+ 15.70h -> 9.83h, port 24-48h 16.54h -> 12.91h. The three short-lead
cells ML loses are now rejected on error, which is the honest reason to reject them.
Physics keeps the short-lead cells it genuinely owns.

**A stale-artifact bug fell out of it.** `eta_champion_map.json` (1 cell, mtime
2026-08-16) disagreed with `eta_ml_meta.json` (6 cells, mtime 2026-07-01). Nothing reads
the standalone file - `ETAModel.load` takes the map out of the meta - so serving was
unaffected, but the two are written by the same `save()` and had no business differing.
The retrain rewrote both consistently.

**Phase G closed.** `freight-eta-retrain.{service,timer}` runs the gated retrain weekly
(Sun 02:20 UTC), under the same analytics `flock`, scheduled ahead of the 03:20 derived
stages so a newly promoted champion is served the same night. It is no-promote-safe by
construction: it writes artifacts only for cells the challenger wins, and writes nothing
at all if it wins none. It carries `OnFailure=alert-email@%N.service` - a retrain that
fails silently leaves a stale champion in service and changes nothing externally
observable, which is precisely how the backup breakage hid for ten nights.
Measured cost: 1m32s wall, 1.25 GB peak RSS, so `MemoryMax=3G`.

**A defect found, measured, and deliberately not fixed.** The production model refits on
`train+calib` and then calibrates conformal on `calib` - which is inside that fit. That is
in-sample calibration, and split-conformal's guarantee needs a held-out set, so in
principle the served band is too narrow. Measured rather than argued: it realises **0.801**
test coverage against a target of 0.80. Carrying the eval model's genuinely held-out
offsets gives 0.822; a voyage-grouped cross-conformal (CV+, K=4, four extra fits) gives
0.806. Both are *further* from target than what ships. The impurity does not bite because
the offsets are 0.0-0.2h against a ~51h median band - the raw heads set the width and
conformal is a rounding correction on top. Left as-is with the measurement recorded in the
code, to revisit only if offsets ever become a material fraction of the band.

**Not addressed, and worth a session each.** (1) The destination-prediction LightGBM
reranker was last trained 2026-07-04 on 40k labelled transitions and has no retrain timer
either - same staleness, same fix. (2) `LGB_PARAMS` was tuned when history was 3 weeks
(shallow trees, `min_child_samples=100`, 400 rounds); the capacity ceiling has not been
re-measured on 2.5x the data, and that is the most likely remaining source of ML gain.
Deliberately not changed here so a capacity effect could not be confounded with the band
fix. (3) The history is 2026-06-09 to 2026-08-05 plus 2026-09-08 onward - the AIS outage
left a 33-day hole, so it is ~8 weeks of clean data spread over 13 weeks, not 13 weeks of
data.

## 2026-09-08 (later) - the map was defaced the whole time and nothing caught it

Unparking made the tracker reachable again. Opening it in a real browser showed the basemap
covered in a repeated "API KEY REQUIRED" watermark. **None of the checks that passed earlier
that evening could have caught it**: CARTO serves the watermark as HTTP 200 with a valid PNG,
so there is no 4xx, no failed request, and no console error. `curl` said 200, the API said
`feed=live`, and the map still looked broken to a human.

**Cause.** CARTO began gating its keyless *raster* endpoint in late August 2026 - after the
2026-08-16 parking, which is why it went unseen. Raster is being retired outright; *vector*
stays keyless. squiidwiki's maps were unaffected because they already use the vector GL
style, which is what pointed the way.

**Fix: vector tiles, restyled at runtime.** `lib/basemap.ts` + `components/tracker/VectorBasemap.tsx`
mount a MapLibre GL vector basemap as a Leaflet layer. Deliberately a layer swap, not a
migration - VesselLayer, markercluster, deck.gl, chokepoints, pipelines and risk all stay on
Leaflet untouched.

**The palette is the actual win.** CARTO dark-matter draws water *lighter* than land, which is
backwards for a vessel tracker: markers sit on water, so they were competing with the brightest
surface on the map. Repainted to MarineTraffic's scheme - water `#191F24`, land `#32414E` - and
the segment colours carry properly. This is only possible because vector styles are restylable
in the browser; raster could never have done it. MarineTraffic itself is Mapbox with a custom
style, which is why no off-the-shelf basemap matches it.

**Guards added, because the failure mode was silence:**
- Layer ids are **pinned, never substring-matched**. An upstream rename now fires a drift
  warning instead of quietly un-painting the map - the same silent shape as the watermark.
- `assertStyle` rejects a 200 that is not actually a style document, so a CDN error page
  cannot render as a broken map.
- OpenFreeMap is wired as an automatic fallback: a provider policy change degrades to a
  different basemap instead of a blank one.
- `basemap.test.ts`, 9 vitest cases including the drift path and a luminance assertion that
  land stays brighter than water.

**Two bugs found by checking rather than assuming:**
- Attribution rendered **twice**. Both styles declare it in their own TileJSON and maplibre
  propagates it into Leaflet's control, so the manual line was redundant - and would have
  credited CARTO while OpenFreeMap was serving the tiles. Removed.
- `@maplibre/maplibre-gl-leaflet` matches the `leaflet` manualChunks rule, which pulled all
  790 kB of maplibre-gl into the eagerly-loaded map chunk and defeated the dynamic import.
  Its own chunk rule now sits before the leaflet one: leaflet chunk 841 kB -> 49 kB.

**A CARTO key was obtained** (free tier, 5M tile req/month) and threaded through in
`VITE_CARTO_KEY`, though vector does not require it today. Verified harmless first: keyed and
unkeyed vector tiles both return 200. CARTO confirmed in writing that the key requirement is
coming to vector with notice - the point is that it will be a non-event here.

Verified live: no watermark, single attribution, 0 console errors, 0 raster tile requests,
1,036 vessels rendering.

## 2026-09-08 - unparked: the aisstream.io outage was upstream and transient

Freight had been parked since 2026-08-16 behind a 503 page because the AIS feed went
silent. Checked whether it was still down. It is not.

**The feed is healthy.** Three tests, escalating from protocol to full pipeline, all with
the production key:

| test | result |
|---|---|
| raw WebSocket, world bounding box | 9,856 messages / 60s |
| the collector's own 29-region subscription | 4,576 messages / 60s, no drop |
| `AISCollector` for 3 min against a throwaway DB | snapshots of 264 → 548 → 818 vessels, 6,850 tracked |

The third test ran the real collector class with `db_path` pointed at the scratchpad, so
the parked production DuckDB was not touched while the question was still open. Vessel
counts climbing is the exact inverse of the parking signature (`0 vessels held in memory,
feed appears silent`), and classification came back intact - Capesize, Suezmax, Aframax,
Supramax, VLCC. **No code change was needed.** The outage was entirely upstream.

**The outage started 2026-08-06, not 2026-08-16.** `/api/meta` reported
`last_seen: 2026-08-06T02:28:54` on restart. The feed had been dead for ten days before
the parking, and nothing alerted. The collector is not a batch unit, so it carries no
`OnFailure=alert-email@%N.service`, and it never exits non-zero when the feed goes quiet -
it reconnects, logs a warning and backs off to 1024s. UptimeRobot saw the site up the
whole time because nginx was serving a frontend fine; only the vessel count was zero.
There is still no alert for this shape of failure.

**What was flipped back on.** `freight-api`, `ais-collector`, `freight-analytics.timer`,
`freight-analytics-derived.timer`, all `enable --now`. The vhost was reverted to the
pre-parking config.

**The documented restore step was stale.** The header of `nginx-freight.conf` said
`git checkout nginx-freight.conf` to get the pre-parking file back, which assumed the
parking edit was uncommitted. It had since been committed as `0d60dde`, making that
checkout a no-op that would have silently left the 503 in place. The pre-parking version
had to be recovered with `git checkout 3386f4b -- nginx-freight.conf`. Note also that
`/etc/nginx/sites-enabled/freight.conf` is a symlink straight into the repo, so editing
the working tree edits the live config - there is no separate copy step.

**Memory, the coupled half.** Parking had raised `MemoryHigh` on `user-1000.slice` from
9G to 10752M because freight's ~1.7 GB left `system.slice`. Measured with freight running
again: `system.slice` 5.10 GB. By the sizing rule (`total - system.slice - 2G headroom`)
that gives 16 - 5.10 - 2 = 8.9 GB, so `MemoryHigh=9216M` with `MemoryMax=9728M`, keeping
the 0.5 GB band that session's note explains is load-bearing.

**`freight-analytics` at 1.5 GB is normal, not the stuck run.** The parking note recorded
a run "sitting in `activating` holding 1.49 GB" as evidence of a hang. The catch-up run
triggered by `Persistent=true` hovered at that same 1.49-1.51 GB while progressing through
stages normally, and finished clean: `Result=success`, exit 0, 7min 44s CPU, **1.7 GB peak**
against its 3 GB cap (`transits=0 anchored=1065 density=1344 reroutes=0 dark_voyages=25
spoof=285`). That figure is this job's ordinary working set, not a symptom. Note the live
`MemoryCurrent` sampling understated it - systemd's recorded peak is the number to trust.

**Known data gap.** No AIS history 2026-08-06 → 2026-09-08. The fleet-dispersion series
has a ~4.5 week hole and nothing can backfill it, since the source is a live feed with no
history endpoint.

**Backups need no edit.** `backup.sh` skips `ais_positions.duckdb` while an archive is
newer than the source, and says in its own comment that it resumes on its own when the
feed returns. Verified: source is now 2026-09-08 22:43 against a newest archive of
2026-08-23, so the condition is already false and tonight's run copies it again.

## 2026-08-09 (session 23) - the hourly analytics job was OOM-killed every run; the same ratchet as session 20, in a different place

`freight-analytics` failed with `oom-kill` on every run of 2026-08-09 (08:33, 09:32,
10:32, 11:19, 12:32), reaching ~5.2 GB against its 5 GB cgroup. Session 21 had pinned
DuckDB to 2 GB and that limit was being applied correctly; the overrun was in pandas,
which that fix explicitly did not cover.

**Locating it.** The journal puts the kill after `destination_labels` logs its counts and
before `eta_samples` (stage 7c) logs anything - it ran 14 minutes in silence, then died.
`_run_derived_stages` says in its own docstring that 7b-7e read the full AIS history and
are "the most memory-hungry part of the job", and the timer invokes `analytics.build`
with neither `--max-window-hours` nor `skip_derived`, so every hourly run does all of it.

**The bug, and it is the session-20 bug wearing a different hat.** Every arrival needs
only its trailing `_MAX_LEAD_H` (72h) of track. But `build_samples` took
`min(arrival_ts) - 72h` over the *entire* arrivals table as its scan lower bound. That
bound only ever moves backwards, so the scan grew by one day for every day the collector
ran while the data actually used stayed at 72 hours per arrival. With 117,067 arrivals
spanning 2026-06-09 to 2026-07-26, it was loading all 25.2M snapshot rows into pandas to
use 72-hour slices of them. Exactly the ratchet the watermark fix killed in session 20:
a window whose lower bound is a minimum over accumulated history.

Two costs compounded it. The **mmsi filter ran in pandas after the load**
(`tracks[tracks["mmsi"].isin(mmsis)].copy()`), so every vessel's rows were materialised
before ~19% of them were discarded - and the `.copy()` doubled the peak at that moment.
And `rows` accumulated **2.4M Python dicts** of ~19 fields each across the whole build,
which is several GB of interpreter objects before pandas ever sees them.

**Fixed** by chunking the track load over arrival time (`_TRACK_CHUNK_DAYS`, 7 by
default, env-overridable) and pushing both time bounds *and* the mmsi filter into SQL.
Peak memory now scales with the chunk width rather than with the length of collected
history, which is the property that was missing.

**Measured, including the part that did not work.** Two full production runs. Chunking
alone: completes, 39m01s wall, **peak 3.84 GB**, stage 7c builds and persists 3,095,683
approach samples where every prior run that day died in that stage without logging a
line. A second change - converting each chunk to columnar form instead of accumulating
3.1M Python dicts - was expected to cut the peak substantially and **did not**: 35m24s,
**peak 3.87 GB**, statistically identical. It is kept because holding millions of dicts
is worse practice regardless, but it is documented in code as measured-neutral so nobody
credits it with a saving it does not deliver. An earlier note in this session claiming a
~1.4 GB peak was wrong: that came from 60-second RSS sampling that missed the peak
between polls.

**Split hourly/daily, which is the structural half of the fix.** Under systemd's own
cgroup accounting - authoritative, and higher than `/usr/bin/time` RSS because it counts
the page cache for the ~516 MB scratch DB copy - two consecutive successful runs read
**4.0 GB and 4.1 GB against the 5 GB cap**, each consuming **42 minutes of CPU**, on an
*hourly* timer: 14:38-15:14, then 15:14-15:50. The job was running essentially back to
back and the box was never idle. The derived stages are the entire cost and they do not
depend on the incremental window, so `freight-analytics.service` now runs
`--skip-derived` hourly (**6m31s, 962 MB**, identical detector output: anchored 1065,
density 1344, dark 25, spoof 285) and a new `freight-analytics-derived.service` runs the
full pass daily at 03:20 UTC, clear of the registry (04:30) and MyShipTracking (05:00)
crawlers. Hourly cap dropped to 3 GB so the frequent job can never be the machine's
largest process; the daily one keeps 5 GB.

**The honest read on headroom.** 3.87 GB against a 5 GB cap leaves 1.13 GB, which is not
comfortable. Worse, in both runs `7d eta_serving` and `7f destination_serving` reported
0.0s because the AIS feed was dead and no live vessel could be scored; in normal
operation they add work on top of that peak. Where the remaining 3.87 GB sits has **not**
been measured - the obvious candidates (the dict list, the metrics concat) were both
checked and neither is it - so the next person should instrument rather than guess. The
structural answer is the cadence split filed in ROADMAP: the derived stages do not need
to run hourly.

**This changes when rows are read, never which.** The central test asserts
`pd.testing.assert_frame_equal` between the chunked build and the pre-fix whole-history
loader on a fixture whose arrivals span five weeks, plus a parametrised check that chunk
widths of 1, 3, 7 and 400 days all produce identical frames - the width is a memory knob
and must never be a correctness one. 11 tests in `tests/test_eta_sample_chunking.py`.

**Checked the neighbours for the same pattern**, since one instance of a ratchet suggests
others: `eta_serving._trailing_speed` is bounded by `_TRAIL_H`, and `eta_labels`
bbox-filters per target before reading. Neither shares it.

**Also cleared 37 accumulated ruff errors in `app/main.py`**, which had made the
pre-commit hook unusable on that file - every change touching it went in with
`--no-verify`, silently disabling the check for everything else in the same commit. Most
were mechanical, three were dead code (including a `fleet_mmsis` set built by `iterrows()`
over the whole live-positions frame and never read, so its removal takes a full-frame
Python-level scan out of that endpoint), and two were `B023` closure-binding warnings that
turn out to be correct only by accident of call ordering, now bound explicitly.

**Operational note, third occurrence.** `systemctl stop freight-analytics` also stops
`freight-analytics.timer`. Sessions 20 and 21 both left the timer inactive after
hand-running the job, and this session reproduced it. Restart the timer explicitly, and
check `systemctl list-timers` rather than the service status.

**Still open.** A full run takes 30-45 minutes on an hourly timer, so the job nearly
overlaps itself and is why the box sat at load 21 during this session. The derived ETA
stages almost certainly do not need to run hourly; `--max-window-hours` on the incremental
pass plus a daily derived pass is the obvious shape, and it is not built.

## 2026-08-09 (session 22) - the site said nothing while it was broken; both free AIS fallbacks are now ruled out on their own terms

The tracker had been serving an empty map since 2026-08-06 02:28 UTC and giving the
visitor no reason for it. `/api/health` returned `ok: true, tracked: 0,
last_update: null` throughout, which is the worst of both worlds: monitoring stayed
green while the product was blank.

**The reason `last_update` was null is worth writing down, because it defeats the
obvious fix.** Every vessel read filters to `VISIBLE_HOURS` (24h), so past a day of
outage they all return nothing. But the deeper problem is that the collector *prunes*
`live_positions` once rows age out of its staleness window - so during a long outage
that table empties completely and destroys the evidence of when the feed last worked.
On inspection it held 0 rows while `ais_snapshots` held 25.2M and knew the feed died at
02:28:54. `_feed_status()` therefore reads `live_positions` unfiltered and falls back to
`max(snapshot_ts)`, and only calls the state `unknown` when both are empty. Without the
fallback the banner would have said "no AIS positions have been collected yet" next to a
25-million-row store.

Four states (`live` / `stale` / `down` / `unknown`) ride along on `/api/meta`, which the
frontend already polls on the 60s tier, so the banner costs no extra request. `ok` on
`/api/health` deliberately stays `true` during an upstream outage: it is a statement
about this service, uptime monitoring watches it, and paging for an aisstream failure no
deploy of ours can fix would train the operator to ignore it. 14 tests.

Verified in production: the banner reads *"Live AIS feed is down: no new positions for
3 days. Last message Aug 6, 2026, 2:28 AM UTC. The upstream provider (aisstream.io) is
accepting connections but sending no data; the map is empty for that reason, not because
there are no ships. Historical analytics are unaffected."* Zero console errors. One bug
caught only because the check was done from a UTC browser: the timestamp was formatted
in the viewer's local zone and labelled UTC, which is wrong for everyone outside it and
silently so. Pinned to `timeZone: 'UTC'`.

**The outage itself is aisstream's, and that is now established rather than assumed.**
The collector's new close-code logging reports `close=1011 keepalive ping timeout`: the
server accepts the socket, then answers neither pings nor the subscription. A control
probe with a deliberately invalid API key gets *identical* silence, so the key is not the
problem. The recurring HTTP 429 was self-collision - one concurrent connection per key,
and our own reconnects racing a half-open server connection.

**Both free fallback candidates are now ruled out, checked against their own terms
rather than community summary.** AISHub is contributor-only (*"applications without an
operational AIS station and feed will not be approved"*), explicitly bans feeds "from
publicly available AIS sources or services" so re-feeding aisstream to qualify is out,
and gates API access behind >=10 vessels and >=90% uptime over 7 days; a receiver is not
viable from Geneva regardless. Data Docked advertises a free tier and full particulars,
and fails on shape rather than price: vessel type is not inline with area queries, and
at 10 credits per area call our 29 basins would cost ~42k credits/day at 10-minute
polling against a 20-credit free tier.

The requirement that eliminated both, and the first thing to test on any future
provider: **the feed must carry `ship_type` and dimensions**, because `classify()`
derives every segment from them and every segment-keyed surface breaks without it.
Recorded in `docs/reference/landscape.md` with the arithmetic.

## 2026-08-07 (session 21) - 80% of the analytics DB was empty space; the disk scare was never a capacity problem

Session 20 restarted the analytics job by hand but never restarted its timer, so
`freight-analytics.timer` was still `enabled`/`inactive` and the last run was 2026-07-26. The root
disk was at 93% (5.4 GB free of 75 GB) with a 10.6 GB `freight_analytics.new.duckdb` orphaned
beside the live DB. Two candidate explanations were on the table - the DB had genuinely grown, or
we needed a bigger box - and both were wrong.

**The measurement that settled it.** `PRAGMA database_size` on the 9.8 GiB live DB reported
`used_blocks 8,083` against `free_blocks 32,398` at a 256 KB block size: ~2.0 GB of data in a
9.8 GB file, **80% free space**. DuckDB reuses free blocks but never returns them to the OS, and
`_open_analytics_scratch()` `shutil.copy2`s the whole file every run, so each hourly build was
copying ~8 GB of holes and needed 10 GB free just to start. A full rewrite via
`COPY FROM DATABASE` took it to **0.45 GB - 4.2% of the original** - with all 26 tables and all
4,282,927 rows verified equal before the swap.

A caution for the next person measuring this: `duckdb_tables().estimated_size` reported
`eta_arrivals` at 57M rows when the real count is 117,067. It is an estimate and it is not close.
Count explicitly.

**DuckDB was allowed to allocate past its own cgroup cap.** `build.py` set no `memory_limit`, so
DuckDB defaulted to 80% of system RAM (~6.1 GB on this 7.6 GB box) against the unit's
`MemoryMax=5G`. It would keep allocating until the cgroup killed it rather than spilling to disk -
which reframes the July OOMs: the guard added in session 20 could not work as intended while the
engine's own ceiling sat above it. Now pinned to 2 GB (`FREIGHT_DUCKDB_MEMORY_LIMIT`), leaving the
rest of the 5 GB budget for the pandas frames DuckDB does not count.

**The WAL pairing bug, closed.** A DuckDB WAL is bound to a database *path*, not an inode.
`_open_analytics_scratch()` unlinked a stale `.new.duckdb` but not its `.wal`, so a dead run's
writes would replay into the next build's fresh copy; `_commit_scratch()`'s `os.replace` stranded
the scratch WAL under the old name and left any live-side WAL pointing at a replaced file. Both
now move in step with their DB. This was not theoretical - two killed runs (2026-08-05, and one
this session) each stranded exactly this pair. 7 unit tests in `tests/test_build_scratch.py`.

**Verified.** Bounded catch-up cleared the backlog with peak RSS 798 MB against the 4.66 GB that
died in July; watermark advanced to 2026-08-06 02:28:54, which is the end of available snapshots.
683 existing tests still pass.

**Hardware, for the record.** netcup VPS pricing was compared against a Hetzner cx43 rescale at
several points during this session. Net of VAT the two are near-identical on RAM (VPS 2000 €16.18
vs cx43 €15.99) and netcup wins heavily on disk; ARM64 is better still (VPS 3000 ARM, 24 GB /
768 GB, €15.93 net) but is currently sold out. No move was made, because after compaction there is
no capacity problem to solve: the binding constraint was free-space bloat and an unbounded window,
both fixed in code. Also corrected: `~/ops/README.md` lists cx43 as 160 GB and volumes at
~€0.04/GB; the console shows 80 GB on the disk-preserving rescale and €0.06864/GB incl. VAT.

**Found, not fixed (separate incidents).**
- The AIS collector has been delivering **zero vessels since 2026-08-06 02:29** while reporting
  `ais connected: 29 regions`. Same code that worked until then, subscription accepted, no
  messages - points at `AISSTREAM_API_KEY` or an aisstream.io outage. Untouched deliberately:
  aisstream permits one concurrent connection per key, so probing it would disconnect the live
  collector. This is why the analytics backlog "cleared" at 02:28:54.
- `vessel master upsert failed (ON CONFLICT DO UPDATE command cannot affect row a second time)`
  on every ~10 min collector cycle for as long as the journal goes back, so
  `vessel-master rows upserted` has been 0 throughout. Needs a dedupe on the conflict key before
  the upsert.

## 2026-08-05 (session 20) - The analytics job had been dead for 10 days; bounded catch-up passes and a memory guard

`freight-analytics.timer` was `enabled` but `inactive`, last run 2026-07-26 15:00. The exit state
read `Result=success` with `ExecMainStatus=9`, which is not an application failure: code 2 is
`CLD_KILLED`, and the kernel log has `Out of memory: Killed process 3675307 (python)
anon-rss:4658924kB task_memcg=/system.slice/freight-analytics.service`. Three OOM kills in total
(2026-07-20, and twice on 2026-07-26 at 4.99 GB and 4.66 GB) on a 7.6 GB box. Each was a *global*
OOM, so the victim was whichever process was largest: postgres, the AIS collector and every other
live service were in the blast radius. The 15:33 kill was triggered by an unrelated `claude`
process pushing the machine over.

**Why it could not recover on its own.** `_run_inner` loaded every snapshot since the watermark
into a single DataFrame with no upper bound. Once a run dies, the watermark stops advancing, so
the next run reads a strictly larger window and dies sooner - a ratchet. By the time it was
noticed the gap was 10.3 days, 6.2M snapshot rows against ~25k for a normal hourly increment.

- `_window_bounds(watermark, max_window_hours)` now resolves `[since, until)`, exposed as
  `--max-window-hours`. It rejects any width at or below the 6h overlap, since such a window can
  never advance the watermark and a walk-forward on it would livelock. 9 unit tests
  (`tests/test_build_window.py`), including the net-advance invariant.
- An empty *bounded* window with rows beyond it advances the watermark to `until` instead of
  returning early, so a stretch with no collector coverage cannot stall the walk.
- Stages 7b-7e (ETA labels, destination labels, ETA samples/serving, drift) moved into
  `_run_derived_stages()`, skippable via `--skip-derived`. They read the full AIS history, are
  independent of the incremental window, and only their final state matters - so intermediate
  catch-up passes should not pay for them. The 15:33 kill landed in 7c (`eta_samples`), ~11 min
  after `destination_labels` finished.
- `MemoryAccounting=yes` + `MemoryMax=5G` on `freight-analytics.service`. This does not stop an
  overrun, it *contains* it: a cgroup OOM kills only this job instead of letting the kernel pick
  a victim machine-wide. Swap left unlimited on purpose - thrashing to a slow finish beats dying.
- Cleared an orphaned `freight_analytics.new.duckdb.wal` (7.9 MB) left by the killed run.
  `_open_analytics_scratch()` unlinks a leftover `.new.duckdb` but not its WAL, so a fresh scratch
  copy would have replayed it. Related and still open: `os.replace` promotes the scratch `.duckdb`
  but leaves `freight_analytics.duckdb.wal` orphaned beside the live DB (one from 2026-07-04 is
  still there).

Backlog walked forward in 48h passes (42h net advance each), peak RSS 1.27 GB on the first pass
against the 4.66 GB that died - the window bound, not the guard, is what made it fit.

**Still open:** the derived ETA stages scale with total AIS history, not with the increment, so
their footprint grows daily regardless of the watermark. That is the next thing to fix, and it is
what the 5 GB guard is really protecting against.

## 2026-07-27 (session 19) - Tanker demolition and two fleet-age proxies; the bulker age signal came back breached

Filled the last capacity-side hole on `/cycle`. 11 signals -> 13.

**Crude tanker demolition, gap -> registered.** No free per-period demolition count exists, but a
cumulative tally does: 52 crude tankers scrapped across 2022 to mid-2026 (7 VLCC, 16 Suezmax, 23
Aframax), which annualises to 11.6/yr against a fleet with roughly 500 vessels already past 20.
The threshold is deliberately anchored to that same tally - a single year beating the whole
2022-2026 total means the wave has started - so value and threshold share a basis. It ships
`verified: false` on purpose: the annual rate is our own arithmetic on someone else's cumulative
figure and the publisher blocks automated reads, so the number reached us through search rather
than off the page. Cross-checks recorded on the tile: NGO Shipbreaking Platform counted 88 ships
dismantled in Q1 2026 and 71 in Q2 across all types worldwide, and BIMCO expects tanker scrapping
subdued through 2026, surging only from 2028.

**Fleet age at scrapping age, two live signals from our own registry.** `_fleet_age_over_20`
joins Equasis build years (PostgreSQL `vessels`) with MyShipTracking filling gaps, over vessels
seen in the last 24h, and reports the share at or past 20 years with the cutoff computed from the
current year rather than hardcoded. Tanker 20.8% (61 of 293 known build years, 24% of 1,202
tracked, mean age 14.6y). Bulk **31.9%** (137 of 430, 23% of 1,847, mean age 16.8y) - **breached**,
and the more interesting of the two: an ageing bulker fleet now meeting the rising orderbook that
last session's verification uncovered, which is the 2009 setup in miniature.

The tanker figure lands at 20.8% against BIMCO's independent 22%-of-crude-fleet-over-20, which is a
useful sanity check that the sample is not wildly skewed - but it is still roughly a quarter of the
fleet, selected in crawl order rather than at random, so the coverage count travels with the value
everywhere it is displayed and the caveat says to read the direction rather than the level.

New tests: every `live` signal in the shipped registry must name a resolver that actually exists in
DEFAULT_RESOLVERS (a typo would otherwise render an empty tile forever), and `per_year` formatting.
Backend 674 passing, frontend 50 passing.

Board now reads: 2 breached (tanker orderbook 27%, bulker fleet age 31.9%), 1 approaching
(dry-bulk orderbook 14%), 1 published gap (Hormuz transits).

## 2026-07-26 (session 18b) - Verified the four registered figures; two were wrong and both flipped a read

Every hand-recorded number on `/cycle` shipped flagged `verified: false`. Checking them against
primary and free trade-press sources took an hour and changed the board's conclusion on two of
three subsectors. Worth recording precisely, because it is the argument for the tiering:

| Signal | Was (secondary) | Now (verified) | Effect |
|---|---|---|---|
| Dry-bulk orderbook | 7.0%, "multi-year low", 2026-02-16 | **14.0%** capacity basis (Clarksons H1-2026); 11.0% by count, up from 9.5% YoY, orderbook +20% against 3.8% fleet growth (Breakwave, 2026-07-07) | holding -> **approaching** |
| Tanker orderbook | 14.7%, 2025-11-10 | **27.0%** - crude orderbook 130m dwt, 151 VLCC contracts by mid-2026, largest tally since 1973 (BIMCO via IndexBox, 2026-07-09) | holding -> **breached** |
| Container orderbook | 38.7% | **38.3%** - 1,592 ships / 12.98m TEU (Alphaliner via PortNews, 2026-06-24) | confirmed, corrected off the top of a range |
| Container rates | SCFI 3,184.83 / CCFI 1,873.15, 2026-07-10 | **CCFI 1,901.27**, SCFI 3,062.95, both 2026-07-24, read off the SSE index pages | superseded by a current fixing |

The dry-bulk error was the serious one: ~7% was not merely stale, it was the wrong picture. The
thin-orderbook supply cushion that made "early upcycle" the read is closing, and the falsifier
written into that signal - a dry-bulk ordering wave - is the thing that has been happening.
Tankers are worse still: every current source puts the orderbook above the 20% overshoot
threshold, so the signal is breached and the subsector card now reads "late expansion, ordering
has overshot" rather than "renewed expansion".

Also fixed a units mismatch found while verifying: `container_rates` stored the SCFI but carried a
CCFI threshold, so the distance-to-threshold was comparing two different indices. It now stores the
CCFI with the SCFI alongside as a note.

Review intervals on both orderbook signals cut from 90 to 60 days - they demonstrably move faster
than a quarter. New test: `verified: true` requires a `verified_note` and a source URL, so the flag
cannot be set without recording what was read and when. Backend 673 passing.

## 2026-07-26 (session 18) - Freight Cycle board: three clocks, thresholds, falsifiers, and a published gap register

**The premise: shipping is not one cycle.** Container, dry bulk and tanker run on different clocks
and the variable that separates them is the orderbook, not the spot rate. The framing came from a
Kimi Deep Research scrollytelling essay (2026-07-10 data), archived verbatim at
`docs/reference/kimi-shipping-cycles-2026-07.md`. Its numbers were *not* adopted as data - only its
structure was: the four-field signal contract (value / threshold / expected lag / falsifier), and
the discipline of publishing a gap register instead of interpolating over one.

**Data check came first, and it reshaped the design.** We had BWET (an ETF proxy, weekly, 169 rows)
and a static 5TC FFA seed. No BDI, no BDTI, no SCFI, no orderbook. And the single most quotable
figure in that genre - "Suez transits down x% vs 2023" - is uncomputable here: `transit_events`
starts 2026-06-09 and AIS history cannot be backfilled by definition. So the board is tiered by
provenance rather than pretending to uniform coverage:

- `live` - computed from a series we ingest (5 signals)
- `registered` - a disclosed observation typed in by hand, with source and as-of, that goes visibly
  stale on a stated cadence (4 signals)
- `missing` - no acceptable source; the tile renders anyway, carrying the reason (2 signals)

Registered numbers additionally carry `verified: false` plus a provenance line while they remain
unchecked against the primary source, and the UI says so on the tile. Nothing is interpolated,
nothing is carried forward silently.

**C1 - Baltic indices ingestion (market-data).** New `fetchers/baltic_indices.py` pulling BDI, BCI,
BPI, BDTI and BCTI from akshare into a new series-keyed `baltic_indices` table (35,031 rows; BDI
back to 1988-10-19, the tanker pair to 2001-12). akshare re-serves the daily fixings that Chinese
portals publish free - the only free machine-readable source with real history, since yfinance's
`^BDIY` is a 404. Loaders `load_baltic_index` / `load_baltic_indices` added, vintaging enabled, 8
unit tests on the normaliser (Chinese column headers, repeated fixing dates, null tails, upstream
schema change must raise rather than silently empty the table).

Diagnosed the stale `freight_5tc_ffa` (ends 2025-12-16) while there: it is not a broken fetcher,
it is a static case-study seed from freight-dispersion. Left as-is, not surfaced as a tile.

**C2 - signal registry.** `backend/app/cycle_signals.yaml` holds 11 signals and the three subsector
cards; `backend/app/cycle.py` loads, validates and resolves it. Validation is strict on purpose - a
signal with no falsifier, no expected lag or no threshold label is a startup error, not a blank
tile. Threshold state (`breached` / `approaching` / `holding` / `unknown`) and staleness are pure
functions with boundary tests: equality does not count as a crossing, an observation with no as-of
date is stale by definition, and a qualitative signal never asserts a read with no observation
behind it. 40 unit tests.

**C3 - API.** `GET /api/cycle/signals`, `/api/cycle/subsectors`, `/api/cycle/series`, 5-minute
in-process cache, 10 endpoint tests against a fixture registry with stubbed resolvers - including
that a dead PostgreSQL yields an empty series rather than a 500, and that gaps are returned rather
than filtered out.

**C4 - `/cycle` page.** Three subsector cards, a Baltic series chart with the threshold drawn on
it, a signal grid sorted by proximity to changing the read (breached first, gaps last), and a
closing "what this board cannot tell you" block that enumerates every gap and every unverified
observation. Provenance badges are visually distinct so a hand-recorded number can never be
mistaken for a live one. 23 vitest cases on the pure presentation logic.

Backend 672 passing, frontend 50 passing. Current read as of 2026-07-24 fixings: BDI 2,743
(+21% YoY), BDTI 2,532 (+186% YoY), BCTI 1,352, Capesize/Panamax 2.12 - all holding well clear of
their thresholds; Suez ~109 transits/day of tankers and bulkers on our own count; Hormuz a
permanent gap (no terrestrial receiver coverage in the Gulf).

## 2026-07-04 (session 17) - Destination predictor: drop redundant gc_dist_nm from ML features

**Two distance columns fighting for the same split budget - one of them strictly worse.**
Feature-importance on the just-shipped `route_dist_nm` model showed LightGBM gain concentrated on
`gc_dist_nm` (608k) well ahead of the sea-route-corrected `route_dist_nm` (162k), despite the
latter being the more accurate signal by construction. The two are highly collinear (same
distance, differing mainly on canal/cape-routed candidates), so `gc_dist_nm` was capturing split
budget that should have gone to the better feature. Same discipline as every feature change this
week: hypothesis first, then an ablation dry-run to confirm it before touching production - dropping
`gc_dist_nm` from the training/eval feature set (dry-run, not persisted) moved ml top1 0.678 ->
0.681 and top3 0.954 -> 0.957, confirming the redundancy cost real accuracy, not just wasted
capacity.

Removed `"gc_dist_nm"` from `destination_predict.py`'s `NUMERIC_FEATURES` only - it's untouched
everywhere else: candidate selection (`np.argsort(gc)` in `build_training_candidates`),
`canal_backtrack`, and `heuristic_raw_score`'s own `gc_dist_nm` fallback when `route_dist_nm` is
absent all still use it. The heuristic scorer is unaffected by this change entirely (it doesn't
read `NUMERIC_FEATURES`).

**Retrained + repromoted:** ml top1 0.6778 -> 0.6778 (flat - the dry-run's 40k-voyage snapshot
already sat close to this before the extra data the real run picked up), top3 0.9536 -> 0.9554
(n=39,975 -> 40,030) - a smaller real-run gain than the dry-run ablation preview, muddied by ~55
more voyages completing between the two runs, but still a genuine top3 improvement with no top1
cost. Heuristic essentially unchanged (0.6299/0.9085 -> 0.6329/0.9085 - the top1 drift is from the
extra voyages, not this change). Still clear of heuristic; champion/challenger gate re-verified;
promoted.

No new tests: this is a pure feature-set ablation, not a new signal - existing coverage in
`test_destination_predict.py` already exercises `heuristic_raw_score`'s `gc_dist_nm` fallback and
`candidate_frame`'s `gc_dist_nm` computation independently of what's in `NUMERIC_FEATURES`. Full
suite 615 passing (unchanged).

**Series recap (sessions 13-17, all this week):** `laden` -> `draught` -> `sog_trail6h` ->
`route_dist_nm` -> drop `gc_dist_nm`. ml moved 0.680/0.959 -> 0.678/0.955 net (a small give-back
from `route_dist_nm`'s redundancy with `gc_dist_nm` before this session's fix clawed most of it
back), while the heuristic champion picked up a real, durable gain from sea-route correction
(0.622/0.907 -> 0.633/0.909) that will keep paying off on every hourly build regardless of which
scorer is promoted that day.

## 2026-07-04 (session 16) - Destination predictor: sea-route distance for both scorers

**Great-circle distance cuts through land - the destination predictor never corrected for it,
even though True ETA already solved this problem.** `gc_dist_nm` is a straight line; a real vessel
routed via Suez, Panama, the Cape of Good Hope, or Cape Horn travels a materially longer path, and
`eta_routing.RouteCache` already computes that (a `searoute`-backed, grid-cell-memoized lookup,
persisted to `eta_route_cache`). Notably, `heuristic_raw_score` was *already written* to prefer
`route_dist_nm` over `gc_dist_nm` when present (`row.get("route_dist_nm")` with a `gc_dist_nm`
fallback) - it simply never had a live value to prefer, since nothing upstream ever populated it.

Wired in: `destination_features.candidate_frame` gained a `route_cache` param (mirrors
`trail_by_mmsi`/`laden_by_mmsi`); `destination_serving.py` now creates and flushes a `RouteCache`
per hourly build, reusing the exact `eta_route_cache` table True ETA's own `build_predictions` just
warmed moments earlier in the same build cycle, so most lookups are cache hits, not fresh
`searoute` calls (the training-set rebuild's first pass logged 1.1M hits vs 2,873 misses);
`destination_predict.py`'s `build_training_candidates` computes it per training candidate via its
own `RouteCache(conn)`, and added `route_dist_nm` to `NUMERIC_FEATURES`. This is the one feature
this series (`laden`, `draught`, `sog_trail6h`) that reaches *both* scorers, not just the ML side.

**Retrained + repromoted, mixed but instructive result:** heuristic top1 0.622 -> **0.630**, top3
0.907 -> **0.909** (n=39,975) - a genuine, real gain, exactly where expected: the heuristic's
`inv_dist` term was silently using straight-line distance for every canal/cape route until now. ML
moved the other way: top1 0.6803 -> 0.6778, top3 0.9608 -> 0.9536 - a small regression, not an
improvement, though it still clears the promotion gate on both metrics (0.678 > 0.630 top1, 0.954
>= 0.909 top3). Read honestly: `route_dist_nm` is highly correlated with `gc_dist_nm` (same
underlying geometry, differing mainly on canal/cape routes), and LightGBM's own split search likely
found `gc_dist_nm` alone at least as informative pre-correction, so the added feature cost the
model some capacity without buying it anything back - a plausible feature-redundancy story, not
clear evidence to revert (the ML model still comfortably beats the heuristic). Kept in because the
heuristic's improvement is unambiguous and the ML regression is small and still passes the gate.

- Tests: `test_destination_features.py` (+2: `route_dist_nm` passthrough via a real `RouteCache`,
  default-None without one), `test_destination_predict.py` (+2: `_prepare` default,
  training-candidate `route_dist_nm >= gc_dist_nm` invariant), `test_destination_serving.py`
  (+1: live `eta_route_cache` wiring + flush). Full suite 615 passing.

## 2026-07-04 (session 15) - Destination predictor: trailing-speed (deceleration) feature

**A vessel slowing down while pointed at a candidate is committing to arrival there - a stronger
signal than static bearing/distance, and True ETA already computes it.** `eta_serving.
_trailing_speed` (live) and `eta_samples.sog_trail6h` (training) are a rolling 6-hour median SOG
(`_TRAIL_H = 6.0`), a denoised deceleration-on-approach signal True ETA's own ML has used for
some time. Neither had reached the destination predictor - unlike `draught` (already sitting
unused in `candidate_frame`), this one needed real serving-side plumbing since
`destination_serving.py` never touched the trailing-speed scan at all.

Wired in the same shape as `laden`/`draught` before it: `destination_features.candidate_frame`
gained a `trail_by_mmsi` param (mirrors `laden_by_mmsi`), `destination_serving.py` now calls
`eta_serving._trailing_speed` directly - reusing True ETA's already-computed scan rather than a
second independent pass over `ais_snapshots` - and `destination_predict.py` added `sog_trail6h`
to `NUMERIC_FEATURES` plus the training SQL `SELECT` and per-row candidate construction. ML-only
(same rationale as `laden`/`draught`).

**Retrained + repromoted:** ml top1 0.6803 / top3 0.9608 (n=39,909), essentially flat against
0.6803/0.9609 without `sog_trail6h` earlier today (n=39,850) - the signal didn't move accuracy at
this training-set size, but it's cheap, already-computed, and physically well-motivated, so it
stays in rather than being reverted; a fresh angle (kinematics rather than geometry/history) may
pay off more as the training set grows. Still clear of heuristic (0.624/0.907). Champion/
challenger gate re-verified the challenger still wins on both top1 and top3; promoted.

- Tests: `test_destination_features.py` (+2: `sog_trail6h` passthrough/default-None cases),
  `test_destination_predict.py` (+2: `_prepare` default, training-candidate carry-through),
  `test_destination_serving.py` (+1: live `ais_snapshots` wiring case). Full suite 610 passing.

## 2026-07-04 (session 14) - Destination predictor: draught feature

**A VLCC can't call at a shallow terminal, laden or not - a vessel-size/depth signal the ML
challenger never saw, even though it was sitting right there.** `destination_features.
candidate_frame` already carried raw `draught` on every candidate row (needed for nothing until
now), and True ETA Phase C already mines it straight into `eta_samples.draught` per observation.
Neither had reached `destination_predict`'s feature set: `NUMERIC_FEATURES` stopped at
`canal_backtrack`, and `build_training_candidates`'s SQL never selected the column at all. Unlike
`laden` (a coarse draught-ratio-derived boolean), raw draught is a continuous size/depth proxy -
two laden VLCCs don't draw the same water, and a candidate port's practical reachability depends
on the absolute number, not just laden/ballast state.

Wired in by adding `"draught"` to `NUMERIC_FEATURES`, selecting `eta_samples.draught` in
`build_training_candidates`'s SQL, and attaching it per training row alongside the existing
`laden` extraction. Serving-side needed no change - `candidate_frame` was already populating it.
ML-only (same rationale as `laden`: the heuristic has no hand-built notion of draught-vs-port
compatibility to weight against it).

**Retrained + repromoted:** ml top1 0.6803 / top3 0.9609 (n=39,850), up from 0.6796/0.9610 without
`draught` earlier today - a small but real top1 gain, top3 flat within noise. Still clear of
heuristic (0.622/0.908). Champion/challenger gate re-verified the challenger still wins on both
top1 and top3; promoted.

- Tests: `test_destination_predict.py` (+2: `_prepare` defaults missing `draught` to `NaN` not a
  fabricated value; `build_training_candidates` carries `draught` through from `eta_samples`).
  Full suite 605 passing.

## 2026-07-04 (session 13) - Destination predictor: laden/ballast feature

**A laden crude tanker heads to a discharge port, a ballast one to a load port - a strong
signal the destination predictor's ML challenger never saw.** True ETA already computes a
leakage-free laden classification per observation (`eta_labels._laden_bool`, draught-ratio
against the vessel's own historical max, `True`/`False`/`None`), persisted straight into
`eta_samples.laden`; the live equivalent already exists too (`vessel_state.laden`, read via
`eta_serving._laden_map`). Neither had ever been wired into the destination predictor, even
though `destination_features.candidate_frame` already carried `draught` per candidate unused.

Wired both sources through, reusing the exact `True`/`False`/`None` encoding at both ends so
train and serve line up: `candidate_frame` gained a `laden_by_mmsi` param (sourced from
`_laden_map` at serving time), and `destination_predict.build_training_candidates` now selects
`eta_samples.laden` directly. `laden` joins `target_type`/`segment` in `CATEGORICAL_FEATURES` -
ML-only, since the heuristic scorer has no hand-built model of which ports are load-only vs
discharge-only to weight it against.

**Retrained + repromoted:** ml top1 0.680 / top3 0.961 (n=39,850), up from 0.680/0.959 without
`laden` two days ago at n=39,798, and still clear of heuristic (0.622/0.908). Champion/challenger
gate re-verified the challenger still wins on both top1 and top3; promoted.

- Tests: `test_destination_features.py` (+2 `laden` passthrough cases), `test_destination_serving.py`
  (+1 `vessel_state` wiring case). Full suite 603 passing.

## 2026-07-04 (session 12) - Destination predictor: route-leg resolver bug fix + reported-origin cold-start prior

**Found and fixed a real correctness bug in the destination predictor's reported-destination
signal.** `destination_resolver.resolve()` is supposed to resolve the *destination* leg of a
route-style AIS string ("NLRTM>USORF"), but `_try_locode`'s "first 5-char token" heuristic ran
on the unsplit string and grabbed the *origin* leg's LOCODE instead - `resolve("NLRTM>USORF")`
was returning Rotterdam, not Norfolk. This directly corrupted `reported_match`/`resolver_score`
(the heuristic scorer's "the crew agrees with this candidate" signal) for every two-LOCODE route
string in the live fleet. Fixed by splitting origin/destination legs first (same arrow/VIA/weak-
separator cascade as `app/main.py`'s `_canonical_destination`, ported into the analytics layer
to keep it dependency-free of `app`), then resolving only the relevant leg. Also fixed: "N/A"
normalizing to "N A" and fuzzy-matching an unrelated port (single-character tokens are now
dropped before the fuzzy pass), and the gazetteer's `USORF` row, which carried Tasmanian
coordinates (-42.78, 147.07) instead of Norfolk, VA's (36.85, -76.29) - a coord-fill error
silently misrouting every Norfolk-bound vessel's reported-destination ETA and candidate.

**New signal: the reported origin as a cold-start transition prior.** A vessel with no mined
arrival history yet (`eta_arrivals` has never seen it) fell back to the marginal `__any__`
transition prior, discarding all route information - even though its live AIS string often
already says where it came from ("NLRTM>USORF"). New `destination_resolver.resolve_origin()` +
`destination_features.resolve_origin_target_id()` resolve that origin leg to a curated
`eta_targets` row (within 20nm, the same threshold `reported_match` uses), and
`destination_serving._origin_target_by_mmsi()` uses it as a substitute `prev_target_id` for the
transition prior - but only for vessels lacking real arrival history; a vessel with mined history
always uses that fact instead of a hand-typed string. Serving-only (mirrors how the heuristic
already gets `reported_match`/`resolver_score` for free while ML's training set excludes them -
no training-time leakage risk since `build_training_candidates` never touches this).

**Retrained + repromoted:** ml top1 0.680 / top3 0.959 vs heuristic 0.623 / 0.907 (n=39,798 held-out
observation-groups, up from 38,642 last training run 2026-07-03). Champion/challenger gate
re-verified the challenger still wins on both top1 and top3; promoted.

**Also:** `/api/vessels` now splits route-style destinations into `origin`/`destination` fields
(`app/main.py` `_split_route`/`_canonical_origin`) instead of only folding onto the destination
leg and discarding the origin - the frontend's `VesselDetail.tsx` "Origin" row already existed
but had been silently rendering nothing since the destination-predictor commit, since the
backend never actually populated `Vessel.origin` until now.

- Tests: `test_destination_resolver.py` (+7 route-leg/regression cases), `test_destination_features.py`
  (+4 `resolve_origin_target_id` cases), `test_destination_serving.py` (+3 `_origin_target_by_mmsi`
  cases), `test_canonical_port.py` (+route-splitting/origin cases for the app-layer display path).
  Full suite 600 passing.

## 2026-07-02 (session 11) - Destination-change hysteresis (fixes ETA discontinuity from AIS destination churn)

**Quantified how often destination changes actually break the resolved-destination
ETA, then fixed it.** Pulled 24 days of `ais_snapshots` destination history
(2026-06-09 to 2026-07-02): 82% of vessels observed >=5 days changed their raw
destination string at least once, but most of that is cosmetic noise (median
"spell" length ~11h; terminal suffixes, abbreviation swaps, near-port chatter).
Re-resolving both sides through the app's real `destination_resolver` to filter
noise from genuine reroutes: **46.6% of tracked vessels (5,042/10,831) had a
destination-string change that resolved to a genuinely different real port while
still >50nm from the previously-declared one** - a true mid-voyage redirect, not
just "arrived, showing next voyage." Median great-circle jump between old and new
port: 787nm, a **~52-72h discontinuity** in the served ETA at typical laden speed.
The scored physics/ML ETA (geometric chokepoint/port targets, `eta_labels.py`)
was never exposed to this - only the `target_type='destination'` row shipped
2026-07-01, which re-resolved the live string fresh every hourly build with no
memory of what it served last time.

Fix: `eta_serving._committed_target` + a new persisted `eta_destination_state`
table (survives across builds, unlike `eta_predictions` which is fully rewritten
each run). A vessel's destination target only switches once the *same* newly
resolved port wins `_DEST_CONFIRM_STREAK` (3) consecutive hourly builds; a brand
new commitment (first sighting) is still adopted immediately since there's nothing
to be inconsistent with yet. An unresolvable/missing destination string no longer
drops the row - it just keeps serving the last committed target. State for a
vessel not seen with a resolvable destination in 30 days is garbage-collected so a
later sighting adopts fresh.

- New test `test_destination_change_is_hysteresis_gated`: commits to Port Said,
  confirms two Rotterdam readings don't switch it, the third does, and a single
  stray reading back doesn't immediately flip it again. Full suite 531 passing.

## 2026-07-01 (session 10c) - ETA to the resolved AIS destination (UN/LOCODE resolver, wired into serving)

**Now we show a computed ETA to where the ship *says* it's going - not by trusting
the raw string, but by resolving it.** New `analytics/destination_resolver.py` turns
the hand-typed AIS `destination` into a real seaport with coordinates via a cascade:
UN/LOCODE exact ("NLRTM", "NL RTM") -> exact normalized name -> rapidfuzz WRatio
(aliases/typos/terminal suffixes), same-name ports disambiguated by vessel proximity,
junk ("FOR ORDERS") left unresolved. Backed by a committed 14,582-port gazetteer
built from the free UN/LOCODE list (coordless major ports coord-filled from same-name
ports); no runtime network dependency. Resolves **79% of live vessels' destinations**.
The motivating case: **"MACAS" -> Casablanca** (MA+CAS is the LOCODE) - never garbage,
just an un-decoded code.

Wired into serving (`eta_serving._destination_rows`): each live vessel's resolved
destination gets an ETA row (`target_type='destination'`, `target_id='dest:<locode>'`),
routed to the port coords and scored by physics (the champion map has no 'destination'
cell, so ML is not applied to arbitrary destinations - honest, since it was never
validated there). Not bearing-gated: we trust the reported destination's direction.
433 live vessels now carry a destination ETA. The vessel card shows it first and
prominently ("Destination -> Casablanca, ETA ...") above the geometric waypoint ETAs -
so the card finally answers "when does it reach where it's going", like the paid AIS
products, but with our own validated model and an honest interval.

- Adds `rapidfuzz`. Robust to NaN/non-string destination values from the DB.
- Tests: `test_destination_resolver.py` (10) + destination-row coverage in
  `test_eta_serving.py`. Full suite 530 passing.
- Frontend card change (`VesselDetail.tsx`) built and live; the resolved-destination
  ETA renders above the waypoint ETAs.

## 2026-07-01 (session 10b) - Measured canal staging (replaces the hardcoded queue constants)

**The "canal queue" was two magic numbers; now it's measured from AIS.** The physics
queue term used a hardcoded `CANAL_STAGING_HOURS` (Suez 6h, Panama 10h) applied inside
a 60nm band. New `backend/analytics/eta_canal_queue.py` measures it from the transit
tracks we already mine: for each completed canal transit, the observed staging =
time spent loitering (SOG < 3kn) within the staging band before the gate crossing;
the per-canal estimate is the **median** over recent transits (robust to the long
anchorage tail), kept only when a canal has >= 20 transits.

- **Measured vs nominal (live):** Suez **6h -> 10.0h** (the constant materially
  underestimated it), Panama **10h -> 9.0h** (constant was about right). 91-96% of
  transits show real waiting; n=122 (Suez) / 109 (Panama).
- **Wiring without threading.** Rather than plumb a staging dict through ~10 physics
  call sites, `quant_lib.freight.eta` gains a process-level override:
  `set_measured_staging(map)` installs the measured values and `canal_staging_hours()`
  resolves measured -> nominal constant -> default. The hourly build measures from the
  freshly rebuilt `eta_samples`, installs it, refreshes the `dest_queue_h` feature, and
  scores physics with it; serving loads it from the new `eta_canal_queue` table. Empty
  map (fresh import / tests) falls back to the constants, so nothing else changes.
- **Leakage-safe** (mined from completed transits, never the fix being predicted) and
  **not built on the anchorage-dwell detector** (which has a flat ~6.8h artifact) - the
  loiter time is timed directly off each transit's own track.
- Tests: `tests/test_eta_canal_queue.py` (measurement, transit-count gate, non-canal
  exclusion, persist/load round-trip, override precedence in `queue_wait`). Full suite
  519 passing.

## 2026-07-01 (session 10) - True ETA Phase D: LightGBM quantile ML challenger (blended champion)

**Physics was structurally optimistic at long lead; ML fixes it, but only where it
earns promotion.** The shipped physics model (`physics_v1`) is excellent at short
range (0-6h median |err| ~1h) but the great-circle/effective-speed formula divides
a small route distance by current speed for a vessel loitering near a target and
reports near-arrival, so 24-48h+ forecasts carry a large negative (too-early) bias
no position+speed model can remove. That residual is *learnable*, so Phase D adds a
LightGBM quantile challenger and blends it with physics per lead bucket.

**What was built** (`backend/analytics/eta_ml.py`, +`lightgbm` dep):
- Three quantile boosters (alpha 0.05/0.50/0.95) on `eta_samples`. Features are all
  serve-time-known: route/gc distance, sog, trailing-6h sog, service-speed prior,
  draught, dest_queue_h, approach_bearing, and categoricals segment/target_id/
  target_type/is_canal/laden. Importance is led by `target_id`, `approach_bearing`,
  `route_dist_nm` - no `destination`-string leakage.
- **Leakage-free time-based, voyage-grouped split** (`time_voyage_split`): voyages
  ordered by arrival, split 60/15/25 into train/calib/test so the test window is
  strictly *later* than train (a real walk-forward, not a shuffle) and no voyage
  straddles a boundary.
- **Split-conformal (CQR) intervals, per predicted-lead bucket, clamped >= 0**
  (only ever widen). LightGBM's raw quantile heads are under-dispersed out-of-time
  on ~3 weeks of history (a P10/P90 head realises only ~0.71 coverage on test), and
  the calibration slice systematically *over*-covers relative to the strictly-later
  test window - so trusting a negative conformal offset would shrink the band and
  make it overconfident. The wider P05/P95 heads + non-negative CQR land realised
  walk-forward coverage at **0.83 overall**, inside the honest [0.75,0.85] band.
- **Champion/challenger, per (target_type, physics-predicted-lead) cell**
  (`build_champion_map`): ML is promoted to `method='ml'` only where it beats
  physics on held-out median |err| AND its realised P05-P95 coverage stays in
  [0.75,0.85]. Everything else stays physics.

**Walk-forward result** (leakage-free test half, by actual lead, target_type=all):

| lead | physics \|err\| | ML \|err\| | physics bias | ML bias |
|---|--:|--:|--:|--:|
| 0-6h | 1.1h | 7.1h | +0.7 | +7.1 |
| 6-12h | 2.4h | 6.1h | -0.1 | +5.9 |
| 12-24h | 9.5h | **6.7h** | -8.5 | +3.0 |
| 24-48h | 27.7h | **13.2h** | -27.6 | -12.2 |
| 48h+ | 51.2h | **34.2h** | -51.1 | -34.2 |

Physics owns short lead (kinematics win); ML roughly halves long-lead error and
collapses the bias. Overall median bias -10.1h -> -0.9h. The **6 promoted cells**
(by physics-predicted bucket): `chokepoint|12-24h`, `chokepoint|24-48h`,
`port|0-6h`, `port|6-12h`, `port|12-24h`, `port|48h+`. The gate correctly
*withheld* `chokepoint|48h+` (ML wins on |err| but coverage 0.72 < 0.75) and
`port|24-48h` (coverage 0.86 > 0.85) - rigor working, not silently promoting
overconfident cells. Ports promote broadly because anchorage/queue behaviour
(which physics cannot model) inflates physics error to 13-18h across all buckets.

**Serving + scoreboard.** `eta_serving.build_predictions` now loads the artifact
(`ETAModel.load`, None -> physics-only serving, graceful), batch-predicts ML, and
blends per the champion map keyed by the physics-predicted-lead bucket (physics is
always computed, so the routing decision is serve-time deterministic). On the live
snapshot this routed 1245/1676 predictions to `ml`. `score_and_write_ml` re-scores
the frozen champion on its own leakage-free time-split each hourly build, sharing
the run's `run_ts`, so the public accuracy scoreboard surfaces `ml` beside
`naive`/`naive+route`/`physics_v1` like-for-like (physics is deterministic and
split-invariant, so its random-split score stays a fair comparator).

**Artifacts + retrain.** Models + champion map live under
`backend/analytics/models/` (gitignored build artifacts; regenerated by
`python -m analytics.eta_ml`). The hourly build only *reads* them - it never
retrains - so it never mutates the champion mid-cycle. The weekly gated auto-retrain
(Phase G) is the remaining follow-up. Tests: `tests/test_eta_ml.py` (10 cases -
split ordering/disjointness, deterministic fit, monotone quantiles, non-negative
CQR, champion-map gating, artifact round-trip, serving-blend routing + physics
fallback). Full suite 512 passing.
