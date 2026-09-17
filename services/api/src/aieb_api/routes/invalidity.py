"""Exact historical invalidity evidence and append-only classification reviews.

A review agrees/disagrees with the recorded classification; it is not a verdict
correction, retry request, or publication action. Never project raw event payloads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Identity, require_role, resolve_roles
from ..db import get_session
from ..errors import conflict, forbidden, not_found
from ..idempotency import check_or_reserve, finalize
from ..models import AttemptEventRow, AttemptRow, AuditEventRow, ReviewRow, TrialRow, User

router = APIRouter(prefix="/v1/campaigns", tags=["invalidity"])
_TARGET = "attempt_invalidity"
_STATUSES = ("infrastructure_invalid", "cancelled")
EventType = Literal["phase.started", "candidate.collected", "evaluation.recorded", "attempt.terminal", "regrade.requested"]
_EVENT_TYPES = ("phase.started", "candidate.collected", "evaluation.recorded", "attempt.terminal", "regrade.requested")


class InvalidityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    rationale: str = Field(min_length=1, max_length=4000)

    @field_validator("rationale")
    @classmethod
    def nonblank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review requires a rationale")
        return value.strip()


class InvalidityReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    attempt_id: UUID
    reviewer_id: UUID
    decision: Literal["approve", "reject"]
    rationale: str
    created_at: datetime


class InvalidityEventSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    event_type: EventType
    created_at: datetime


class InvalidAttemptEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    trial_id: UUID
    attempt_id: UUID
    attempt_number: int
    phase: Literal["terminal"]
    terminal_status: Literal["infrastructure_invalid", "cancelled"]
    created_at: datetime
    events: list[InvalidityEventSummary]
    reviews: list[InvalidityReviewSummary]
    can_review: bool


def _attempt(session: Session, campaign_id: UUID, attempt_id: UUID, *, lock: bool = False) -> AttemptRow:
    query = select(AttemptRow).join(TrialRow, TrialRow.id == AttemptRow.trial_id).where(
        TrialRow.campaign_id == campaign_id, AttemptRow.id == attempt_id,
    )
    if lock:
        query = query.with_for_update(of=AttemptRow)
    row = session.execute(query).scalar_one_or_none()
    if row is None:
        raise not_found()
    if row.phase != "terminal" or row.terminal_status not in _STATUSES:
        raise conflict("only terminal infrastructure-invalid or cancelled attempts can be reviewed")
    return row


def _review_summary(row: ReviewRow) -> InvalidityReviewSummary:
    # Deliberately do not spread the evidence JSON into the response.
    return InvalidityReviewSummary(
        id=row.id, attempt_id=row.target_id, reviewer_id=row.reviewer_id,
        decision=row.decision, rationale=row.evidence["rationale"], created_at=row.created_at,
    )


@router.get("/{campaign_id}/invalid-attempts/{attempt_id}", response_model=InvalidAttemptEvidence)
def invalid_attempt_evidence(
    campaign_id: UUID, attempt_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> InvalidAttemptEvidence:
    row = _attempt(session, campaign_id, attempt_id)
    events = session.execute(select(
        AttemptEventRow.sequence, AttemptEventRow.event_type, AttemptEventRow.created_at,
    ).where(
        AttemptEventRow.attempt_id == row.id, AttemptEventRow.event_type.in_(_EVENT_TYPES),
    ).order_by(AttemptEventRow.sequence)).all()
    reviews = session.execute(select(ReviewRow).where(
        ReviewRow.target_type == _TARGET, ReviewRow.target_id == row.id,
    ).order_by(ReviewRow.created_at, ReviewRow.id)).scalars().all()
    return InvalidAttemptEvidence(
        campaign_id=campaign_id, trial_id=row.trial_id, attempt_id=row.id, attempt_number=row.number,
        phase=row.phase, terminal_status=row.terminal_status, created_at=row.created_at,
        events=[InvalidityEventSummary(sequence=seq, event_type=kind, created_at=at) for seq, kind, at in events],
        reviews=[_review_summary(review) for review in reviews],
        can_review=bool(set(resolve_roles(session, identity)) & {"reviewer", "administrator"}),
    )



@router.post("/{campaign_id}/invalid-attempts/{attempt_id}/reviews", response_model=InvalidityReviewSummary, status_code=201)
def review_invalid_attempt(
    campaign_id: UUID, attempt_id: UUID, body: InvalidityReviewRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> InvalidityReviewSummary:
    reviewer_id = session.execute(select(User.id).where(
        User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject,
    )).scalar_one_or_none()
    if reviewer_id is None:
        raise forbidden("review requires a provisioned reviewer")
    scope = f"invalidity:{campaign_id}:{attempt_id}:{reviewer_id}"
    request_body = body.model_dump(mode="json")
    # Serialize reviews of this historical attempt; validate access on replay.
    attempt = _attempt(session, campaign_id, attempt_id, lock=True)
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return InvalidityReviewSummary.model_validate(cached)
    now = datetime.now(timezone.utc)
    review = ReviewRow(
        reviewer_id=reviewer_id, target_type=_TARGET, target_id=attempt.id,
        decision=body.decision, created_at=now,
        evidence={"rationale": body.rationale, "campaign_id": str(campaign_id),
                  "trial_id": str(attempt.trial_id), "terminal_status": attempt.terminal_status},
    )
    session.add(review)
    session.flush()
    session.add(AuditEventRow(
        actor_user_id=reviewer_id, target_type=_TARGET, target_id=attempt.id,
        action="invalidity_review_recorded", created_at=now,
        evidence={"review_id": str(review.id), "decision": body.decision, "rationale": body.rationale},
    ))
    summary = _review_summary(review)
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
                      status_code=201, response_body=summary.model_dump(mode="json"))
    return InvalidityReviewSummary.model_validate(replay) if replay is not None else summary
