"""Provider-neutral, capability-scoped access to private holdout objects.

The registry stores only an opaque URI and content identity.  This module is
the narrow boundary an evaluator may use: it receives a frozen manifest's
capability, never a path or credential, verifies the bytes against the frozen
digest/length, and records success or failure in the append-only audit log.

The checked-in directory adapter is development-only and requires an explicit
configuration.  S3/GS references fail closed until the deployment supplies an
approved private provider adapter; they are never silently fetched over a
public URL or replaced with a repository-local fallback.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from . import db
from .errors import ApiError, service_unavailable
from .holdouts import audit_access, require_frozen
from .models import HoldoutManifestRow


class HoldoutStorageError(RuntimeError):
    """A private provider could not safely supply the selected object."""


@dataclass(frozen=True)
class HoldoutCapability:
    """Opaque, least-authority input given to an evaluator adapter."""

    holdout_id: UUID
    object_digest: str
    object_length: int
    access_scope: str


class PrivateHoldoutStore(Protocol):
    def read(self, capability: HoldoutCapability) -> bytes: ...


def _capability(row: HoldoutManifestRow) -> HoldoutCapability:
    return HoldoutCapability(
        holdout_id=row.id,
        object_digest=row.object_digest,
        object_length=row.object_length,
        access_scope=row.access_scope,
    )


class DirectoryPrivateHoldoutStore:
    """Explicit local development store; never an official provider.

    The URI's ``private://`` suffix is treated as a relative object key.  The
    resolved path must remain below ``root`` and symlinks are rejected so a
    fixture cannot escape the configured private directory.
    """

    def __init__(self, root: Path, *, official: bool = False) -> None:
        self.root = root.resolve()
        if official:
            raise HoldoutStorageError("the directory adapter cannot be used for official holdouts")

    def read(self, capability: HoldoutCapability, *, object_key: str) -> bytes:
        normal_key = object_key.replace("\\", "/")
        if (
            not normal_key
            or normal_key.startswith("/")
            or re.match(r"^[A-Za-z]:", normal_key)
            or "\\" in object_key
            or ".." in normal_key.split("/")
        ):
            raise HoldoutStorageError("private holdout object key is invalid")
        # Check every un-resolved component first. Calling ``resolve`` before
        # this check erases symlinks and would accidentally allow an
        # intermediate link (or Windows junction/reparse point) to redirect
        # an otherwise-contained object key.
        raw_path = self.root / object_key
        component = self.root
        for part in Path(normal_key).parts:
            if part in ("", "."):
                continue
            component = component / part
            is_junction = getattr(component, "is_junction", lambda: False)()
            if component.is_symlink() or is_junction:
                raise HoldoutStorageError("private holdout object symlinks are not permitted")
        path = raw_path.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise HoldoutStorageError("private holdout object escaped its configured root") from exc
        if not path.is_file():
            raise HoldoutStorageError("private holdout object is unavailable")
        data = path.read_bytes()
        if len(data) != capability.object_length or hashlib.sha256(data).hexdigest() != capability.object_digest:
            raise HoldoutStorageError("private holdout object failed digest or length verification")
        return data


def configured_directory_store() -> DirectoryPrivateHoldoutStore:
    value = os.environ.get("AIEB_PRIVATE_HOLDOUT_ROOT", "").strip()
    if not value:
        raise service_unavailable("private holdout storage is not configured")
    if os.environ.get("AIEB_HOLDOUT_LOCAL_MODE") != "1":
        raise service_unavailable("the local private holdout adapter is disabled")
    return DirectoryPrivateHoldoutStore(Path(value))


def read_frozen_holdout(
    session: Session,
    *,
    holdout_id: UUID,
    access_scope: str,
    actor_identity: str,
    campaign_id: UUID | None = None,
    attempt_id: UUID | None = None,
    request_id: str | None = None,
) -> bytes:
    """Read a frozen object through a capability and audit the attempt.

    The caller supplies only the manifest ID and declared access scope. The
    provider adapter resolves the opaque manifest URI internally; no path or
    signed URL crosses the evaluator boundary.
    """
    row = require_frozen(session, holdout_id, access_scope=access_scope)
    capability = _capability(row)
    try:
        if row.storage_uri.startswith("private://"):
            object_key = row.storage_uri.removeprefix("private://")
            data = configured_directory_store().read(capability, object_key=object_key)
        else:
            # No public URL fallback.  A deployment must register an approved
            # S3/GS adapter before an official holdout can be consumed.
            raise HoldoutStorageError("configured private provider adapter is unavailable")
    except (HoldoutStorageError, ApiError):
        _persist_access_audit(
            holdout_id=holdout_id, actor_identity=actor_identity,
            operation="read", object_digest=row.object_digest, success=False,
            access_scope=access_scope, campaign_id=campaign_id,
            attempt_id=attempt_id, request_id=request_id,
        )
        raise
    _persist_access_audit(
        holdout_id=holdout_id, actor_identity=actor_identity,
        operation="read", object_digest=row.object_digest, success=True,
        access_scope=access_scope, campaign_id=campaign_id,
        attempt_id=attempt_id, request_id=request_id,
    )
    return data


def _persist_access_audit(**values: object) -> None:
    """Commit an access event independently of the evaluator transaction."""
    audit_session = None
    try:
        audit_session = db.session_factory()()
        audit_access(audit_session, **values)
        audit_session.commit()
    except Exception:
        if audit_session is not None:
            audit_session.rollback()
        # A failed-read audit must never hide the original storage/provider
        # failure. Successful reads fail closed rather than returning an
        # unaudited private object.
        if not values.get("success"):
            return
        raise HoldoutStorageError("private holdout access audit could not be persisted")
    finally:
        if audit_session is not None:
            audit_session.close()
