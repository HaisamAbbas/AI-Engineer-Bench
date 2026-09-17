"""PostgreSQL persistence models for hosted metadata (ENG-014).

Resolved manifests live as JSONB alongside typed searchable columns; JSONB
does not replace core referential integrity (spec section 30). Money is
stored as NUMERIC micro-USD, never binary float. NULL means unknown usage,
distinct from zero (spec section 30/20).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import event

from .db import Base

# Upper bound for a single worker artifact blob, shared by the API-level
# validation in PostgresArtifactStore.put_bytes and the database-level
# CHECK constraint below. Matches the submission policy's own
# max_artifact_bytes cap (52 MiB) used across the codebase.
MAX_WORKER_ARTIFACT_BYTES = 52_428_800


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _timestamps() -> tuple[Mapped[datetime], Mapped[datetime]]:
    created = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    return created, updated


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    oidc_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    oidc_issuer: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_issuer_subject"),)


class RoleBinding(Base):
    __tablename__ = "role_bindings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role in ('visitor','local_developer','submitter','operator','reviewer','administrator')",
            name="ck_role_bindings_role",
        ),
        UniqueConstraint("user_id", "role", "scope", name="uq_role_bindings_identity"),
        Index("ix_role_bindings_user", "user_id"),
    )


class TaskRevisionRow(Base):
    __tablename__ = "task_revision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("evaluator_revision.id"), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Public ticket narrative from the task's instruction.md. Kept outside
    # the canonical task manifest, but bound into revision_digest alongside
    # the manifest so the exact published ticket is part of revision identity.
    ticket_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("slug", "version", name="uq_task_revision_slug_version"),
        Index("ix_task_revision_family_category", "family_id", "category"),
    )


class EvaluatorRevisionRow(Base):
    __tablename__ = "evaluator_revision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending-independent-review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("code_digest", "contract_version", name="uq_evaluator_revision_digest_contract"),
        CheckConstraint(
            "review_status in ('pending-independent-review','reviewed','rejected')",
            name="ck_evaluator_revision_review_status",
        ),
    )


class ProtocolRevisionRow(Base):
    """Immutable, queryable protocol documents used by frozen campaigns."""

    __tablename__ = "protocol_revision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    version: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    scoring_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FixtureRevisionRow(Base):
    __tablename__ = "fixture_revision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    family_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (CheckConstraint("visibility in ('public','restricted')", name="ck_fixture_revision_visibility"),)


class SuiteReleaseRow(Base):
    __tablename__ = "suite_release"

    id: Mapped[uuid.UUID] = _uuid_pk()
    release_version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (CheckConstraint("status in ('draft','released','deprecated','withdrawn')", name="ck_suite_release_status"),)


class SuiteTaskRow(Base):
    __tablename__ = "suite_task"

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_release_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("suite_release.id"), nullable=False)
    task_revision_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("task_revision.id"), nullable=False)
    weight: Mapped[str] = mapped_column(Numeric(9, 6), nullable=False, default="1.0")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    frozen: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (UniqueConstraint("suite_release_id", "task_revision_id", name="uq_suite_task_release_task"),)


class EntrantRevisionRow(Base):
    __tablename__ = "entrant_revision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    track: Mapped[str] = mapped_column(String(16), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("slug", "version", name="uq_entrant_revision_slug_version"),
        CheckConstraint("track in ('agents','models')", name="ck_entrant_revision_track"),
    )


class CampaignRow(Base):
    __tablename__ = "campaign"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cohort_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Who created the campaign (ENG-017). Nullable for rows predating the
    # column; set at create time. Used to reject self-approval of a
    # publication (a reviewer may not approve a campaign they created).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    submitter_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state in ('draft','frozen','running','paused','cancelling','cancelled','completed','incomplete')",
            name="ck_campaign_state",
        ),
        Index("ix_campaign_state", "state"),
    )


class TrialRow(Base):
    __tablename__ = "trial"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaign.id"), nullable=False)
    task_revision_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("task_revision.id"), nullable=False)
    entrant_revision_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("entrant_revision.id"), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False)
    cell_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "task_revision_id", "entrant_revision_id", "repetition", name="uq_trial_campaign_task_entrant_repetition"
        ),
    )


class AttemptRow(Base):
    __tablename__ = "attempt"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trial_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("trial.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    terminal_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("trial_id", "number", name="uq_attempt_trial_number"),
        CheckConstraint(
            "phase in ('queued','provisioning','engineering','collecting','building','verifying','finalizing','terminal')",
            name="ck_attempt_phase",
        ),
    )


class AttemptEventRow(Base):
    """Append-only lifecycle observations available to Run Evidence."""

    __tablename__ = "attempt_event"

    id: Mapped[uuid.UUID] = _uuid_pk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("attempt.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("attempt_id", "sequence", name="uq_attempt_event_sequence"),
        Index("ix_attempt_event_chronology", "attempt_id", "sequence"),
    )


class WorkItemRow(Base):
    __tablename__ = "work_item"

    id: Mapped[uuid.UUID] = _uuid_pk()
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("attempt.id"), nullable=True)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("evaluation.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("state in ('ready','leased','done','failed')", name="ck_work_item_state"),
        # `type` deliberately has no CHECK constraint restricting its values:
        # ENG015-007 added a second type ("verification", alongside the
        # original "engineering") without a schema migration for this column.
        Index("ix_work_item_ready_lease", "type", "state", "lease_expiry", postgresql_where=(state == "ready")),
    )


class CandidateRow(Base):
    __tablename__ = "candidate"

    id: Mapped[uuid.UUID] = _uuid_pk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("attempt.id"), nullable=False)
    tree_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # The full StoredCandidate (aieb_runner.artifacts) - manifest plus every
    # changed file's artifact-store reference - serialized as JSON (ENG015-007).
    # Without this, only tree/manifest digests were persisted, which cannot
    # reconstruct a candidate: independently-leased verification (possibly a
    # different worker, possibly after this one crashed) needs the actual
    # file references to call reconstruct_candidate(), not just its digest.
    stored_candidate: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    stored_candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("attempt_id", "tree_digest", name="uq_candidate_attempt_tree"),
        CheckConstraint("validation_status in ('pending','valid','rejected')", name="ck_candidate_validation_status"),
    )


@event.listens_for(TaskRevisionRow, "before_insert")
def _bind_task_revision_identity(_mapper, _connection, target: TaskRevisionRow) -> None:
    from .evidence_integrity import task_revision_digest

    target.revision_digest = task_revision_digest(target.manifest, target.ticket_text)


@event.listens_for(CandidateRow, "before_insert")
def _bind_candidate_display_evidence(_mapper, _connection, target: CandidateRow) -> None:
    from .evidence_integrity import evidence_digest

    target.stored_candidate_digest = evidence_digest(target.stored_candidate)


class EvaluationRow(Base):
    __tablename__ = "evaluation"

    id: Mapped[uuid.UUID] = _uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("candidate.id"), nullable=False)
    evaluator_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("evaluator_revision.id"), nullable=False)
    fixture_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("fixture_revision.id"), nullable=False)
    schedule_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evaluation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # Set when this evaluation was produced by a scorer-correction regrade
    # (ENG-018). NULL for original campaign evaluations. Corrected evaluations
    # fold the correction's scoring digest into schedule_digest, so they carry
    # a distinct identity and are retained ALONGSIDE the originals, never
    # replacing them (historical raw envelopes are never mutated).
    correction_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("correction_run.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("candidate_id", "evaluator_id", "fixture_id", "schedule_digest", name="uq_evaluation_plan_digest"),
        CheckConstraint("verdict is null or verdict in ('pass','fail','contract_violation','indeterminate')", name="ck_evaluation_verdict"),
    )


@event.listens_for(EvaluationRow, "before_insert")
def _bind_evaluation_evidence(_mapper, _connection, target: EvaluationRow) -> None:
    from .evidence_integrity import evaluation_digest

    target.evaluation_digest = evaluation_digest(
        candidate_id=target.candidate_id,
        evaluator_id=target.evaluator_id,
        fixture_id=target.fixture_id,
        schedule_digest=target.schedule_digest,
        verdict=target.verdict,
        result=target.result,
    )


class ArtifactRow(Base):
    __tablename__ = "artifact"

    id: Mapped[uuid.UUID] = _uuid_pk()
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ArtifactRefRow(Base):
    __tablename__ = "artifact_ref"

    id: Mapped[uuid.UUID] = _uuid_pk()
    artifact_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("artifact.id"), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (CheckConstraint("visibility in ('public','private')", name="ck_artifact_ref_visibility"),)


class UsageRequestRow(Base):
    __tablename__ = "usage_request"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("attempt.id"), nullable=True)
    reservation_usd: Mapped[str | None] = mapped_column(Numeric(20, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("actor_role", "request_id", name="uq_usage_request_scoped_identity"),
        CheckConstraint(
            "actor_role in ('engineer','dev_application','verifier_application','verifier_judge')",
            name="ck_usage_request_actor_role",
        ),
        Index("ix_usage_request_attempt", "attempt_id"),
    )


class UsageReceiptRow(Base):
    __tablename__ = "usage_receipt"

    id: Mapped[uuid.UUID] = _uuid_pk()
    usage_request_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("usage_request.id"), nullable=False)
    physical_retry: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reported_cost_usd: Mapped[str | None] = mapped_column(Numeric(20, 6), nullable=True)
    estimated_cost_usd: Mapped[str | None] = mapped_column(Numeric(20, 6), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("usage_request_id", "physical_retry", name="uq_usage_receipt_scoped_identity"),
    )


class PublicationRow(Base):
    __tablename__ = "publication"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaign.id"), nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("publication.id"), nullable=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Versioned immutable manifest maps trial IDs to explicit inclusion and
    # exact attempt/candidate/evaluation IDs. NULL legacy publications are
    # deliberately ineligible for public run evidence.
    evidence_manifest: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    evidence_manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ENG-018 signed manifest. The Ed25519 signature (base64) over the
    # canonical signed_manifest, the PEM public key and its id, and the
    # canonical document itself - all set at insert (publish) time and frozen
    # by the publication immutability trigger. review_kind is the honest
    # 'single_maintainer' vs 'independent' label; reason records a
    # withdrawal/correction rationale (mutable, unlike the signed evidence).
    manifest_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signing_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    signing_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_manifest: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    review_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Two DISTINCT reasons: `reason` records the correction/supersession
    # rationale (frozen at publish by the immutability trigger);
    # `withdrawal_reason` records why a publication was withdrawn (the only
    # provenance field a withdrawal may set, exactly once).
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ranked = eligible to be the canonical ranking release; non_ranked =
    # explicitly excluded from canonical ranks (e.g. an incomplete cohort
    # published with disclosure). Defaulted for historical rows.
    publication_class: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ranked")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("status in ('published','withdrawn','superseded')", name="ck_publication_status"),
        CheckConstraint("review_kind is null or review_kind in ('single_maintainer','independent')", name="ck_publication_review_kind"),
        CheckConstraint("publication_class in ('ranked','non_ranked')", name="ck_publication_class"),
        CheckConstraint(
            "(evidence_manifest IS NULL) = (evidence_manifest_digest IS NULL)",
            name="ck_publication_evidence_manifest_digest_pair",
        ),
        Index("ix_publication_chronology", "created_at"),
    )


@event.listens_for(PublicationRow, "before_insert")
def _bind_publication_evidence_manifest(_mapper, _connection, target: PublicationRow) -> None:
    if target.evidence_manifest is not None:
        from .evidence_integrity import evidence_digest

        target.evidence_manifest_digest = evidence_digest(target.evidence_manifest)


class ReviewRow(Base):
    __tablename__ = "review"

    id: Mapped[uuid.UUID] = _uuid_pk()
    reviewer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (CheckConstraint("decision in ('approve','reject')", name="ck_review_decision"),)


class AuditEventRow(Base):
    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_audit_event_target", "target_type", "target_id"),)


class BudgetReservationRow(Base):
    """ENG-017 budget reservation for a started campaign.

    With no provider reservation integration (ENG-008), a hosted reservation is
    an ESTIMATED_TIME_LIMITED recorded intent - the summed per-role budget the
    frozen cohort declared - not a real hard hold on provider spend. Modeled
    explicitly so the honest enforcement level is visible, never implied to be
    a hard cap.
    """

    __tablename__ = "budget_reservation"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaign.id"), nullable=False)
    reservation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enforcement: Mapped[str] = mapped_column(String(32), nullable=False)
    reserved_usd: Mapped[object | None] = mapped_column(Numeric(20, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("campaign_id", name="uq_budget_reservation_campaign"),
        CheckConstraint("enforcement in ('hard','estimated_time_limited')", name="ck_budget_reservation_enforcement"),
        CheckConstraint("status in ('active','released','consumed')", name="ck_budget_reservation_status"),
    )


class CorrectionRunRow(Base):
    """ENG-018 scorer-correction regrade run: re-scores a campaign's retained
    candidates with a corrected evaluator/fixture. `scoring_correction_digest`
    is folded into each regrade evaluation's schedule_digest so a corrected
    evaluation carries a DISTINCT identity (no `uq_evaluation_plan_digest`
    collision) and is retained alongside the original."""

    __tablename__ = "correction_run"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaign.id"), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    corrected_evaluator_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("evaluator_revision.id"), nullable=False)
    corrected_fixture_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("fixture_revision.id"), nullable=False)
    scoring_correction_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("status in ('running','completed','failed')", name="ck_correction_run_status"),
        Index("ix_correction_run_campaign", "campaign_id"),
    )


class PublicationPreparationRow(Base):
    """ENG-018 publication preparation and review record. Kept separate from the
    strictly-immutable `PublicationRow` (which is inserted only at publish time)
    so the prepare -> review -> publish flow never mutates a published snapshot.
    """

    __tablename__ = "publication_preparation"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("campaign.id"), nullable=False)
    prepared_by_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    review_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prepared")
    supersedes_publication_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("publication.id"), nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_publication_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("publication.id"), nullable=True)
    publication_class: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ranked")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("status in ('prepared','approved','rejected','published')", name="ck_publication_preparation_status"),
        CheckConstraint("review_kind is null or review_kind in ('single_maintainer','independent')", name="ck_publication_preparation_review_kind"),
        CheckConstraint("publication_class in ('ranked','non_ranked')", name="ck_publication_preparation_class"),
        Index("ix_publication_preparation_campaign", "campaign_id"),
    )


class IdempotencyRecordRow(Base):
    """API-01: same key + same body replays the stored response; same key + different body is 409."""

    __tablename__ = "idempotency_record"

    id: Mapped[uuid.UUID] = _uuid_pk()
    scope: Mapped[str] = mapped_column(String(256), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)


class WorkerArtifactBlobRow(Base):
    """Content-addressed candidate bytes, in the SAME PostgreSQL database
    every worker already connects to (AIEB_DATABASE_URL) - not a local
    filesystem path that would need separate shared/network storage
    provisioning across every worker host to make cross-host recovery real.
    This is the hosted worker's PostgresArtifactStore backing table
    (ENG-015 review finding #5 - "the hosted storage requirement remains
    unimplemented"); the local CLI's FilesystemArtifactStore (ENG-003) is
    unaffected and unchanged, since it has no database at all.

    Deviation from ADR-08 ("PostgreSQL for hosted metadata, object storage
    for artifacts") is explicit and documented: the frozen spec files are
    hash-pinned (scripts/dev.py), so the superseding decision lives in the
    decisions ledger - docs/implementation/DECISIONS.md, ENG015-010 "ADR-11
    (recorded in DECISIONS.md)" - which supersedes ADR-08 for
    candidate-staging artifacts, with the S3-compatible backend still the
    required end state for the ENG-019 hosted registry. Constraint
    correspondence with the spec's own artifact contract (section 33):
    `octet_length(data) <= MAX_WORKER_ARTIFACT_BYTES` (and `= byte_length`)
    bounds per-blob size to the same cap the submission policy already
    enforces on collected candidates, checked against the REAL stored bytes
    rather than only the caller-supplied `byte_length` column (review
    finding #5); `retention_class` and `staged_until` implement the
    staging/orphan semantics from spec section 37 (unreferenced staging
    objects expire after 24 hours; committed evidence is never deleted by
    that job, and shared blobs with live references are never removed).
    These columns/constraints and `candidate_id` below were added by a
    separate migration (f2b6c9a417de), not by editing the original
    e20d5d09b489 in place (review finding #3: that revision was already
    committed before this hardening began)."""

    __tablename__ = "worker_artifact_blob"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(16), nullable=False, server_default="staging")
    staged_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # A real size guarantee, not merely a check on the caller-supplied
        # byte_length column (ENG-015 review finding #5): a row could
        # previously claim byte_length=1 while storing up to MAX bytes of
        # actual `data`, since nothing compared byte_length against
        # octet_length(data). Both constraints together (migration
        # f2b6c9a417de) require byte_length to genuinely equal the stored
        # bytes' real length, nonnegative, and within the submission
        # policy's cap.
        CheckConstraint(
            "byte_length >= 0 AND octet_length(data) = byte_length", name="ck_worker_artifact_blob_byte_length_matches_data",
        ),
        CheckConstraint(
            f"octet_length(data) <= {MAX_WORKER_ARTIFACT_BYTES}", name="ck_worker_artifact_blob_max_bytes",
        ),
        CheckConstraint("retention_class in ('staging', 'evidence')", name="ck_worker_artifact_blob_retention_class"),
    )


class WorkerArtifactReferenceRow(Base):
    """An access-controlled reference to one worker_artifact_blob row -
    mirrors FilesystemArtifactStore's on-disk reference metadata exactly
    (id/blob digest/access_scope/visibility), just persisted in Postgres
    instead of a JSON file next to the blob.

    `candidate_id` ties the reference to the exact candidate whose
    engineering phase created it (review finding #2: the previous schema
    had no candidate linkage and therefore no notion of retention
    ownership); it stays NULL for the legacy server_default rows that
    predate this column."""

    __tablename__ = "worker_artifact_reference"

    id: Mapped[uuid.UUID] = _uuid_pk()
    blob_sha256: Mapped[str] = mapped_column(String(64), ForeignKey("worker_artifact_blob.sha256"), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("candidate.id"), nullable=True)
    access_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="restricted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("visibility in ('public','restricted')", name="ck_worker_artifact_reference_visibility"),
        Index("ix_worker_artifact_reference_candidate", "candidate_id"),
        Index("ix_worker_artifact_reference_blob", "blob_sha256"),
    )
