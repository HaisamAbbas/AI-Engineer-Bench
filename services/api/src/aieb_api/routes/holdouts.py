"""Private holdout-manifest administration (V2-GAP-005).

No endpoint accepts fixture bytes or returns private content. Operators submit
opaque storage identity; reviewers append decisions; only a reviewed manifest
can freeze and become evaluator-accessible.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import holdouts
from ..auth import current_principal_id, require_role
from ..db import get_session
from ..errors import conflict, not_found
from ..idempotency import check_or_reserve, finalize, principal_scope, required_idempotency_key
from ..models import HoldoutManifestRow, HoldoutReviewRow
from ..schemas import HoldoutManifestCreateRequest, HoldoutManifestSummary, HoldoutReviewRequest, HoldoutReviewSummary

router = APIRouter(prefix="/v1/maintainer/holdouts", tags=["maintainer-holdouts"])


def _user_id(session: Session, identity) -> UUID:
    value = current_principal_id(session, identity)
    if value is None:
        raise conflict("holdout administration requires a provisioned user")
    return UUID(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _summary(session: Session, row: HoldoutManifestRow) -> HoldoutManifestSummary:
    approved = session.execute(
        select(HoldoutReviewRow).where(
            HoldoutReviewRow.holdout_manifest_id == row.id,
        ).order_by(HoldoutReviewRow.created_at.desc(), HoldoutReviewRow.id.desc())
    ).scalars().all()
    latest = holdouts._latest_reviews(approved)  # noqa: SLF001 - route projection uses the same freeze rule
    return HoldoutManifestSummary(
        id=row.id, task_revision_id=row.task_revision_id, family_id=row.family_id,
        evaluator_revision_digest=row.evaluator_revision_digest, storage_uri=row.storage_uri,
        object_digest=row.object_digest, object_length=row.object_length,
        manifest_digest=row.manifest_digest, overlap_review_digest=row.overlap_review_digest,
        protocol_version=row.protocol_version, access_scope=row.access_scope,
        split=row.split, retention_class=row.retention_class,
        expires_at=_iso(row.expires_at),
        status=row.status, created_by_user_id=row.created_by_user_id,
        frozen_by_user_id=row.frozen_by_user_id, frozen_at=_iso(row.frozen_at),
        approved_review_kinds=sorted(kind for kind, review in latest.items() if review.decision == "approve"),
    )


@router.post("", response_model=HoldoutManifestSummary, status_code=201)
def create_holdout(
    body: HoldoutManifestCreateRequest,
    idempotency_key: str = Depends(required_idempotency_key),
    identity=Depends(require_role("operator", "administrator")),
    session: Session = Depends(get_session),
) -> HoldoutManifestSummary:
    actor = _user_id(session, identity)
    values = body.model_dump()
    request_body = body.model_dump(mode="json")
    scope = principal_scope("maintainer:holdout:create", str(actor))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return HoldoutManifestSummary.model_validate(cached)
    row = holdouts.create_manifest(session, created_by_user_id=actor, **values)
    response = _summary(session, row)
    replay = finalize(session, scope=scope, key=idempotency_key or "", body=request_body,
                      status_code=201, response_body=response.model_dump(mode="json"))
    return response if replay is None else HoldoutManifestSummary.model_validate(replay)


@router.get("/{holdout_id}", response_model=HoldoutManifestSummary)
def get_holdout(
    holdout_id: UUID,
    identity=Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> HoldoutManifestSummary:
    row = session.get(HoldoutManifestRow, holdout_id)
    if row is None:
        raise not_found()
    return _summary(session, row)


@router.post("/{holdout_id}/reviews", response_model=HoldoutReviewSummary, status_code=201)
def review_holdout(
    holdout_id: UUID,
    body: HoldoutReviewRequest,
    idempotency_key: str = Depends(required_idempotency_key),
    identity=Depends(require_role("reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> HoldoutReviewSummary:
    actor = _user_id(session, identity)
    request_body = body.model_dump(mode="json")
    scope = principal_scope(f"maintainer:holdout:{holdout_id}:review", str(actor))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return HoldoutReviewSummary.model_validate(cached)
    row = session.execute(
        select(HoldoutManifestRow).where(HoldoutManifestRow.id == holdout_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    review = holdouts.record_review(session, holdout=row, reviewer_user_id=actor, **request_body)
    response = HoldoutReviewSummary(
        id=review.id, holdout_manifest_id=review.holdout_manifest_id,
        reviewer_user_id=review.reviewer_user_id, review_kind=review.review_kind,
        decision=review.decision, evidence_digest=review.evidence_digest,
        reason=review.reason, created_at=_iso(review.created_at) or "",
    )
    replay = finalize(session, scope=scope, key=idempotency_key or "", body=request_body,
                      status_code=201, response_body=response.model_dump(mode="json"))
    return response if replay is None else HoldoutReviewSummary.model_validate(replay)


@router.post("/{holdout_id}/freeze", response_model=HoldoutManifestSummary)
def freeze_holdout(
    holdout_id: UUID,
    idempotency_key: str = Depends(required_idempotency_key),
    identity=Depends(require_role("operator", "administrator")),
    session: Session = Depends(get_session),
) -> HoldoutManifestSummary:
    actor = _user_id(session, identity)
    request_body = {"holdout_id": str(holdout_id), "action": "freeze"}
    scope = principal_scope(f"maintainer:holdout:{holdout_id}:freeze", str(actor))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return HoldoutManifestSummary.model_validate(cached)
    row = session.execute(
        select(HoldoutManifestRow).where(HoldoutManifestRow.id == holdout_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    holdouts.freeze_manifest(session, holdout=row, frozen_by_user_id=actor)
    response = _summary(session, row)
    replay = finalize(session, scope=scope, key=idempotency_key or "", body=request_body,
                      status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else HoldoutManifestSummary.model_validate(replay)


@router.post("/{holdout_id}/retire", response_model=HoldoutManifestSummary)
def retire_holdout(
    holdout_id: UUID,
    idempotency_key: str = Depends(required_idempotency_key),
    identity=Depends(require_role("operator", "administrator")),
    session: Session = Depends(get_session),
) -> HoldoutManifestSummary:
    actor = _user_id(session, identity)
    request_body = {"holdout_id": str(holdout_id), "action": "retire"}
    scope = principal_scope(f"maintainer:holdout:{holdout_id}:retire", str(actor))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return HoldoutManifestSummary.model_validate(cached)
    row = session.execute(
        select(HoldoutManifestRow).where(HoldoutManifestRow.id == holdout_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    if row.status != "frozen":
        raise conflict("only a frozen holdout can be retired")
    row.status = "retired"
    session.flush()
    response = _summary(session, row)
    replay = finalize(session, scope=scope, key=idempotency_key or "", body=request_body,
                      status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else HoldoutManifestSummary.model_validate(replay)
