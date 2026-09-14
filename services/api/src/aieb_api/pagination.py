"""Cursor pagination: default 50, max 200 (spec section 33)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from .errors import invalid_request

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1 or limit > MAX_LIMIT:
        raise invalid_request(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    payload = json.dumps([created_at.isoformat(), str(row_id)])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        created_at_raw, row_id_raw = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        return datetime.fromisoformat(created_at_raw), UUID(row_id_raw)
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is a client error
        raise invalid_request("invalid pagination cursor") from exc


def page(items: list[Any], limit: int, *, created_at_attr: str = "created_at", id_attr: str = "id") -> tuple[list[Any], str | None]:
    """Given rows already fetched with limit+1, split into a page and a next cursor."""
    has_more = len(items) > limit
    page_items = items[:limit]
    next_cursor = None
    if has_more and page_items:
        last = page_items[-1]
        next_cursor = encode_cursor(getattr(last, created_at_attr), getattr(last, id_attr))
    return page_items, next_cursor
