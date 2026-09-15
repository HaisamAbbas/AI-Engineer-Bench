"""A real, genuinely shared ArtifactStore backed by PostgreSQL (ENG-015
review finding #5): "the topology claim is more honest, but the hosted
storage requirement remains unimplemented." Candidate bytes previously
lived only in a worker-local `FilesystemArtifactStore` - cross-host
recovery ("a different worker can reconstruct a candidate from the
database alone") was only true if operators separately provisioned a
shared/network filesystem for every worker's `AIEB_WORKER_WORK_ROOT`, which
was documented as a limitation rather than actually the default.

`PostgresArtifactStore` implements the exact same `ArtifactStore` protocol
`aieb_runner.artifacts.FilesystemArtifactStore` does, so `collect_candidate`/
`reconstruct_candidate` (aieb-runner, unchanged) work identically against
either - but every worker already needs `AIEB_DATABASE_URL` to participate
in leasing at all, so pointing candidate bytes at that same database makes
shared storage genuinely the default, with no second infrastructure
dependency (no S3/object-store client, no operator-provisioned network
mount) and no change to aieb-runner's own generic storage abstraction.

The local CLI is unaffected: it has no database at all (spec: "Local
reports require no hosted account") and continues to use
FilesystemArtifactStore exclusively.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from aieb_runner.artifacts import (
    ArtifactAccessDenied,
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactReference,
    ArtifactValidationError,
    BlobRef,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ..models import WorkerArtifactBlobRow, WorkerArtifactReferenceRow


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PostgresArtifactStore:
    """Implements the same four-method `ArtifactStore` protocol
    `FilesystemArtifactStore` does, backed by `worker_artifact_blob`/
    `worker_artifact_reference` instead of local files. Opens and closes its
    own session per call (via the supplied `session_factory`) rather than
    holding one open across calls, matching how every other worker-side
    repository function in this codebase manages sessions."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def put_bytes(self, data: bytes) -> BlobRef:
        digest = _sha256_bytes(data)
        blob = BlobRef(digest, len(data))
        with self._session_factory() as session:
            existing = session.get(WorkerArtifactBlobRow, digest)
            if existing is not None:
                if existing.byte_length != len(data):
                    raise ArtifactIntegrityError("stored blob length mismatch for existing digest")
                return blob
            session.add(WorkerArtifactBlobRow(sha256=digest, byte_length=len(data), data=data))
            try:
                session.commit()
            except IntegrityError:
                # A concurrent worker already committed the same content-addressed
                # blob between our read and our insert - the same bytes under the
                # same digest, so this is a safe no-op, not a conflict.
                session.rollback()
        return blob

    def create_reference(self, blob: BlobRef, *, access_scope: str, visibility: str = "restricted") -> ArtifactReference:
        if visibility not in {"restricted", "public"} or not access_scope:
            raise ArtifactValidationError("reference visibility and scope are required")
        self.verify(blob)
        reference_id = uuid4()
        with self._session_factory() as session:
            session.add(
                WorkerArtifactReferenceRow(id=reference_id, blob_sha256=blob.sha256, access_scope=access_scope, visibility=visibility)
            )
            session.commit()
        return ArtifactReference(reference_id, blob, access_scope, visibility)

    def read(self, reference: ArtifactReference, *, principal_scope: str) -> bytes:
        with self._session_factory() as session:
            row = session.get(WorkerArtifactReferenceRow, reference.id)
            if row is None:
                raise ArtifactNotFound(f"reference does not exist: {reference.id}")
            if (row.blob_sha256, row.access_scope, row.visibility) != (reference.blob.sha256, reference.access_scope, reference.visibility):
                raise ArtifactIntegrityError("reference metadata does not match supplied reference")
            if reference.visibility != "public" and reference.access_scope != principal_scope:
                raise ArtifactAccessDenied("artifact reference is not readable by this scope")
            blob_row = session.get(WorkerArtifactBlobRow, reference.blob.sha256)
            if blob_row is None:
                raise ArtifactNotFound(f"blob does not exist: {reference.blob.sha256}")
            data = bytes(blob_row.data)
        if len(data) != reference.blob.byte_length or _sha256_bytes(data) != reference.blob.sha256:
            raise ArtifactIntegrityError("stored blob digest or length mismatch")
        return data

    def verify(self, blob: BlobRef) -> None:
        with self._session_factory() as session:
            row = session.get(WorkerArtifactBlobRow, blob.sha256)
            if row is None:
                raise ArtifactNotFound(f"blob does not exist: {blob.sha256}")
            data = bytes(row.data)
        if len(data) != blob.byte_length or _sha256_bytes(data) != blob.sha256:
            raise ArtifactIntegrityError("stored blob digest or length mismatch")
