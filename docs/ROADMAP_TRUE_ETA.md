# Roadmap: True ETA

Forward-looking delivery blueprint for replacing the naive kinematic ETA
(`distance_great_circle / instantaneous_SOG`) with a rigorously validated,
production "true ETA" for the Freight Hub. Completed work moves to
`docs/CHANGELOG.md`; this file only ever describes what is left to build.

---

## Vision

**One sentence.** A defensible vessel-arrival ETA that accounts for the route
ships actually sail (sea lanes, canals, capes), the speed they actually hold,
and the delays they actually hit (canal queues, anchorage waits), served with an
honest confidence interval and a live, public accuracy scoreboard.

**Why this, why now.** The current ETA is `great_circle_distance / current_SOG`.
A backtest against 16 days of reconstructed ground truth (72k samples across 6
chokepoints) showed it is good only at short range and breaks down with lead time:

| Lead | median \|err\| | bias | reading |
|---|--:|--:|---|
| 0-6h | 22 min | ~0 | excellent |
| 6-12h | 1.6h | ~0 | good |
| 12-24h | 7.8h | +2h | weak |
| 24-48h | 22h | **-13.6h** | optimistic, unusable |

Two structural errors cause this: (1) great-circle distance understates any
voyage that rounds a cape or transits a canal, and (2) instantaneous speed with
no congestion model makes 1-2 day forecasts systematically too early. The
aggregate is also flattered by Dover (81% of samples, a short fast strait);
long-haul gates (Suez +8h, Malacca +6h bias) are far worse.

**What's on the line.** This hub is a quant-job portfolio piece. A 13-hour
optimistic ETA at two days out is exactly what a sharp interviewer pokes. The bar
here is not "ship an ETA" - it is "ship an ETA you can defend in an interview":
real routing, real labels, leakage-free validation, calibrated intervals, no
overclaiming, and a visible honest baseline comparison. Rigor is the deliverable.

**Honest constraints (these shape every phase).**
- **History is ~16 days and cannot be backfilled.** The collector runs 24/7 so
  it grows daily, but ML must be *gated* on accumulated clean history. The
  physics model must carry production until the data earns the ML model.
- **The AIS `destination` free-text is garbage-in.** True ETA is computed to a
  *resolved* target only: the 9 chokepoint zones (geometric, no text needed) and
  the curated port/anchorage zones that `_match_eur_port` / terminal lists
  already map. We never trust a raw destination string as an ETA target.
- **DuckDB single-writer.** The collector owns `ais_positions.duckdb`, the
  analytics job owns `freight_analytics.duckdb`. The API writes nothing. All new
  tables live in `freight_analytics.duckdb` and are produced by the batch job.
- **No synthetic data.** Labels are reconstructed from real AIS arrivals; routes
  from a real marine network; speed priors from real AIS distributions.

**Not building (v1).** See the bottom section - weather/ERA5 routing, traffic
interaction, paid AIS, and destination-string NLP are all explicitly out.

---

## Stack

Mostly fixed by the existing app. Additions are marked.

| Layer | Choice | Why |
|---|---|---|
| Label/ feature batch | Python in `backend/analytics/` (extends `build.py`) | Same job that owns `freight_analytics.duckdb`; single writer preserved |
| Sea routing | **`searoute` (PyPI, +new dep)** with a vendored marnet GeoJSON fallback | Free marine-network shortest path that respects canals/capes; no paid API |
| Speed/ delay model | NumPy/pandas + constants in a new `quant_lib.freight` submodule | Reuse the shared freight domain lib; testable pure functions |
| ML | **LightGBM quantile regression (+new dep)** via `quant-lib[ml]` | Already used in `battery-dispatch`; gradient boosting fits tabular voyage features; quantile objective gives P10/P50/P90 |
| Validation | `quant_lib.validation` + a new `eta_backtest.py` harness | Walk-forward, leakage-free, baseline-relative scoring |
| Serving | FastAPI `freight-api` (existing) | New `/api/analytics/eta*`; integrate into inbound/lng/chokepoint endpoints |
| Frontend | React 19 + Recharts (existing) | ETA + interval + method badge in cards; accuracy scoreboard page |
| Scheduling | systemd timer (mirrors `energy-refresh.timer`) | Nightly label refresh + gated retrain |

