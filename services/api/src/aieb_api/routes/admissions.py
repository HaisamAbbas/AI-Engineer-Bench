"""Private, persisted task-admission lifecycle (V2-GAP-003)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import admission
from ..auth import current_principal_id, require_role
from ..db import get_session
from ..errors import conflict, forbidden, not_found, service_unavailable
from ..idempotency import check_or_reserve, finalize, principal_scope
from ..models import (
    TaskAdmissionGateRow,
    TaskAdmissionResetRow,
    TaskAdmissionRunRow,
    TaskRevisionRow,
)
from ..schemas import (
    AdmissionCancelRequest,
    AdmissionGateSummary,
    AdmissionReviewRequest,
    AdmissionReviewSummary,
    AdmissionRunSummary,
    AdmissionStartRequest,
)

router = APIRouter(prefix="/v1/maintainer", tags=["maintainer-admission"])


def _principal_uuid(session: Session, identity) -> UUID:
    value = current_principal_id(session, identity)
    if value is None:
        raise forbidden("task admission requires a provisioned user")
    return UUID(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _gate_summary(row: TaskAdmissionGateRow) -> AdmissionGateSummary:
    return AdmissionGateSummary(
        gate_name=row.gate_name,
        required=row.required,
        status=row.status,
        observed_digest=row.observed_digest,
        evidence_reference=row.evidence_reference,
        details=row.details,
        started_at=_iso(row.started_at),
        completed_at=_iso(row.completed_at),
    )


def _run_summary(session: Session, run: TaskAdmissionRunRow, *, include_gates: bool = True) -> AdmissionRunSummary:
    state = admission.admission_state(session, run.task_revision_id)
    gates = []
    if include_gates:
        rows = session.execute(
            select(TaskAdmissionGateRow)
            .where(TaskAdmissionGateRow.admission_run_id == run.id)
            .order_by(TaskAdmissionGateRow.gate_name)
        ).scalars().all()
        gates = [_gate_summary(row) for row in rows]
    passing_resets = session.execute(
        select(func.count()).select_from(TaskAdmissionResetRow).where(
            TaskAdmissionResetRow.admission_run_id == run.id,
            TaskAdmissionResetRow.status == "pass",
        )
    ).scalar_one()
    return AdmissionRunSummary(
        id=run.id,
        task_revision_id=run.task_revision_id,
        admission_state=state.status if state is not None else "missing",
        status=run.status,
        protocol_version=run.protocol_version,
        protocol_digest=run.protocol_digest,
        revision_digest=run.revision_digest,
        manifest_digest=run.manifest_digest,
        source_digest=run.source_digest,
        evaluator_digest=run.evaluator_digest,
        result_digest=run.result_digest,
        failure_reason=run.failure_reason,
        started_at=_iso(run.started_at),
        completed_at=_iso(run.completed_at),
        created_at=_iso(run.created_at) or "",
        gates=gates,
        passing_resets=passing_resets,
    )


@router.post("/task-revisions/{revision_id}/admissions", response_model=AdmissionRunSummary, status_code=201)
def start_admission(
    revision_id: UUID,
    body: AdmissionStartRequest = AdmissionStartRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity=Depends(require_role("operator", "administrator")),
    session: Session = Depends(get_session),
) -> AdmissionRunSummary:
    requester = _principal_uuid(session, identity)
    request_body = body.model_dump(mode="json")
    scope = principal_scope(f"maintainer:task-revision:{revision_id}:admission:start", str(requester))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return AdmissionRunSummary.model_validate(cached)
    revision = session.execute(
        select(TaskRevisionRow).where(TaskRevisionRow.id == revision_id).with_for_update()
    ).scalar_one_or_none()
    if revision is None:
        raise not_found()
    protocol = admission.resolve_protocol(body.protocol_version)
    try:
        run = admission.start_run(session, revision=revision, requester_id=requester, protocol=protocol)
        response = _run_summary(session, run)
        replay = finalize(
            session, scope=scope, key=idempotency_key or "", body=request_body,
            status_code=201, response_body=response.model_dump(mode="json"),
        )
    except IntegrityError as exc:
        session.rollback()
        raise conflict("an admission run is already active for this task revision") from exc
    return response if replay is None else AdmissionRunSummary.model_validate(replay)


@router.post("/admissions/{admission_id}/execute", response_model=AdmissionRunSummary)
def execute_admission(
    admission_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity=Depends(require_role("operator", "administrator")),
    session: Session = Depends(get_session),
) -> AdmissionRunSummary:
    actor = _principal_uuid(session, identity)
    request_body = {"admission_id": str(admission_id), "action": "execute"}
    scope = principal_scope(f"maintainer:admission:{admission_id}:execute", str(actor))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return AdmissionRunSummary.model_validate(cached)
    executor = admission.configured_executor()
    if executor is None:
        raise service_unavailable("AIEB_ADMISSION_EXECUTOR is not configured; admission cannot execute")
    run = session.execute(
        select(TaskAdmissionRunRow).where(TaskAdmissionRunRow.id == admission_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise not_found()
    protocol = admission.resolve_protocol(run.protocol_version)
    claimed = admission.claim_for_execution(session, run.id)
    if claimed is None:
        raise conflict(f"admission run is {run.status}; only a pending run can execute")
    admission.begin_execution_state(session, run.task_revision_id)
    context = admission.build_context(session, claimed, protocol)
    failure_reason: str | None = None
    outcome = None
    try:
        outcome = executor.execute(context)
    except admission.AdmissionExecutorError as exc:
        failure_reason = str(exc)[:2000]
    status = admission.finalize_run(
        session,
        run=claimed,
        protocol=protocol,
        outcome=outcome,
        failure_reason=failure_reason,
        actor_user_id=actor,
    )
    if status is None:
        raise conflict("admission execution lost its terminal-state claim")
    session.flush()
    response = _run_summary(session, claimed)
    replay = finalize(
        session, scope=scope, key=idempotency_key or "", body=request_body,
        status_code=200, response_body=response.model_dump(mode="json"),
    )
    return response if replay is None else AdmissionRunSummary.model_validate(replay)


@router.get("/admissions/{admission_id}", response_model=AdmissionRunSummary)
def get_admission(
    admission_id: UUID,
    identity=Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> AdmissionRunSummary:
    run = session.get(TaskAdmissionRunRow, admission_id)
    if run is None:
        raise not_found()
    return _run_summary(session, run)


@router.get("/admissions/{admission_id}/gates", response_model=list[AdmissionGateSummary])
def get_admission_gates(
    admission_id: UUID,
    identity=Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> list[AdmissionGateSummary]:
    if session.get(TaskAdmissionRunRow, admission_id) is None:
        raise not_found()
    rows = session.execute(
        select(TaskAdmissionGateRow)
        .where(TaskAdmissionGateRow.admission_run_id == admission_id)
        .order_by(TaskAdmissionGateRow.gate_name)
    ).scalars().all()
    return [_gate_summary(row) for row in rows]


@router.post("/admissions/{admission_id}/review", response_model=AdmissionReviewSummary)
def review_admission(
    admission_id: UUID,
    body: AdmissionReviewRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity=Depends(require_role("reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> AdmissionReviewSummary:
    reviewer = _principal_uuid(session, identity)
    request_body = body.model_dump(mode="json")
    scope = principal_scope(f"maintainer:admission:{admission_id}:review", str(reviewer))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return AdmissionReviewSummary.model_validate(cached)
    run = session.execute(
        select(TaskAdmissionRunRow).where(TaskAdmissionRunRow.id == admission_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise not_found()
    review = admission.record_review(
        session,
        run=run,
        reviewer_id=reviewer,
        decision=body.decision,
        scope=body.scope,
        evidence_digest_value=body.evidence_digest,
        independence_declaration=body.independence_declaration,
        reason=body.reason,
    )
    response = AdmissionReviewSummary(
        id=review.id,
        admission_run_id=review.admission_run_id,
        task_revision_id=review.task_revision_id,
        reviewer_user_id=review.reviewer_user_id,
        decision=review.decision,
        scope=review.scope,
        evidence_digest=review.evidence_digest,
        independence_declaration=review.independence_declaration,
        reason=review.reason,
        created_at=_iso(review.created_at) or _iso(datetime.now(timezone.utc)) or "",
    )
    replay = finalize(
        session, scope=scope, key=idempotency_key or "", body=request_body,
        status_code=200, response_body=response.model_dump(mode="json"),
    )
    return response if replay is None else AdmissionReviewSummary.model_validate(replay)


@router.post("/admissions/{admission_id}/cancel", response_model=AdmissionRunSummary)
def cancel_admission(
    admission_id: UUID,
    body: AdmissionCancelRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity=Depends(require_role("operator", "administrator")),
    session: Session = Depends(get_session),
) -> AdmissionRunSummary:
    actor = _principal_uuid(session, identity)
    request_body = body.model_dump(mode="json")
    scope = principal_scope(f"maintainer:admission:{admission_id}:cancel", str(actor))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return AdmissionRunSummary.model_validate(cached)
    run = session.execute(
        select(TaskAdmissionRunRow).where(TaskAdmissionRunRow.id == admission_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise not_found()
    admission.cancel_run(session, run=run, actor_user_id=actor, reason=body.reason)
    session.flush()
    response = _run_summary(session, run)
    replay = finalize(
        session, scope=scope, key=idempotency_key or "", body=request_body,
        status_code=200, response_body=response.model_dump(mode="json"),
    )
    return response if replay is None else AdmissionRunSummary.model_validate(replay)
