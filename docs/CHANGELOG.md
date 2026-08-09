# Freight Hub Changelog

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

## 2026-06-30 (session 9) - MyShipTracking enrichment: persisted voyage history + port calls

**New external enrichment source. myshiptracking.com vessel pages are fully
server-side rendered** (the only XHR calls load ads + a "featured company" box), so a
single `httpx.get` + BeautifulSoup parses them deterministically: no headless browser.
This fills the *movement* gap that Equasis (registry/compliance) and our own
AIS analytics (limited to covered basins, last 90d) both leave open.

What the page yields, confirmed by parsing two live vessels + a saved fixture:
- **Voyage history** ("Last Trips", up to 10, <=3 months): origin/dest ports, departure/
  arrival timestamps, distance, duration, draught, avg/max speed, stop count. Carried in
  rich `data-*` attrs on `td.tbl-ta-3m`. **Immutable once a trip completes.**
- **Port calls**: port, arrival, departure.
- **Current voyage**: destination + ETA (the `.myst-arrival-cont` block carrying the
  `ETA*` label - there are two, origin + destination), nav status, draught.
- **Particulars**: GT/DWT/build/type/flag/call-sign/size (th/td tables).
- **Exact lat/lon**: the position table masks them as `---` for anonymous visitors, but
  they leak in the embedded `contributorMap.php?lat=&lng=` ajax URL.

