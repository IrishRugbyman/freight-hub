"""freight-api: live vessel tracker, freight analytics and the research tabs.

This module only builds the application: rate limiting, compression, CORS and the
routers. Endpoints live in ``app/routers/``, one module per page or analytics area;
shared helpers live in ``app/common.py`` (freshness cutoffs, coercion),
``app/live.py`` (the cached ``live_positions`` frame) and ``app/ports.py`` (port and
terminal reference data).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .routers import (
    analytics_cargo,
    analytics_chokepoints,
    analytics_eta,
    analytics_fleet,
    analytics_ports,
    analytics_risk,
    cycle,
    events,
    fleet,
    pipelines,
    research,
    tracker,
    vessels,
)

limiter = Limiter(key_func=get_remote_address, default_limits=["240/minute"])
app = FastAPI(title="freight-api", version="0.1.0")
app.state.limiter = limiter


def _rate_limited(_request: Request, _exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})


app.add_exception_handler(RateLimitExceeded, _rate_limited)
# Gzip large JSON responses (vessels payload is 1.5MB+ raw with 5000+ vessels)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://freight.lbzgiu.xyz", "http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

for _module in (
    tracker,
    vessels,
    research,
    events,
    fleet,
    pipelines,
    cycle,
    analytics_fleet,
    analytics_risk,
    analytics_chokepoints,
    analytics_ports,
    analytics_cargo,
    analytics_eta,
):
    app.include_router(_module.router)
