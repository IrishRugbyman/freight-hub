# Freight Hub Changelog

## 2026-06-27 - Fix: duplicate ports in the live destination-distribution lists

Reported from the live Ports & Cargo tab: Rotterdam appeared as five separate
rows (`NLRTM` 115, `ROTTERDAM` 91, `NL RTM` 24, `ROTTERDAM 3E PETROHA` 12,
`ROTTERDAM BOTLEK BO` 9), Antwerp as seven, Amsterdam as five. Cause:
`/api/analytics/ports` and `/api/analytics/destination-flows` grouped by raw
`UPPER(TRIM(destination))`, so every AIS free-text spelling of one port became
its own entry. Three duplication classes: spaced-vs-unspaced UN/LOCODE
(`NL RTM` = `NLRTM`), LOCODE-vs-name (`NLRTM` = `ROTTERDAM`), and name+berth
(`ROTTERDAM 3E PETROHA`).

**New `_canonical_port()` in `app/main.py`** folds a raw destination onto a
canonical city: collapses the `XX YYY` LOCODE space, resolves a curated
LOCODE/name alias map (`_PORT_CANON`, 27 ports), folds name+berth on the leading
token, and falls back to a cleaned label for unrecognised destinations - never
fabricating a port it is not sure of (uncurated LOCODEs like `CNSHA`/`ZACPT` pass
through as the raw code). Deliberately *not* reusing `_EUR_TERMINALS`, which is
coarse on purpose (it lumps Amsterdam/Ghent into Rotterdam/Antwerp energy
clusters); this map keeps each city distinct. This is descriptive aggregation
only - it is not an ETA target, so the "destination text stays untrusted for ETA"
rule is unaffected.

Both endpoints now group by raw destination in SQL, fold to canonical in Python,
then apply `top_n` (folding after the LIMIT would split a port across spellings).
Live result: Rotterdam one row (370), Antwerp one (280), Amsterdam one (136).

**Tests**: new `test_canonical_port.py` (27 cases: variant folding, LOCODE space
collapse, distinct-cities-not-merged, TRIST=Istanbul vs ITTRS=Trieste, junk
dropped, unknown-not-guessed). Updated two destination-flows endpoint tests where
`KRPUS` now correctly reads `Busan`. Full suite 405 passing.

## 2026-06-27 - True ETA Phase G (monitoring half): champion drift watch

Closed the monitoring half of Phase G. Key finding while scoping it: the "nightly
ETA-refresh timer" the roadmap called for is **redundant** - the existing
`freight-analytics.timer` already runs `analytics.build` hourly, and that job
already mines labels (`eta_labels`), rebuilds samples + scores naive/+route/physics
(`eta_samples`), and refreshes the live serving snapshot (`eta_serving`). So
`eta_model_metrics` is always current. Adding a second timer doing the same work
would just risk write contention for no gain. What was genuinely missing was an
*alert* when the champion quietly degrades - so that is what got built, and no new
timer was installed.

**New `analytics/eta_drift.py`**. Pure, unit-tested `assess_drift(history)` reads
the champion (`physics_v1`) overall-aggregate rows (`lead_bucket='all',
target_type='all'`) per run and flags two regressions: (1) interval coverage
leaving the `[0.70, 0.90]` band (the P10-P90 interval is nominally 80%, so this
catches calibration breaking too-tight or too-wide), and (2) median |err| jumping
more than +50% **and** +1h above its 7-day trailing median (the absolute floor
suppresses noise on the near-zero short buckets; min 3 prior runs before comparing).
`run_in_conn` persists any alerts to a new `eta_drift_alerts` table (idempotent per
run_ts/kind) and emits a `log.warning` (so degradation shows in `journalctl`).

**Wired into `build.py`** as step 7e, after the serving scorer, inside the same
try/except-guarded, atomically-swapped scratch build. Runs every hour for free.

**API**: `/api/analytics/eta-accuracy` now carries a `drift` array (latest run's
active alerts only, so a recovered past blip does not linger). New `EtaDriftAlert`
schema; defensive query returns `[]` if the table is absent on an older DB.

**Frontend**: `EtaAccuracyCard` shows an amber drift banner listing active alert
details when present, and the footer note documents the watch so the feature is
discoverable when healthy. New `EtaDriftAlert` type + optional `drift` on
`EtaAccuracyResponse`.

**Tests**: 9 new in `test_eta_drift.py` (clean / empty / coverage below=alert /
coverage above=warn / err regression fires / abs-floor suppresses / min-trail
guard / persistence is idempotent / missing-table safe). Full backend suite 378
passing; TSC + vite build clean. Verified live: endpoint returns `drift: []` on
current data (coverage 0.797, in band).

**Remaining in Phase G**: the weekly *gated retrain + auto-promote challenger*
loop, which depends on the ML model (Phase D) and is therefore still history-gated
(~8 weeks; collection started 2026-06-09).

## 2026-06-26 - True ETA Phase F: ETA chip + interval + method badge + accuracy scoreboard

The visible payoff of the True ETA build. Inbound cards now show the calibrated
physics ETA with its method badge and an on-hover band + vs-naive delta, and a new
public accuracy scoreboard proves the upgrade against the honest baseline. Frontend
only; no backend changes (the `/api/analytics/eta-accuracy` endpoint shipped with
the prior commit).

**New `lib/eta.ts`** (pure, vitest-covered): `formatEtaHours` (m / h / d), the
urgency color ramp, the method-badge token map, and `resolveEta(vessel, fallback)`
which turns the raw true-ETA fields into the chip's display model - primary value
(true when resolvable, else naive), the `P10-P90` band label, the method, the
signed true-minus-naive delta, and a tooltip. 9 unit tests.

**New `components/EtaChip.tsx`**: renders the primary ETA (colored by urgency) +
optional band + a small `physics`/`ml`/`naive` badge, with the tooltip on hover.
Colors come from `lib/eta.ts`, never hardcoded. Wired into the European Supply
Intelligence rows (full chip with band) and the LNG carrier rows (compact, badge
only); both keep the naive value visible (hover delta). Verified live: ~86 physics
badges on the European card, naive fallback where no target resolved, 0 console
errors.

**New `EtaAccuracyCard`** (Ports & Cargo tab, count 12 -> 13): the credibility
centerpiece. A grouped Recharts bar of median |err| by lead bucket for naive ->
+sea-route -> physics, plus an overall rollup table (median |err|, bias, P90 |err|,
80% interval coverage, n) and the honest "history starts at collection date; ML is
gated" note. Consumes `useEtaAccuracy` -> `/api/analytics/eta-accuracy`. Live
numbers: naive 12.72h -> +route 11.18h -> physics 10.95h overall, 80% coverage,
n=128,688.

**api.ts**: `TrueEtaFields` mixed into `EuropeanInboundVessel` / `LngVessel`;
`EtaAccuracyRow` / `EtaAccuracyResponse` types + `useEtaAccuracy` hook.

**Tracker vessel-detail popup** now carries a "True ETA to <target>" row (the
soonest resolvable target from `/api/analytics/eta?mmsi=`, via `useVesselEta`),
alongside the relabelled "ETA (reported)" raw AIS string - the trusted computed
estimate sits next to the untrusted reported one. Verified live (e.g. a vessel
inbound to Rotterdam shows "True ETA to rotterdam 3.0h PHYSICS").

**Serving quality fix surfaced by the popup:** `eta_serving` now drops any
prediction whose P50 exceeds a 14-day horizon (`_MAX_PRED_ETA_H = 336h`). A
barely-underway vessel (effective speed floored at 2 kn) on a cape/canal sea route
from a 1500 nm great-circle origin was producing multi-thousand-hour ETAs - real
arithmetic, but physically meaningless and not worth serving. Live max P50 is now
335.75h; the inbound cards (shorter horizons) are unaffected.

`npm run build` clean; full vitest green (27 passed) + backend 369 passed; visual
check passed (chart + chips + popup render, 0 console errors).

## 2026-06-26 - True ETA Phase E: live serving scorer + API + inbound-card integration

Brought the validated physics model (Phases A-C) to production. The analytics job
now scores every live underway vessel to a true ETA with a calibrated interval and
serves it through a new endpoint and inside the European / LNG inbound cards. No ML
yet (Phase D is gated on >= 8 weeks of history); the fallback chain is therefore
`ml -> physics -> naive` with physics as champion. First user-facing payoff of the
True ETA build.

**New module `analytics/eta_serving.py`** (the live scorer):
- `build_predictions(conn, ais_query)` reads the freshest `live_positions`, keeps
  underway vessels (SOG >= 1 kn), and resolves each to its plausible targets
  *geometrically* (never the dirty `destination` string): a target counts when it
  sits ahead of the vessel's COG/heading (approach bearing within 75 deg) and within
  1500 nm great-circle. The nearest 3 such targets are scored.
- Per (vessel, target): sea-route distance via the warm `RouteCache` (+ the same
  cell-centre snap correction as the offline build), `effective_speed` + canal
  staging -> `physics_eta` P50, plus the calibrated [P10, P90] band from an
  `IntervalModel` fit on **all** accumulated `eta_samples` (correct for serving:
  no held-out set to leak into). The `method` is recorded per row; a vessel with no
  valid effective speed degrades to a zero-width `naive` row, labelled honestly.
- Writes the `eta_predictions` snapshot table (rewritten each run, PK
  `(mmsi, target_id)`), carrying P50/P10/P90, the naive baseline, route + gc
  distance, route method, segment/laden and the target centroid. Registered in
  `build.py`'s run order (step 7d) after the sample/physics phase so the interval is
  fit on the freshest samples; wrapped in try/except so a failure never breaks the
  hourly build. First live run: **5,406 predictions across 2,086 vessels**, intervals
  monotone, zero negative lows, 54 distinct targets populated.

**LNG regas terminals are now ETA targets.** `eta_labels._curated_port_points` also
seeds the `_LNG_EU_TERMINALS` (Zeebrugge, Isle of Grain, Dunkerque LNG, Montoir,
Eemshaven, ...), so the LNG-inbound card's destinations are first-class targets the
scorer can attach a true ETA to (those within 20 nm of an existing port/zone, e.g.
Gate LNG Rotterdam, dedupe into it as before).

