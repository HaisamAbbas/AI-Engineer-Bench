"""POST /v1/campaigns, PATCH /v1/campaigns/{id}, POST /v1/campaigns/{id}/freeze.

Draft editing uses optimistic revision control (If-Match). Freezing is
immutable: any change after freeze must create a new campaign (spec
section 13/14).
"""

from __future__ import annotations

from uuid import UUID

from aieb_core.models import CampaignDraft, EntrantRevision, TaskRevision
from aieb_core.planner import PlanningError, Registry, freeze_campaign
from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, invalid_request, not_found, stale_revision
from ..idempotency import check_or_reserve, store
from ..models import CampaignRow, EntrantRevisionRow, TaskRevisionRow
from ..schemas import CampaignCreateRequest, CampaignPatchRequest, CampaignSummary, FreezeRegistry

router = APIRouter(prefix="/v1/campaigns", tags=["campaigns"])


def _summary(row: CampaignRow) -> CampaignSummary:
    return CampaignSummary(
        id=row.id, name=row.name, state=row.state, revision=row.revision,
        manifest_digest=row.manifest_digest, cohort_digest=row.cohort_digest,
    )


@router.post("", response_model=CampaignSummary, status_code=201)
def create_campaign(
    body: CampaignCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignSummary:
    request_body = body.model_dump(mode="json")
    cached = check_or_reserve(session, scope="POST /v1/campaigns", key=idempotency_key, body=request_body)
    if cached is not None:
        return CampaignSummary.model_validate(cached)

    row = CampaignRow(name=body.name, state="draft", draft=body.draft.model_dump(mode="json"), revision=0)
    session.add(row)
    session.flush()
    summary = _summary(row)
    store(
        session, scope="POST /v1/campaigns", key=idempotency_key, body=request_body,
        status_code=201, response_body=summary.model_dump(mode="json"),
    )
    session.commit()
    return summary


@router.patch("/{campaign_id}", response_model=CampaignSummary)
def patch_campaign(
    campaign_id: UUID,
    body: CampaignPatchRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignSummary:
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    if row.state != "draft":
        raise conflict("only a draft campaign can be edited")
    if if_match is None:
        raise invalid_request("If-Match header with the current revision is required")
    try:
        expected_revision = int(if_match.strip('"'))
    except ValueError as exc:
        raise invalid_request("If-Match must be an integer revision") from exc
    if expected_revision != row.revision:
        raise stale_revision()

    row.draft = body.draft.model_dump(mode="json")
    row.revision += 1
    session.commit()
    return _summary(row)


@router.post("/{campaign_id}/freeze", response_model=CampaignSummary)
def freeze(
    campaign_id: UUID,
    registry_body: FreezeRegistry,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignSummary:
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()

    request_body = registry_body.model_dump(mode="json")
    scope = f"POST /v1/campaigns/{campaign_id}/freeze"
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return CampaignSummary.model_validate(cached)

    if row.state != "draft":
        raise conflict(f"cannot freeze a campaign in state {row.state}")

    draft = CampaignDraft.model_validate(row.draft)
    task_rows = session.execute(select(TaskRevisionRow).where(TaskRevisionRow.slug.in_(draft.task_ids))).scalars().all()
    entrant_rows = session.execute(select(EntrantRevisionRow).where(EntrantRevisionRow.slug.in_(draft.entrant_ids))).scalars().all()
    tasks = {r.slug: TaskRevision.model_validate(r.manifest) for r in task_rows}
    entrants = {r.slug: EntrantRevision.model_validate(r.manifest) for r in entrant_rows}
    registry = Registry(
        tasks=tasks, entrants=entrants,
        cohorts={registry_body.cohort.id: registry_body.cohort},
        protocols={registry_body.protocol.id: registry_body.protocol},
        budgets={registry_body.budget.id: registry_body.budget},
    )
    try:
        resolved = freeze_campaign(draft, registry)
    except PlanningError as exc:
        raise invalid_request(f"campaign cannot be frozen: {exc}") from exc

    row.state = "frozen"
    row.manifest_digest = resolved.digest()
    row.cohort_digest = resolved.cohort.digest()
    row.resolved = resolved.model_dump(mode="json")
    row.revision += 1
    session.flush()
    summary = _summary(row)
    store(session, scope=scope, key=idempotency_key, body=request_body, status_code=200, response_body=summary.model_dump(mode="json"))
    session.commit()
    return summary
