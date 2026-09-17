"""Staging correction runs over retained candidates."""
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, forbidden, not_found
from ..evidence_integrity import evidence_digest
from ..idempotency import check_or_reserve, finalize, principal_scope
from ..models import AttemptEventRow, CampaignRow, CorrectionRunRow
from ..regrading import enqueue_regrade, installed_scoring_bundle
from ..schemas import CorrectionRunSummary, RegradeRequest
from .campaigns import _current_user_id

router = APIRouter(prefix="/v1", tags=["corrections"])


def summary(session: Session, run: CorrectionRunRow) -> CorrectionRunSummary:
    count = session.query(AttemptEventRow).filter(
        AttemptEventRow.event_type == "regrade.requested",
        AttemptEventRow.payload["correction_run_id"].astext == str(run.id),
    ).count()
    return CorrectionRunSummary(id=run.id, campaign_id=run.campaign_id, status=run.status,
        regrade_work_items=count, created_at=run.created_at.isoformat())


@router.get("/campaigns/{campaign_id}/scoring-bundle")
def scoring_bundle(campaign_id: UUID, identity: Identity = Depends(require_role("reviewer", "administrator")), session: Session = Depends(get_session)) -> dict:
    if session.get(CampaignRow, campaign_id) is None:
        raise not_found()
    try:
        bundle = installed_scoring_bundle(session, campaign_id)
    except ValueError as exc:
        raise conflict(str(exc)) from exc
    return {"scoring_digest": evidence_digest(bundle), "scope": "installed-staging-only"}


@router.post("/campaigns/{campaign_id}/regrade", response_model=CorrectionRunSummary)
def regrade(campaign_id: UUID, body: RegradeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("reviewer", "administrator")), session: Session = Depends(get_session)) -> CorrectionRunSummary:
    if os.environ.get("AIEB_ENV") not in {"test", "local", "staging"}:
        raise forbidden("regrade is enabled only for local/staging fixtures")
    campaign = session.execute(select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()).scalar_one_or_none()
    if campaign is None:
        raise not_found()
    reviewer_id = _current_user_id(session, identity)
    scope = principal_scope(f"POST /v1/campaigns/{campaign_id}/regrade", str(reviewer_id))
    request_body = body.model_dump(mode="json")
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return CorrectionRunSummary.model_validate(cached)
    if campaign.state not in {"completed", "incomplete"}:
        raise conflict("regrade requires a terminal campaign")
    if not (body.reason or "").strip():
        raise conflict("regrade requires a correction reason")
    registry = body.registry.model_dump(mode="json")
    frozen = campaign.resolved
    if registry["cohort"] != frozen["cohort"] or registry["budget"] != frozen["budget"]:
        raise conflict("regrade cannot change the frozen cohort or budget")
    expected_protocol = dict(frozen["protocol"])
    expected_protocol["scoring_digest"] = registry["protocol"]["scoring_digest"]
    if registry["protocol"] != expected_protocol:
        raise conflict("regrade may only change the protocol scoring digest")
    try:
        run = enqueue_regrade(session, campaign, scoring_digest=body.registry.protocol.scoring_digest,
            reason=body.reason, user_id=reviewer_id)
    except ValueError as exc:
        raise conflict(str(exc)) from exc
    run_summary = summary(session, run)
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=run_summary.model_dump(mode="json"))
    return run_summary if replay is None else CorrectionRunSummary.model_validate(replay)


@router.get("/correction-runs/{run_id}", response_model=CorrectionRunSummary)
def get_run(run_id: UUID, identity: Identity = Depends(require_role("operator", "reviewer", "administrator")), session: Session = Depends(get_session)) -> CorrectionRunSummary:
    run = session.get(CorrectionRunRow, run_id)
    if run is None:
        raise not_found()
    return summary(session, run)
