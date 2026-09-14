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
