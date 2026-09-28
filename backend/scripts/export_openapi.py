"""Write the API's OpenAPI document to ``frontend/openapi.json``.

That snapshot is the contract between the two halves: the frontend generates its
response types from it (``npm run gen:api``), and ``tests/test_openapi_snapshot.py``
fails when the live schema drifts from it. After changing an endpoint or a model in
``app/schemas.py``, run from ``backend/``::

    .venv/bin/python scripts/export_openapi.py
    cd ../frontend && npm run gen:api
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SNAPSHOT = BACKEND.parent / "frontend" / "openapi.json"


def render() -> str:
    """The current schema, serialised deterministically (sorted keys, trailing newline)."""
    sys.path.insert(0, str(BACKEND))
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SNAPSHOT.write_text(render())
    print(f"wrote {SNAPSHOT}")