**API (`app/runner_eta.py` + `main.py`):**
- `GET /api/analytics/eta?mmsi=` -> the vessel's resolvable-target ETAs (P50 +
  [P10, P90] + method + arrival_ts + naive baseline), soonest first.
- `runner_eta` is a thin read layer (mirrors `runner_routes`): `vessel_predictions`,
  bulk `predictions_by_mmsi`, and `nearest_prediction(preds, lat, lon)` which picks
  the prediction whose target centroid is nearest a card's resolved terminal (within
  30 nm) - decoupling the cards from target-id slugs / the seeding dedupe.
- `/api/analytics/european-inbound` and `/api/analytics/lng-inbound` now carry
  `eta_true_h`, `eta_low_h`, `eta_high_h`, `eta_naive_h`, `eta_method`; `eta_hours`
  becomes the true estimate when resolvable, else the naive one (the naive value
  stays visible for transparency). European-inbound enriches ~half its fleet live
  (103/210 at first run); LNG enrichment is sparse only because LNG carriers are few
  and the currently-visible ones are anchored/arrived (excluded by the underway gate)
  or not EU-bound - the wiring is exercised and correct.

**Schemas:** new `EtaPrediction` / `EtaResponse`; `EuropeanInboundVessel` and
`LngVessel` gained the five true-ETA fields (all optional, backward compatible).

**Tests:** 5 new in `tests/test_eta_serving.py` - the scorer excludes anchored
vessels and bearing-gates targets behind the vessel, produces monotone non-negative
intervals with `method='physics'` and the canal-staging floor; `run_in_conn`
persists; empty live -> empty frame; the endpoint returns soonest-first predictions
and an empty list for an unknown vessel. Full backend suite green (366 passed).

## 2026-06-26 - True ETA Phase C: physics ETA, calibrated intervals, and the kinematic-ceiling finding

Third phase of the True ETA build (`docs/ROADMAP_TRUE_ETA.md`). Goal: a deterministic "physics" ETA good enough to serve now and become the floor the gated ML model must beat, plus an honest confidence interval. The headline result is as much a *finding* as a model: the long-lead error is irreducible by kinematics, which is exactly why ML is gated for Phase D. No user-visible change yet (serving is Phase E).

**New module `quant_lib.freight.eta`** (pure, dependency-free, exported from `quant_lib.freight`):
- `effective_speed`, `service_speed`, `queue_wait`/`canal_dwell`, `physics_eta`, `initial_bearing`, plus `SEGMENT_SERVICE_SPEED` / `CANAL_STAGING_HOURS` constants. The model is `eta = route_dist / effective_speed + queue_wait`.
- **Segment cruise priors are measured, not assumed**: the median SOG of *steaming* fixes (SOG >= 8 kn) per segment, taken from the hub's own AIS sample table (Capesize 12.6, VLCC 11.9, Suezmax 11.0, Small 9.8, ... kn). No synthetic numbers.
- **`queue_wait` is proximity-gated and conservative**: a canal gate (Suez 6 h, Panama 10 h) adds a staging allowance *only* once a vessel is within 60 nm; ports add nothing. We deliberately do **not** source a port queue from `anchored_episodes`: its dwell is a flat ~6.8 h median / ~7.0 h p90 across *every* zone (Rotterdam == Singapore == a tiny port), i.e. a detection-window artifact, not a real wait. Fabricating a queue from it would be worse than admitting we cannot measure one yet.

**New module `analytics/eta_physics.py`**: `physics_p50` (wraps the pure functions; gated on instantaneous SOG so all models score the identical underway set), `IntervalModel` (empirical residual P10/P90 by predicted-ETA bucket, fit leakage-free on the train split), and `make_physics_fn` (returns `{p50, low, high}` for the harness).

**`eta_samples` Phase-C features populated** (created NULL in Phase B, no migration): `sog_trail6h` (trailing 6 h median SOG, computed on the full pre-thinned track), `draught` (per fix), `approach_bearing` (vessel->target initial bearing), `service_speed` (segment prior), `dest_queue_h` (the proximity-gated canal allowance). These are the inputs the Phase-D model will actually learn on.

**The kinematic-ceiling finding (the rigorous core).** Several "smarter speed" point estimates were built and backtested leakage-free (voyage-grouped 50/50 split): trailing-median speed, a segment cruise-prior blend, a global speed-made-good efficiency factor, a proximity-gated SMG decay, and a 2-D empirical (distance x speed) surface. **None beat `route_dist / instantaneous_SOG` on aggregate median error.** Two facts explain why, both visible in the data:
- At short-to-mid lead the instantaneous SOG is already the best speed proxy (any blend toward trailing/cruise adds error in the 0-12 h bucket, which dominates the sample).
- At long lead the error is not a speed error at all. A vessel 24-48 h from arrival sits at a median sea-route distance of only ~50-60 nm - it is loitering / anchored / waiting for a canal slot, or making a fast *near-pass* whose true closest approach comes much later (for fixes 0-15 nm out at >=13 kn the actual remaining time runs p10 0.7 h / p50 6.9 h / **p90 60 h**). The route-time term is ~5 h there; no speed estimate can close a 20-50 h loiter gap. The 2-D empirical surface *could* cut the long-lead bias to ~0, but only by inflating every short-lead estimate (the distribution is bimodal and unresolvable from position+speed alone).

So Phase C ships the honest thing: keep the routing P50 (at the deterministic ceiling) with a robust speed estimate for serving, and add the value kinematics *can* give - a calibrated band and the canal staging term.

**Result** (re-scored over one held-out test half, 121,079 underway samples; all three models on the identical set):

| lead | naive med \|err\| | +route | physics_v1 | physics bias | interval cov |
|---|--:|--:|--:|--:|--:|
| 0-6h | 0.65h | 1.09h | 1.09h | +0.51 | 0.68 |
| 6-12h | 3.62h | 3.31h | **3.25h** | -0.71 | 0.95 |
| 12-24h | 12.56h | 10.41h | **10.25h** | -9.23 | 0.99 |
| 24-48h | 29.16h | 26.53h | **26.49h** | -26.24 | 1.00 |
| 48h+ | 53.85h | 50.43h | 50.48h | -50.41 | 0.48 |
| **all** | **12.72h** | **11.36h** | **11.16h** | **-8.20** | **0.796** |

physics_v1 is the best point model at 6-48 h and overall (the small gain over routing comes from the canal staging term reducing optimism on Suez/Panama approaches) and never regresses 0-6 h. Its calibrated interval hits **79.6% overall coverage** (target 80%). Per-actual-bucket coverage is uneven by construction (the band is bucketed by *predicted* ETA): mid-lead over-covers, while long-actual-lead loiterers - predicted short, arriving late - escape the band (0.48 at 48 h+). That residual is precisely the loiter/congestion signal the history-gated Phase-D model is meant to learn (trailing dynamics, nav-status, anchorage state), now that its feature columns are populated.

**Tests**: 6 new in `tests/test_eta.py` - effective speed prefers instantaneous with trailing/prior fallbacks and clamps; physics ETA monotonic in distance and speed with canal staging adding time (and only in-band); cardinal-direction bearings; `_add_physics_features` service-speed laden adjustment + canal-queue gating; `IntervalModel` offsets straddle zero and cover ~80% with non-negative lows; `build_samples` populates trailing speed / draught / bearing. Full backend suite green (361 passed).

## 2026-06-26 - True ETA Phase B: sea-route distance + the eta_samples training table

Second phase of the True ETA build (`docs/ROADMAP_TRUE_ETA.md`). Goal: replace the great-circle distance in the ETA with the distance a ship actually sails, and persist the per-observation training table the later phases (history-gated ML, calibrated intervals) will fit on. The naive great-circle ETA cuts across continents - Fujairah->Rotterdam is 2,851 nm as the crow flies but 6,123 nm by sea (2.15x), because the real voyage rounds Arabia, threads Bab-el-Mandeb and transits Suez - and that under-distance is the dominant cause of the Phase A long-haul optimism. No user-visible change.

**New tables** (`freight_analytics.duckdb`, written by the analytics job):
- `eta_samples` - one row per (approach, observation): label `remaining_h`, both distances (`route_dist_nm`, `gc_dist_nm`), `route_method`, `sog`, `segment`, `laden`, `target_type`, `is_canal`, `lead_bucket`. 756,440 rows; 240,951 underway and routed. The Phase C feature columns (`sog_trail6h`, `service_speed`, `draught`, `dest_queue_h`, `approach_bearing`) are created now and left NULL, so Phase C needs no schema migration. PK `(mmsi, target_id, arrival_ts, obs_ts)`; `voyage_id` is the train/test split unit.
- `eta_route_cache` - memoized `(snapped 0.25deg cell, target)` -> sea-route distance, with the method and compute timestamp. 2,293 distinct cells after the cold backfill. Persists across analytics runs (survives the atomic DB swap), so steady-state hourly builds route only never-before-seen cells.

**New module `analytics/eta_routing.py`**:
- `searoute` (PyPI 1.6.0, added as a backend dep) computes shortest paths over a vendored marnet GeoJSON graph that respects canals and capes. It ships its data in-package and runs fully offline - no runtime network call.
- **Grid snapping for memoization**: routing is the expensive step (~90 routes/s warm), so every origin is snapped to a 0.25deg cell centre (~15 nm) and the (cell, target) distance is cached. An hourly approach track revisits the same handful of cells, collapsing 240,951 routed fixes onto 2,293 distinct cell routings.
- **Fallback chain searoute -> great-circle**, method flagged on every row. The roadmap's middle "vendored marnet" tier is redundant in practice (searoute *is* the vendored marnet shortest path), so the honest chain is two real tiers. A missing/broken searoute degrades cleanly to great-circle for the whole build.
- **Great-circle floor**: a routed value shorter than its great circle can only be a graph-snapping artifact (both endpoints landing on one nearby node), so it is clamped to the physical lower bound at routing time.

