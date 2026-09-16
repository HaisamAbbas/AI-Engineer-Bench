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

Storage-architecture note (fifth review, finding #2): storing candidate
bytes in PostgreSQL is a DOCUMENTED deviation from ADR-08 ("PostgreSQL for
hosted metadata, object storage for artifacts"), superseded for
candidate-staging artifacts by the decision recorded as "ADR-11" in
docs/implementation/DECISIONS.md (the frozen spec files are hash-pinned by
scripts/dev.py, so the supersession lives in the decisions ledger, not by
editing the architecture doc); an S3-compatible backend remains the end
state for the ENG-019 hosted registry. The guardrails the spec's artifact
contract requires are enforced here and at the database level: a per-blob
size cap matching the submission policy's own max_artifact_bytes (52 MiB),
a retention class distinguishing staging from committed evidence, and a
24-hour expiry on unreferenced staging blobs (spec section 37: "Orphaned
unreferenced staging objects expire after 24 hours by default; committed
evidence is never deleted by that cleanup job") - implemented by
repository.purge_expired_worker_artifacts, which the reconciler runs. Blobs
acquired by a committed candidate are promoted to the 'evidence' class by
repository.attach_candidate_references and are never removed by the purge
job; shared blobs with remaining live references are likewise never
deleted.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
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

from ..models import MAX_WORKER_ARTIFACT_BYTES, WorkerArtifactBlobRow, WorkerArtifactReferenceRow

# Spec section 37: unreferenced staging objects expire after 24 hours by
# default. Stamped onto every staging blob at first insert and enforced by
# the reconciler's purge pass.
WORKER_ARTIFACT_STAGING_TTL = timedelta(hours=24)


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
        self._owned_engine = None

    def __getstate__(self) -> dict[str, str]:
        """Reopen the database connection pool inside the isolated BUILD child.

        SQLAlchemy engines and sessionmakers are process-local. Passing only
        the URL avoids inheriting a live connection pool across spawn while
        keeping the generic runner independent of this implementation.
        """
        bind = self._session_factory.kw.get("bind")
        url = getattr(bind, "url", None)
        if url is None:
            raise TypeError("PostgresArtifactStore requires a bound engine for isolated BUILD")
        return {"database_url": url.render_as_string(hide_password=False)}

    def __setstate__(self, state: dict[str, str]) -> None:
        from sqlalchemy import create_engine

        engine = create_engine(state["database_url"], pool_pre_ping=True)
        self._owned_engine = engine
        self._session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def close(self) -> None:
        if self._owned_engine is not None:
            self._owned_engine.dispose()
            self._owned_engine = None

    def put_bytes(self, data: bytes, *, retention_class: str = "staging", staged_until: datetime | None = None) -> BlobRef:
        if len(data) > MAX_WORKER_ARTIFACT_BYTES:
            # Same cap the submission policy enforces on a collected candidate;
            # enforced here at the store boundary AND at the database level
            # (ck_worker_artifact_blob_max_bytes) so an oversized artifact can
            # never silently accumulate in the control-plane database.
            raise ArtifactValidationError(
                f"artifact exceeds the maximum worker artifact size ({len(data)} > {MAX_WORKER_ARTIFACT_BYTES} bytes)"
            )
        if retention_class not in {"staging", "evidence"}:
            raise ArtifactValidationError(f"unknown retention class: {retention_class!r}")
        digest = _sha256_bytes(data)
        blob = BlobRef(digest, len(data))
        with self._session_factory() as session:
            existing = session.get(WorkerArtifactBlobRow, digest)
            if existing is not None:
                if existing.byte_length != len(data):
                    raise ArtifactIntegrityError("stored blob length mismatch for existing digest")
                return blob
            if staged_until is None and retention_class == "staging":
                staged_until = datetime.now(timezone.utc) + WORKER_ARTIFACT_STAGING_TTL
            session.add(
                WorkerArtifactBlobRow(
                    sha256=digest, byte_length=len(data), data=data,
                    retention_class=retention_class, staged_until=staged_until,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # A concurrent worker already committed the same content-addressed
                # blob between our read and our insert - the same bytes under the
                # same digest, so this is a safe no-op, not a conflict. The first
                # writer's retention metadata stands.
                session.rollback()
        return blob

    def create_reference(
        self, blob: BlobRef, *, access_scope: str, visibility: str = "restricted", candidate_id=None,
    ) -> ArtifactReference:
        if visibility not in {"restricted", "public"} or not access_scope:
            raise ArtifactValidationError("reference visibility and scope are required")
        self.verify(blob)
        reference_id = uuid4()
        with self._session_factory() as session:
            session.add(
                WorkerArtifactReferenceRow(
                    id=reference_id, blob_sha256=blob.sha256, candidate_id=candidate_id,
                    access_scope=access_scope, visibility=visibility,
                )
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
