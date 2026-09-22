"""V2-GAP-005 private holdout registry and scoped access boundary.

This module deliberately never accepts or returns holdout bytes. It stores an
opaque private-provider URI plus content identity, requires explicit overlap,
storage, isolation, and manifest review decisions before freezing, and records
every evaluator access.
"""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .evidence_integrity import evidence_digest
from .errors import conflict, forbidden, invalid_request, not_found
from .models import EvaluatorRevisionRow, HoldoutAccessAuditRow, HoldoutManifestRow, HoldoutReviewRow, TaskRevisionRow

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_URI_RE = re.compile(r"^(?:s3|gs|private)://[^\s]+$")
_REVIEW_KINDS = frozenset(("overlap", "storage", "isolation", "manifest"))


def _latest_reviews(reviews: list[HoldoutReviewRow]) -> dict[str, HoldoutReviewRow]:
    """Return the newest decision per review kind.

    Callers must provide rows ordered newest-first, with ``id`` as a stable
    tie-breaker after ``created_at``. A prior approval is never sufficient once
    a later reject or inconclusive decision exists.
    """
    latest: dict[str, HoldoutReviewRow] = {}
    for review in reviews:
        latest.setdefault(review.review_kind, review)
    return latest


def _bounded_report(value: dict) -> dict:
    if not isinstance(value, dict):
        raise invalid_request("holdout review report must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise invalid_request("holdout review report must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise invalid_request("holdout review report exceeds the 64 KiB bound")
    return value


def _lock_manifest(session: Session, holdout: HoldoutManifestRow) -> HoldoutManifestRow:
    """Serialize reviews, freeze, and retirement on the manifest row."""
    locked = session.execute(
        select(HoldoutManifestRow).where(HoldoutManifestRow.id == holdout.id).with_for_update()
    ).scalar_one_or_none()
    if locked is None:
        raise not_found()
    return locked


def validate_private_uri(value: str) -> str:
    value = value.strip()
    if not _PRIVATE_URI_RE.fullmatch(value):
        raise invalid_request("holdout storage_uri must be an opaque private s3://, gs://, or private:// reference")
    return value


def manifest_identity(*, task_revision_id: UUID, storage_uri: str, object_digest: str,
                      object_length: int, protocol_version: str, access_scope: str,
                      overlap_review_digest: str | None, family_id: str = "",
                      evaluator_revision_digest: str = "", split: str = "official",
                      retention_class: str = "official", expires_at: datetime | None = None) -> str:
    return evidence_digest({
        "schema_version": "aieb.private-holdout-manifest/v1",
        "task_revision_id": str(task_revision_id),
        "family_id": family_id,
        "evaluator_revision_digest": evaluator_revision_digest,
        "storage_uri": storage_uri,
        "object_digest": object_digest,
        "object_length": object_length,
        "protocol_version": protocol_version,
        "access_scope": access_scope,
        "split": split,
        "retention_class": retention_class,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "overlap_review_digest": overlap_review_digest,
    })


def create_manifest(session: Session, *, task_revision_id: UUID, storage_uri: str,
                    object_digest: str, object_length: int, protocol_version: str,
                    access_scope: str, created_by_user_id: UUID, split: str = "official",
                    retention_class: str = "official", expires_at: datetime | None = None,
                    overlap_review_digest: str | None = None) -> HoldoutManifestRow:
    task = session.get(TaskRevisionRow, task_revision_id)
    if task is None:
        raise not_found()
    storage_uri = validate_private_uri(storage_uri)
    if not _DIGEST_RE.fullmatch(object_digest) or object_length < 0:
        raise invalid_request("holdout object digest/length is invalid")
    if overlap_review_digest is not None and not _DIGEST_RE.fullmatch(overlap_review_digest):
        raise invalid_request("overlap_review_digest must be a lowercase sha256 digest")
    if split not in {"official", "development"}:
        raise invalid_request("holdout split must be official or development")
    if retention_class not in {"official", "development", "ephemeral"}:
        raise invalid_request("holdout retention_class is invalid")
    if expires_at is not None and expires_at.tzinfo is None:
        raise invalid_request("expires_at must include a timezone")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise invalid_request("expires_at must be in the future")
    if not protocol_version.strip() or not access_scope.strip():
        raise invalid_request("protocol_version and access_scope are required")
    evaluator = session.get(EvaluatorRevisionRow, task.evaluator_id)
    if evaluator is None:
        raise invalid_request("task revision evaluator is unavailable")
    digest = manifest_identity(
        task_revision_id=task_revision_id, storage_uri=storage_uri,
        object_digest=object_digest, object_length=object_length,
        protocol_version=protocol_version, access_scope=access_scope,
        overlap_review_digest=overlap_review_digest, family_id=task.family_id,
        evaluator_revision_digest=evaluator.code_digest, split=split,
        retention_class=retention_class, expires_at=expires_at,
    )
    row = HoldoutManifestRow(
        task_revision_id=task_revision_id, family_id=task.family_id,
        evaluator_revision_digest=evaluator.code_digest, storage_uri=storage_uri,
        object_digest=object_digest, object_length=object_length,
        manifest_digest=digest, overlap_review_digest=overlap_review_digest,
        protocol_version=protocol_version, access_scope=access_scope,
        split=split, retention_class=retention_class, expires_at=expires_at,
        status="draft", created_by_user_id=created_by_user_id,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("a holdout manifest with this task and identity already exists") from exc
    return row


def record_review(session: Session, *, holdout: HoldoutManifestRow, reviewer_user_id: UUID,
                  review_kind: str, decision: str, evidence_digest_value: str,
                  report: dict, reason: str) -> HoldoutReviewRow:
    holdout = _lock_manifest(session, holdout)
    if holdout.status in ("frozen", "retired"):
        raise conflict("frozen or retired holdout manifests cannot receive reviews")
    if review_kind not in _REVIEW_KINDS:
        raise invalid_request("review_kind must be overlap, storage, isolation, or manifest")
    if decision not in ("approve", "reject", "inconclusive"):
        raise invalid_request("decision must be approve, reject, or inconclusive")
    if not _DIGEST_RE.fullmatch(evidence_digest_value):
        raise invalid_request("review evidence_digest must be a lowercase sha256 digest")
    if not reason.strip():
        raise invalid_request("review reason is required")
    if reviewer_user_id == holdout.created_by_user_id:
        raise conflict("holdout author cannot independently review the holdout")
    row = HoldoutReviewRow(
        holdout_manifest_id=holdout.id, reviewer_user_id=reviewer_user_id,
        review_kind=review_kind, decision=decision,
        evidence_digest=evidence_digest_value, report=_bounded_report(report),
        reason=reason.strip(),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("a reviewer has already submitted this review kind") from exc
    if decision == "approve":
        holdout.status = "reviewed"
    return row


def freeze_manifest(session: Session, *, holdout: HoldoutManifestRow,
                    frozen_by_user_id: UUID) -> HoldoutManifestRow:
    holdout = _lock_manifest(session, holdout)
    if holdout.status not in ("draft", "reviewed"):
        raise conflict(f"holdout manifest is {holdout.status}; only a reviewed manifest can freeze")
    if frozen_by_user_id == holdout.created_by_user_id:
        raise conflict("holdout author cannot freeze its official manifest")
    reviews = session.execute(
        select(HoldoutReviewRow).where(
            HoldoutReviewRow.holdout_manifest_id == holdout.id,
        ).order_by(HoldoutReviewRow.created_at.desc(), HoldoutReviewRow.id.desc())
    ).scalars().all()
    latest = _latest_reviews(reviews)
    blocked = sorted(
        kind for kind in _REVIEW_KINDS
        if latest.get(kind) is None or latest[kind].decision != "approve"
    )
    if blocked:
        raise conflict("holdout freeze requires the latest decision for every review kind to approve: " + ", ".join(blocked))
    reviewers = [review.reviewer_user_id for review in latest.values() if review.decision == "approve"]
    if len(set(reviewers)) < 2:
        raise conflict("holdout freeze requires approvals from at least two independent reviewers")
    task = session.get(TaskRevisionRow, holdout.task_revision_id)
    evaluator = session.get(EvaluatorRevisionRow, task.evaluator_id) if task else None
    if task is None or evaluator is None:
        raise conflict("holdout task/evaluator identity is unavailable")
    if holdout.family_id != task.family_id or holdout.evaluator_revision_digest != evaluator.code_digest:
        raise conflict("holdout family/evaluator identity does not match its task revision")
    overlap = latest["overlap"].evidence_digest
    holdout.overlap_review_digest = overlap
    # The overlap evidence is part of the frozen identity.  Draft manifests may
    # not know it yet; recompute once, before the row becomes immutable.
    holdout.manifest_digest = manifest_identity(
        task_revision_id=holdout.task_revision_id,
        storage_uri=holdout.storage_uri,
        object_digest=holdout.object_digest,
        object_length=holdout.object_length,
        protocol_version=holdout.protocol_version,
        access_scope=holdout.access_scope,
        overlap_review_digest=overlap,
        family_id=holdout.family_id,
        evaluator_revision_digest=holdout.evaluator_revision_digest,
        split=holdout.split,
        retention_class=holdout.retention_class,
        expires_at=holdout.expires_at,
    )
    holdout.status = "frozen"
    holdout.frozen_by_user_id = frozen_by_user_id
    holdout.frozen_at = datetime.now(timezone.utc)
    session.flush()
    return holdout


def require_frozen(session: Session, holdout_id: UUID, *, access_scope: str) -> HoldoutManifestRow:
    row = session.get(HoldoutManifestRow, holdout_id)
    if row is None:
        raise not_found()
    if row.status != "frozen":
        raise forbidden("holdout is not frozen for evaluator access")
    if row.access_scope != access_scope:
        raise forbidden("holdout access scope does not match the campaign/evaluator scope")
    if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
        raise forbidden("holdout retention period has expired")
    task = session.get(TaskRevisionRow, row.task_revision_id)
    evaluator = session.get(EvaluatorRevisionRow, task.evaluator_id) if task else None
    if task is None or evaluator is None:
        raise forbidden("holdout task/evaluator identity is unavailable")
    if row.family_id != task.family_id or row.evaluator_revision_digest != evaluator.code_digest:
        raise forbidden("holdout task/evaluator identity is inconsistent")
    expected_digest = manifest_identity(
        task_revision_id=row.task_revision_id, storage_uri=row.storage_uri,
        object_digest=row.object_digest, object_length=row.object_length,
        protocol_version=row.protocol_version, access_scope=row.access_scope,
        overlap_review_digest=row.overlap_review_digest,
        family_id=row.family_id, evaluator_revision_digest=row.evaluator_revision_digest,
        split=row.split, retention_class=row.retention_class, expires_at=row.expires_at,
    )
    if row.manifest_digest != expected_digest:
        raise forbidden("holdout manifest digest is inconsistent")
    reviews = session.execute(
        select(HoldoutReviewRow).where(
            HoldoutReviewRow.holdout_manifest_id == row.id,
        ).order_by(HoldoutReviewRow.created_at.desc(), HoldoutReviewRow.id.desc())
    ).scalars().all()
    latest = _latest_reviews(reviews)
    if any(latest.get(kind) is None or latest[kind].decision != "approve" for kind in _REVIEW_KINDS):
        raise forbidden("holdout review approvals are no longer valid")
    if latest["overlap"].evidence_digest != row.overlap_review_digest:
        raise forbidden("holdout overlap review digest is inconsistent")
    if len({review.reviewer_user_id for review in latest.values()}) < 2:
        raise forbidden("holdout review independence is invalid")
    if row.frozen_by_user_id is None or row.frozen_by_user_id == row.created_by_user_id or row.frozen_at is None:
        raise forbidden("holdout freeze identity is invalid")
    return row


def audit_access(session: Session, *, holdout_id: UUID, actor_identity: str,
                 operation: str, object_digest: str, success: bool,
                 access_scope: str, campaign_id: UUID | None = None,
                 attempt_id: UUID | None = None, request_id: str | None = None) -> None:
    if operation == "review":
        row = session.get(HoldoutManifestRow, holdout_id)
        if row is None:
            raise not_found()
        if row.access_scope != access_scope:
            raise forbidden("holdout access scope does not match the declared scope")
    else:
        row = require_frozen(session, holdout_id, access_scope=access_scope)
    if object_digest != row.object_digest:
        raise invalid_request("holdout access object digest does not match the frozen manifest")
    if operation not in ("read", "list", "download", "verify", "review"):
        raise invalid_request("unsupported holdout audit operation")
    session.add(HoldoutAccessAuditRow(
        holdout_manifest_id=row.id, actor_identity=actor_identity[:256],
        operation=operation, campaign_id=campaign_id, attempt_id=attempt_id,
        object_digest=object_digest, success=success, request_id=request_id,
    ))