---

## Data Model

All new tables in `freight_analytics.duckdb`, written by the analytics batch job.

### eta_targets   *(static, seeded once; the only legal ETA destinations)*
  target_id        TEXT  PRIMARY KEY     -- e.g. "cp:suez", "port:rotterdam"
  target_type      TEXT                  -- 'chokepoint' | 'port'
  name             TEXT
  lat, lon         DOUBLE                -- centroid used as the ETA point
  reach_nm         DOUBLE                -- arrival radius (half bbox diagonal)
  is_canal         BOOLEAN               -- adds transit/queue dwell

### eta_arrivals   *(ground truth, mined from ais_snapshots)*
  mmsi             BIGINT
  target_id        TEXT
  arrival_ts       TIMESTAMP             -- closest-approach time to target point
  min_dist_nm      DOUBLE
  segment          TEXT
  laden            BOOLEAN
  approach_start_ts TIMESTAMP            -- first qualifying approach fix
  PRIMARY KEY (mmsi, target_id, arrival_ts)

### eta_samples   *(training rows: one per (approach, observation))*
  mmsi, target_id, arrival_ts           -- FK to eta_arrivals (the voyage group)
  obs_ts           TIMESTAMP
  remaining_h      DOUBLE                -- LABEL: hours from obs_ts to arrival_ts
  -- features --
  route_dist_nm    DOUBLE               -- sea-route distance (Phase B)
  gc_dist_nm       DOUBLE               -- great-circle (baseline + feature)
  sog              DOUBLE               -- instantaneous
  sog_trail6h      DOUBLE               -- trailing median speed
  service_speed    DOUBLE               -- segment prior
  segment          TEXT
  laden            BOOLEAN
  draught          DOUBLE
  target_type      TEXT
  is_canal         BOOLEAN
  dest_queue_h     DOUBLE               -- expected anchorage wait at target
  approach_bearing DOUBLE
  voyage_id        BIGINT               -- = hash(mmsi,target_id,arrival_ts); split key

### eta_route_cache   *(memoized sea-route distances)*
  from_cell        TEXT                  -- snapped 0.25deg grid cell of origin
  target_id        TEXT
  route_dist_nm    DOUBLE
  computed_ts      TIMESTAMP
  PRIMARY KEY (from_cell, target_id)

### eta_predictions   *(live serving snapshot, rewritten each run)*
  mmsi, target_id, as_of TIMESTAMP
  eta_p50_h, eta_p10_h, eta_p90_h DOUBLE
  method           TEXT                  -- 'ml' | 'physics' | 'naive'
  eta_arrival_ts   TIMESTAMP

### eta_model_metrics   *(one row per backtest/retrain run)*
  run_ts TIMESTAMP, model TEXT, lead_bucket TEXT, target_type TEXT,
  n INT, med_abs_err_h, bias_h, mape, p90_abs_err_h, interval_coverage DOUBLE

### Relationships
- `eta_arrivals` belongs to one `eta_targets` via `target_id`.
- `eta_samples` belong to one `eta_arrivals` voyage via `(mmsi,target_id,arrival_ts)`; `voyage_id` is the train/test split unit (no voyage spans the split).
- `eta_predictions` / `eta_model_metrics` are serving + monitoring outputs.

---

## Phases

### Phase A - Ground truth + baseline harness [COMPLETE 2026-06-25]

---

### Phase B - Sea-route distance [COMPLETE 2026-06-25]

---

### Phase C - Physics ETA v1 (production model) [COMPLETE 2026-06-26]

---

### Phase D - ML ETA (LightGBM quantile) - GATED on history
*Goal: a learned model that beats physics where data is dense, served only if it earns it on a leakage-free walk-forward test.*
*Depends on: C and >= a minimum clean-history threshold (target: >= 8 weeks and >= N voyages/target). Until the gate passes, physics stays champion - this is a deliberate hold, not a blocker.*
*Estimated effort: 2-3 sessions when unlocked.*

**What's new.** Three LightGBM quantile regressors (alpha 0.1/0.5/0.9) on
`eta_samples`, with champion/challenger promotion against physics.

