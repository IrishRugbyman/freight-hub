# MMSI Flag State - Design

**Date:** 2026-06-27
**Status:** Approved (pending implementation plan)

## Problem

The freight hub already has flag-state intelligence (`/api/fleet/flag-risk`,
`FlagRiskCard`), but it is gated on the Equasis registry crawler, which currently
covers only ~13% of live-fleet IMOs. So flag analysis is rich (Paris/Tokyo MOU,
OFAC, risk score) but sparse.

Every AIS vessel broadcasts an MMSI whose first three digits are the ITU Maritime
Identification Digits (MID), which map deterministically to a flag state. We can
therefore derive a flag for ~100% of the live fleet for free, with no crawling.
A spot check found 4004 of 4005 live MMSIs are valid 9-digit ship-station numbers.

This feature adds the cheap, full-coverage flag layer beside the existing rich,
sparse one. Where both exist, the MMSI-vs-registry disagreement is itself a
new signal (recent reflag / possible obfuscation).

## Non-goals (YAGNI)

- No collector or DuckDB schema changes; no migration. Flag is a pure function of
  the MMSI already stored in `live_positions`, derived on read.
- No historical flag backfill.
- No per-vessel sanctions assertion. We categorize flags from public lists; we
  never claim a specific vessel is sanctioned. This honours the roadmap's
  "Deliberately Not Building: external sanctions lists / named-entity matching."
- No paid flag registry or runtime network fetch. All reference data is vendored.
- The existing Equasis `flag-risk` card stays as the authoritative rich layer; this
  is the complementary full-coverage layer, not a replacement.

## Architecture

Three layers, each independently testable:

1. **Reference + derivation** in `quant_lib.freight` (shared domain lib) - pure,
   no I/O.
2. **Serving** in the FastAPI `freight-api` - derives flag on read, exposes two
   new analytics endpoints, extends `/api/vessels`.
3. **Presentation** in the React frontend - vessel-detail badge, Intelligence-tab
   breakdown card, Risk-tab mismatch card, tracker filter.

### Layer 1: `quant_lib.freight` reference data + derivation

New module `quant_lib/freight/flags.py`, re-exported from `quant_lib.freight`.

Constants:

- `MID_COUNTRY: dict[int, tuple[str, str]]`
  ITU MID table. Key = 3-digit MID (int, e.g. `636`). Value =
  `(country_name, iso2_code)` (e.g. `("Liberia", "LR")`). ~450 entries. Vendored
  from the public ITU Table of Maritime Identification Digits. A module-level
  comment cites the source and retrieval date.

- `FLAGS_OF_CONVENIENCE: frozenset[str]`
  ISO2 codes on the ITF Fair Practices Committee flags-of-convenience list
  (~35 flags: PA, LR, MH, MT, BS, CY, ...). Source cited inline.

