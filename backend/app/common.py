"""Small helpers shared by the API modules: freshness cutoffs, value coercion, geometry."""

from __future__ import annotations

import contextlib
import math
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import db


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a sibling tempfile and ``os.replace``.

    Readers never observe a half-written file; on failure the tempfile is removed.
    """
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def fresh_cutoff() -> datetime:
    """Naive-UTC instant before which a vessel is stale (dark), per ``db.STALE_HOURS``."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=db.STALE_HOURS)


def visible_cutoff() -> datetime:
    """Naive-UTC instant before which a vessel is hidden, per ``db.VISIBLE_HOURS``."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=db.VISIBLE_HOURS)


def valid_imo(v) -> int | None:
    """Return int IMO if valid, else None. Handles pandas NA/NaN/None."""
    if v is None:
        return None
    s = str(v)
    if s in ("nan", "None", "NA", "<NA>", ""):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def str_or_none(v) -> str | None:
    """Return string value or None, treating NaN/NA/None as None."""
    if v is None:
        return None
    s = str(v)
    return None if s in ("nan", "None", "NA", "<NA>", "") else s


def iso(ts) -> str | None:
    """ISO-8601 string for a timestamp-like value; ``None`` passes through."""
    if ts is None:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""

    R = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))
