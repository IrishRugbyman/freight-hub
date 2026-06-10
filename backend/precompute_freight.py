"""Run at deploy time to precompute routes and dispersion static JSON.

Usage: .venv/bin/python precompute_freight.py
Writes app/static/routes_default.json and app/static/dispersion_default.json.
"""

from app.main import _write_atomic
from app.runner_dispersion import run_dispersion_default
from app.runner_routes import run_routes_default
from pathlib import Path

static = Path(__file__).parent / "app" / "static"
static.mkdir(exist_ok=True)

out_routes = static / "routes_default.json"
result_routes = run_routes_default()
_write_atomic(out_routes, result_routes.model_dump_json())
print(f"Written {out_routes} ({out_routes.stat().st_size // 1024} KB)")

out_disp = static / "dispersion_default.json"
result_disp = run_dispersion_default()
_write_atomic(out_disp, result_disp.model_dump_json())
print(f"Written {out_disp} ({out_disp.stat().st_size // 1024} KB)")
