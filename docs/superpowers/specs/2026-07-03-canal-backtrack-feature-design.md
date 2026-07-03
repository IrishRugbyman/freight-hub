# Destination Predictor - Canal-Backtrack Feature - Design

**Date:** 2026-07-03
**Status:** Approved (pending implementation plan)

## Problem

The vessel destination predictor (shipped 2026-07-03, `analytics/destination_predict.py`)
scores candidate ports using `gc_dist_nm`, `bearing_align`, `transition_prior`,
`visit_freq`, `target_type`, `segment`. `bearing_align` looks only at the vessel's
*instantaneous* course - it has no memory that the vessel recently committed to a
direction by transiting a canal. A vessel that just cleared Suez northbound is
firmly on the Mediterranean/Atlantic side; a candidate back in the Persian Gulf or
Asia should score far worse than one ahead in Europe, even if a noisy current
heading points vaguely toward both. Today it doesn't - the model has no signal to
distinguish them beyond raw distance.

This is the first phase of a four-part roadmap to improve predictor accuracy
(canal-direction consistency -> trajectory/temporal signal -> operator fleet prior
-> calibration/drift monitoring), sequenced by what's buildable with data already
in hand. See "Deferred" below for why the other three are separate phases.

## Non-goals (YAGNI)

- **Not a hard filter.** No candidate is removed from the frame. The existing
  heuristic/LightGBM scorer never hard-eliminates candidates (even
  `reported_match` is just a feature); a hard filter risks silently dropping the
  true answer on the rare real diversion (Cape of Good Hope / Cape Horn reroutes
  around sanctions, tolls, or draught restrictions).
- **Suez + Panama only**, not all 9 chokepoints. These are the two `is_canal=True`
  targets where the alternate route is a genuine ocean-scale diversion. For
  straits (Dover, Bosphorus, Gibraltar, Hormuz, Malacca, Cape of Good Hope,
  Bab-el-Mandeb) "wrong side" mostly restates a low `bearing_align` and would be
  redundant. Extending to all 9 is a cheap follow-up if this phase proves the
  signal is worth it - not built now.
- **No new data ingestion.** Every input (`transit_events`, `_CHOKEPOINT_GATES`,
  `CHOKEPOINT_AXES`) already exists and is already validated in production.
- **No segment-type gating.** `dest_transitions` is already keyed by
  `(prev_target_id, next_target_id, segment)`, so segment compatibility (an LNG
  carrier vs. a coal terminal) is already implicit in `transition_prior`. A
  separate hard segment filter would be redundant, and no port is tagged by
  cargo type today anyway.
- **No port-depth/DWT-based pruning.** No port-side depth data is ingested
  anywhere in the app (checked: `eta_targets` has no depth column). DWT exists
  per-vessel via Equasis/MST but covers only ~30-40% of live vessels. Building
  this honestly requires ingesting a real port-depth source (e.g. NGA World Port
  Index) - a separate data-ingestion project, deferred to its own future phase.

## Design

### Feature: `canal_backtrack` (binary, 0/1)

Added to the candidate row alongside `reported_match` in
`destination_features.candidate_frame`, and to both the heuristic scorer and the
LightGBM reranker's feature set.

**Computation, per (vessel, candidate) pair:**

1. Look up the vessel's most recent `transit_events` row where
   `chokepoint IN ('suez', 'panama')` and `exited_ts` is within a **21-day**
   recency window of the observation time (`obs_ts` in training,
   "now" in live serving). 21 days comfortably covers a full
   Suez -> NW-Europe or Panama -> Asia leg; older transits are stale and the
   feature reverts to its neutral default rather than penalizing indefinitely.
2. If no qualifying transit exists, `canal_backtrack = 0` for all candidates
   (absence of signal - same convention as `bearing_align`'s neutral default
   when course is unknown).
3. If a qualifying transit exists for chokepoint `C`: using `C`'s axis
   (`CHOKEPOINT_AXES[C]`, lat for Suez / lon for Panama) and gate coordinate
   (`_CHOKEPOINT_GATES[C]`), compute which side of the gate the vessel's
   *current* position is on, and which side the *candidate target* is on.
   `canal_backtrack = 1` if they differ (the candidate would require
   re-transiting the same gate in reverse); else `0`.

### Wiring

- **Heuristic scorer** (`heuristic_raw_score`, `_HEURISTIC_WEIGHTS`): add
  `canal_backtrack` with a new negative weight, tuned like the existing weights
  (hand-picked, documented inline, not fit).
- **LightGBM reranker**: add `canal_backtrack` to `NUMERIC_FEATURES`. No changes
  to `CATEGORICAL_FEATURES`, training procedure, or the champion/challenger
  promotion gate (`train_and_evaluate`, `_MIN_TEST_GROUPS`) - the existing
  held-out walk-forward comparison decides whether this feature earns
  promotion, exactly as it already does for every other feature.
- **Training-set builder** (`build_training_candidates`): must compute
  `canal_backtrack` using only `transit_events` rows with `exited_ts <= obs_ts`
  for that historical observation - never a transit that happens *after* the
  observation being labeled. This is the same leakage discipline the rest of
  the pipeline already applies via the `voyage_id`-grouped, time-based split.

### Data sources (all pre-existing, no schema changes)

- `transit_events` (`freight_analytics.duckdb`, written by `detect.py`'s
  `transit_episodes`): `(mmsi, chokepoint, direction, exited_ts)`.
- `_CHOKEPOINT_GATES` (`analytics/eta_labels.py`): exact gate coordinate per
  chokepoint, e.g. `"suez": (30.50, 32.34)`.
- `CHOKEPOINT_AXES` (`analytics/zones.py`): axis + direction-sign convention per
  chokepoint, e.g. `"suez": ("lat", "northbound", "southbound")`.

## Testing

- `test_destination_features.py`: a vessel with a Suez-northbound transit in the
  last 21 days gets `canal_backtrack=1` against a Persian Gulf candidate and `0`
  against a Rotterdam candidate. Mirror case for Panama. A transit older than 21
  days reverts to `0`. No qualifying transit -> `0` for all candidates.
- `test_destination_predict.py` (training-set builder): a transit dated *after*
  `obs_ts` must not affect `canal_backtrack` for that observation (leakage
  guard).
- Re-run `train_and_evaluate` on production history; report whether the
  challenger's top-1/top-3 accuracy improves enough to earn promotion under the
  existing gate. If it doesn't clear the bar, the champion stays as-is - same
  honest, no-overclaiming posture as the rest of the predictor.

## Deferred (future phases, not in this spec)

1. **Trajectory/temporal signal** - replace single-tick `bearing_align` with a
   trailing window (course stability, bearing trend over recent fixes).
2. **Fleet/operator behavior prior** - operator-level transition prior from
   Equasis ownership data, to help cold-start vessels with thin per-MMSI
   history.
3. **Calibration + drift monitoring** - validate that predicted probabilities
   are calibrated, and add a champion-accuracy-degradation watch mirroring
   `eta_drift.py`. Blocked until enough live prediction-vs-outcome history
   accumulates (the predictor deployed 2026-07-03).
4. **Port-depth/DWT-based candidate pruning** - requires ingesting a real
   port-depth source (e.g. NGA World Port Index); a data-ingestion project in
   its own right.
5. **Extending canal-backtrack to all 9 chokepoints** - cheap if Suez/Panama
   prove the signal earns promotion.
