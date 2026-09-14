"""Shared validation for reading a stored task/entrant revision manifest.

Used by both the registry read routes and campaign freeze, which resolves
task/entrant manifests to build the frozen campaign snapshot - the same
corrupt-manifest failure mode applies at both call sites.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .errors import service_unavailable

ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_stored_manifest(model_type: type[ModelT], manifest: dict, *, kind: str, row_id: object) -> ModelT:
    try:
        return model_type.model_validate(manifest)
    except ValidationError as exc:
        raise service_unavailable(f"stored {kind} revision {row_id} failed contract validation") from exc
