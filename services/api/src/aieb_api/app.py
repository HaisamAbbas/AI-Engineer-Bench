"""FastAPI application factory."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth
from .errors import ApiError, api_error_handler, http_exception_handler
from .routes import authorized, campaigns, corrections, identity, invalidity, publications, registry, results

# The public website (apps/web) is a browser SPA on its own origin
# (localhost:5173 in local dev); without CORS a browser's own preflight
# blocks every request before it reaches a route at all - curl/pytest never
# hit this because CORS is a browser-enforced restriction, not a server-side
# check that a non-browser client would fail. No default falls back to
# "allow everything": an explicit, configurable allowlist, empty by default
# outside local dev, matches this project's fail-closed posture for
# anything auth-adjacent (see auth.py's own fail-closed rule).
_DEFAULT_LOCAL_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    auth.configure_from_environment()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AI Engineer Bench API", version="v1", lifespan=_lifespan)

    origins = [
        origin.strip()
        for origin in os.environ.get("AIEB_CORS_ALLOWED_ORIGINS", _DEFAULT_LOCAL_ORIGINS).split(",")
        if origin.strip()
    ]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,  # bearer tokens are a header, not a cookie/credential CORS mode cares about
            allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match"],
            expose_headers=["X-Request-Id"],
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    app.include_router(registry.router)
    app.include_router(results.router)
    app.include_router(campaigns.router)
    app.include_router(authorized.router)
    app.include_router(publications.router)
    app.include_router(corrections.router)
    app.include_router(invalidity.router)
    app.include_router(identity.router)

    return app


app = create_app()
