"""Idempotency-key handling for mutating routes (spec section 33, test API-01).

Same key + same body replays the stored response. Same key + different body
is 409. Keys are retained for 7 days (spec); expiry sweeping is a deployment
job, not enforced by this module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import conflict, invalid_request
from .models import IdempotencyRecordRow


def request_digest(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def principal_scope(scope: str, principal: str | None) -> str:
    """Scope an idempotency key to BOTH the route/resource AND the
    authenticated principal.

    A key is a replay handle for a stored response, so two principals using
    the same key against the same resource must never see each other's
    response (and must not collide on the (scope, key) unique constraint).
    The principal is the server-resolved user id - resolved from the OIDC
    identity against the `users` table, never a client-claimable string.
    Every mutating route must route its scope through this helper.
    """
    return f"{scope}|u={principal if principal else 'unauthenticated'}"


def required_idempotency_key(key: str = Header(..., alias="Idempotency-Key")) -> str:
    """FastAPI dependency for mutation routes.

    The database key column is bounded to 128 characters. Validate and trim at
    the HTTP boundary so oversized or whitespace-only values become a stable
    client error instead of a driver/database exception.
    """
    normalized = key.strip()
    if not normalized:
        raise invalid_request("Idempotency-Key must not be blank")
    if len(normalized) > 128:
        raise invalid_request("Idempotency-Key must be at most 128 characters")
    return normalized


def check_or_reserve(session: Session, *, scope: str, key: str | None, body: dict[str, Any]) -> dict[str, Any] | None:
    """Returns a cached response body to replay, or None if the caller should proceed and later call `store`."""
    if key is None or not key.strip():
        raise invalid_request("Idempotency-Key header is required for this mutation")
    if len(key) > 128 or len(key.strip()) > 128:
        raise invalid_request("Idempotency-Key must be at most 128 characters")
    digest = request_digest(body)
    existing = session.execute(
        select(IdempotencyRecordRow).where(IdempotencyRecordRow.scope == scope, IdempotencyRecordRow.key == key)
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_digest != digest:
        raise conflict("idempotency key reused with a different request body")
    return existing.response_body


def store(session: Session, *, scope: str, key: str, body: dict[str, Any], status_code: int, response_body: dict[str, Any]) -> None:
    if not key or not key.strip():
        # The (scope, key) unique constraint is the correctness mechanism for
        # concurrent replays; a NULL key would insert an unusable record that
        # no client could ever replay (and, on PostgreSQL, would not even
        # conflict with another NULL). Fail closed instead.
        raise invalid_request("Idempotency-Key header is required for this mutation")
    if len(key) > 128 or len(key.strip()) > 128:
        raise invalid_request("Idempotency-Key must be at most 128 characters")
    session.add(
        IdempotencyRecordRow(
            scope=scope, key=key, request_digest=request_digest(body), response_status=status_code, response_body=response_body,
        )
    )


def finalize(
    session: Session, *, scope: str, key: str, body: dict[str, Any], status_code: int, response_body: dict[str, Any]
) -> dict[str, Any] | None:
    """Commit the caller's business-logic writes together with the idempotency record.

    `check_or_reserve`'s earlier read is only a fast-path optimization; two
    concurrent requests with the same key can both pass it. Correctness comes
    from the (scope, key) unique constraint enforced here: if a concurrent
    request already committed first, this commit fails, the caller's
    not-yet-committed writes roll back with it, and the winning transaction's
    stored response is returned for replay instead (or a 409 if its body
    differed) - never an unhandled IntegrityError.
    """
    store(session, scope=scope, key=key, body=body, status_code=status_code, response_body=response_body)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # Only the idempotency-key unique constraint means "a concurrent identical request
        # already won"; any other integrity violation in this transaction is a different,
        # genuine failure and must propagate rather than being misdiagnosed as a replay.
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != "uq_idempotency_scope_key":
            raise
        existing = session.execute(
            select(IdempotencyRecordRow).where(IdempotencyRecordRow.scope == scope, IdempotencyRecordRow.key == key)
        ).scalar_one()
        if existing.request_digest != request_digest(body):
            raise conflict("idempotency key reused with a different request body") from exc
        return existing.response_body
    return None
