"""Fleet registry view: the filterable vessel table, KPIs, owner/flag risk, CSV export."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import db
from .. import fleet as _fleet
from ..common import iso
from ..schemas import (
    FlagRiskResponse,
    FlagRiskRow,
    FleetAgeBand,
    FleetAgeResponse,
    FleetFacets,
    FleetKPIs,
    FleetResponse,
    OwnerRiskItem,
    OwnerRiskResponse,
)

router = APIRouter()


@router.get("/api/fleet", response_model=FleetResponse)
def fleet(
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
):
    """Filterable, sortable, paginated fleet registry (registry + live AIS join)."""
    return _fleet.query_fleet(
        q=q,
        flag=flag,
        owner=owner,
        class_society=class_society,
        pi_club=pi_club,
        paris_mou=paris_mou,
        tokyo_mou=tokyo_mou,
        kind=kind,
        segment=segment,
        built_min=built_min,
        built_max=built_max,
        dwt_min=dwt_min,
        dwt_max=dwt_max,
        detention_min=detention_min,
        risk_min=risk_min,
        live_only=live_only,
        sort=sort,
        order=order,
        page=page,
    )


@router.get("/api/fleet/facets", response_model=FleetFacets)
def fleet_facets():
    """Distinct filter values with counts for the Fleet Explorer dropdowns."""
    return _fleet.query_facets()


@router.get("/api/fleet/owner-risk", response_model=OwnerRiskResponse)
def fleet_owner_risk(min_vessels: int = 2, top_n: int = 30):
    """Owner concentration analysis: which owners control the most high-risk tonnage.

    Only includes owners with >= min_vessels (clamped 1-10) vessels in the registry.
    Returns top_n owners by avg risk score (clamped 1-100).
    """
    min_vessels = max(1, min(10, min_vessels))
    top_n = max(1, min(100, top_n))

    df = db.pg_query(
        "SELECT owner, risk_score, flag, ofac_sanctioned "
        "FROM vessels "
        "WHERE fetch_ok = true AND owner IS NOT NULL AND risk_score IS NOT NULL",
    )
    if df.empty:
        return OwnerRiskResponse(as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "", rows=[])

    rows = []
    for owner, grp in df.groupby("owner"):
        if len(grp) < min_vessels:
            continue
        avg_risk = float(grp["risk_score"].mean())
        max_risk = int(grp["risk_score"].max())
        high_risk = int((grp["risk_score"] >= 50).sum())
        ofac_count = (
            int(grp["ofac_sanctioned"].fillna(False).astype(bool).sum())
            if "ofac_sanctioned" in grp.columns
            else 0
        )
        flags = sorted(set(grp["flag"].dropna().tolist()))[:5]
        rows.append(
            OwnerRiskItem(
                owner=str(owner),
                vessel_count=len(grp),
                avg_risk_score=round(avg_risk, 1),
                max_risk_score=max_risk,
                high_risk_count=high_risk,
                ofac_count=ofac_count,
                flags=flags,
            )
        )

    rows.sort(key=lambda r: r.avg_risk_score, reverse=True)
    return OwnerRiskResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        rows=rows[:top_n],
    )


@router.get("/api/fleet/flag-risk", response_model=FlagRiskResponse)
def fleet_flag_risk(top_n: int = 30):
    """Flag-state risk analysis: which flags concentrate the most risk tonnage.

    Groups vessel_registry by flag (fetch_ok=true, risk_score not null).
    Sorts by avg_risk_score descending. top_n clamped 5-100.
    """
    top_n = max(5, min(100, top_n))
    df = db.pg_query(
        "SELECT flag, flag_code, risk_score, "
        "       COALESCE(ofac_sanctioned, false) AS ofac_sanctioned, "
        "       paris_mou, tokyo_mou "
        "FROM vessels "
        "WHERE fetch_ok = true AND flag IS NOT NULL AND risk_score IS NOT NULL",
    )
    if df.empty:
        return FlagRiskResponse(as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "", rows=[])

    rows = []
    for flag, grp in df.groupby("flag"):
        avg_risk = float(grp["risk_score"].mean())
        max_risk = int(grp["risk_score"].max())
        high_risk = int((grp["risk_score"] >= 50).sum())
        ofac_count = (
            int(grp["ofac_sanctioned"].fillna(False).astype(bool).sum())
            if "ofac_sanctioned" in grp.columns
            else 0
        )
        flag_code_vals = grp["flag_code"].dropna().tolist()
        flag_code = flag_code_vals[0] if flag_code_vals else None
        # Most common paris/tokyo MOU status for this flag
        paris_counts = grp["paris_mou"].dropna().value_counts()
        tokyo_counts = grp["tokyo_mou"].dropna().value_counts()
        paris_mou = paris_counts.index[0] if len(paris_counts) > 0 else None
        tokyo_mou = tokyo_counts.index[0] if len(tokyo_counts) > 0 else None
        rows.append(
            FlagRiskRow(
                flag=str(flag),
                flag_code=flag_code,
                vessel_count=len(grp),
                avg_risk_score=round(avg_risk, 1),
                max_risk_score=max_risk,
                high_risk_count=high_risk,
                ofac_count=ofac_count,
                paris_mou=paris_mou,
                tokyo_mou=tokyo_mou,
            )
        )

    rows.sort(key=lambda r: r.avg_risk_score, reverse=True)
    return FlagRiskResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        rows=rows[:top_n],
    )


@router.get("/api/fleet/kpis", response_model=FleetKPIs)
def fleet_kpis():
    """Aggregate risk intelligence KPIs for the fleet registry.

    Single-query summary: total vessels, risk coverage, OFAC count,
    high/critical risk counts, avg score among scored vessels.
    """
    df = db.pg_query(
        "SELECT risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned "
        "FROM vessels WHERE fetch_ok = true",
    )
    if df.empty:
        return FleetKPIs(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            total_registry=0,
            scored=0,
            elevated=0,
            high_risk=0,
            critical=0,
            ofac_count=0,
            avg_risk_score=None,
            pct_scored=0.0,
        )

    total = len(df)
    scored_df = df[df["risk_score"].notna()]
    scored = len(scored_df)
    elevated = int((scored_df["risk_score"] >= 25).sum()) if scored else 0
    high_risk = int((scored_df["risk_score"] >= 50).sum()) if scored else 0
    critical = int((scored_df["risk_score"] >= 75).sum()) if scored else 0
    ofac_count = int(df["ofac_sanctioned"].fillna(False).astype(bool).sum())
    avg_risk = round(float(scored_df["risk_score"].mean()), 1) if scored else None
    pct_scored = round(scored / total * 100, 1) if total else 0.0

    return FleetKPIs(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        total_registry=total,
        scored=scored,
        elevated=elevated,
        high_risk=high_risk,
        critical=critical,
        ofac_count=ofac_count,
        avg_risk_score=avg_risk,
        pct_scored=pct_scored,
    )


@router.get("/api/fleet/age", response_model=FleetAgeResponse)
def fleet_age():
    """Fleet age distribution by 5-year bands from vessel registry.

    Includes avg risk score and high-risk count per band. Shows how vessel age
    correlates with risk profile across the registry.
    """
    ref_year = datetime.now(UTC).year
    df = db.pg_query(
        "SELECT year_built, risk_score, COALESCE(ofac_sanctioned, false) AS ofac_sanctioned, dwt "
        "FROM vessels WHERE fetch_ok = true AND year_built IS NOT NULL",
    )
    if df.empty:
        return FleetAgeResponse(
            as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
            reference_year=ref_year,
            bands=[],
        )

    df["age"] = ref_year - df["year_built"].astype(int)
    df["band"] = pd.cut(
        df["age"],
        bins=[0, 5, 10, 15, 20, 25, 200],
        labels=["0-4", "5-9", "10-14", "15-19", "20-24", "25+"],
        right=False,
    )

    bands = []
    for band_label in ["0-4", "5-9", "10-14", "15-19", "20-24", "25+"]:
        grp = df[df["band"] == band_label]
        if grp.empty:
            continue
        scored = grp[grp["risk_score"].notna()]
        avg_risk = round(float(scored["risk_score"].mean()), 1) if not scored.empty else None
        high_risk = int((scored["risk_score"] >= 50).sum()) if not scored.empty else 0
        dwt_vals = grp["dwt"].dropna()
        avg_dwt = round(float(dwt_vals.mean()), 0) if not dwt_vals.empty else None
        bands.append(
            FleetAgeBand(
                age_band=band_label,
                vessel_count=len(grp),
                avg_risk_score=avg_risk,
                high_risk_count=high_risk,
                avg_dwt=avg_dwt,
            )
        )

    return FleetAgeResponse(
        as_of=iso(datetime.now(UTC).replace(tzinfo=None)) or "",
        reference_year=ref_year,
        bands=bands,
    )


@router.get("/api/fleet/export")
def fleet_export(
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
):
    """Download current filtered fleet as CSV."""
    csv_text = _fleet.export_csv(
        q=q,
        flag=flag,
        owner=owner,
        class_society=class_society,
        pi_club=pi_club,
        paris_mou=paris_mou,
        tokyo_mou=tokyo_mou,
        kind=kind,
        segment=segment,
        built_min=built_min,
        built_max=built_max,
        dwt_min=dwt_min,
        dwt_max=dwt_max,
        detention_min=detention_min,
        risk_min=risk_min,
        live_only=live_only,
    )
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fleet.csv"},
    )