**New module `analytics/eta_samples.py`** (registered in `build.py` run order after the Phase-A labels, also standalone via `python -m analytics.eta_samples`):
- `enrich_routes()`: adds `route_dist_nm` + `route_method` via one `RouteCache` over the whole frame. **Underway filter** - only fixes with `sog >= 1` are routed (a drifting/anchored fix has no kinematic ETA, is never scored, carries no routing signal); the other ~3x of rows get `route_dist_nm = NULL`, cutting the cold-cache budget threefold.
- **Snap correction (the key rigor decision)**: the cache stores the route from each cell *centre*, but a fix sits inside its cell, so the per-fix distance is `cell_route - gc(cell_centre -> target) + gc(fix -> target)`. This swaps the cell-centre's straight leg for the fix's own. At short range over open water the two gc terms cancel and `route_dist -> gc(fix->target)`, so routing never adds snapping noise to the already-excellent 0-6h naive estimate; at long range the gc terms are near-equal while `cell_route` carries the cape/canal detour, so the full routing gain survives. Because `cell_route >= gc(cell->target)`, the result is provably never shorter than `gc(fix->target)`.
- **Crash-safe cold backfill**: `RouteCache` flushes every 2,000 new cells, so an interruption during the first run over fresh history keeps everything routed so far. The full backfill (756,440 samples, 240,951 routed) took ~1h40m cold; subsequent builds are mostly cache hits.

**`analytics/eta_backtest.py`**: `build_samples()` now emits the obs lat/lon, `arrival_ts`, `segment`, `laden` and `is_canal` needed to persist `eta_samples`; new `route_eta_fn` divides `route_dist_nm` (falling back to `gc_dist_nm` when NULL) by SOG.

**Result - routing beats naive everywhere, most where geometry demands it.** Re-scored over the same 240,951 underway test samples (`eta_model_metrics`, models `naive` vs `naive+route`):

| lead | naive med \|err\| (all) | +route | naive bias | +route bias |
|---|--:|--:|--:|--:|
| 0-6h | 0.65h | 1.10h | +0.02 | +0.52 |
| 6-12h | 3.63h | 3.32h | -2.74 | -0.60 |
| 12-24h | 12.54h | 10.42h | -12.25 | -9.03 |
| 24-48h | 29.35h | 26.65h | -29.24 | -26.21 |
| 48h+ | 53.61h | 50.36h | -53.54 | -50.20 |
| **all** | **12.53h** | **11.17h** | **-11.39** | **-7.56** |

Aggregate bias drops 34% (-11.4h -> -7.6h) and median |err| 12.5h -> 11.2h. The chokepoint *targets* themselves improve only modestly (the strait gate is reachable in a near-straight line over water - median gc to Malacca is 6nm, to Suez 52nm - so there is little detour to recover); the win concentrates in the port targets inside the `all` aggregate and where geometry is unavoidable (Cape of Good Hope: median route 874nm vs gc 33nm). The large residual long-lead bias (still -50h at 48h+) is a speed/queueing problem, not a distance one - it is what Phase C (trailing speed, service-speed prior, anchorage wait) and the history-gated model target.

**Tests**: 7 new in `tests/test_eta.py` - snap-cell centring + key stability, routing avoids landmass (a Gulf-of-Aden->Rotterdam route is materially longer than its great circle), cache hit returns an identical value and persists, fallback to great-circle when searoute is unavailable, route never shorter than great-circle, enrich+persist round-trips with the >=gc invariant on routed rows, and `route_eta_fn` uses the route distance. Full backend suite green (355 passed).

## 2026-06-25 - True ETA Phase A: ground truth + naive baseline harness

First phase of the True ETA build (`docs/ROADMAP_TRUE_ETA.md`). Goal: make every ETA function in the repo scoreable against reconstructed real arrivals, by lead bucket and target, with one command, and commit the naive baseline as the reference all later phases must beat. No user-visible change.

**New tables** (all in `freight_analytics.duckdb`, written by the analytics job):
- `eta_targets` - the only legal ETA destinations. Seeded with 55 targets: the 9 transit chokepoints (region-bbox centroids) plus 46 de-duplicated ports/anchorage zones.
- `eta_arrivals` - reconstructed ground truth: per (mmsi, target) closest-approach to the target centroid, distinct calls split by a 24h min-gap. 35,328 arrivals mined over the 16-day history.
- `eta_model_metrics` - lead-bucket x target-type scoreboard (one row set per backtest run); seeded with `model='naive'`.

**New module `analytics/eta_labels.py`** (registered in `build.py` run order, also standalone via `python -m analytics.eta_labels`):
- `build_targets()`: deterministic target list. All 9 chokepoints kept unconditionally; ports (bbox anchorage zones, then `_EUR_TERMINALS` / `_US_LNG_LOADING_TERMINALS` point terminals) are de-duped *among themselves* within 20nm (e.g. zone-Rotterdam vs point-Rotterdam). A port is never de-duped against a chokepoint (the Suez gate and Suez Roads anchorage are distinct ETA targets).
- **Chokepoint anchoring (the key rigor decision)**: a chokepoint target is anchored to its real transit GATE coordinate (`_CHOKEPOINT_GATES`), NOT the basin-bbox centroid. The first miner run against box centroids gave a ~53nm median closest-approach because the AIS subscription boxes are basin-wide; switching to published strait coordinates dropped chokepoint median closest-approach to **3.7nm**. Reach is a single documented transit-capture radius (30nm) - one physically meaningful knob ("committed to the transit"), not a per-target fudge or a cap on a derived value.
- **Gate validation against data**: gate coordinates were cross-checked against where underway (sog>8) vessels actually concentrate in each region. This caught a mis-placed Cape of Good Hope gate (captured 4,880 underway fixes on the wrong side of the cape vs 34,377 on the real rounding lane); moved to the AIS-validated lane and its transit cross-check went from 79% -> 18% disagreement.
- Arrival miner: SQL bbox pre-filter -> exact vectorised haversine -> per-mmsi 24h gap-split -> closest-approach fix as `arrival_ts`, first qualifying fix as `approach_start_ts`. Full re-mine clears a target's prior arrivals first (the PK includes `arrival_ts`, so a changed gate would otherwise leave stale rows). `laden` uses the canonical `detect.laden_status` against the vessel's GLOBAL max draught (a per-approach max would read everything laden, since draught is ~constant within one approach). Read path injected (read-only lock-retry in prod, temp DB in tests).
- `cross_check_chokepoints()`: compares mined chokepoint arrivals to the independently-detected `transit_events` by distinct-vessel count, logging a warning past 50% relative divergence. Run automatically.
- **Coverage transparency**: a coverage summary logs every target with 0 arrivals. 32/56 targets have data; 24 are in regions the AIS collector does not yet feed (Hormuz, Bab-el-Mandeb, the Arabian Gulf, most Asia-Pacific and Med boxes - only 15 of the 24 `regions.py` boxes are in the current free-tier subscription). These stay seeded as legal targets and populate as collector coverage grows. Flagged as a data-coverage follow-up (collector domain, not this app).

**New module `analytics/eta_backtest.py`** (standalone via `python -m analytics.eta_backtest`):
- `build_samples()`: replays each arrival's approach track (one bulk AIS scan + in-memory groupby, not ~15k per-mmsi scans), samples fixes thinned to ~1h cadence up to 72h before arrival, labels each with actual `remaining_h` and great-circle distance. 756,691 samples / 236,868 scored (underway only).
- `score(eta_fn, ...)`: any `eta_fn(obs) -> hours` (or `{p50,low,high}` dict) -> median |err|, bias, MAPE, P90 |err|, interval coverage, by lead bucket x target type.
- Leakage control: `voyage_id = hash(mmsi,target_id,arrival_ts)`; `voyage_split` partitions on it so no voyage straddles train/test. Buckets are by *actual* remaining time.

**Committed baseline artifact**: `analytics/baselines/eta_naive_baseline.csv` (749,905 samples, 237,771 scored underway). The naive `great_circle/SOG` model reproduces the roadmap's signature on high-fidelity labels - excellent short range, optimistic at long lead:

| lead | med \|err\| (all) | bias | chokepoint med \|err\| | reading |
|---|--:|--:|--:|---|
| 0-6h | 0.67h | +0.02 | 0.45h | excellent |
| 6-12h | 3.63h | -2.74 | 1.71h | good |
| 12-24h | 12.6h | -12.3 | 11.4h | weak |
| 24-48h | 29.5h | -29.4 | 30.6h | optimistic, unusable |
| 48h+ | 53.7h | -53.7 | 53.7h | unusable |

Label quality after the gate fix: chokepoint closest-approach median **3.7nm** (was 53.6nm with basin centroids), port/anchorage **5.5nm**; `laden` distribution realistic (20k laden / 7k ballast / 7k unknown, vs the all-laden bug before using global max draught).

**Tests**: `tests/test_eta.py` (11 tests) - seeded temp DuckDB; asserts the miner finds exactly the real arrival, miner idempotency, chokepoints anchored to real gate coords with uniform reach, ports de-duped but chokepoints exempt, `laden` uses global (not per-approach) max draught so a historically-laden VLCC arriving light reads ballast, the transit cross-check reports agreement, harness math on an ideal approach (naive ETA == true remaining, |err| < 0.25h), lead-bucket edges, and no-leakage voyage split. Full backend suite green (348 passed).

## 2026-06-25 - Landing page: front door for the hub

The hub previously opened cold on the live tracker map (no context for a first-time visitor / recruiter). Added a proper landing page so the brand has a one-screen pitch before the dashboards.

- **New landing at `/`** (`frontend/src/routes/index.tsx`): hero with a live "N vessels tracked live" badge (pulsing dot, pulled from `/api/meta` `total_tracked`, falls back to "Live AIS feed" when offline), a one-line pitch, and an "Open the tracker" CTA. Below it a 7-card dashboard grid (Live Tracker spanning 2 cols + Analytics + Pipelines featured; Fleet, Events, Routes, Dispersion secondary) and a data-sources strip. Mirrors the energy hub's landing pattern and dark aesthetic.
- **Tracker moved `/` -> `/tracker`** (`frontend/src/routes/tracker.tsx`): same component, route id and `Route.useSearch()` retargeted. The brand/logo in the header now links to `/`; the "Tracker" nav item points to `/tracker`.
- **Deep-link retargeting**: all in-app navigations that opened a vessel/event on the map (`events.tsx`, `fleet.tsx`, and the four `analytics/-*Cards.tsx` modules) updated from `to: '/'` to `to: '/tracker'`, preserving their `mmsi`/`lat`/`lon`/`pipeline_id` search params.
- Build + typecheck clean; verified live in dev (landing renders with live count, grid links, tracker reachable at new path, 0 console errors).

## 2026-06-25 - LNG Intelligence: live carrier tracker with EU terminal ETAs, origin inference, US loading monitoring