**Database changes.** None new; consumes `eta_samples`, writes `eta_model_metrics`.

**Infrastructure.** Add `lightgbm` dep; model artifact under
`backend/analytics/models/eta_lgbm_{quantile}.txt` (committed or rebuilt by timer).

**Task checklist.**
- Data Layer
  - [ ] Feature matrix from `eta_samples`; document each feature; drop anything leaky (nothing derived from future fixes).
- ML
  - [ ] Train P10/P50/P90 LightGBM; **time-based split** with `voyage_id` grouping so no voyage crosses train/test.
  - [ ] Champion/challenger: promote ML to `method='ml'` only per-segment/lead-bucket where it beats physics on held-out median |err| AND interval coverage stays in [0.75, 0.85].
- Validation
  - [ ] Walk-forward across the available weeks; feature importance sanity (route_dist, effective speed should dominate - if `destination`-ish junk leaks in, stop).
  - [ ] Calibration plot data into `eta_model_metrics` (`interval_coverage`).
- Testing & Polish
  - [ ] pytest: training is deterministic on a seed; predictor loads artifact and returns monotone quantiles (P10<=P50<=P90).

**Definition of done.** A documented, reproducible walk-forward shows ML beating physics on the dense targets without leakage; calibrated intervals; champion map persisted.

---

### Phase E - Serving + API
*Goal: live vessels get a true ETA with an interval and an explicit method, through one endpoint and inside the existing inbound/LNG/chokepoint cards.*
*Depends on: C (D optional). Estimated effort: 1-2 sessions.*

**Database changes.** `eta_predictions` rewritten each analytics run.

**API routes.**
- `GET /api/analytics/eta?mmsi=` -> P50 + [P10,P90] + method + arrival_ts for a vessel's resolvable targets.
- Integrate true ETA into `/api/analytics/european-inbound`, `/api/analytics/lng-inbound`, and the chokepoint-arrivals endpoint (replace `eta_hours`, keep it as `eta_naive_h` for transparency).

**Task checklist.**
- Data Layer
  - [ ] Live scorer in the analytics run: for each underway vessel + resolvable target, fallback chain **ml -> physics -> naive**, write `eta_predictions`.
- API
  - [ ] `runner_eta.py` read layer (mirrors `runner_routes.py`); Pydantic schemas with `method`, `eta_low_h`, `eta_high_h`.
  - [ ] Wire into the three existing inbound endpoints; bucket by P50; expose both true and naive for honesty.
- Testing & Polish
  - [ ] pytest endpoints over seeded `eta_predictions`; assert fallback labelling and interval ordering.

**Definition of done.** `/api/analytics/eta` returns calibrated true ETAs live; inbound/LNG cards consume them; naive value still visible.

---

### Phase F - Frontend: ETA + interval + accuracy scoreboard
*Goal: the showcase visibly demonstrates the upgrade - ETAs show a confidence band and method, and a public scoreboard proves accuracy vs the naive baseline.*
*Depends on: E. Estimated effort: 1-2 sessions.*

**Frontend.**
- Inbound/LNG/chokepoint cards: show `P50 (P10-P90)`, a small `ml`/`physics` method badge, and keep the honest "vs naive" delta on hover.
- New **ETA Accuracy** panel (Analytics tab or its own route): rolling `eta_model_metrics` - median |err| and interval coverage by lead bucket and target, plus the naive baseline line. This is the credibility centerpiece.
- Vessel detail popup: ETA to its nearest resolvable target with interval.

**Task checklist.**
- Frontend
  - [ ] `lib/api.ts` types + hooks for `/api/analytics/eta` and a metrics endpoint.
  - [ ] ETA chip component (P50 + band + method badge); reuse `lib/segments.ts` color conventions, no hardcoded colors.
  - [ ] Accuracy scoreboard with Recharts; baseline vs model series; honest "starts at collection date" note.
- Testing & Polish
  - [ ] vitest for the ETA chip formatting + bucket logic; `npm run build` clean; quick visual check.

**Definition of done.** Cards render true ETA + interval + method; scoreboard live; build + vitest green; visual check passes.

---

### Phase G - Retraining + monitoring
*Goal: the model stays honest and improves as history grows, automatically.*
*Depends on: D, E, F. Estimated effort: 1 session.*

