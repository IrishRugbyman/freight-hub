from __future__ import annotations

from pydantic import BaseModel


class Vessel(BaseModel):
    mmsi: int
    name: str | None = None
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None
    destination: str | None = None
    kind: str
    segment: str | None = None
    region: str | None = None
    updated_ts: str


class ChokepointCount(BaseModel):
    region: str
    bbox: list[list[float]]  # [[lat_min, lon_min], [lat_max, lon_max]]
    total: int
    by_segment: dict[str, int]


class Meta(BaseModel):
    kinds: list[str]
    segments: list[str]
    regions: list[str]
    total_tracked: int
    last_update: str | None = None
