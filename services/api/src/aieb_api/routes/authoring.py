"""Private maintainer task-draft lifecycle: create, update, freeze."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from aieb_core.models import TaskRevision
from fastapi import APIRouter, Depends, Header
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import current_principal_id, require_role
from ..db import get_session
from ..errors import conflict, invalid_request, not_found
from ..evidence_integrity import task_revision_digest
from ..idempotency import check_or_reserve, finalize, principal_scope
from ..models import EvaluatorRevisionRow, TaskAdmissionStateRow, TaskDraftRow, TaskRevisionRow
from ..schemas import AuthoredTaskDraftRequest, LiveWindowTaskDraftRequest, MinedPrTaskDraftRequest, TaskDraftCreateRequest, TaskDraftResponse, TaskDraftUpdateRequest

router = APIRouter(prefix="/v1/maintainer", tags=["maintainer-authoring"])
MAINTAINER_ROLES = ("operator", "reviewer", "administrator")


def _validate(request: TaskDraftCreateRequest) -> TaskRevision:
    try:
        return TaskRevision.model_validate(request.manifest)
    except ValidationError as exc:
        raise invalid_request(f"task manifest failed validation: {exc.errors()}") from exc


def _response(draft: TaskDraftRow, evaluator_id: UUID | None = None, *, status: str | None = None) -> dict[str, Any]:
    return TaskDraftResponse(
        id=draft.id, slug=draft.slug, version=draft.version,
        revision_digest=task_revision_digest(draft.manifest, draft.ticket_text),
        evaluator_id=evaluator_id, status=status or draft.status,
    ).model_dump(mode="json")


@router.post("/task-drafts", response_model=TaskDraftResponse, status_code=201)
def create_task_draft(
    request: TaskDraftCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    identity=Depends(require_role(*MAINTAINER_ROLES)),
) -> dict[str, Any]:
    manifest = _validate(request)
    principal = current_principal_id(session, identity)
    body = request.model_dump(mode="json")
    scope = principal_scope("maintainer:task-draft:create", principal)
    replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=body)
    if replay is not None:
        return replay
    draft = TaskDraftRow(
        slug=manifest.id, version=manifest.version, manifest=request.manifest,
        ticket_text=request.ticket_text, evaluator_code_digest=request.evaluator_code_digest,
        evaluator_contract_version=request.evaluator_contract_version,
        created_by_user_id=UUID(principal) if principal else None,
    )
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("task draft slug/version already exists") from exc
    response = _response(draft)
    return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=201, response_body=response) or response


@router.post("/task-drafts/authored", response_model=TaskDraftResponse, status_code=201)
def create_authored_task_draft(
    request: AuthoredTaskDraftRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    identity=Depends(require_role(*MAINTAINER_ROLES)),
) -> dict[str, Any]:
    """Create a draft from an authored, pinned repository source.

    This adapter intentionally accepts only authored sources; mined-PR and
    live-window metadata require separate adapters and cohorts.
    """
    manifest = _validate(request)
    principal = current_principal_id(session, identity)
    body = request.model_dump(mode="json")
    scope = principal_scope("maintainer:task-draft:authored", principal)
    replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=body)
    if replay is not None:
        return replay
    draft = TaskDraftRow(
        slug=manifest.id, version=manifest.version, manifest=request.manifest,
        ticket_text=request.ticket_text, evaluator_code_digest=request.evaluator_code_digest,
        evaluator_contract_version=request.evaluator_contract_version,
        source_strategy=request.source_strategy, repository_url=request.repository_url,
        source_revision=request.source_revision, source_content_digest=request.source_content_digest,
        source_license_id=request.source_license_id, source_provenance_digest=request.source_provenance_digest,
        created_by_user_id=UUID(principal) if principal else None,
    )
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("authored task draft slug/version already exists") from exc
    response = _response(draft)
    return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=201, response_body=response) or response


@router.post("/task-drafts/mined-pr", response_model=TaskDraftResponse, status_code=201)
def create_mined_pr_task_draft(
    request: MinedPrTaskDraftRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    identity=Depends(require_role(*MAINTAINER_ROLES)),
) -> dict[str, Any]:
    """Register a mined PR as a development draft with explicit provenance.

    The patch and PR metadata are retained for contamination and split review;
    this endpoint never implies that the task is admitted or official.
    """
    manifest = _validate(request)
    principal = current_principal_id(session, identity)
    body = request.model_dump(mode="json")
    scope = principal_scope("maintainer:task-draft:mined-pr", principal)
    replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=body)
    if replay is not None:
        return replay
    source_metadata = {
        "pull_request_url": request.pull_request_url,
        "pull_request_number": request.pull_request_number,
        "base_commit": request.base_commit,
        "patch_commit": request.patch_commit,
        "split": request.split,
        "contamination_cutoff": request.contamination_cutoff,
    }
    draft = TaskDraftRow(
        slug=manifest.id, version=manifest.version, manifest=request.manifest,
        ticket_text=request.ticket_text, evaluator_code_digest=request.evaluator_code_digest,
        evaluator_contract_version=request.evaluator_contract_version,
        source_strategy=request.source_strategy, repository_url=request.repository_url,
        source_revision=request.base_commit, source_content_digest=request.source_content_digest,
        source_license_id=request.source_license_id, source_provenance_digest=request.source_provenance_digest,
        source_metadata=source_metadata, created_by_user_id=UUID(principal) if principal else None,
    )
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("mined PR task draft slug/version already exists") from exc
    response = _response(draft)
    return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=201, response_body=response) or response


@router.post("/task-drafts/live-window", response_model=TaskDraftResponse, status_code=201)
def create_live_window_task_draft(
    request: LiveWindowTaskDraftRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    identity=Depends(require_role(*MAINTAINER_ROLES)),
) -> dict[str, Any]:
    """Register a time-windowed snapshot with explicit expiry and cutoff."""
    try:
        collected = datetime.fromisoformat(request.collected_at.replace("Z", "+00:00"))
        released = datetime.fromisoformat(request.released_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(request.expires_at.replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(request.model_cutoff.replace("Z", "+00:00"))
    except ValueError as exc:
        raise invalid_request("live-window timestamps must be ISO-8601") from exc
    if any(value.tzinfo is None for value in (collected, released, expires, cutoff)):
        raise invalid_request("live-window timestamps must include an explicit timezone")
    if not collected <= released < expires:
        raise invalid_request("live-window timestamps must satisfy collected_at <= released_at < expires_at")
    if released <= cutoff:
        raise invalid_request("release must occur after the declared model cutoff")
    manifest = _validate(request)
    principal = current_principal_id(session, identity)
    body = request.model_dump(mode="json")
    scope = principal_scope("maintainer:task-draft:live-window", principal)
    replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=body)
    if replay is not None:
        return replay
    source_metadata = {
        "cohort_id": request.cohort_id, "collected_at": request.collected_at,
        "released_at": request.released_at, "model_cutoff": request.model_cutoff,
        "expires_at": request.expires_at,
    }
    draft = TaskDraftRow(
        slug=manifest.id, version=manifest.version, manifest=request.manifest,
        ticket_text=request.ticket_text, evaluator_code_digest=request.evaluator_code_digest,
        evaluator_contract_version=request.evaluator_contract_version,
        source_strategy=request.source_strategy, repository_url=request.repository_url,
        source_revision=request.source_revision, source_content_digest=request.source_content_digest,
        source_license_id=request.source_license_id, source_provenance_digest=request.source_provenance_digest,
        source_metadata=source_metadata, created_by_user_id=UUID(principal) if principal else None,
    )
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict("live-window task draft slug/version already exists") from exc
    response = _response(draft)
    return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=201, response_body=response) or response


@router.patch("/task-drafts/{draft_id}", response_model=TaskDraftResponse)
def update_task_draft(
    draft_id: UUID,
    request: TaskDraftUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    identity=Depends(require_role(*MAINTAINER_ROLES)),
) -> dict[str, Any]:
    draft = session.get(TaskDraftRow, draft_id)
    if draft is None:
        raise not_found()
    if draft.status != "draft":
        raise conflict("frozen task drafts are immutable")
    manifest = _validate(request)
    principal = current_principal_id(session, identity)
    body = request.model_dump(mode="json")
    scope = principal_scope(f"maintainer:task-draft:update:{draft_id}", principal)
    replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=body)
    if replay is not None:
        return replay
    draft.slug, draft.version, draft.manifest = manifest.id, manifest.version, request.manifest
    draft.ticket_text = request.ticket_text
    draft.evaluator_code_digest = request.evaluator_code_digest
    draft.evaluator_contract_version = request.evaluator_contract_version
    session.flush()
    response = _response(draft)
    return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=200, response_body=response) or response


@router.post("/task-drafts/{draft_id}/freeze", response_model=TaskDraftResponse)
def freeze_task_draft(
    draft_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    identity=Depends(require_role(*MAINTAINER_ROLES)),
) -> dict[str, Any]:
    draft = session.get(TaskDraftRow, draft_id)
    if draft is None:
        raise not_found()
    principal = current_principal_id(session, identity)
    body = {"draft_id": str(draft_id), "action": "freeze"}
    scope = principal_scope(f"maintainer:task-draft:freeze:{draft_id}", principal)
    replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=body)
    if replay is not None:
        return replay
    if draft.status == "frozen" and draft.frozen_revision_id:
        revision = session.get(TaskRevisionRow, draft.frozen_revision_id)
        evaluator_id = revision.evaluator_id if revision else None
        response = _response(draft, evaluator_id, status="pending-independent-review")
        return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=200, response_body=response) or response
    manifest = _validate(TaskDraftCreateRequest(
        manifest=draft.manifest, ticket_text=draft.ticket_text,
        evaluator_code_digest=draft.evaluator_code_digest,
        evaluator_contract_version=draft.evaluator_contract_version,
    ))
    evaluator = session.execute(select(EvaluatorRevisionRow).where(
        EvaluatorRevisionRow.code_digest == draft.evaluator_code_digest,
        EvaluatorRevisionRow.contract_version == draft.evaluator_contract_version,
    )).scalar_one_or_none()
    if evaluator is None:
        evaluator = EvaluatorRevisionRow(code_digest=draft.evaluator_code_digest, contract_version=draft.evaluator_contract_version, review_status="pending-independent-review")
        session.add(evaluator)
        session.flush()
    revision = TaskRevisionRow(
        slug=manifest.id, version=manifest.version, family_id=manifest.family_id,
        category=manifest.category.value, source_digest=manifest.source.repository_digest,
        manifest_digest=manifest.digest(), evaluator_id=evaluator.id, manifest=draft.manifest,
        ticket_text=draft.ticket_text, revision_digest=task_revision_digest(draft.manifest, draft.ticket_text),
    )
    session.add(revision)
    session.flush()
    # Freezing pins revision identity only.  Admission is a separate persisted
    # lifecycle, and always starts in the explicit `frozen` state.
    session.add(TaskAdmissionStateRow(
        task_revision_id=revision.id,
        status="frozen",
        author_user_id=UUID(principal) if principal else None,
    ))
    session.flush()
    draft.status, draft.frozen_revision_id = "frozen", revision.id
    response = _response(draft, evaluator.id, status="pending-independent-review")
    try:
        return finalize(session, scope=scope, key=idempotency_key or "", body=body, status_code=200, response_body=response) or response
    except IntegrityError as exc:
        session.rollback()
        raise conflict("task revision already exists") from exc
