"""Pure detection functions for freight analytics.

All functions operate on pandas DataFrames; they read nothing from disk.
Each function returns a list of dicts ready for INSERT OR REPLACE into the
analytics DB. Keeping them pure makes unit testing tractable.

Expected input schema (subset of ais_snapshots columns):
  mmsi BIGINT, snapshot_ts TIMESTAMP, lat DOUBLE, lon DOUBLE,
  sog DOUBLE, nav_status INTEGER, draught DOUBLE,
  kind VARCHAR, segment VARCHAR, region VARCHAR, destination VARCHAR
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from math import atan2, cos, radians, sin, sqrt

import pandas as pd

from .zones import ANCHORAGE_ZONES, CHOKEPOINT_AXES, DESIGN_DRAUGHT, REGIONS

# --- constants ---------------------------------------------------------------

_CHOKEPOINT_REGIONS = set(CHOKEPOINT_AXES.keys())

# Transit: minimum fixes and displacement to distinguish a transit from mere presence
_MIN_TRANSIT_FIXES = 2
_MIN_DISPLACEMENT_DEG = 0.3

# Gap that splits one episode into two separate episodes
_EPISODE_GAP_H = 2.0

# Anchored episode minimum duration
_MIN_ANCHOR_HOURS = 2.0

# Speed threshold for "stopped / anchored" inference (knots)
_SOG_ANCHOR_KN = 0.5

# --- Phase 3 constants -------------------------------------------------------

# AIS gap: minimum fixes in last 48h to consider a vessel "active", min silence, min last SOG
_GAP_MIN_FIXES = 6
_GAP_MIN_SILENCE_H = 6.0
_GAP_MIN_SOG_KN = 2.0
_GAP_EDGE_MARGIN_DEG = 0.4  # must be this far from region bbox edge

# Loitering: minimum episode duration, max mean SOG, min margin from region edge
_LOITER_MIN_HOURS = 12.0
_LOITER_MAX_SOG_KN = 1.0
_LOITER_EDGE_MARGIN_DEG = 0.2

# STS: max distance between two stopped tankers, min co-location duration
_STS_MAX_DIST_M = 500
_STS_MIN_HOURS = 2.0

# --- helpers -----------------------------------------------------------------


def _split_by_gap(group: pd.DataFrame, gap_hours: float) -> list[pd.DataFrame]:
    """Split a time-sorted group into episodes wherever the inter-fix gap > gap_hours."""
    if group.empty:
        return []
    threshold = timedelta(hours=gap_hours)
    diffs = group["snapshot_ts"].diff()
    split_idx = [0] + list(group.index[diffs > threshold]) + [None]
    # convert positional boundaries
    pos = group.index.tolist()
    boundaries: list[int] = []
    for si in split_idx[1:-1]:
        boundaries.append(pos.index(si))
    boundaries = [0] + boundaries + [len(pos)]
    return [group.iloc[boundaries[i] : boundaries[i + 1]] for i in range(len(boundaries) - 1)]


def _in_zone(lat: float, lon: float, zone_key: str) -> bool:
    (lat_min, lon_min), (lat_max, lon_max) = ANCHORAGE_ZONES[zone_key]
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _any_zone(lat: float, lon: float) -> str | None:
    for name in ANCHORAGE_ZONES:
        if _in_zone(lat, lon, name):
            return name
    return None


# --- laden/ballast status -----------------------------------------------------


def laden_status(draught: float | None, max_seen: float | None, segment: str | None) -> str:
    """Return 'laden', 'ballast', or 'unknown' for a single vessel reading.

    Uses max_seen as a proxy for design draught. Falls back to DESIGN_DRAUGHT table
    when history is too shallow (max_seen < 70% of design draught).
    """
    if draught is None or draught <= 0:
        return "unknown"

    design = DESIGN_DRAUGHT.get(segment or "", None) if segment else None

    effective_max = max_seen if (max_seen and max_seen > 0) else None

    # Use design draught as effective_max when history is shallow
    if effective_max is None or (design and effective_max < 0.7 * design):
        effective_max = design

    if effective_max is None or effective_max <= 0:
        return "unknown"

    ratio = draught / effective_max
    if ratio >= 0.8:
        return "laden"
    if ratio <= 0.65:
        return "ballast"
    return "unknown"


# --- transit detection -------------------------------------------------------


def transit_episodes(df: pd.DataFrame) -> list[dict]:
    """Detect chokepoint transit events from a snapshot DataFrame.

    Filters to rows where region is one of the 9 chokepoints, groups by
    (mmsi, chokepoint), splits episodes by 2h gaps, and qualifies transits
    by the minimum-displacement rule.

    Returns a list of dicts compatible with the transit_events table schema:
      mmsi, chokepoint, entered_ts, exited_ts, direction, kind, segment, laden
    """
    if df.empty or "region" not in df.columns:
        return []

    cp_df = df[df["region"].isin(_CHOKEPOINT_REGIONS)].copy()
    if cp_df.empty:
        return []

    cp_df = cp_df.sort_values("snapshot_ts")
    results: list[dict] = []

    for (mmsi, chokepoint), grp in cp_df.groupby(["mmsi", "region"], sort=False):
        axis, pos_label, neg_label = CHOKEPOINT_AXES[chokepoint]
        episodes = _split_by_gap(grp.reset_index(drop=True), _EPISODE_GAP_H)

        for ep in episodes:
            if len(ep) < _MIN_TRANSIT_FIXES:
                continue
            first = ep.iloc[0]
            last = ep.iloc[-1]

            if axis == "lat":
                displacement = float(last["lat"]) - float(first["lat"])
            else:
                displacement = float(last["lon"]) - float(first["lon"])

            if abs(displacement) < _MIN_DISPLACEMENT_DEG:
                continue

            direction = pos_label if displacement > 0 else neg_label

            # Laden status: use draught from last fix and max seen in the episode
            draughts = ep["draught"].dropna() if "draught" in ep.columns else pd.Series([], dtype=float)
            last_draught = float(draughts.iloc[-1]) if not draughts.empty else None
            max_draught = float(draughts.max()) if not draughts.empty else None
            segment = str(first.get("segment", None) or "")
            laden = laden_status(last_draught, max_draught, segment or None)

            results.append(
                {
                    "mmsi": int(mmsi),
                    "chokepoint": chokepoint,
                    "entered_ts": first["snapshot_ts"],
                    "exited_ts": last["snapshot_ts"],
                    "direction": direction,
                    "kind": first.get("kind", None),
                    "segment": segment or None,
                    "laden": laden == "laden",  # bool for DB column; unknown -> False
                }
            )

    return results


# --- anchored episode detection ----------------------------------------------


def anchored_episodes(df: pd.DataFrame) -> list[dict]:
    """Detect anchored episodes inside known anchorage zones.

    A fix is anchored when nav_status IN (1, 5) OR sog < SOG_ANCHOR_KN.
    Consecutive anchored fixes inside the same zone form an episode.
    Episodes shorter than MIN_ANCHOR_HOURS are discarded.

    Returns a list of dicts compatible with the anchored_episodes table schema:
      mmsi, zone, start_ts, end_ts, kind, segment
    """
    if df.empty:
        return []

    df = df.sort_values(["mmsi", "snapshot_ts"])
    results: list[dict] = []

    for mmsi, grp in df.groupby("mmsi", sort=False):
        grp = grp.reset_index(drop=True)

        # Determine anchored flag per row
        nav = grp["nav_status"] if "nav_status" in grp.columns else pd.Series([None] * len(grp))
        sog = grp["sog"] if "sog" in grp.columns else pd.Series([None] * len(grp))

        anchored = (nav.isin([1, 5])) | (sog.fillna(999) < _SOG_ANCHOR_KN)

        # Find the zone for each anchored fix
        zones_col: list[str | None] = []
        for i, row in grp.iterrows():
            if not anchored.iloc[i]:
                zones_col.append(None)
            else:
                zones_col.append(_any_zone(float(row["lat"]), float(row["lon"])))

        grp = grp.copy()
        grp["_zone"] = zones_col

        # Group consecutive rows with the same non-None zone
        active_rows: list[int] = []
        active_zone: str | None = None

        def _flush(rows: list[int], zone: str) -> None:
            if not rows:
                return
            ep = grp.iloc[rows]
            duration = (ep["snapshot_ts"].iloc[-1] - ep["snapshot_ts"].iloc[0]).total_seconds() / 3600
            if duration >= _MIN_ANCHOR_HOURS:
                results.append(
                    {
                        "mmsi": int(mmsi),
                        "zone": zone,
                        "start_ts": ep["snapshot_ts"].iloc[0],
                        "end_ts": ep["snapshot_ts"].iloc[-1],
                        "kind": ep["kind"].iloc[0] if "kind" in ep.columns else None,
                        "segment": ep["segment"].iloc[0] if "segment" in ep.columns else None,
                    }
                )

        for i in range(len(grp)):
            z = grp["_zone"].iloc[i]
            if z == active_zone and z is not None:
                active_rows.append(i)
            else:
                if active_zone is not None and active_rows:
                    # Check for gap > 2h before appending (episode already split by continuity)
                    _flush(active_rows, active_zone)
                active_rows = [i] if z is not None else []
                active_zone = z

        if active_zone is not None and active_rows:
            _flush(active_rows, active_zone)

    return results


# --- fleet density snapshot --------------------------------------------------


def fleet_density_rows(df: pd.DataFrame, ts: pd.Timestamp, vessel_states: dict) -> list[dict]:
    """Compute laden/ballast counts per (region, kind, segment) for a snapshot timestamp.

    vessel_states: {mmsi: {'max_draught_seen': float, 'laden': str}} from analytics DB.
    Returns list of dicts for fleet_density table.
    """
    if df.empty or "region" not in df.columns:
        return []

    rows = []
    grouped = df.groupby(["region", "kind", "segment"], sort=False)
    for (region, kind, segment), grp in grouped:
        laden_count = ballast_count = unknown_count = 0
        for _, row in grp.iterrows():
            mmsi = int(row["mmsi"])
            state = vessel_states.get(mmsi, {})
            max_seen = state.get("max_draught_seen")
            draught = row.get("draught") if "draught" in grp.columns else None
            if pd.isna(draught) if draught is not None else True:
                draught = None
            status = laden_status(draught, max_seen, str(segment) if segment else None)
            if status == "laden":
                laden_count += 1
            elif status == "ballast":
                ballast_count += 1
            else:
                unknown_count += 1
        rows.append(
            {
                "ts": ts,
                "region": region,
                "kind": kind,
                "segment": segment,
                "laden_count": laden_count,
                "ballast_count": ballast_count,
                "unknown_count": unknown_count,
            }
        )
    return rows


# --- Phase 3 helpers ---------------------------------------------------------


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres."""
    R = 6_371_000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlam = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _event_id(type_: str, mmsi: int, start_ts: object, mmsi2: int | None = None) -> str:
    ts_str = start_ts.isoformat() if hasattr(start_ts, "isoformat") else str(start_ts)
    key = f"{type_}|{mmsi}|{mmsi2 or ''}|{ts_str}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _in_region_interior(lat: float, lon: float, region: str, margin: float) -> bool:
    """Return True if (lat, lon) is inside the region bbox shrunk by `margin` deg."""
    if region not in REGIONS:
        return False
    bbox = REGIONS[region]
    lat_min, lon_min = bbox[0]
    lat_max, lon_max = bbox[1]
    return (
        lat_min + margin <= lat <= lat_max - margin
        and lon_min + margin <= lon <= lon_max - margin
    )