**New endpoint `/api/analytics/lng-inbound`** (Phase 55):
- Cross-references live AIS positions with vessel_registry by IMO to identify LNG carriers (ship_type = 'LNG Tanker')
- 20 European LNG regas terminals: Gate LNG Rotterdam, Zeebrugge, Dunkerque, Montoir, South Hook, Isle of Grain, Dragon LNG, Eemshaven, Swinoujscie, Revithoussa, Porto Levante, Panigaglia, Livorno FSRU, Barcelona, Mugardos, Huelva, Sagunto, Cartagena, Krk FSRU, Klaipeda, Nynashamn, Manga LNG (Finland)
- Origin inference from transit_events: Suez NB laden -> Qatar/ME, Gibraltar/Dover E laden -> US Gulf LNG, Cape NB laden -> Atlantic LNG, Malacca W laden -> Asia Pacific LNG
- US loading terminal monitoring: vessels within 80nm of Sabine Pass, Calcasieu Pass, Corpus Christi, Freeport, Cove Point; status = loading (SOG < 1.5kn) or departing with EU ETA estimate (~14-18d)
- bcm estimate: 0.099 bcm per cargo (160k m3 TFDE LNG standard)
- Live data (2026-06-25): 19 LNG tankers visible, 3 inbound to EU (ORION MONET -> Eemshaven 7.6h Qatar-origin, OIZMENDI -> Huelva 1.7h, SEAGAS -> Manga/Finland), 4 loading at US Gulf (Sabine Pass, Calcasieu, Freeport), 2 departing EU ETA ~12-15d

**New `LngIntelligenceCard`** in Analytics Ports & Cargo tab (first card):
- KPI bar: LNG in AIS / EU inbound / bcm inbound
- EU terminal arrivals: vessel list by ETA, color-coded by origin, clickable -> tracker
- Origin breakdown (mini bars) and terminal receiving list
- US loading terminals: amber=loading, blue=departing with EU ETA estimate
- Fleet in transit: remaining LNG carriers not yet matched to terminal

**Tests**: 5 new pytest tests. Full suite 337 passed.

## 2026-06-25 - European supply intelligence: inbound vessel forecast with cargo origin inference

**New endpoint `/api/analytics/european-inbound`** (Phase 54):
- 15 European energy import terminals: Rotterdam, Antwerp, Zeebrugge, Hamburg, Wilhelmshaven, Le Havre, Milford Haven, Fos-Marseille, Barcelona, Huelva, Sines, Genova, Trieste, Augusta, Algeciras, Gdansk
- Origin inference from transit_events: Suez NB -> Middle East, Bosphorus S -> Black Sea, Cape NB -> East/long-haul, Malacca W -> Asia Pacific, Gibraltar E -> Atlantic/Americas
- Returns: vessel list sorted by ETA, per-vessel DWT estimates (segment proxies), by_origin, by_port, eta_bucket aggregates
- Live data: 266 vessels / 167 laden / 6.4M DWT inbound in 48h window

**New `EuropeanInboundCard`** in Analytics Ports & Cargo tab (first card):
- ETA timeline grouped 0-6h / 6-12h / 12-24h / 24-48h with vessel count per bucket
- Origin badges colour-coded by loading region (amber=Middle East, purple=Black Sea, green=W Africa, blue=Americas, teal=Asia Pacific)
- Laden-only filter, horizon selector (24h / 48h / 72h)
- Sidebar: origin breakdown with mini bars, port count list

**Tests**: 6 new pytest tests. Full suite 332 passed.

## 2026-06-22 - Straight-line fallback routes for remaining US pipelines (+11 routes, 433/618 total)

**Routes added (via new `ingest_wm_straightline_routes.py`, 2-point routes from pipeline_registry start_lat/lon and end_lat/lon, stored in eia_oil_pipeline_routes, 72 total there):**

- `bangl-pipeline-us` - Pecos TX to Big Spring TX, 194 km straight-line (WM: 845 km)
- `capline-oil-pipeline-patoka-to-catlettsburg-expansion-us` - Patoka IL to Catlettsburg KY, 557 km
- `eaglebine-express-crude-oil-pipeline-us` - Central TX to Beaumont TX, 269 km
- `heavy-louisiana-sweet-crude-oil-pipeline-system-us` - GOM offshore to Baton Rouge LA, 411 km
- `hobbs-east-gathering-system-rio-grande-pipeline-us` - Hobbs NM to El Paso TX, 345 km
- `kpc-gas-pipeline-us` - SW Kansas to central Kansas corridor, 250 km (WM: 1817 km - complex gathering network)
- `lone-star-express-y-grade-pipeline-us` - Midland TX to Beaumont TX coast, 701 km
- `lone-star-express-y-grade-pipeline-expansion-us` - Midland TX to Corsicana TX, 536 km
- `matterhorn-express-gas-pipeline-us` - Houston area to West TX (Permian to Gulf gas), 566 km
- `poseidon-oil-pipeline-us` - GOM deepwater (27.9N, 92.6W) to Louisiana coast, 242 km
- `sunrise-pipeline-system-us` - Wichita Falls TX to Midland TX, 390 km

**Excluded (bad placeholder data):** `cameron-highway-oil-pipeline-system-chops-us` and `zydeco-oil-pipeline-us` have identical start/end coords in the WM registry.
**Excluded (too large for straight-line):** `houston-gas-pipeline-hpl-system-us` (6116 km), `tejas-gas-pipeline-us` (5221 km).
**Excluded (cancelled):** `keystone-xl-cancelled` (never built).
**Note:** These 2-point routes are approximations for proximity analysis only - they show the terminal-to-terminal corridor, not the actual pipe path.

## 2026-06-22 - EIA NG intrastate pipeline routes (+5 routes, 422/618 total)

**Routes added (via new `ingest_eia_ng_intrastate_routes.py`, operator-based matching, stored in eia_oil_pipeline_routes, 61 total there):**

US gas intrastate pipelines from EIA Natural Gas Interstate+Intrastate Pipelines FeatureServer (operator field, no system name):
- `acadian-gas-pipeline-system-us` - Acadian Gas Pipeline + Gathering System (Louisiana), 168 segs, 816 km
- `bridgeline-gas-pipeline-us` - Bridgeline Holdings Pipeline (Louisiana), 40 segs, 1025 km
- `louisiana-intrastate-gas-lig-pipeline-us` - Louisiana Intrastate Gas Co (LIG, Louisiana), 434 segs, 2265 km
- `oasis-gas-pipeline-us` - Oasis Pipeline (Louisiana), 102 segs, 1358 km
- `socalgas-pipeline-us` - Southern California Gas Co (California), 222 segs, 3102 km

**Excluded (operator too broad - covers multiple systems):**
- "Houston Pipeline Co" (662 segs, 7058 km) -> `houston-gas-pipeline-hpl-system-us` - HPL operator covers all Texas Gulf Coast gas distribution, cannot isolate HPL trunk
- "Kinder Morgan Texas Pipeline Co" (812 segs, 7023 km) -> `tejas-gas-pipeline-us` - covers most of Texas gas infrastructure, not just the historical Tejas system

**Not found in EIA NG dataset:**
- `kpc-gas-pipeline-us` - KPC not identified under any matching operator name
- `matterhorn-express-gas-pipeline-us` - 2024 pipeline, not in EIA dataset yet

**Remaining US unrouted (17):** `cameron-highway`, `capline-expansion`, `eaglebine-express`, `heavy-louisiana-sweet`, `high-plains`, `hobbs-east-rio-grande`, `hpl-system`, `keystone-xl-cancelled`, `kpc-gas`, `lone-star-express-y-grade` (x2), `matterhorn-express`, `poseidon`, `sunrise`, `tejas-gas`, `bangl`, `zydeco`.
Note: `keystone-xl-cancelled` should be skipped (pipeline was never built).

## 2026-06-21 - EIA HGL NGL pipeline routes (+8 routes, 417/618 total)

**Routes added (via new `ingest_eia_hgl_routes.py`, stored in eia_oil_pipeline_routes, 56 total there):**

US NGL/Y-grade/ethane pipelines from EIA Hydrocarbon Gas Liquids Pipelines FeatureServer:
- `overland-pass-ngl-pipeline-us` - ONEOK Overland Pass (Opal WY to Conway KS), 1427 km
- `elk-creek-ngl-pipeline-us` - ONEOK Elk Creek Pipeline (Powder River Basin to Conway KS), 1362 km
- `grand-prix-y-grade-pipeline-north-texas-mont-belvieu-us` - Targa Resources Grand Prix (Permian/Mid-Con to Mont Belvieu TX), 1569 km
- `sterling-ngl-pipelines-lines-i-ii-and-iii-us` - ONEOK Sterling III (Elk City OK to Conway KS), 831 km
- `bakken-ngl-pipeline-us` - ONEOK Bakken NGL Pipeline (Williston Basin to Medford OK), 774 km
- `skelly-belvieu-pipeline-us` - Enterprise Products Skelly-Belvieu (Skellytown TX to Mont Belvieu TX), 751 km
- `mariner-west-pipeline-us` - Sunoco/MPLX Mariner West (Appalachian Basin to Sarnia Ontario), 638 km
- `utopia-ethane-pipeline-us` - Kinder Morgan Utopia East (Harrison County OH to Windsor Ontario), 319 km

**Source:** `Hydrocarbon_Gas_Liquids_Pipelines_1/FeatureServer/0` (EIA ArcGIS, same org as crude/products endpoints).
133 segments, 70 operator+name groups, 8 WM matches via manual overrides in `_MANUAL` dict.

**Remaining US unrouted (21 of original 29):** Cameron Highway, Lone Star Express Y-Grade, BANGL, Matterhorn Express,
Acadian Gas, HPL, SoCalGas, KPC, Tejas, Oasis, Bridgeline, LIG - not in EIA HGL dataset.

## 2026-06-21 - OSM pipeline routes (continuation: Middle East/SE Asia/Africa session) (409/618 total)

**Routes added (+5 net, 178 total in global_pipeline_routes, 409/618 = 66.2% WM coverage):**