Design (the user's call): persist by **volatility**, don't cache. Immutable
voyages/port-calls are written once to `mst.duckdb` and never re-scraped (PK
`(mmsi, vkey)` / `(mmsi, pkey)`, INSERT OR IGNORE); only the volatile live-state row is
overwritten each visit. So once a vessel is crawled its history is served instantly from
DuckDB - the original "why a long cache?" was only ever true for the live fields.

- `app/myshiptracking.py`: scraper + pure `parse()` -> `VesselSnapshot` dataclass with
  typed normalizers (`30,201 Tons` -> 30201, `11.1 m` -> 11.1, ETA `(UTC)` stripped).
  `MyShipTrackingBlocked` raised only on a real bot-wall - the login recaptcha widget
  present on every page must NOT read as a block (gated behind `looks_like_vessel_page`).
- `registry/crawl_mst.py`: single writer of `mst.duckdb`. Discovers live MMSIs (filtered
  to valid 9-digit ship MMSIs 2xx-7xx; base stations/AtoN/SAR skipped), priority =
  never-scraped then >7d-stale, cap 80/run, 4-8s sleep. Aborts on a block, mirroring the
  Equasis crawler discipline. `freight-mst.timer` runs it daily at 05:00 (30min after Equasis).
- `GET /api/vessels/{mmsi}/myshiptracking` (read-only from `mst.duckdb`, like the Equasis
  endpoint). 12 tests in `tests/test_myshiptracking.py` against a saved real fixture.
- Note: AIS destination is crew free-text and arrives messy (e.g. `MOERDIIK===`); stored
  faithfully rather than scrubbed.

## 2026-06-28 (session 8) - Dual-basis ETA accuracy scoreboard (expose the conditioning artifact)

**Feature: the True ETA accuracy scoreboard now conditions per-bucket error on
either actual or predicted lead time, switchable in the UI.** The old scoreboard
bucketed every model's error by the *actual* remaining time only, which made the
physics champion look catastrophically optimistic at long range (48h+ bias -50h,
median |err| 50h). That number is a selection artifact: conditioning a signed-error
mean on the true outcome pulls it negative (regression to the mean). Re-bucketing
the identical predictions by the model's *own served ETA* - what a user actually
knows at decision time - reverses the gradient and tells a far more defensible
story: at 48h+ *predicted* lead the physics median |err| is ~17h (not 50h) and the
bias is +16h. Neither single conditioning is "the truth"; the unconditional bias is
~-8h. Serving both, with an in-card explainer, surfaces the artifact instead of
hiding it - squarely on the project's "an ETA you can defend in an interview" bar.

- `eta_model_metrics` gains a `lead_basis` column ('actual' | 'predicted' | 'all')
  in the primary key. `_metric_rows` now emits each per-lead-bucket aggregate twice
  (once per conditioning basis); the unconditional `lead_bucket='all'` rollups are
  basis-independent and tagged 'all', so the Phase-G drift watch (which reads only
  those rows) is unaffected.
- `lead_buckets()`: a vectorized `np.digitize` twin of the scalar `lead_bucket`,
  used to label a whole scored frame by actual and predicted lead in one pass.
- `_ensure_lead_basis()`: idempotent in-place migration. The hourly build copies the
  live DB forward, so `CREATE TABLE IF NOT EXISTS` cannot add the new PK column;
  the migration recreates the table, tagging the 69 runs of existing history
  (per-bucket -> 'actual', overall -> 'all') with zero data loss. Verified on the
  live 2210-row table.
- `GET /api/analytics/eta-accuracy` takes a `lead_basis` param (default 'actual',
  preserving the original framing) and echoes it; it returns the selected basis's
  per-bucket rows plus the basis-independent overall rows. Falls back to the legacy
  by-actual query if the live DB predates the migration, so it never 500s during the
  deploy window before the next build runs.
- Frontend: a "bucket by Actual lead / Predicted lead" toggle on the True ETA
  Accuracy card, a dynamic description, and a footnote explaining the conditioning
  reversal and pointing at the unconditional bias as the headline number.
- Tests: vectorized-bucket parity, dual-basis emission, the migration (column added,
  history preserved, idempotent), and the endpoint's default vs predicted basis.
  Total tests: 466 -> 471.

## 2026-06-28 (session 7) - Freeze the ETA baseline reference; stop the hourly git-tree churn

**Fix: the hourly analytics job no longer mutates git-tracked baseline CSVs.**
`eta_samples.score_baselines` was calling `export_baseline` on every run, so the
hourly `build.py` rewrote `backend/analytics/baselines/{naive,naive+route,physics_v1}_baseline.csv`
each cycle. The working tree was therefore never clean (three permanently-modified
files), and a "baseline" that moves every hour is not a baseline. The metrics already
persist to `eta_model_metrics`, which is what the live scoreboard (`/api/analytics/eta-accuracy`)
reads, so the CSVs were redundant churn.

- `score_baselines(..., export_csv=False)` now gates the CSV write; the hourly path
  (via `run_in_conn`) leaves the reference snapshots frozen and only writes DB metrics.
- The deliberate standalone path (`python -m analytics.eta_samples` -> `run()`) passes
  `export_csv=True` to intentionally refresh the committed reference snapshot.
- Test: `test_score_baselines_csv_export_gated` asserts metrics always persist to the
  DB and the CSVs are rewritten iff `export_csv=True`. Total tests: 465 -> 466.

**Fix: `.gitignore` now ignores the `backend/.venv` symlink.** The pattern had a
trailing slash (`backend/.venv/`), which matches only a real directory; the venv is a
symlink to the data-volume venv, so it leaked into `git status` as untracked. Dropped
the slash so the symlink is ignored. Working tree is now clean.

## 2026-06-28 (session 6d) - Comprehensive Small-segment sweep completion + test coverage

**Fix: Small filter applied to all remaining endpoints with live/analytics data:**
- `high-risk-positions`: live_positions join now excludes Small
- `anomaly-watchlist`: all four ais_events count queries exclude Small (docstring already promised this, SQL was missing the guard)
- `sts-offenders`: ais_events query now excludes Small (docstring promised, SQL was missing)
- `arrivals`: eta_arrivals aggregate excludes Small so river barge port-zone approaches don't rank as ship arrivals
- `eta_serving._load_live()`: Small vessels excluded before ETA computation so they never appear in eta_predictions or eta-upcoming
- `eta-upcoming`: additional defense-in-depth pre-filter via live AIS join; total reduced from ~2048 to ~820 ocean vessels only

**Test: smoke tests added for all 22 previously-untested analytics endpoints:**
Every analytics endpoint now has at least one test verifying 200 OK + correct top-level keys.
Total tests: 442 -> 465.

## 2026-06-28 (session 6c) - Final Small-segment sweep: ais_events + ais_snapshots analytics

**Fix: Small filter applied to remaining ais_events queries:**
sts-risk, shadow-fleet STS detection, event-rate-timeline, and syndication feeds
(`_fetch_events_raw` backing RSS/Atom/JSON) all now filter `segment != 'Small'`.
ARA river barges were inflating STS event counts and appearing in the intelligence feeds.

**Fix: Small filter applied to remaining ais_snapshots analytics queries:**
destination-changes (barge dest changes appeared as route intelligence), cargo-transitions
(barge draught variation detected as cargo load/discharge), event-rate-timeline
(reroutes from river barges counted in volatility signal).

**Fix: STS proximity detector excludes Small vessels:**
River barges in ARA congregate in dense clusters and were generating spurious STS pairs
in the proximity grid scan. Now only ocean-going vessels are considered.

## 2026-06-28 (session 6b) - Complete Small-segment sweep; NaN sanitization; endpoint health

**Fix: ocean_only filter propagated to 5 more endpoints:**
european-inbound (Small AIS tankers appeared as terminal arrivals), slow-steamers
(inland barges distorted segment median SOGs), speed-anomalies (segment MAD distorted),
chokepoint-status live counts, fleet-flags (NL/BE flag counts inflated by ARA barges),
flag-mismatches (inland barge flag mismatches polluted shadow-fleet signal).

**Fix: ocean_only filter propagated to all transit_events and anchored_episodes queries:**
transits, transit-risk, transit-rate-timeline, market-summary transits_24h,
chokepoint-heatmap, chokepoint-anomaly (2 queries), cargo-state-changes, and
chokepoint-status now all filter `segment != 'Small'`. Total: 8 additional SQL fixes.

**Fix: owner-intelligence 500 error from pandas NaN in segment/name fields:**
`live_info.get("segment")` returned pandas float NaN, which is truthy in Python,
causing it to be appended to the segments accumulator and then picked as `top_segment`.
Pydantic v2 rejects float NaN for a `str|None` field. Fixed with `_str_or_none()`.
Same guard applied to transit-risk, shadow-fleet, and eta-upcoming segment/name reads.

**Chore: drop unused @heroicons/react dependency; update ETA baselines with 24h of new samples.**

## 2026-06-28 (session 6) - Systematic ocean-only propagation; AIS coverage disclosure; region selector fixes

**Fix: ocean_only (Small segment) filter propagated across all remaining analytics endpoints:**
Applied `segment != 'Small'` to region-util (ARA 8310 -> 432 ocean vessels), ports
(Rotterdam 376 -> 117), analytics-speed, congestion, anchorage-dwell (via
`_merged_anchored_spans`), laden, fleet-trend, fleet-at-time, density endpoints.
Root cause: the ~6300 ARA inland waterway barges (Small segment) contaminated all
fleet-level metrics when ocean_only was not enforced. Now 16 occurrences of the filter
applied consistently. Tests updated where COASTER (Small) fixture changed expected counts.

**Fix: eta-upcoming P10 clamp bug:** The expression `_fn(p10_orig) and rem - (p50 - p10)`
short-circuited to `0.0` whenever `p10_orig == 0.0` (a Python truthiness trap), returning
incorrect zero instead of the computed remainder. Fixed with explicit `max(0.0, ...)` expression.

**Fix: AIS coverage disclosure in chokepoint dropdowns:** TransitRiskCard and TransitsCard
now annotate Hormuz and Bab el-Mandeb with "(no AIS)" in their selectors and show a clear
"No terrestrial AIS receivers" message instead of a blank card. The existing `has_coverage`
flag on `/api/chokepoints` is now wired into these dropdowns.

**Fix: FleetAtTimeCard region selector used wrong region names:** Options like "hormuz",
"malacca", "dover", "taiwan_strait" didn't match any `ais_snapshots.region` values and
would always return 0 results. Replaced with the 13 actual live-covered regions (ara,
singapore_malacca, dover_channel, bosphorus_dardanelles, etc.).

**Fix: DensityCard region list and default:** Removed uncovered regions (hormuz, bab_el_mandeb)
from DENSITY_REGIONS and added covered ones (ara, japan_korea, us_gulf, cape_good_hope, saldanha_richards_bay).
Changed default from hormuz (no data) to singapore_malacca.

## 2026-06-28 (session 5) - Ocean-only filters, Small-segment event noise, ETA upcoming metadata

**Fix: TransitRiskCard default changed from hormuz to dover_channel:** Hormuz has zero
collector coverage (no terrestrial AIS receivers); dover_channel has 24,764 transits in 14d.

**Fix: market-summary event counts exclude Small segment:** All `ais_events` counts in
`/api/analytics/market-summary` now filter `segment != 'Small'`. Reroutes dropped 691->314
(377 were ARA inland waterway barges, 55% of total). Gaps similarly reduced.

**Fix: reroutes endpoint adds ocean_only=true default:** `/api/analytics/reroutes` excludes
Small segment by default. Toggle via `?ocean_only=false`.

**Fix: /api/events adds ocean_only=true default:** Intelligence event feed now excludes
Small segment vessels by default. Events are now all ocean-going vessels (Aframax, Panamax,
Suezmax, etc.) with meaningful destination changes like port-to-port moves.

**Fix: fleet-at-time uses segment-specific draught thresholds:** The `laden` classification
previously used a flat 5m cutoff for all segments. Now uses `DESIGN_DRAUGHT` table: laden
>= 80%, ballast <= 65% of design. Affects VLCC (22m design), Aframax (14.9m), etc.

**Fix: eta-upcoming vessel metadata (3 bugs):** (1) Wrong column name `timestamp` should be
`updated_ts` in live_positions join - silently caught by except, emptying live_meta. (2)
`FREIGHT_STALE_HOURS` was undefined (should be `db.STALE_HOURS`) - same silent catch.
(3) Missing `laden` column in live_positions - now derived from draught+segment thresholds.
Result: vessel metadata (segment, sog, laden) now populated for 63-66% of upcoming arrivals.
Added 0.25h minimum remaining filter to suppress already-arrived predictions.

## 2026-06-28 (session 4) - Analytics data quality fixes; 06:00 build verified (16m26s)

**06:00 UTC build completed in 16m26s** (1,037,263 samples, 3432 unique route pairs, 3287
live predictions as_of 06:15:21 UTC). Route cache had only 12 misses - warm from prior run.

**Fix: region momentum excludes inland waterway (Small) vessels by default:** Added
`ocean_only=true` parameter (default true) to `/api/analytics/region-momentum`. Without
this filter, ARA's ~6300 inland barge count (vs ~1967 ocean-going) dominated the chart
with delta of -3846, masking meaningful ocean fleet shifts. With the filter, ARA delta
is a readable +182. Toggle added to the card header.

**Fix: crude-on-water excludes Small tanker segment:** Added `crude_only=true` parameter
(default true) to `/api/analytics/crude-on-water`. Small tankers (primarily ARA river
barges) were inflating the estimate by 223 mb at 45k DWT/vessel. With crude_only,
restricted to ULCC/VLCC/Suezmax/Aframax/Panamax (vessels that actually carry crude).

**Perf: vectorize `fleet_density_rows`:** Eliminated the double Python loop
(groupby + iterrows per vessel) in detect.py. Full numpy vectorized computation of
max_seen/design draught lookup, effective max resolution, and laden-ratio classification.
Reduces Python overhead per build for high-density regions like ARA (~8000 vessels/hour).

**Docs: mark Phase F complete** (True ETA vessel-detail popup seam was closed in session 3).

## 2026-06-28 (session 3) - Build time: 17 min; upcoming arrivals card; serving pre-warm

**Total analytics build time: 17 minutes** (down from 2h+ with the killed build 499181).
Root cause of previous slow builds: the legacy `build_predictions` loop called
`cache.distance()` per (vessel, target) pair without pre-warming the route cache,
causing thousands of synchronous searoute calls in the hot path.

**Fix: NA boolean in `vectorized_physics_p50`:** The `laden` column from DuckDB has
`pd.BooleanDtype` with NA values. `to_numpy()` returned pandas NA objects, breaking
`laden == True` comparisons in numpy. Fixed with `to_numpy(dtype=object, na_value=None)`.

**Perf: pre-warm route cache in `build_predictions`:** A new `_candidate_pairs()` first
pass collects all unique (from_cell, target_id) pairs for every live-vessel candidate.
All unique pairs are routed in one batch (no interleaving with the prediction loop), so
the hot path pays only in-memory dict lookups. `cache.flush()` is called once at the end.

**Feature: `/api/analytics/eta-upcoming` endpoint:** Returns predicted inbound vessels
arriving within a configurable horizon (default 96h), computed with remaining hours
relative to now via epoch arithmetic. Supports `target_id` and `target_type` filters.
Sorts by remaining ETA ascending.

**Feature: Upcoming chokepoint arrivals card (Chokepoints tab):** Shows the predicted
vessel queue for each chokepoint (or a specific selected chokepoint) within 24/48/72h.
Displays vessel name, segment, remaining ETA with uncertainty band, and route distance.
In the "all CPs" view, groups by chokepoint sorted by next arrival. Uses physics P50 ETA.

**Perf: replace executemany with bulk insert in `persist_predictions` and `write_metrics_by_target`:**
Minor cleanup for consistency with the `persist_samples` bulk pattern.

**Fix: vessel detail ETA uses absolute arrival timestamp:** The panel was showing
`eta_p50_h` (hours from the prediction `as_of`), which drifts stale between builds.
Now computes `remaining_h = (eta_arrival_ts - now) / 3600` so the ETA display stays
correct regardless of prediction age. Predictions whose arrival has already passed are
hidden from the panel.

**Fix: `analytics_laden` endpoint uses `fleet_density` instead of broken cross-DB join:**
The previous query tried to join `vessel_state` (analytics DB) with `live_positions` (AIS
DB) inside a single DuckDB connection - this silently returned no rows. `fleet_density`
already stores laden/ballast/unknown counts per (region, kind, segment) per hour;
the endpoint now queries the latest hour from that table, giving correct per-segment data.

**Fix: shadow fleet enrichment falls back to `ais_events` for kind/segment:** Dark/spoofing
vessels are often not in `live_positions`, so kind/segment was always null. The event
record always stores these fields at detection time; 50 of 73 shadow fleet vessels now
show their vessel class.

**Test: 3 new tests for `/api/analytics/eta-upcoming`:** Horizon filter (48h returns
only hormuz not rotterdam), target_type filter (chokepoints only), sort order ascending.
(441 tests passing total.)

## 2026-06-28 (session 2) - ETA performance and scoreboard improvements

**Fix: missing per-type rollup in metric rows:** `_metric_rows()` in `eta_backtest.py`
only emitted `lead_bucket='all'` rows for `target_type='all'`. Added the same overall
rollup for `chokepoint` and `port` target types. Enables the new scoreboard filter to
show the overall "all leads" accuracy for each target class.

**UX: target-type filter on ETA accuracy scoreboard:** `EtaAccuracyCard` now has
All/Ports/Chokepoints filter buttons (mirrors the arrival card). Switches the lead-bucket
bar chart and calibration coverage chart to the selected target class.

**Perf: vectorized `enrich_routes`:** The Python loop over all N samples
(typically 1M+) was replaced with: (1) snap all fixes to cells via numpy, (2) deduplicate
to unique (cell, target) pairs (typically ~1920 pairs per 1M rows, a ~500x reduction),
(3) call cache/searoute once per unique pair, (4) merge results back via list comprehension.
Warm-cache runs drop from ~2s to ~0.3s for 1M samples.

**Diagnostic: build scratch commit logging:** Added pre-close and post-close log lines
around `conn.close()` in build.py to diagnose the "scratch file missing" race condition
that caused two consecutive builds (466718, 499181) to lose their results.

## 2026-06-28 - Vectorized ETA scoring + per-target accuracy breakdown + trend chart

**Performance fix:** The analytics build's ETA scoring step was iterating over 1M+
samples in a Python loop (one dict per row, per model), taking hours on the first run
after the target expansion from 72 to 96 targets inflated the route cache rebuild time.
Replaced with vectorized numpy/pandas operations:

- `vectorized_physics_p50(samples)`: single numpy pass over columnar data, ~100x faster
  than the row-by-row `physics_p50()` loop.
- `IntervalModel.fit()` now calls `vectorized_physics_p50` instead of iterating rows.
- `IntervalModel.offsets_batch(pred_h)`: vectorized per-bucket interval lookup via
  `np.digitize`.
- `score_vectorized(samples, model, run_ts, interval)` in `eta_backtest.py`: returns
  both aggregate and per-target metrics in one pass without a second data sweep.
- `score_baselines()` in `eta_samples.py` updated to use `score_vectorized` for all
  three models.

**New feature - per-target accuracy (eta_metrics_by_target):**
- New table `eta_metrics_by_target` (run_ts, model, target_id, n, med_abs_err_h,
  bias_h, mape, p90_abs_err_h, interval_coverage) populated every hourly build for
  naive + physics_v1 models.
- New endpoint `GET /api/analytics/eta-by-target`: physics_v1 rows enriched with naive
  baseline MAE per target, sorted best to worst.
- New `EtaByTargetCard` on the analytics Ports & Cargo tab: type filter
  (port/chokepoint), bar chart showing physics MAE vs naive MAE for the top 15 targets,
  per-target table with improvement %, bias, P90, canal indicator. Shows where the
  physics model wins and where it underperforms naive (negative improvement signals
  edge cases worth investigating).

**New feature - accuracy trend chart:**
- New endpoint `GET /api/analytics/eta-trend`: pivots `eta_model_metrics` on `run_ts`
  for the `all` lead bucket, returning one row per build run with naive_mae,
  route_mae, physics_mae, and n.
- Trend LineChart embedded in `EtaAccuracyCard` (below the calibration bar): shows
  physics vs naive overall MAE over time (one point per day, last run of each day).
  Demonstrates the data flywheel and confirms the model is not regressing as the
  sample set grows toward the ML gate.

**Critical perf fix - persist_samples:** `persist_samples()` was using
`conn.executemany()` with a 1M-row Python list, serializing each row via Python
type conversion. At ~1ms/row this takes ~17-34 minutes per build. Replaced with
DuckDB register-then-bulk-copy: `conn.register("_f", df)` + `INSERT ... SELECT
FROM _f`, completing in ~4 seconds (~250x speedup). Confirmed in benchmark.

**UX: multi-target ETA in vessel popup:** `VesselDetail.tsx` previously showed
only `predictions[0]` (the nearest target). Now shows all resolved targets (up to
3) - nearest with P10-P90 band, secondaries showing P50 + method badge only.

**UX: ML gate progress bar:** Added to `EtaAccuracyCard` - shows days collected
vs the 56-day ML gate, computed from `trendData.points[0]` (no new endpoint).

**Tests:** 4 new tests added (score_vectorized, per-target write roundtrip, eta-
trend endpoint, eta-by-target endpoint). Total: 438 passing.

## 2026-06-27 - Feature: MMSI-derived flag state (FOC / shadow-fleet / mismatch)

**Tried:** Borrowed the one genuinely new idea from the open-source Hormuz tracker
(yasumorishima/hormuz-ship-tracker) teardown - deriving flag state from the MMSI MID.
The existing flag intelligence (`/api/fleet/flag-risk`) is rich (MOU/OFAC) but gated on
the Equasis crawler, which covers only ~13% of live IMOs. The MMSI's first three digits
are the ITU Maritime Identification Digits, which map deterministically to a flag, giving
a free ~100%-coverage flag layer for every AIS vessel.

**Found:** Live, 4079 of 4080 vessels resolved a flag (1 unresolved). Distribution is
realistic (Liberia/Panama/Marshall Islands/Malta lead the FOC flags; 1374 FOC, 130 shadow).
The flag-mismatch signal (MMSI-MID flag vs Equasis registry flag) initially showed 395
"mismatches" because the registry stores ISO3 codes (LBR) and the MID derivation produces
ISO2 (LR); adding an ISO3->ISO2 normalization (`to_iso2`) dropped it to 6 genuine
country disagreements (e.g. Somalia-MMSI vs Cameroon-registry), which are plausible
recent-reflag / obfuscation signals.

**Decision:** Pure derivation lives in `quant_lib.freight.flags` (ITU MID table, ITF
flags-of-convenience set, curated high-shadow-activity set, `flag_from_mmsi`, `to_iso2`).
We categorize *flags* against public lists, never assert a specific vessel is sanctioned -
staying inside the roadmap's "public facts only, no sanctions matching" line. Backend adds
flag fields + `flag`/`foc`/`shadow` filters to `/api/vessels` and two endpoints
(`/api/analytics/fleet-flags`, `/api/analytics/flag-mismatches`). Frontend adds a
vessel-detail flag badge, an Intelligence "Live Fleet by Flag" card, a Risk "Flag
Mismatches" card, and tracker flag-class/flag-state filters. Design spec at
`docs/superpowers/specs/2026-06-27-mmsi-flag-state-design.md`. 14 backend tests added.

**Artifacts:** `quant_lib/freight/flags.py`; backend `flag-state` schemas + endpoints;
frontend `FleetFlagsCard`, `FlagMismatchCard`, flag filter in `FilterControls`.

## 2026-06-27 - Feature: disclose basins with no terrestrial AIS coverage

**Tried:** Investigated the "AIS region coverage gap" (9 of 24 subscribed basins -
Hormuz, Arab Gulf, Bab-el-Mandeb, etc. - empty). Confirmed it is upstream, not a config
bug: all 24 boxes are subscribed with correct coordinates, but aisstream.io's free
terrestrial network has no receivers there. A purpose-built Hormuz tracker on the same
feed hit the same wall (43k positions, zero strait transits). Unfixable without paid
satellite AIS.

**Decision:** Disclose rather than hide. `/api/chokepoints` carries a self-healing
`has_coverage` flag (any snapshot in the trailing 7 days); the map draws dead zones
dashed/muted with a "no terrestrial AIS coverage" label and the Chokepoints tab lists them
in a footnote. Self-heals if a basin ever gets a receiver.

## 2026-06-27 - Fix: Equasis registry crawler kept getting the account locked

Investigating low owner/registry coverage (only 1,926 of 15,235 live IMOs had
`fetch_ok=true`; 13,309 "failed"). The failures were **not** bad IMOs - 99.6% of
the failed IMOs have a valid IMO check digit. A live diagnostic found the real
cause: the Equasis login response carries *"User page download limit reached. Your
account is locked for 7 days."* The crawler was **over-querying and getting the
account locked**, so most "fetches" hit a logged-out page that parses empty.

Two compounding causes: (1) `EquasisClient._is_expired()` flagged *every* page as
logged-out because it keyed on `"authen/HomePage"`/`"j_email"`, strings that appear
in the nav/header of every authenticated page too - so the scraper re-logged-in on
**every single fetch**, tripling request volume; (2) the timer ran every 2h x 200
ships = ~2,400 ships/day, x3 for the re-login = ~7,000 requests/day, far over the
free-account quota. `_login()` also false-positived on `"My Equasis"` (a nav link
present when logged out), so it never noticed the lock.

**Fixes (`app/equasis.py`):** positive ship-page detection (`_looks_like_ship_page`
checks for real markers like "Gross tonnage"/"Registered owner") replaces the
false-positive `_is_expired`, so a re-login happens only on a genuine bounce, not
every fetch. `_is_locked()` detects the lock page; `fetch_ship_info` now raises a
new `EquasisAccountLocked` on it. `_login()` seeds the JSESSIONID from the public
home before posting creds and stops trusting nav-string heuristics. **Crawler
(`registry/crawl.py`):** catches `EquasisAccountLocked` and **aborts the run
immediately** (marking nothing as failed - hammering a locked account only prolongs
the lock and is exactly the abuse the project forbids); per-run cap cut 200 -> 100.
**Timer:** every-2h -> once daily (04:30 UTC), so ~100 ships/day, comfortably under
quota. **API:** `/api/vessels/{imo}/equasis` maps the lock to a graceful 503.

**Tests:** new lock-page fixture + 4 cases (lock detection, ship-page detection,
endpoint 503 on lock, crawler aborts and writes nothing). Suite 420 passing.
**Caveat:** the account is currently locked (~7 days), so the successful-login path
can't be re-verified live until it unlocks; the lock-detection + rate fixes are
verified by tests and the live diagnostic. Historical spurious failures self-heal
as the 7-day retry path re-attempts them under the new sustainable cadence.

## 2026-06-27 - Fix: Port Congestion Monitor + Anchorage Dwell showed zero current vessels

Reported from the live Ports & Cargo tab: every zone in the Port Congestion
Monitor read `Now = 0` / `0.00x LOW` despite large baselines (Rotterdam baseline
893.5 but Now=0). Root cause: both endpoints derived "currently anchored" from
`anchored_episodes WHERE end_ts IS NULL`, but the analytics job **never leaves an
episode open**. Its sliding window (`watermark - 6h overlap`) stores one
continuous anchoring as a *chain* of overlapping ~6h CLOSED fragments, every one
with `end_ts` set to its window's last fix. So `end_ts IS NULL` matched nothing
(0 of 248,581 episodes open) and the "now" count was always zero. The same flaw
made the baseline ~4x too high: it summed `dwell_hours` over all overlapping
fragments (Rotterdam 893.5 vs the true ~204).

**New pure helpers in `analytics/detect.py`** (unit-tested, 11 cases):
`merge_anchored_spans()` collapses a vessel's fragment chain (overlap or within
the 2h episode gap) back into continuous spans with the true start/end;
`current_anchored()` builds on it, reporting a vessel present at the zone of its
single latest span when that span ends within 2h of the freshest episode (a
vessel is never double-counted across zones).

