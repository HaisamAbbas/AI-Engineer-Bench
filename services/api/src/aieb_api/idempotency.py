"""Idempotency-key handling for mutating routes (spec section 33, test API-01).

Same key + same body replays the stored response. Same key + different body
is 409. Keys are retained for 7 days (spec); expiry sweeping is a deployment
job, not enforced by this module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import conflict, invalid_request
from .models import IdempotencyRecordRow


def request_digest(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_or_reserve(session: Session, *, scope: str, key: str | None, body: dict[str, Any]) -> dict[str, Any] | None:
    """Returns a cached response body to replay, or None if the caller should proceed and later call `store`."""
    if not key:
        raise invalid_request("Idempotency-Key header is required for this mutation")
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