# --- AIS gap detection -------------------------------------------------------


def ais_gap_events(df: pd.DataFrame, max_ts: pd.Timestamp) -> list[dict]:
    """Detect vessels that were active (>= 6 fixes in last 48h) but went silent > 6h.

    Only fires for vessels whose last fix is > 0.4 deg inside their region bbox
    (to avoid false positives from vessels sailing out of terrestrial coverage).
    Last SOG must be > 2 kn (vessel was underway, not anchored).

    Returns a list of dicts compatible with the ais_events table schema.
    """
    if df.empty:
        return []

    results: list[dict] = []
    gap_cutoff = max_ts - timedelta(hours=_GAP_MIN_SILENCE_H)

    for mmsi, grp in df.groupby("mmsi", sort=False):
        grp = grp.sort_values("snapshot_ts")
        last = grp.iloc[-1]
        last_ts = last["snapshot_ts"]

        if last_ts >= gap_cutoff:
            continue

        if len(grp) < _GAP_MIN_FIXES:
            continue

        last_sog = last.get("sog")
        if last_sog is None or pd.isna(last_sog) or float(last_sog) < _GAP_MIN_SOG_KN:
            continue

        region = last.get("region")
        if not region:
            continue

        lat, lon = float(last["lat"]), float(last["lon"])
        if not _in_region_interior(lat, lon, region, _GAP_EDGE_MARGIN_DEG):
            continue

        start_ts = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        silence_h = (max_ts.to_pydatetime() - start_ts).total_seconds() / 3600

        results.append(
            {
                "event_id": _event_id("gap", int(mmsi), start_ts),
                "type": "gap",
                "mmsi": int(mmsi),
                "mmsi2": None,
                "start_ts": start_ts,
                "end_ts": start_ts,
                "lat": lat,
                "lon": lon,
                "region": region,
                "kind": last.get("kind"),
                "segment": last.get("segment"),
                "details": json.dumps(
                    {
                        "silence_hours": round(silence_h, 1),
                        "last_sog": round(float(last_sog), 1),
                        "fix_count_48h": len(grp),
                    }
                ),
            }
        )

    return results


