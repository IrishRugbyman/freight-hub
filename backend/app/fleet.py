"""Fleet Explorer query layer.

Joins vessel_registry (Phase 5) with live_positions on IMO number.
All filters use parameterized SQL - no string interpolation of user input.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from . import db
from .schemas import FleetFacetItem, FleetFacets, FleetResponse, FleetRow, FleetSummary

_PAGE_SIZE = 100

_SORT_COLS = {
    "ship_name", "flag", "ship_type", "year_built", "gross_tonnage", "dwt",
    "owner", "class_society", "detention_rate_pct", "paris_mou", "tokyo_mou",
    "sog", "region", "segment", "risk_score",
}


def _fresh_cutoff() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=db.STALE_HOURS)


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return int(f)
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def _safe_str(val: Any) -> str | None:
    if val is None:
        return None
    try:
        if pd.isna(val):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return s if s else None


def _load_registry(
    q: str | None,
    flag: str | None,
    owner: str | None,
    class_society: str | None,
    pi_club: str | None,
    paris_mou: str | None,
    tokyo_mou: str | None,
    built_min: int | None,
    built_max: int | None,
    dwt_min: int | None,
    dwt_max: int | None,
    detention_min: float | None,
    risk_min: int | None = None,
) -> pd.DataFrame:
    where = ["fetch_ok = true"]
    params: list = []

    if q:
        ql = q.lower()
        where.append("(LOWER(COALESCE(ship_name,'')) LIKE ? OR CAST(imo AS VARCHAR) LIKE ?)")
        params += [f"%{ql}%", f"%{q}%"]
    if flag:
        where.append("flag = ?")
        params.append(flag)
    if owner:
        where.append("LOWER(COALESCE(owner,'')) LIKE ?")
        params.append(f"%{owner.lower()}%")
    if class_society:
        where.append("class_society = ?")
        params.append(class_society)
    if pi_club:
        where.append("pi_club = ?")
        params.append(pi_club)
    if paris_mou:
        where.append("paris_mou = ?")
        params.append(paris_mou)
    if tokyo_mou:
        where.append("tokyo_mou = ?")
        params.append(tokyo_mou)
    if built_min is not None:
        where.append("year_built >= ?")
        params.append(built_min)
    if built_max is not None:
        where.append("year_built <= ?")
        params.append(built_max)
    if dwt_min is not None:
        where.append("dwt >= ?")
        params.append(dwt_min)
    if dwt_max is not None:
        where.append("dwt <= ?")
        params.append(dwt_max)
    if detention_min is not None:
        where.append("detention_rate_pct >= ?")
        params.append(detention_min)
    if risk_min is not None:
        where.append("risk_score >= ?")
        params.append(risk_min)

    sql = f"SELECT * FROM vessel_registry WHERE {' AND '.join(where)}"  # noqa: S608
    return db.query(sql, params, db=db.registry_db_path())


def query_fleet(
    q: str | None = None,
    flag: str | None = None,
    owner: str | None = None,
    class_society: str | None = None,
    pi_club: str | None = None,
    paris_mou: str | None = None,
    tokyo_mou: str | None = None,
    kind: str | None = None,
    segment: str | None = None,
    built_min: int | None = None,
    built_max: int | None = None,
    dwt_min: int | None = None,
    dwt_max: int | None = None,
    detention_min: float | None = None,
    risk_min: int | None = None,
    live_only: bool = False,
    sort: str = "ship_name",
    order: str = "asc",
    page: int = 1,
) -> FleetResponse:
    reg_df = _load_registry(
        q, flag, owner, class_society, pi_club, paris_mou, tokyo_mou,
        built_min, built_max, dwt_min, dwt_max, detention_min, risk_min,
    )

    if reg_df.empty:
        return FleetResponse(
            total=0, page=page, page_size=_PAGE_SIZE,
            summary=FleetSummary(total=0, top_flags=[], top_owners=[]),
            rows=[],
        )

    # Load live positions (only IMO-bearing, fresh only)
    live_df = db.query(
        "SELECT CAST(imo AS BIGINT) AS imo, mmsi, name, lat, lon, sog, region, kind, segment "
        "FROM live_positions WHERE imo >= 1000000 AND imo <= 9999999 AND updated_ts > ?",
        [_fresh_cutoff()],
    )
    live_df = live_df.rename(columns={"name": "live_name"}) if not live_df.empty else pd.DataFrame(
        columns=["imo", "mmsi", "live_name", "lat", "lon", "sog", "region", "kind", "segment"]
    )

    # Merge: registry LEFT JOIN live on imo
    how = "inner" if live_only else "left"
    merged = reg_df.merge(live_df, on="imo", how=how)

    # Apply live-field filters (kind, segment only meaningful when joined with live)
    if kind and not merged.empty:
        merged = merged[merged["kind"].str.lower() == kind.lower()]
    if segment and not merged.empty:
        merged = merged[merged["segment"].str.lower() == segment.lower()]

    # Apply q filter to MMSI too (live name search when q is numeric)
    if q and not merged.empty and q.isdigit():
        merged = merged[
            merged["imo"].astype(str).str.contains(q, na=False) |
            merged["mmsi"].astype(str).str.contains(q, na=False)
        ]

    if merged.empty:
        return FleetResponse(
            total=0, page=page, page_size=_PAGE_SIZE,
            summary=FleetSummary(total=0, top_flags=[], top_owners=[]),
            rows=[],
        )

    # Compute summary from full filtered set before pagination
    current_year = datetime.now(UTC).year
    total_dwt_val = merged["dwt"].dropna().sum() if "dwt" in merged.columns else 0
    ages = (current_year - merged["year_built"].dropna()).astype(float)
    avg_age = round(float(ages.mean()), 1) if len(ages) > 0 else None

    top_flags = (
        merged["flag"].dropna().value_counts().head(5)
        .reset_index().rename(columns={"flag": "value", "count": "count"})
    )
    top_owners = (
        merged["owner"].dropna().value_counts().head(5)
        .reset_index().rename(columns={"owner": "value", "count": "count"})
    )

    summary = FleetSummary(
        total=len(merged),
        total_dwt=int(total_dwt_val) if total_dwt_val else None,
        avg_age_years=avg_age,
        top_flags=[FleetFacetItem(value=str(r["value"]), count=int(r["count"])) for _, r in top_flags.iterrows()],
        top_owners=[FleetFacetItem(value=str(r["value"]), count=int(r["count"])) for _, r in top_owners.iterrows()],
    )

    # Sort
    sort_col = sort if sort in _SORT_COLS and sort in merged.columns else "ship_name"
    ascending = order.lower() != "desc"
    merged = merged.sort_values(sort_col, ascending=ascending, na_position="last")

    # Paginate
    page = max(1, page)
    offset = (page - 1) * _PAGE_SIZE
    page_df = merged.iloc[offset: offset + _PAGE_SIZE]

    cols = set(page_df.columns)
    rows = []
    for r in page_df.itertuples(index=False):
        ri_raw = getattr(r, "risk_indicators", None) if "risk_indicators" in cols else None
        try:
            ri = json.loads(ri_raw) if ri_raw else None
        except (ValueError, TypeError):
            ri = None
        rows.append(FleetRow(
            imo=int(r.imo),
            ship_name=_safe_str(r.ship_name),
            flag=_safe_str(r.flag),
            flag_code=_safe_str(r.flag_code),
            ship_type=_safe_str(r.ship_type),
            year_built=_safe_int(r.year_built),
            gross_tonnage=_safe_int(r.gross_tonnage),
            dwt=_safe_int(r.dwt),
            owner=_safe_str(r.owner),
            ism_manager=_safe_str(r.ism_manager),
            class_society=_safe_str(r.class_society),
            pi_club=_safe_str(r.pi_club),
            detention_rate_pct=_safe_float(r.detention_rate_pct),
            paris_mou=_safe_str(r.paris_mou),
            tokyo_mou=_safe_str(r.tokyo_mou),
            ship_status=_safe_str(r.ship_status),
            risk_score=_safe_int(getattr(r, "risk_score", None)) if "risk_score" in cols else None,
            risk_indicators=ri,
            mmsi=_safe_int(r.mmsi),
            live_name=_safe_str(r.live_name),
            lat=_safe_float(r.lat),
            lon=_safe_float(r.lon),
            sog=_safe_float(r.sog),
            region=_safe_str(r.region),
            kind=_safe_str(r.kind),
            segment=_safe_str(r.segment),
        ))

    return FleetResponse(total=len(merged), page=page, page_size=_PAGE_SIZE, summary=summary, rows=rows)


def query_facets() -> FleetFacets:
    reg_df = db.query(
        "SELECT flag, class_society, pi_club, paris_mou, tokyo_mou, owner "
        "FROM vessel_registry WHERE fetch_ok = true",
        db=db.registry_db_path(),
    )

    live_df = db.query(
        "SELECT DISTINCT kind, segment FROM live_positions WHERE updated_ts > ?",
        [_fresh_cutoff()],
    )

    def _top(series: "pd.Series", n: int = 50) -> list[FleetFacetItem]:
        vc = series.dropna().value_counts().head(n)
        return [FleetFacetItem(value=str(v), count=int(c)) for v, c in vc.items()]

    if reg_df.empty:
        return FleetFacets(
            flags=[], class_societies=[], pi_clubs=[],
            paris_mou=[], tokyo_mou=[], owners=[],
        )

    return FleetFacets(
        flags=_top(reg_df["flag"]),
        class_societies=_top(reg_df["class_society"]),
        pi_clubs=_top(reg_df["pi_club"]),
        paris_mou=_top(reg_df["paris_mou"]),
        tokyo_mou=_top(reg_df["tokyo_mou"]),
        owners=_top(reg_df["owner"], n=200),
    )


def export_csv(
    q: str | None = None,
    flag: str | None = None,
    owner: str | None = None,
    class_society: str | None = None,
    pi_club: str | None = None,
    paris_mou: str | None = None,
    tokyo_mou: str | None = None,
    kind: str | None = None,
    segment: str | None = None,
    built_min: int | None = None,
    built_max: int | None = None,
    dwt_min: int | None = None,
    dwt_max: int | None = None,
    detention_min: float | None = None,
    risk_min: int | None = None,
    live_only: bool = False,
) -> str:
    # Reuse query_fleet but fetch all pages at once
    reg_df = _load_registry(
        q, flag, owner, class_society, pi_club, paris_mou, tokyo_mou,
        built_min, built_max, dwt_min, dwt_max, detention_min, risk_min,
    )
    if reg_df.empty:
        return "imo,ship_name,flag,owner\n"

    live_df = db.query(
        "SELECT CAST(imo AS BIGINT) AS imo, mmsi, name, lat, lon, sog, region, kind, segment "
        "FROM live_positions WHERE imo >= 1000000 AND imo <= 9999999 AND updated_ts > ?",
        [_fresh_cutoff()],
    )
    if not live_df.empty:
        live_df = live_df.rename(columns={"name": "live_name"})
        how = "inner" if live_only else "left"
        merged = reg_df.merge(live_df, on="imo", how=how)
    else:
        merged = reg_df.copy()

    if kind and not merged.empty and "kind" in merged.columns:
        merged = merged[merged["kind"].str.lower() == kind.lower()]
    if segment and not merged.empty and "segment" in merged.columns:
        merged = merged[merged["segment"].str.lower() == segment.lower()]

    # Drop internal columns
    for col in ("fetch_ok", "fetched_ts", "call_sign", "mmsi_equasis", "uscg_targeting"):
        if col in merged.columns:
            merged = merged.drop(columns=[col])

    buf = io.StringIO()
    merged.to_csv(buf, index=False)
    return buf.getvalue()