Southeast Asia (via new `_FOREIGN_NAME_MAP` entries + rerun of southeast_asia region):
- `indonesia-singapore-west-natuna` - West Natuna Transportation System (WNTS) offshore gas pipeline Indonesia to Singapore, 592 km
- `indonesia-singapore-grissik-sakra` - Grissik-Batam/Sakra Gas Pipeline (South Sumatra to Singapore), matched via "Grissik - Batam Gas Pipeline" OSM name

Middle East:
- `arab-gas-pipeline` - Arab Gas Pipeline (Egypt/Jordan/Syria/Lebanon), matched via Arabic OSM name `خط الغاز العربي`
- `dolphin` - Dolphin Gas Pipeline (Qatar to UAE), matched via "Dolphin Gas Pipeline" OSM name

Africa:
- `tazama-oil-pipeline-tz` - TAZAMA Oil Pipeline (Dar es Salaam to Zambia border), matched via "TAZAMA Pipeline" OSM name
- `tanzania-mtwara-dar` - Mtwara-Dar es Salaam Gas Pipeline, matched via OSM name

**All OSM regions now exhausted - zero new matches in all remaining regions:**
- iran_east, middle_east_gulf (0): Iranian IGAT pipelines not in OSM with English names
- russia_w, russia_c, russia_e, china_ne (0): Cyrillic/Chinese names without Latin equivalents
- latam_n, latam_s, mexico_ca, africa_w, africa_e, middle_east_west (0): OSM coverage gaps confirmed
- us_northeast, us_southeast, us_gulf, us_west (0): US NGL/Y-grade/ethane pipelines not in EIA or OSM

**Script changes (`ingest_osm_named_pipeline_routes.py`):**
- Added `_FOREIGN_NAME_MAP` entries: WNTS (West Natuna), Grissik-Batam, Arab Gas Pipeline (Arabic + English), Dolphin Gas Pipeline, Habshan-Fujairah, TAZAMA, Mtwara-Dar es Salaam, Bolivia-Brazil Gas Pipeline (EN + ES)
- Added `_EXPAND` entries for India pipeline abbreviations (PHBPL, DVPL, HVJ) - carried from previous session

**Script changes (`ingest_eia_oil_routes.py`):**
- Fixed Keystone Phase 1-3 mapping: moved `keystone-oil-pipeline-phase-2-us` to "Keystone" entry; mapped "Gulf Coast Project" to `marketlink`

**Remaining large unrouted blocks (209 total):** CN (41), US (29), IN (23), IR (22), CO (10), RU (9), MX (8), SA (7).
Next data source candidates: PHMSA geospatial data (US NGL/liquid pipelines), Global Energy Monitor tracker, or coordinate-only straight-line interpolation for the remaining 209.

## 2026-06-21 - OSM pipeline routes (China/Myanmar/India/Norway/Nigeria/Australia session) (404/618 total)

**Routes added (+13 net, 175 total in global_pipeline_routes, 404/618 = 65.4% WM coverage):**

Asia/Central:
- `western-crude-oil-pipeline-shanshan-lanzhou-oil-pipeline-cn` - Kazakhstan-China crude (Atasu-Alashankou section mapped in OSM as "Kazakhstan - China Oil Pipeline"), 957 km, 30 pts
- `sino-myanmar-oil-pipeline-sino-myanmar-oil-pipeline-myanmar--mm` - Myanmar section of Sino-Myanmar oil pipeline, 496 km, 18 pts
- `turkmenistan-afghanistan-pakistan-india-gas-pipeline-tm` - TAPI pipeline (Afghanistan section), 257 km, 9 pts
- `jagdishpur-haldia-bokaro-dhamra-natural-gas-pipeline-jhbdpl-in` - JHBDPL India gas trunk (partial, 52 km stub from 4 OSM ways near Bokaro)

Africa:
- `niger-benin-oil-pipeline-ne` - Niger-Benin Export Pipeline (NBEP), 637 km, 16 pts

Australia:
- `eastern-gas-pipeline-au` - Eastern Gas Pipeline (New South Wales), 110 km, 8 pts

Norway (North Sea):
- `langeled` - Langeled subsea gas pipeline; was stored under `langeled-gas-pipeline-no` in eu_pipeline_routes but WM uses short ID - SQL-copied to global_pipeline_routes with correct WM ID, 1169 km, 29 pts
- `asgard-transport` - Åsgard Transport subsea pipeline (Norwegian shelf), 702 km, 15 pts; matched via north_sea region (new region added)

**Regions run with zero matches (OSM coverage gaps confirmed):**
- `africa_e` (80 groups): Tazama, Mtwara-Dar es Salaam not in OSM
- `central_asia_n` (45 groups), `central_asia_s` (15 groups): Kazakhstan/Turkmenistan trunks not in OSM
- `middle_east_west` (67 groups): Arab Gas Pipeline not in OSM with `man_made=pipeline` tag
- `canada_east` (516 groups), `canada_west` (181 groups): intrastate/NGL systems not in OSM

**Script changes (`ingest_osm_named_pipeline_routes.py`):**
- Added `_EXPAND` entries: `\bjhbdpl\b`, `\bphbpl\b`, `\bdvpl\b`, `\bhvj\b` (India abbreviations)
- Added `_FOREIGN_NAME_MAP` entries: Sino-Myanmar Chinese name (`中缅油气管道`), Myanmar-China English OSM name, Kazakhstan-China Atasu-Alashankou English OSM name, West-East Gas Pipeline 2 Lundu branch (`西气东输二线轮吐支干线`)
- Added `north_sea` region bbox (54-68°N, -5-12°E) covering Norwegian/UK shelf
- Removed duplicate dict keys (13 entries added mid-session overwrote originals)
- Fixed snap_km_start/end values stored as computed haversine distances not hardcoded 0

**Remaining large unrouted blocks:** CN (41), US (31), IN (24), IR (22), CO (10), RU (9), MX (8), SA (7).
Iran IGAT pipelines: OSM Iran has no named IGAT ways (generic "خط لوله گاز").
China oil pipelines (Yizheng-Changling, Daqing-Tieling, etc.): not in OSM.
Colombia (OCENSA, Cano Limon, TGI): OSM Colombia has only water/aqueduct pipe names.

---

## 2026-06-21 - OSM pipeline routes (LatAm, Ecuador, Russia expansions) (391/618 total)

**Routes added** (+12 net from OSM ingest, 167 total in global_pipeline_routes):

LatAm South:
- `gasbol` / `gasbol-gas-pipeline-bo` (duplicate pair) - Bolivia-Brazil gas pipeline via GASBOL, 3805 km
- `san-martin-pipeline-ar` - Gasoducto San Martin, Argentina, 2527 km
- `camisea-ngl-pipeline-pe` - Camisea Pipeline, Peru, 203 km
- `norandino-gas-pipeline-ar` - Gasoducto Nor Andino (Argentina-Chile), 636 km
- `bolivia-argentina-yacuiba` - Gasoducto Yacuiba Rio Grande (Bolivia-Argentina GIJA), 798 km
- `cordillerano-patag-nico-gas-pipeline-cordillerano-north-ar` - Gasoducto Cordillerano, Argentina, 310 km

Ecuador:
- `sote-ecuador` / `sote-oil-pipeline-ec` (duplicate pair) - Sistema Oleducto Trans-Ecuatoriano (SOTE), 374 km

Middle East (from prior sub-session):
- `sumed` - SUMED pipeline (Egypt), 97 km
- `bab-habshan-fujairah-oil-pipeline-ae` / `habshan-fujairah` - UAE Habshan-Fujairah oil pipeline, 338 km
- `turkmenistan-afghanistan-pakistan-india-gas-pipeline-tm` - TAPI pipeline, 257 km
- `kochi-koottanad-bangalore-mangalore-gas-pipeline-phase-ii-in` - KKBMPL GAIL, India, 1287 km

**Script fixes and improvements (`ingest_osm_named_pipeline_routes.py`):**
- Fixed Unicode en-dash bug in `_norm()`: non-ASCII non-combining chars now replaced with spaces
  so "Habshan-Fujairah" tokenizes as {habshan, fujairah} not {habshanfujairah}
- Snap km values now stored from computed haversine distances (were hardcoded 0.0)
- Added minimum path_km >= 30 guard before storing, rejecting terminus stubs that pass snap check
- Added `_FOREIGN_NAME_MAP` entries: TAPI, KKBMPL, Iranian pipelines (IGAT-1), SRTO Center,
  Bukhara-Tashkent-Bishkek-Almaty, Gasoducto Yacuiba Rio Grande, Gasoducto Cordillerano
- Added `_EXPAND` entries: norandino/transandino/transecuatoriano compound expansion,
  ecuatoriano -> ecuadorian, nororiental -> northeastern, brasil -> brazil,
  neuba -> neuquen buenos aires, SOTE -> "system trans ecuadorian oil pipeline"
- Added `us_permian` bbox region (28-34N, 107-88W) covering West Texas/NM gap

**Gasbol duplicate fix:** `gasbol-gas-pipeline-bo` had a stale 2428 km route from a prior OSM run;
updated to share the current full `gasbol` geometry (3805 km, 129 pts).

---

## 2026-06-21 - Alberta intra-provincial pipeline routes via AER GIS (377/618 total)

**Added:** 4 Alberta oil-sands pipeline routes via a new script
`backend/ingest_aer_pipeline_routes.py` using the Alberta Energy Regulator GIS layer at
`gis.energy.gov.ab.ca/arcgis/rest/services/Geoview/ERCB_Ext_PROD/MapServer/10`.

The AER layer has 324,617 segments covering every licensed pipeline in Alberta. The script
filters tightly by `CompanyName LIKE '%..%' AND SubstanceCode1 AND PipelineStatus = 'Operating'`,
applies a `min_km` threshold to drop short gathering laterals, and requests output in WGS84
via `outSR=4326`. Supports optional geographic bbox filtering (used for the Horizon attempt).
Paginates automatically via `resultOffset` when result counts exceed `MAX_RECORDS=2000`.

**Routes added:**
- Enbridge Athabasca Oil Pipeline - `athabasca-oil-pipeline-ca` - 37 segs, 1181 km (min_km=15)
- Grand Rapids Oil Pipeline (Grand Rapids Pipeline GP Ltd.) - `grand-rapids-oil-pipeline-ca` - 16 segs, 297 km (min_km=12)
- Cold Lake Pipeline System (Cold Lake Pipeline Ltd.) - `cold-lake-pipeline-system-ca` - 23 segs, 776 km (min_km=20)
- Corridor Oil Pipeline (Inter Pipeline (Corridor) Inc.) - `corridor-oil-pipeline-ca` - 35 segs, 508 km