# --- Loitering detection -----------------------------------------------------


def loitering_events(df: pd.DataFrame) -> list[dict]:
    """Detect vessels drifting for >= 12h with mean SOG < 1 kn, outside anchorages.

    Excludes vessels inside anchorage zones or within 0.2 deg of the region bbox edge
    (to avoid attributing coverage-edge behaviour as loitering).

    Returns a list of dicts compatible with the ais_events table schema.
    """
    if df.empty:
        return []

    results: list[dict] = []

    for mmsi, grp in df.groupby("mmsi", sort=False):
        grp = grp.sort_values("snapshot_ts").reset_index(drop=True)
        episodes = _split_by_gap(grp, _EPISODE_GAP_H)

        for ep in episodes:
            if len(ep) < 2:
                continue

            ep_sog = ep["sog"].fillna(999) if "sog" in ep.columns else pd.Series([999] * len(ep))
            if ep_sog.mean() >= _LOITER_MAX_SOG_KN:
                continue

            duration_h = (
                ep["snapshot_ts"].iloc[-1] - ep["snapshot_ts"].iloc[0]
            ).total_seconds() / 3600
            if duration_h < _LOITER_MIN_HOURS:
                continue

            # All fixes must be outside anchorage zones AND in region interior
            skip = False
            for i in range(len(ep)):
                fix = ep.iloc[i]
                lat, lon = float(fix["lat"]), float(fix["lon"])
                if _any_zone(lat, lon) is not None:
                    skip = True
                    break
                region = fix.get("region")
                if not region or not _in_region_interior(lat, lon, region, _LOITER_EDGE_MARGIN_DEG):
                    skip = True
                    break
            if skip:
                continue

            start_ts_raw = ep["snapshot_ts"].iloc[0]
            start_ts = start_ts_raw.to_pydatetime() if hasattr(start_ts_raw, "to_pydatetime") else start_ts_raw
            end_ts_raw = ep["snapshot_ts"].iloc[-1]
            end_ts = end_ts_raw.to_pydatetime() if hasattr(end_ts_raw, "to_pydatetime") else end_ts_raw

            mid = ep.iloc[len(ep) // 2]
            region = str(mid.get("region") or "")

            results.append(
                {
                    "event_id": _event_id("loiter", int(mmsi), start_ts),
                    "type": "loiter",
                    "mmsi": int(mmsi),
                    "mmsi2": None,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "lat": float(ep["lat"].mean()),
                    "lon": float(ep["lon"].mean()),
                    "region": region,
                    "kind": ep["kind"].iloc[0] if "kind" in ep.columns else None,
                    "segment": ep["segment"].iloc[0] if "segment" in ep.columns else None,
                    "details": json.dumps(
                        {
                            "duration_hours": round(duration_h, 1),
                            "mean_sog": round(float(ep_sog.mean()), 2),
                            "fix_count": len(ep),
                        }
                    ),
                }
            )

    return results


# --- Destination change detection -------------------------------------------

# Minimum consecutive fixes at the old destination before a change is counted
_DEST_MIN_CONSEC_FIXES = 3
# Old destination must have been held for this many minutes (noise filter)
_DEST_MIN_STABLE_MIN = 20.0


def destination_change_events(df: pd.DataFrame) -> list[dict]:
    """Detect vessels that changed their reported destination.

    Noise-filtered: the old destination must have been reported for at least
    _DEST_MIN_CONSEC_FIXES consecutive fixes AND _DEST_MIN_STABLE_MIN minutes.
    Excludes transitions to or from blank/null strings.

    Returns dicts compatible with the ais_events table (type='reroute'):
      event_id, type, mmsi, mmsi2, start_ts, end_ts, lat, lon,
      region, kind, segment, details (JSON with old/new destination + fix count).
    """
    if df.empty or "destination" not in df.columns:
        return []

    results: list[dict] = []

    for mmsi, grp in df.groupby("mmsi", sort=False):
        grp = grp.sort_values("snapshot_ts").reset_index(drop=True)
        # Normalize: strip whitespace, uppercase, collapse multiple spaces
        dest = grp["destination"].fillna("").str.strip().str.upper()
        # Replace empty strings with a sentinel so groupby runs work
        dest_clean = dest.replace("", None)

        prev_dest: str | None = None
        run_start_idx: int = 0
        run_len: int = 0

        for i, row in grp.iterrows():
            cur_dest = dest_clean.iloc[i]
            if cur_dest == prev_dest:
                run_len += 1
            else:
                # Destination changed (or first fix)
                if (
                    prev_dest is not None
                    and cur_dest is not None
                    and run_len >= _DEST_MIN_CONSEC_FIXES
                ):
                    old_duration_min = (
                        grp["snapshot_ts"].iloc[i - 1] - grp["snapshot_ts"].iloc[run_start_idx]
                    ).total_seconds() / 60
                    if old_duration_min >= _DEST_MIN_STABLE_MIN:
                        change_fix = grp.iloc[i]
                        change_ts_raw = change_fix["snapshot_ts"]
                        change_ts = (
                            change_ts_raw.to_pydatetime()
                            if hasattr(change_ts_raw, "to_pydatetime")
                            else change_ts_raw
                        )
                        results.append(
                            {
                                "event_id": _event_id("reroute", int(mmsi), change_ts),
                                "type": "reroute",
                                "mmsi": int(mmsi),
                                "mmsi2": None,
                                "start_ts": change_ts,
                                "end_ts": change_ts,
                                "lat": float(change_fix["lat"]),
                                "lon": float(change_fix["lon"]),
                                "region": str(change_fix.get("region") or "") or None,
                                "kind": change_fix.get("kind"),
                                "segment": change_fix.get("segment"),
                                "details": json.dumps(
                                    {
                                        "old_destination": prev_dest,
                                        "new_destination": cur_dest,
                                        "fixes_at_old": run_len,
                                    }
                                ),
                            }
                        )
                run_start_idx = i
                run_len = 1
                prev_dest = cur_dest

    return results


# --- STS candidate detection -------------------------------------------------


def sts_candidates(df: pd.DataFrame) -> list[dict]:
    """Detect ship-to-ship transfer candidates: two tankers within 500m for >= 2h,
    both SOG < 0.5 kn, outside anchorage zones.

    Uses a 0.01-deg grid hash per snapshot_ts to find candidate pairs efficiently.

    Returns a list of dicts compatible with the ais_events table schema.
    """
    if df.empty or "kind" not in df.columns:
        return []

    # Filter to slow tankers outside anchorage zones
    tankers = df[df["kind"] == "tanker"].copy()
    if tankers.empty:
        return []

    tankers = tankers[tankers["sog"].fillna(999) < _SOG_ANCHOR_KN].copy()
    if tankers.empty:
        return []

    tankers = tankers[
        tankers.apply(lambda r: _any_zone(float(r["lat"]), float(r["lon"])) is None, axis=1)
    ].copy()
    if tankers.empty:
        return []

    # Accumulate (mmsi1, mmsi2) -> sorted list of co-location timestamps
    from collections import defaultdict

    pair_timestamps: dict[tuple[int, int], list] = defaultdict(list)

    for ts, snap in tankers.groupby("snapshot_ts", sort=False):
        if len(snap) < 2:
            continue

        snap = snap.reset_index(drop=True)
        # Grid cell: floor to nearest 0.01 deg
        snap["_gi"] = (snap["lat"] / 0.01).astype(int)
        snap["_gj"] = (snap["lon"] / 0.01).astype(int)

        cell_map: dict[tuple, list] = defaultdict(list)
        for _, row in snap.iterrows():
            gi, gj = int(row["_gi"]), int(row["_gj"])
            # Index vessel into its cell AND all 8 neighbors so pairs straddling borders match
            for di in range(-1, 2):
                for dj in range(-1, 2):
                    cell_map[(gi + di, gj + dj)].append(row)

        checked: set[tuple[int, int]] = set()
        for vessels in cell_map.values():
            for i in range(len(vessels)):
                for j in range(i + 1, len(vessels)):
                    m1, m2 = int(vessels[i]["mmsi"]), int(vessels[j]["mmsi"])
                    if m1 == m2:
                        continue
                    pair = (min(m1, m2), max(m1, m2))
                    if pair in checked:
                        continue
                    checked.add(pair)
                    dist = _haversine_m(
                        float(vessels[i]["lat"]), float(vessels[i]["lon"]),
                        float(vessels[j]["lat"]), float(vessels[j]["lon"]),
                    )
                    if dist <= _STS_MAX_DIST_M:
                        pair_timestamps[pair].append(ts)

    results: list[dict] = []
    for (mmsi1, mmsi2), timestamps in pair_timestamps.items():
        if len(timestamps) < 2:
            continue
        timestamps.sort()
        duration_h = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
        if duration_h < _STS_MIN_HOURS:
            continue

        # Position and metadata from first co-location fix
        first_fix = tankers[(tankers["mmsi"] == mmsi1) & (tankers["snapshot_ts"] == timestamps[0])]
        if first_fix.empty:
            continue
        fix = first_fix.iloc[0]

        v2_fix = tankers[tankers["mmsi"] == mmsi2].sort_values("snapshot_ts").iloc[-1] if not tankers[tankers["mmsi"] == mmsi2].empty else None

        start_ts_raw = timestamps[0]
        end_ts_raw = timestamps[-1]
        start_ts = start_ts_raw.to_pydatetime() if hasattr(start_ts_raw, "to_pydatetime") else start_ts_raw
        end_ts = end_ts_raw.to_pydatetime() if hasattr(end_ts_raw, "to_pydatetime") else end_ts_raw

        results.append(
            {
                "event_id": _event_id("sts", mmsi1, start_ts, mmsi2),
                "type": "sts",
                "mmsi": mmsi1,
                "mmsi2": mmsi2,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "lat": float(fix["lat"]),
                "lon": float(fix["lon"]),
                "region": str(fix.get("region") or ""),
                "kind": "tanker",
                "segment": str(fix.get("segment") or "") or None,
                "details": json.dumps(
                    {
                        "duration_hours": round(duration_h, 1),
                        "co_location_fixes": len(timestamps),
                        "segment2": str(v2_fix.get("segment") or "") if v2_fix is not None else None,
                    }
                ),
            }
        )

    return results


# --- Dark voyage composite detection -----------------------------------------

# A dark voyage is gap -> (STS or loiter) -> gap for the same vessel within a time window.
# The gap must precede the STS/loiter (started before or shortly after) and the second gap
# must follow the STS/loiter end.

_DARK_VOYAGE_WINDOW_H = 72.0  # max hours from first gap start to last gap end
_DARK_VOYAGE_PRE_GAP_MAX_H = 24.0  # max hours between gap-start and STS/loiter-start


def dark_voyage_events(events_df: pd.DataFrame) -> list[dict]:
    """Detect dark voyage composites from existing ais_events rows.

    A dark voyage requires (per vessel, within _DARK_VOYAGE_WINDOW_H):
      - At least one gap event
      - At least one STS or loiter event that starts within _DARK_VOYAGE_PRE_GAP_MAX_H after the gap
      - At least one gap event that ends after the STS/loiter event starts (second gap)
        OR the STS/loiter event itself ends (vessel went dark again)

    Returns dicts for ais_events with type='dark_voyage'.
    event_id is stable: hash of (mmsi, earliest_gap_start).
    details JSON: {gap_ids, sts_loiter_id, gap_count, sts_count, loiter_count, window_hours}
    """
    if events_df.empty:
        return []

    required_cols = {"event_id", "type", "mmsi", "start_ts", "end_ts", "lat", "lon", "region", "kind", "segment"}
    if not required_cols.issubset(events_df.columns):
        return []

    events_df = events_df.copy()
    events_df["start_ts"] = pd.to_datetime(events_df["start_ts"])
    events_df["end_ts"] = pd.to_datetime(events_df["end_ts"])

    results: list[dict] = []
    seen: set[str] = set()

    for mmsi, grp in events_df.groupby("mmsi"):
        mmsi_int = int(mmsi)
        gaps = grp[grp["type"] == "gap"].sort_values("start_ts")
        covert = grp[grp["type"].isin(["sts", "loiter"])].sort_values("start_ts")

        if gaps.empty or covert.empty:
            continue

        for _, gap in gaps.iterrows():
            gap_start = gap["start_ts"]
            window_end = gap_start + timedelta(hours=_DARK_VOYAGE_WINDOW_H)

            # Find STS/loiter events that start within PRE_GAP_MAX_H after the gap starts
            nearby_covert = covert[
                (covert["start_ts"] >= gap_start)
                & (covert["start_ts"] <= gap_start + timedelta(hours=_DARK_VOYAGE_PRE_GAP_MAX_H))
            ]
            if nearby_covert.empty:
                continue

            # Find a second gap that starts after the earliest covert event start
            first_covert_ts = nearby_covert["start_ts"].min()
            trailing_gaps = gaps[
                (gaps["start_ts"] > first_covert_ts)
                & (gaps["start_ts"] <= window_end)
            ]
            if trailing_gaps.empty:
                continue

            # We have: gap -> covert event -> trailing gap. Fire composite.
            event_key = f"dark_{mmsi_int}_{gap_start.isoformat()}"
            if event_key in seen:
                continue
            seen.add(event_key)

            last_gap_end = trailing_gaps["end_ts"].max()
            window_hours = round(
                (last_gap_end - gap_start).total_seconds() / 3600, 1
            )

            # Position: use covert event location (most incriminating point)
            ref = nearby_covert.iloc[0]
            lat = float(ref["lat"]) if ref["lat"] is not None else 0.0
            lon = float(ref["lon"]) if ref["lon"] is not None else 0.0
            region = str(ref.get("region") or "") or None
            kind = str(ref.get("kind") or "") or None
            segment = str(ref.get("segment") or "") or None

            eid = _event_id("dark", mmsi_int, gap["start_ts"].to_pydatetime() if hasattr(gap["start_ts"], "to_pydatetime") else gap["start_ts"], 0)

            results.append(
                {
                    "event_id": eid,
                    "type": "dark_voyage",
                    "mmsi": mmsi_int,
                    "mmsi2": None,
                    "start_ts": gap_start.to_pydatetime() if hasattr(gap_start, "to_pydatetime") else gap_start,
                    "end_ts": last_gap_end.to_pydatetime() if hasattr(last_gap_end, "to_pydatetime") else last_gap_end,
                    "lat": lat,
                    "lon": lon,
                    "region": region,
                    "kind": kind,
                    "segment": segment,
                    "details": json.dumps(
                        {
                            "gap_ids": gaps["event_id"].tolist(),
                            "sts_count": int((grp["type"] == "sts").sum()),
                            "loiter_count": int((grp["type"] == "loiter").sum()),
                            "window_hours": window_hours,
                        }
                    ),
                }
            )

    return results
