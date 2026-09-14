"""FastAPI application factory."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth
from .errors import ApiError, api_error_handler, http_exception_handler
from .routes import authorized, campaigns, registry, results


@asynccontextmanager
async def _lifespan(app: FastAPI):
    auth.configure_from_environment()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AI Engineer Bench API", version="v1", lifespan=_lifespan)

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

    return app


app = create_app()