**`/api/analytics/port-congestion`** now reconstructs both sides from merged
spans: current = `current_anchored`, baseline = avg concurrent vessels over the
window **excluding the present** (each span clipped to `[since, max_end - 2h]`,
so the factor stays a current-vs-typical comparison and a zone whose only
presence is right now correctly has no baseline -> factor 1.0). Live result:
Rotterdam now=255 baseline=204 factor=1.25x, Port Said 2.08x (congested), Busan
0.89x (below normal). **`/api/analytics/anchorage-dwell`** uses the same
reconstruction (30-day lookback so long anchorings merge to a true dwell) instead
of the dead open-episode query.

**Tests**: 6 new `current_anchored`/merge cases in `test_detect.py`; updated the
`dwell_client` and `congestion_client` fixtures + assertions to the closed-episode
model (open episodes were never produced in production). Full suite 416 passing.

**Performance (same day):** the first cut merged the chains in a Python loop over
the ~200k fragments in the window - `/api/analytics/port-congestion?days=14` took
**118s**, so the live card never finished loading. Fixed two ways: (1) vectorised
`merge_anchored_spans` as a gaps-and-islands pass (cummax/shift/cumsum) instead of
a per-group loop; (2) moved the merge into DuckDB via a new `_merged_anchored_spans`
window query so only ~15k merged spans cross into Python rather than 200k fragments.
Endpoint now responds in **~0.7s** (a `current_from_spans` helper lets congestion
reuse the spans it already computed for the baseline instead of merging twice).
Verified the live page loads with Playwright (0 console errors).