**Investigated but not stored (3 pipelines):**
- `horizon-crude-oil-pipeline-ca`: CNRL's Horizon mine has only pump station spurs (<10 km each) in AER at large diameter; no continuous trunk exists in the AER data. Corridor largely duplicates the Enbridge Athabasca entry.
- `alberta-ethane-gathering-system-aegs-ca`: NOVA Chemicals holds <1 km of ethane pipe in AER (Joffre plant connections only). Main AEGS gathering infrastructure is part of CER-regulated NGTL, already stored.
- `co-ed-system-ngl-pipeline-ca`: AER NGL operators don't reach the WM start coordinate at Cochrane (51.19°N); historical pipeline now fragmented across Pembina/Keyera/Wolf operators.

**Artifact:** `backend/ingest_aer_pipeline_routes.py`

---

## 2026-06-21 - CER pipeline routes via NRCan ArcGIS FeatureServer (373/618 total)

**Added:** 7 Canadian federally-regulated pipeline routes via a new script
`backend/ingest_cer_pipeline_routes.py` that queries the Canada Energy Regulator
ArcGIS Online FeatureServer (public, no auth).

The CER service at `services5.arcgis.com/.../CER_Pipeline_Systems_WGS84_view/FeatureServer/3`
returns all 28 CER-regulated pipeline systems as GeoJSON MultiLineString features, each
with `PipelineID`, `Pipeline_Name`, `Company`, and `Commodity` fields. A manual
`_CER_TO_WM` mapping converts `PipelineID` to WM IDs.

**Routes added:**
- NGTL (Nova Gas Transmission / NGTL System) - `nova-gas-transmission-ngtl-...` - 55 segs, 3205 km
- Westcoast (Enbridge BC Pipeline) - `bc-gas-pipeline-westcoast-pipeline-ca` - 41 segs, 1743 km
- Foothills System - `foothills-system-gas-pipeline-ca` - 9 segs, 896 km
- TCPL (TC Canadian Mainline) - `canadian-mainline-gas-pipeline-ca` - 59 segs, 4430 km
- Cochin Pipeline - `cochin-pipeline-system-ca` - 3 segs, 976 km
- Enbridge Bakken System - `enbridge-line-65-oil-pipeline-ca` - 1 seg, 152 km
- Wascana Pipeline (Plains Midstream) - `saskatchewan-oil-pipeline-ca` - 1 seg, 171 km

**Implementation notes:** NGTL raw geometry has 1483 paths (entire Alberta gas grid).
`_MIN_PATH_KM` filter (40 km for NGTL) drops gathering laterals, keeping major
transmission corridors. `_EPSILON_OVERRIDE` gives NGTL eps=0.10 deg (~10 km) to
further reduce to 55 renderable segments. Coordinates swapped from GeoJSON
`[lng,lat]` to WM storage convention `[lat,lon]`.

**Remaining unrouted Canadian (9):** Cold Lake, Grand Rapids, Athabasca, Corridor,
Horizon, AEGS, Co-Ed (all intra-provincial Alberta - AER regulated, not CER);
Keystone XL (cancelled, no geometry); Prince Rupert Gas Transmission (not built).

**Artifact:** `backend/ingest_cer_pipeline_routes.py`

---

## 2026-06-21 - OSM Chinese name map, proximity fix, US sub-regions (366/618 total)

**Added:** 23 more WM pipeline routes, bringing the total to **366/618** (was 343 at session
start after WM dataset was updated from 700 to 618 entries; pipeline-count delta is unrelated
to routing work).

**Code changes (commit f6bd9d1):**
- `_FOREIGN_NAME_MAP`: translates 20+ Chinese-character and Cyrillic OSM `name` tags to
  WM-matchable English. Chinese characters reduce to empty ASCII through NFKD normalization,
  so they were silently dropped without this map. Covered: West-East Gas Pipeline 1-4 and
  subsections, China-Russia East Pipeline phases 1-3, Shaan-Jing 1-4, Sino-Myanmar crude
  and gas, Kazakhstan-China Oil, ESPO-China spur.
- Proximity check switched from centroid to nearest-point sampling over 600 evenly-spaced
  points: a 5000 km pipeline's centroid is 2500 km from sub-section WM endpoint pairs,
  causing all sub-section matches to fail the 600 km guard.
- `_norm()` now keeps single-digit tokens: "2", "3", "4" were being filtered by `len > 1`,
  making all numbered pipeline variants (West-East Gas Pipeline 2 vs 3 vs 4) produce
  identical token sets and prevent specific numbered matches.
- Bboxes: split `us_lower48` into 6 sub-regions (northeast, southeast, gulf, midcontinent,
  rockies_north, west) and `canada` into `canada_west` + `canada_east` to avoid Overpass
  timeouts on large bboxes.
- EIA oil: added `keystone` WM ID to TRANSCANADA Keystone override, added Seminole Red
  Pipeline override for Enterprise and Phillips 66 variants.

**Routes added this session (global_pipeline_routes: 116 -> 137, EIA oil: 45 -> 47):**
- China: WEGP 1/2/3/4 + middle/west subsections, China-Russia East phases 1/2/3, Power of
  Siberia, ESPO-China spur (x2 WM IDs), Sino-Myanmar gas trunk - 14 routes
- India: HVJ (Hazira-Vijaipur-Jagdishpur) Gas Pipeline - 1 route
- US (OSM): Mariner East 2 NGL, Aegis Pipeline, Whistler Pipeline, Atmos Pipeline Texas - 4 routes
- EIA oil: Keystone mainline, Seminole Red Pipeline - 2 routes
- Canada (Enbridge Line 65): +1 route (from canada_east)

**Remaining gaps (252 unrouted):** China domestic (42, need CNPC GIS), US NGL gathering
systems (31, PHMSA NPMS needed), India domestic (25, PNGRB/GAIL), Iran (22, no public GIS),
Canada gas/oil sands (16, NRCan/CER shapefiles), Russia domestic (15).

**Artifacts:** `backend/ingest_osm_named_pipeline_routes.py`, `backend/ingest_eia_oil_routes.py`.

---

## 2026-06-21 - EIA oil manual overrides expanded (374/700 total)

**Added:** 16 more WM pipeline routes by expanding `_MANUAL` in `ingest_eia_oil_routes.py`
from 29 to 45 entries. Key change: converted `_MANUAL` values from `str` to `list[str]`
so one EIA `(opername, pipename)` entry can populate multiple WM IDs sharing the same
physical corridor (aliases, phases, same operator).

New WM IDs covered: `alberta-clipper-oil-pipeline-ca` (Enbridge Line 67/Alberta Clipper),
`diamond-oil-pipeline-us` (Plains All American Patoka-Memphis), `enbridge-line-14-64-oil-pipeline-us`
(North Dakota system), `frontier-oil-pipeline-us` (Holly Energy Big Spring-Denver), `grand-mesa-oil-pipeline-us`
(Magellan DJ Basin-Cushing), `kaw-oil-pipeline-us` (CHS Energy Kansas crude),
`midland-to-echo-pipeline-system-*-us` x2 (Enterprise Midland-ECHO), `ozark-crude-oil-pipeline-patoka-to-lima-expansion-us`,
`permian-express-oil-pipeline-phase-i/ii/iv-us` x3, `seaway-oil-pipeline-system-us`,
`spearhead-oil-pipeline-us`, `teppco-pipeline-us` (Enterprise Gulf Coast-Great Lakes products),
`western-corridor-oil-pipeline-system-glacier-pipeline-bearto-us` (Phillips 66 Glacier).

**Combined total: 374/700 WM pipelines now routed** (from 358 after OSM named-way pass).

---

## 2026-06-21 - OSM named-way pipeline routes (global, 56 new routes; 358/700 total)

**Added:** 56 WM pipeline routes via OSM Overpass named-way assembly, on top of the
existing Dijkstra/IGGIELGN/EIA stack. New script `backend/ingest_osm_named_pipeline_routes.py`
covers 21 global region bboxes, queries `way[man_made=pipeline][name]` per region, groups
way segments by name tag, chains disconnected segments with a greedy nearest-endpoint
algorithm (MAX_CHAIN_GAP_KM=300), fuzzy-matches to WM pipeline IDs by Jaccard score
(threshold=0.38), and stores routes in `global_pipeline_routes`.

Key implementation details:
- Name resolution: prefer `name:en > int_name > alt_name > name` to handle Russian Cyrillic,
  Chinese, and Arabic pipeline names whose bare `name` tag normalises to empty ASCII
- Generic name filter: rejects OSM names with <2 distinctive words (e.g. "Gas Pipeline")
- Centroid distance guard: rejects matches where OSM centroid is >600 km from WM endpoints
- Rate-limit handling: polls Overpass `/api/status` before each query, waits on "Slot
  available after:"; HTML (406) responses get 90s+ exponential backoff
- RDP simplification at epsilon=0.02 deg (~2 km)
- `--region` flag supports multiple invocations for targeted reruns

Regional breakdown (routes stored):
- Middle East West: 7 (East-West Saudi, Greater Nile, Iraq Strategic x3)
- Central Asia: 6 (Kazakhstan-China Oil Pipeline variants)
- Russia Central: 6 (Aleksandrovskoye-Anzhero, Vankor-Purpe, Omsk-Irkutsk)
- Mexico/CA/US: 25 (Wink-to-Webster, Gulf Coast Express, Sur de Texas-Tuxpan, Sand Hills,
  Sistema Nacional de Gasoductos MX, Energia Mayakan, Black Lake, Flanagan South, DAPL,
  North System, Eastern Gas Transmission, Pony Express, Ozark Crude, Red River, etc.)
- Canada: 15 (Enbridge Lines 1/2/3/4/5/6/9/61/78, Trans Mountain, Norman Wells, Minnesota)
- Oceania: 5 (Moomba-Sydney, South West Queensland, Moomba-Adelaide, Dampier-Bunbury)
- South Asia: 5 (Salaya-Mathura, Myanmar-China crude+gas, Dabhol-Bangalore)
- Africa: 2 (Chad-Cameroon, Escravos-Lagos)
- LatAm: 3 (OCP Ecuador, Puerto Rosales-La Plata, Gasoducto al Altiplano)
- SE Asia: 1 (Amadeus Gas)
- China West: 1 (Sebei-Golmud)

