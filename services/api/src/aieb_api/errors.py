"""Typed error envelope and taxonomy (spec section 33).

Error object always has: code, safe message, field_errors, request_id, retryable.
No secret values or raw stack traces in public responses.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or {}
        self.retryable = retryable
        super().__init__(message)


def _envelope(request: Request, status_code: int, code: str, message: str, field_errors: dict[str, str], retryable: bool) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "field_errors": field_errors,
                "request_id": request_id,
                "retryable": retryable,
            }
        },
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _envelope(request, exc.status_code, exc.code, exc.message, exc.field_errors, exc.retryable)


_HTTP_STATUS_CODES = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    412: "precondition_failed",
    422: "validation_error",
    429: "rate_limited",
    503: "service_unavailable",
}


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_CODES.get(exc.status_code, "error")
    message = exc.detail if isinstance(exc.detail, str) else "request failed"
    return _envelope(request, exc.status_code, code, message, {}, exc.status_code in (429, 503))


def not_found() -> ApiError:
    """API-02: unauthorized private resources return 404, never 403, to avoid confirming existence."""
    return ApiError(404, "not_found", "the requested resource does not exist or is not disclosable")


def unauthenticated(message: str = "authentication required") -> ApiError:
    return ApiError(401, "unauthenticated", message)


def forbidden(message: str = "insufficient role") -> ApiError:
    return ApiError(403, "forbidden", message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


def stale_revision() -> ApiError:
    return ApiError(412, "precondition_failed", "the resource has been modified since it was last read")


def invalid_request(message: str, field_errors: dict[str, str] | None = None) -> ApiError:
    return ApiError(400, "invalid_request", message, field_errors=field_errors)