## 2026-06-27 - Ground-truth arrivals ranking (actual vs stated destination)

Surfaced the `eta_arrivals` table (mined by True ETA Phase A, ~30k closest-approach
arrivals over 14d) as a new public analytic. Until now it was only consumed
internally as ETA training labels; nothing showed *where vessels actually arrived*.
The existing "Live Destination Distribution" card ranks the AIS free-text
*stated* destination (garbage-in, self-reported, often wrong). This new view ranks
the same fleet by where they were *observed* arriving (closest-approach to the 72
resolved chokepoint/port targets, one arrival per voyage episode, deduplicated) -
the honest counterpart, and the distinction a sharp interviewer probes.

**New `GET /api/analytics/arrivals?days=&target_type=&top_n=`** (`ArrivalsResponse`).
Per target over the window: arrival count, distinct-vessel count, laden share
(over arrivals with a known laden signal), dominant vessel segment (DuckDB
`mode()`), and last-seen arrival timestamp; plus window totals (arrivals + distinct
vessels) that respect the `target_type` filter but are *not* capped by `top_n`.
`days` clamped [1,90], `top_n` [1,100], `target_type` in {all,chokepoint,port}
(bad values fold to `all`). Gracefully returns empty (not 500) if `eta_arrivals`
is absent on an older analytics DB.

**Frontend**: new `ActualArrivalsCard` in the Ports & Cargo tab (now 14 cards),
placed right after the stated-destination card for direct contrast. Target-type
and 7/14/30-day toggles; ranked bars coloured by target type (sky=port,
amber=chokepoint) with distinct-vessel count and laden-share badge per row.

**Tests**: new `test_arrivals.py` (5 cases: empty-DB graceful path, ranking +
totals + laden share + dominant segment, target_type filter, window cutoff drops
old arrivals, param clamping). Seeded `eta_targets`/`eta_arrivals` into the
analytics test fixture. Full suite 410 passing.

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