Post-ingest cleanup removed 20 routes: 4 false positives (3 unrelated IDs mapped to "Casa
Pipeline System", 1 matched "US Amines Hydrogen Pipeline") and 16 routes with <4 points
(too sparse to render a meaningful line). Final: 104 routes in `global_pipeline_routes`.

**Combined total: 358/700 WM pipelines with full polyline routes** (from 302 at session
start). Route priority: EIA gas (RexTag crosswalk) -> EIA oil -> EU IGGIELGN -> OSM global.

**Artifacts:** `backend/ingest_osm_named_pipeline_routes.py` (new), commits 18ff75a, 570e3f6, c19a077.

---

## 2026-06-21 - EIA crude oil + petroleum product pipeline routes; extended WM-RexTag crosswalk

**Added:** Full polyline geometry for an additional 36 WM pipelines (17 oil + 19 gas)
via two parallel tracks:

**Track 1 - EIA oil shapefile ingest (`ingest_eia_oil_routes.py`):**
Downloads crude oil (231 segments, 40 operators) and petroleum product (329 segments)
pipeline geometries from EIA ArcGIS FeatureServer endpoints. Fuzzy-matches EIA
`(opername, pipename)` pairs to WM pipeline IDs; fuzzy scope restricted to US-endpoint
WM pipelines to prevent false-positive matches to international WM entries. 19 WM oil
pipelines now have full EIA shapefile routes, stored in new `eia_oil_pipeline_routes`
table (wm_id PK). Priority in loader chain: EIA gas -> EIA oil -> EU IGGIELGN -> OSM global.

Notable new oil routes: Enbridge Mainline System (3.15 mbd, 8 segments),
Trans-Alaska Pipeline TAPS (2.1 mbd), Trans Mountain (3 segs), Gray Oak Pipeline,
EPIC Crude Pipeline, Seaway Pipeline, BridgeTex, Southern Lights, Double H Pipeline,
Energy Transfer ETCOP.

**Track 2 - Extended rextag_wm_crosswalk (`ingest_extend_crosswalk.py`):**
23 new WM ID -> RexTag slug mappings for major US/Canada gas pipelines that already
had EIA route geometry but lacked a crosswalk entry. Zero new downloads. New entries
include: ANR, El Paso, Rockies Express, Panhandle Eastern, Kern River, East Tennessee,
NGPL, Alliance, Gulf South, Northwest, Northern Border, Mississippi River Transmission,
WBI/Williston Basin, Enable Oklahoma (EOIT), MountainWest Overthrust, Midcontinent
Express, Gulfstream, Maritimes & Northeast, Mojave, Iroquois, Empire, Ruby, Sabal Trail.

**Combined result:** 286/700 WM pipelines now have full polyline routes (up from 207
before this session), plus 65 RexTag-only US gas pipelines. Oil coverage now includes
TAPS, Enbridge Mainline, Trans Mountain, Gray Oak, and 15 other US/CA crude routes.

**Artifacts:** `backend/ingest_eia_oil_routes.py`, `backend/ingest_extend_crosswalk.py`,
`shared/market-data/loaders/worldmonitor.py` (4th JOIN: eia_oil_pipeline_routes).

---

## 2026-06-20 - Global pipeline route geometry (OSM Overpass Dijkstra)

**Added:** Full polyline geometry for an additional 40 World Monitor pipelines
covering Russia/Central Asia, East Asia, Middle East, Africa, South America,
and Oceania using OSM Overpass API Dijkstra routing. Two-pass ingest:

- Pass 1 (`ingest_global_pipeline_routes.py`): 20 regional bbox Overpass queries,
  per-region DuckDB saves (idempotent on resume), rate-limit auto-retry.
- Pass 2 (`ingest_global_pipeline_routes_pass2.py`): merges sub-region graphs into
  super-regions to handle trans-regional pipelines (e.g. ESPO spans East + Far East
  Russia boxes). Adds a further 4 routes.

Notable new routes: ESPO (4,436 km, 270 pts), West-East Gas Pipeline China
(2,811 km), GASBOL Bolivia (2,428 km), Power of Siberia (634 km), Central
Asia-China Line C (1,838 km), Dampier-Bunbury AU (1,544 km), Express CA
(1,258 km), Chad-Cameroon (1,067 km), Mozambique-SA Gas (858 km).

Combined with EU IGGIELGN (147) and EIA US gas (85 RexTag + 20 WM-linked),
total with full polyline routes: 272/722 (38%). Remaining gaps are mostly US/CA
oil pipelines (TAPS, Enbridge, Keystone, Colonial) where OSM network lacks
connected endpoint topology for Dijkstra routing.

**Loader:** `worldmonitor.py` `load_pipelines_for_map()` now JOINs three route
tables in priority order: EIA -> EU -> global.

**Artifacts:** `backend/ingest_global_pipeline_routes.py`,
`backend/ingest_global_pipeline_routes_pass2.py`,
`shared/market-data/loaders/worldmonitor.py`.

---

## 2026-06-20 - EU + global pipeline full route geometry (IGGIELGN)

**Added:** Full polyline geometry for 147 World Monitor EU/global pipelines using the
SciGRID_gas IGGIELGN dataset (Zenodo CC-BY, 6323 gas network segments covering Europe,
Russia, MENA, and the Caucasus). One-shot ingest script (`backend/ingest_eu_pipeline_routes.py`)
downloads the zip, builds a graph, runs Dijkstra shortest-path routing from each WM
pipeline's start/end coordinates, RDP-simplifies at epsilon=0.02 degrees, and stores
routes in `eu_pipeline_routes` table in `freight_analytics.duckdb`.

Previously only 85 US pipelines had full EIA polylines. Now 232 of 722 total pipelines
have route_coords in the API. Notable routes: Nord Stream 1&2 (Baltic crossing,
52-61N,13-30E), Yamal-Europe (Siberia to Germany, 52-66N), BTC (Azerbaijan through
Georgia to Turkey), Druzhba North/South, TAP (Turkey to Italy via Adriatic submarine),
TANAP, Kirkuk-Ceyhan, Transalpine (TAL), and 140+ others.

**Loader:** `shared/market-data/loaders/worldmonitor.py` updated to LEFT JOIN
`eu_pipeline_routes` in `load_pipelines_for_map()`. EIA US routes take priority;
EU routes fill all others. No frontend changes needed.

**Artifacts:** `backend/ingest_eu_pipeline_routes.py` (new), `shared/market-data/loaders/worldmonitor.py`.

---

## 2026-06-20 - UX: Vessel deep-links + pipeline map-link

**Pipeline label fix:** "Disrupted pipelines" layer toggle renamed to "Pipelines" (it always showed all 618, not just disrupted).

**Pipeline map-link:** MapPin button added to each row in `/pipelines` table. Clicking navigates to `/?pipeline_id=X`, which enables the pipelines layer, highlights the pipeline with a white halo + thicker colored line, calls `map.fitBounds` to the pipeline endpoints (max zoom 6, 80px padding), and opens the popup 600ms later. The `pipeline_id` search param is new in the tracker's `validateSearch`.

**Vessel deep-links from analytics:** Every vessel row in the Intelligence, Ports & Cargo, and Fleet analytics tabs is now clickable and navigates to the tracker map with the vessel selected (and zoomed if lat/lon available). Covers: Anomaly Watchlist, Destination Changes, STS Proximity, STS Offenders, Reroutes, Risk Event Feed, Shadow Fleet Monitor (Intelligence tab); Port Arrivals, Cargo Transitions, Cargo State Changes (Ports & Cargo tab); Speed Anomalies, Slow Steamers (Fleet tab); Vessel Risk Leaderboard (Risk tab). Uses a `useGoToTracker()` hook pattern (`?mmsi=X&lat=Y&lon=Z` when coordinates available, `?mmsi=X` only when not).

**Artifacts:** `frontend/src/components/tracker/{LayerToggles,PipelineLayer,VesselMap}.tsx`, `frontend/src/routes/{index,pipelines}.tsx`, `frontend/src/routes/analytics/{-IntelligenceCards,-RiskCards,-PortsCargoCards,-FleetCards}.tsx`.

---

## 2026-06-20 - Phase 55+56: Owner fleet status card + Pipelines page

**Tried:** Two backlog items: (1) live laden/ballast breakdown per beneficial owner by joining live_positions -> vessel_registry (via IMO) -> vessel_state; (2) dedicated /pipelines page showing all 618 World Monitor pipelines in a searchable/sortable table.

**Found:** Owner fleet status JOIN works cleanly - vessel_state (analytics DB) holds laden/ballast per MMSI, vessel_registry (Equasis registry DB) holds owner per IMO. Only ~30-40% of live vessels have a matched Equasis entry (IMO required), so the card shows a subset of the fleet. Pipeline data (disrupted_only=false) returns 618 rows in ~1s via existing endpoint - fully feasible for client-side filtering with no pagination. Physical states: flowing, offline, reduced, unknown. Disruption descriptions are long-form prose averaging ~80 chars.

**Decision:** Owner fleet card placed first on Fleet analytics tab (most useful daily view for who is moving cargo). Pipelines page added as dedicated nav item between Fleet and Routes - table with KPI bar, state/commodity filters, name search, sortable columns, inline expand for full disruption description. No backend changes needed for pipelines page. 3 new backend tests (326 total passing).

**Artifacts:** `backend/app/main.py` (owner-fleet-status endpoint), `backend/app/schemas.py` (OwnerFleetStatusRow/Response), `backend/tests/test_endpoints.py` (+3 tests), `frontend/src/lib/api.ts` (useOwnerFleetStatus), `frontend/src/routes/analytics/-FleetCards.tsx` (OwnerFleetStatusCard), `frontend/src/routes/pipelines.tsx` (new page), `frontend/src/routes/__root.tsx` (nav link).

---

## 2026-06-19 - Phase 54: Pipeline disruption map layer

**Added:** Toggleable "Disrupted pipelines" layer on the vessel tracker map. Draws the 37
currently offline or reduced global energy pipelines as color-coded Polylines on top of
live vessel positions (offline=red dashed, reduced=orange). Clicking a line opens a popup
with pipeline name, route, capacity, event type, and the disruption description from the
World Monitor database. The layer is off by default and can be toggled via the Controls panel.

Data source: World Monitor (Global Energy Monitor, CC-BY 4.0) - same dataset as the
quant research projects (gas-storage, transport-arb). Current state: 15 pipelines offline
(4.62 mbd / 399 bcm/yr), 22 pipelines reduced. Key offline: Kirkuk-Ceyhan (1.6 mbd, IQ->TR,
Mar 2023), Druzhba North (1.0 mbd, RU->DE, Feb 2023), Nord Stream 1+2 (55 bcm/yr each, sabotage
Sep 2022), Brotherhood/Soyuz Ukraine transit (142 bcm/yr, commercial end Jan 2025).

Backend: New `load_pipelines_for_map(disrupted_only)` loader in `loaders/worldmonitor.py`
(LATERAL JOIN to attach the most recent active disruption per pipeline). `GET /api/pipelines`
endpoint with 1h in-process cache. `PipelineSegment` + `PipelinesResponse` schemas.
3 new tests; 323 total passing.

Frontend: `PipelineLayer.tsx` (imperative L.polyline), `usePipelines()` hook in `api.ts`,
`pipelines` key in `LayerState` and `DEFAULT_LAYERS`, toggle in `LayerToggles`.

**Artifacts:** `backend/app/main.py`, `backend/app/schemas.py`, `backend/tests/test_endpoints.py`,
`frontend/src/components/tracker/PipelineLayer.tsx`, `frontend/src/lib/api.ts`,
`frontend/src/components/tracker/{VesselMap,LayerToggles,types}.tsx`,
`shared/market-data/loaders/worldmonitor.py`.

---

## 2026-06-14 - Phase 53: High-risk events syndication feed (Atom + JSON Feed)

**Added:** Public, no-accounts syndication feeds over the same `ais_events` rows that power
`/api/events`. Closes the last unbuilt backlog item (the "email/RSS digest"), delivered as
feeds rather than email to fit the deliberately no-accounts / no-SMTP public-showcase stance.

- `GET /api/feed.xml` - Atom 1.0 (`application/atom+xml`), well-formed, self/alternate links,
  feed `updated` = newest entry, per-entry stable `urn:freight-event:<event_id>` ids,
  `category`, RFC 3339 timestamps.
- `GET /api/feed.json` - JSON Feed 1.1 (`application/feed+json`).
- Default surfaces only high-risk types (`dark_voyage`, `spoof`, `gap`, `loiter`, `sts`);
  reroutes excluded as noise. Overridable via `?types=`, window via `?days=` (1-30),
  `?limit=` (1-500). Each entry deep-links to the tracker (`/?mmsi=<mmsi>`) and is name-enriched
  from `live_positions` (falls back to `MMSI <n>` when a vessel has aged out).
- New `app/feed.py` pure builders (hand-built Atom via stdlib, JSON Feed as a dict; no new
  deps). Shared `_fetch_events_raw()` read helper in `main.py`.
- Frontend: `SubscribeFeed` RSS popover on the Events page header (copy Atom/JSON URLs, built
  from `window.location.origin` so it works in dev and prod). No new deps.
- 5 new backend tests (Atom well-formedness, high-risk default filter, `types=` override,
  JSON Feed structure, empty-DB valid feed). 324 backend tests passing; frontend build clean.

**Also:** `uv sync --extra dev` added `psycopg2-binary` (env drift after the market-data
loaders migration left the freight venv missing it, which had been erroring the whole suite).

**Artifacts:** `backend/app/feed.py`, `backend/app/main.py` (Response import, feed module
import, `_fetch_events_raw`, `_feed_types`, `feed_atom`, `feed_json`), `backend/tests/test_endpoints.py`,
`frontend/src/components/SubscribeFeed.tsx`, `frontend/src/routes/events.tsx`.

---

## 2026-06-12 - Phase 51: Analytics build crash fixes + fleet trend chart + events UX

**Fixed:** Three production bugs that had been causing every analytics build to crash before the watermark was set (forcing 9-min full rebuilds every hour instead of 30-sec incremental runs):
1. `build.py` gap-recheck loop iterated `numpy.int64` MMSIs directly into DuckDB parameters: `NotImplementedException`. Fixed with `.tolist()` on the numpy unique array.
2. `detect.py _dest_edit_dist()` received float NaN destination values (pandas coerces None to float in object columns): `TypeError: float has no len()`. Added `math.isnan()` guard.
3. Each detection step could crash the entire build. Added per-step try/except so individual failures log a WARNING and continue; watermark still advances. 4 dead-code lines removed from port-arrivals endpoint.

**Added:** `GET /api/analytics/fleet-trend?days=30&region=` endpoint aggregating `fleet_density` daily (laden/ballast/unknown/total). Powers new FleetTrendCard area chart in the Overview analytics tab showing 30-day fleet composition trend. 4 new backend tests.

**Improved:** Events page now fetches all events client-side and sorts by severity (dark voyage > position jump > signal lost > loitering > STS > reroute) then time. Per-type counts shown in filter chips; empty-type chips hidden. Limit raised to 500.

**Added:** Events nav badge showing 24h event count (updates every 5 min via `useRecentEventCount` hook). Refreshes automatically.

**Artifacts:** `backend/analytics/build.py` (per-step isolation, numpy fix), `backend/analytics/detect.py` (NaN guard, numpy fix), `backend/app/main.py` (fleet-trend endpoint), `backend/app/schemas.py` (FleetTrendDay, FleetTrendResponse), `backend/tests/test_endpoints.py` (4 new tests), `frontend/src/routes/events.tsx` (severity sort, type counts), `frontend/src/routes/__root.tsx` (event badge), `frontend/src/lib/api.ts` (useRecentEventCount, useFleetTrend, FleetTrendResponse), `frontend/src/routes/analytics/-OverviewCards.tsx` (FleetTrendCard).

---

## 2026-06-12 - Phase 50: Zero-downtime analytics build + vectorized zone detection

**Tried:** Analytics build held an exclusive DuckDB write lock for the entire 7-10 min build window. All analytics API calls returned empty data during that time. Root cause: `_open_analytics()` opened a write connection at the start and held it until the last line.

**Found:** First full run had 91MB AIS DB with 2,697 transit events, 2,723 anchored episodes, 5,184 AIS events, 7,413 vessel states. STS `apply(lambda r: _any_zone...)` was the biggest hot-path (Python row iteration on all slow tanker rows = O(n) with interpreter overhead). After the job finished, analytics page showed real data: 1,404 laden tankers, 1,599 ballast, 319 transits/24h.

**Decision:** Analytics build now writes to `freight_analytics.new.duckdb`, atomically renames it to live at completion (`os.replace` = POSIX rename, atomic on same filesystem). Live DB is never locked during the build. Added `_in_any_zone_vec()` vectorized zone check using numpy broadcasting; replaced `apply` in `sts_candidates` and the per-row loop in `loitering_events`. 311 tests still passing.

**Artifacts:** `backend/analytics/build.py` (`_open_analytics_scratch`, `_commit_scratch`, `run` -> `_run_inner` refactor), `backend/analytics/detect.py` (`_in_any_zone_vec`, vectorized STS/loiter zone checks).

---

## 2026-06-12 - Phase 49: SOTA tabbed analytics layout

**Tried:** Restructured the monolithic analytics page (3,624-line `AnalyticsCharts.tsx`, 41 cards, all in one chunk) into a 6-tab production layout inspired by Kpler/Vortexa/MarineTraffic.

**Found:** Build output confirms 6 distinct rollup chunks (FleetCards 12 kB, OverviewCards 14 kB, ChokepointCards 15 kB, IntelligenceCards 19 kB, RiskCards 23 kB, PortsCargoCards 29 kB) plus a 0.7 kB shared analyticsShared chunk. 311 tests passing. TanStack Router `-` prefix convention suppresses non-route file warnings cleanly.

**Decision:** Code splitting works end-to-end. New ChokepointStatusCard added to Chokepoints tab (live transiting/waiting counts per chokepoint). Sticky KPI bar + deep-linkable ?tab= search param replace the old endless scroll. AnalyticsCharts.tsx deleted.

**Artifacts:** `frontend/src/routes/analytics/-{Overview,Chokepoint,PortsCargo,Risk,Intelligence,Fleet}Cards.tsx`, `frontend/src/components/ui/tabs.tsx`, `frontend/src/routes/analytics/-analyticsShared.tsx`, backend `chokepoint-status` endpoint.

---

## 2026-06-12 - Phases 40-46: Intelligence analytics + fix disappearing vessels

**Tried:** Autonomous SOTA-grade feature sprint. Built Phases 40-46 sequentially: STS offenders ranking, fleet historical snapshots, destination change intelligence, owner fleet risk aggregation, chokepoint throughput anomaly detection, cargo loading/discharge detection, and live-fleet speed anomaly detection. Also diagnosed and fixed the disappearing vessels regression.

**Found:** 300 backend tests passing. Three root causes of vessel disappearing identified: (1) AIS DB write-lock exhaustion causes `db.query` to return empty DataFrame, API returns HTTP 200 `[]`, frontend replaces full vessel list; (2) VesselLayer diff removes all markers on any empty array, even transient ones; (3) SSE stream uses 30-min window but replaces the full cache which is built from a 3-hour window, silently dropping vessels seen 31-180 min ago. Phase 46 had a Pydantic v2 immutability bug - `registry_risk` could not be set post-construction; fixed by building intermediate dicts before constructing Pydantic objects.

**Decision:** All three disappearing-vessel causes fixed: VesselLayer skips diff on empty+existing markers; `useVessels` throws on suspiciously empty response to trigger TanStack Query retry; `useVesselStream` merges updates instead of replacing. Phase 46 endpoint now uses dict-first pattern (build, sort, enrich, then construct Pydantic). `SpeedAnomalyRow` gained `imo` field. MAD-based z-score (factor 1.4826) used for robust segment-peer comparison.

**Artifacts:** New endpoints: `/api/analytics/fleet-at-time`, `/api/analytics/destination-changes`, `/api/analytics/owner-intelligence`, `/api/analytics/chokepoint-anomaly`, `/api/analytics/cargo-state-changes`, `/api/analytics/speed-anomalies`. New frontend cards in Analytics page. 6 new test fixtures added.

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
