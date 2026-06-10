from __future__ import annotations

from pathlib import Path

_RESEARCH_ROOT = Path("~/quant/research").expanduser()

_SLUGS = {"transport-arb", "freight-dispersion"}


def project_dir(slug: str) -> Path:
    if slug not in _SLUGS:
        raise ValueError(f"Unknown project slug: {slug!r}")
    return _RESEARCH_ROOT / slug