**Infrastructure.** `eta-refresh.timer` (systemd, mirrors `energy-refresh.timer`):
nightly label mine + metrics refresh; weekly gated retrain + auto-promote
challenger only if it beats champion on the latest walk-forward.

**Task checklist.**
- Infra
  - [ ] systemd timer + unit; document in `freight/CLAUDE.md` services table.
  - [ ] Drift watch: alert (log + Events feed entry) if rolling median |err| or interval coverage degrades past a threshold.
- Testing & Polish
  - [ ] Dry-run the retrain path on current data; confirm no-promote when challenger loses.

**Definition of done.** Timer installed; a retrain cycle runs end-to-end and correctly keeps the better model; monitoring emits on degradation.

---

## Build Order

| Phase | Goal | New tables | New routes | Sessions | Gate |
|---|---|---|---|---|---|
| A | Ground truth + harness | eta_targets, eta_arrivals | 0 | 1-2 | - |
| B | Sea-route distance | eta_route_cache, eta_samples | 0 | 1-2 | - |
| C | Physics ETA v1 (ship it) | - | 0 | 2 | - |
| D | ML quantile ETA | - | 0 | 2-3 | >=8wk history |
| E | Serving + API | eta_predictions | +1 +3 wired | 1-2 | C |
| F | Frontend + scoreboard | - | +1 metrics | 1-2 | E |
| G | Retrain + monitor | eta_model_metrics (live) | 0 | 1 | D,E,F |

**Critical path to a shippable upgrade is A -> B -> C -> E -> F** (physics, no ML).
D and G layer learning + automation on top once history justifies them.

---

## Schema Evolution Map

| Table | A | B | C | D | E | F | G |
|---|---|---|---|---|---|---|---|
| eta_targets | ✓ | | | | | | |
| eta_arrivals | ✓ | | | | | | |
| eta_route_cache | | ✓ | | | | | |
| eta_samples | | ✓ (dist) | +features | | | | |
| eta_predictions | | | | | ✓ | | |
| eta_model_metrics | ✓ (naive) | +route | +physics | +ml | | | live |

---

## API Surface Map

| Route | Phase | Auth | Purpose |
|---|---|---|---|
| GET /api/analytics/eta | E | - | True ETA + interval + method for a vessel |
| GET /api/analytics/european-inbound | E | - | Now carries true ETA + `eta_naive_h` |
| GET /api/analytics/lng-inbound | E | - | Now carries true ETA + `eta_naive_h` |
| GET /api/analytics/eta-accuracy | F | - | Rolling `eta_model_metrics` for the scoreboard |

---

## Deliberately Not Building (v1)

- **Weather-routed ETA (ERA5 winds/waves).** Real signal, but a second model on
  top; revisit after physics+ML prove out. ERA5 is available but adds heavy
  feature plumbing.
- **Traffic-interaction / port-berth scheduling.** We model anchorage wait
  statistically, not berth allocation - that needs data we do not have.
- **Destination-string NLP geocoding.** The free-text field stays untrusted; we
  only ETA to resolved targets. (This supersedes the old ROADMAP "not building
  ETA" line: we *are* building ETA, but only to geometric/known targets.)
- **Paid/satellite AIS.** Against the free-source ethos; coverage gaps are
  disclosed, not bought around.
- **History backfill.** Impossible; the scoreboard starts at collection date and
  says so. The ML gate exists precisely because of this.
- **Per-tick live recompute.** ETAs refresh on the analytics-job cadence, not per
  AIS message.

---

## Decisions taken (no need to ask)

- **Targets = chokepoints + curated ports only** (not raw destinations) - the
  only way to a "true" ETA given dirty `destination` text.
- **Physics ships first, ML is gated on history** - honest given 16 days of data;
  avoids an overfit model masquerading as rigor.
- **Quantile intervals (P10/P50/P90)** over point estimates - an interval you can
  defend beats a precise-looking single number.
- **Leakage control via `voyage_id` grouped, time-based split** - the one thing an
  interviewer will check first.
- **`searoute` (free) for routing**, vendored marnet fallback - no paid deps, no
  runtime network dependency.
