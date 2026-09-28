"""The committed OpenAPI snapshot must match the live schema.

The frontend's response types are generated from ``frontend/openapi.json``. If a model
changes without re-exporting, the generated types silently describe an API that no
longer exists; this test turns that into a failure with the fix in the message.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_openapi.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_openapi", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openapi_snapshot_matches_live_schema():
    exporter = _exporter()
    assert exporter.SNAPSHOT.read_text() == exporter.render(), (
        "frontend/openapi.json is stale: run `.venv/bin/python scripts/export_openapi.py` "
        "from backend/, then `npm run gen:api` in frontend/"
    )