- `SHADOW_FLEET_FLAGS: frozenset[str]`
  ISO2 codes of flags with high documented shadow-fleet reflagging activity
  (GA Gabon, CM Cameroon, CK Cook Islands, PW Palau, KM Comoros, ...). Sourced
  from public maritime reporting (KSE Institute, Lloyd's List), cited inline.
  Framed strictly as "high shadow-fleet activity," never "sanctioned."

Type:

```python
class Flag(NamedTuple):
    country: str      # "Liberia"
    code: str         # "LR" (ISO2)
    is_foc: bool      # on the ITF FOC list
    is_shadow: bool   # on the curated shadow-fleet list
```

Function:

```python
def flag_from_mmsi(mmsi: int | None) -> Flag | None:
    """Derive flag state from an MMSI via its ITU MID prefix.

    Returns None for non-ship-station MMSIs (leading digit not in 2-7:
    coast stations 00x, SAR aircraft 111, aids-to-nav 99x, craft 98x,
    group 0xx) and for MIDs absent from the table.
    """
```

Behaviour:
- `mid = mmsi // 1_000_000` (top 3 digits of a 9-digit MMSI).
- Resolve only when the MMSI is a ship station: `2 <= mid // 100 <= 7` and the
  MMSI is in `[100_000_000, 999_999_999]`. Otherwise `None`.
- Look up `mid` in `MID_COUNTRY`; unknown MID → `None`.
- `is_foc = code in FLAGS_OF_CONVENIENCE`; `is_shadow = code in SHADOW_FLEET_FLAGS`.

### Layer 2: backend (`freight-api`)

**Schema (`app/schemas.py`).** `Vessel` gains four optional fields, all defaulting
to `None`/`False` so existing tests and clients are unaffected:

```python
flag: str | None = None         # country name
flag_code: str | None = None    # ISO2
flag_foc: bool = False
flag_shadow: bool = False
```

New response models:

```python
class FleetFlagRow(BaseModel):
    flag: str
    flag_code: str | None
    vessel_count: int
    length_sum_m: float          # tonnage proxy (sum of length_m)
    is_foc: bool
    is_shadow: bool
    by_segment: dict[str, int]

class FleetFlagsResponse(BaseModel):
    as_of: str
    total_with_flag: int
    total_unresolved: int        # live vessels whose MMSI did not resolve
    foc_count: int               # vessels under any FOC flag
    shadow_count: int            # vessels under any shadow flag
    rows: list[FleetFlagRow]     # sorted by vessel_count desc

class FlagMismatchRow(BaseModel):
    mmsi: int
    imo: int | None
    name: str | None
    segment: str | None
    mmsi_flag: str               # derived from MID
    mmsi_flag_code: str | None
    registry_flag: str           # from Equasis
    registry_flag_code: str | None

class FlagMismatchResponse(BaseModel):
    as_of: str
    rows: list[FlagMismatchRow]
```

**Endpoints.**

- `GET /api/vessels` - each row enriched via `flag_from_mmsi`. Gains optional
  query params `flag` (ISO2 or country), `foc` (bool), `shadow` (bool) applied
  after derivation. Existing `region`/segment filters unchanged.

- `GET /api/analytics/fleet-flags` - reads live `live_positions`, derives flag per
  row, groups by `flag_code`, returns `FleetFlagsResponse`. Unresolved MMSIs are
  counted in `total_unresolved`, not grouped. Reuses the existing `_live_all()`
  30s cache.

- `GET /api/analytics/flag-mismatches` - reads live `live_positions` (mmsi, imo,
  name, segment), derives MMSI flag, and joins to `vessel_registry` on `imo`
  (`fetch_ok = true AND flag_code IS NOT NULL`). Emits a row only where both codes
  are present and `mmsi_flag_code != registry_flag_code`. Registry is read from
  `db.registry_db_path()`, the same pattern as `fleet_flag_risk`.

### Layer 3: frontend

- `lib/api.ts`: add the four flag fields to the `Vessel` interface; add
  `FleetFlagsResponse` / `FlagMismatchResponse` types and `useFleetFlags()` /
  `useFlagMismatches()` query hooks (60s polling, matching the others).

- **Vessel-detail panel** (tracker): render flag as `country (CODE)` with a small
  badge: amber "FOC" when `flag_foc`, red "shadow" when `flag_shadow`. Hidden when
  no flag resolved.

- **Intelligence tab** (`-IntelligenceCards.tsx`): new `FleetFlagsCard` - the live
  fleet by flag (top N), bar or table with count + tonnage proxy, FOC/shadow rows
  visually marked, header KPIs for `foc_count` / `shadow_count` / `total_unresolved`,
  and a callout with the mismatch count.

- **Risk tab** (`-RiskCards.tsx`): new `FlagMismatchCard` - table of MMSI-vs-registry
  flag disagreements (name, segment, MMSI flag vs registry flag); row click
  navigates to the tracker for that MMSI (mirrors existing card behaviour).

- **Tracker controls** (`routes/index.tsx`): a flag filter with two boolean
  toggles (FOC-only, shadow-only) plus a single-flag dropdown populated from the
  flags present in the current vessel list. Toggles and dropdown combine with AND.
  Filtering is client-side over the already-flagged vessel list; no extra request.

Segment→color and existing UI tokens are reused per `lib/segments.ts` and the
shared analytics helpers; no new color hardcoding.

## Data flow

```
MMSI (live_positions)
   │  flag_from_mmsi()  [quant_lib.freight, pure]
   ▼
Flag(country, code, is_foc, is_shadow)
   ├─► /api/vessels rows ........► tracker map + vessel detail badge + client filter
   ├─► /api/analytics/fleet-flags ► Intelligence FleetFlagsCard
   └─► + vessel_registry (imo) ──► /api/analytics/flag-mismatches ► Risk FlagMismatchCard
```

## Error handling

- `flag_from_mmsi(None)` and malformed/non-ship MMSIs return `None`; vessels then
  carry null flag fields and are counted as `total_unresolved`. No exceptions.
- `fleet-flags` on an empty/locked DB returns an empty response with zero counts
  (reuses `_live_all()` stale-cache fallback).
- `flag-mismatches` returns an empty list when the registry DB is missing/locked
  (registry `db.query` already degrades to an empty DataFrame).
- Unknown MID (valid ship MMSI, MID not in table) → `None`, counted as unresolved;
  logged at debug only to avoid noise.

## Testing

- **quant-lib (`tests/test_flags.py`)**, pure unit tests:
  - Known mappings: `636…` → Liberia/LR/FOC; `538…` → Marshall Islands/FOC;
    `232-235…` → United Kingdom/GB (not FOC); `477…` → Hong Kong.
  - Shadow: a `SHADOW_FLEET_FLAGS` MMSI sets `is_shadow=True`.
  - Non-ship: `00…` (coast), `111…` (SAR), `99…` (aids), `None`, and out-of-range
    integers → `None`.
  - MID boundary: lowest/highest valid MID resolve; a MID gap returns `None`.
- **Backend (`backend/tests/`)**:
  - `/api/vessels` rows carry expected `flag`/`flag_code`/`flag_foc`/`flag_shadow`
    for seeded MMSIs; `?foc=true` and `?flag=LR` filter correctly.
  - `/api/analytics/fleet-flags`: grouping, counts, `total_unresolved` for a seeded
    non-ship MMSI, `foc_count`/`shadow_count`.
  - `/api/analytics/flag-mismatches`: a seeded vessel whose MMSI MID country differs
    from its `vessel_registry.flag_code` appears; a matching one does not; empty
    registry → empty list. Extend `conftest.py` seeds with one mismatch pair.
- **Frontend (vitest)**: if any non-trivial pure helper is added (e.g. flag-filter
  predicate), unit-test it in `lib/`. Card rendering is not unit-tested (matches
  existing convention).

## Reference-data provenance

| Dataset | Source | Notes |
|---|---|---|
| MID → country | ITU Table of Maritime Identification Digits (public) | ~450 entries, vendored as a dict, source + date in comment |
| Flags of convenience | ITF Fair Practices Committee FOC list (public) | ~35 ISO2 codes |
| Shadow-fleet flags | Public maritime reporting (KSE Institute, Lloyd's List) | Curated, "high shadow activity" framing, sources in comment |

All static, free, no runtime fetch. The categorization is of *flags*, never of
specific vessels, keeping the feature inside the project's "public facts only" line.
