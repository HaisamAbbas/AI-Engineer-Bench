"""Canonical JSON and SHA-256 identities owned by AIEB."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class CanonicalizationError(ValueError):
    """Raised when a value cannot participate in an AIEB content identity."""


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="json", exclude_none=False))
    if isinstance(value, dict):
        return {str(key): _normalise(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(child) for child in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalizationError("non-finite decimal is not canonical")
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("NaN and Infinity are forbidden")
        raise CanonicalizationError("floating-point values are forbidden; use schema-owned strings")
    return value


def canonical_bytes(value: Any) -> bytes:
    """Serialize with sorted keys, UTF-8, explicit nulls, and no float ambiguity."""
    try:
        encoded = json.dumps(
            _normalise(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(str(exc)) from exc
    return encoded.encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
