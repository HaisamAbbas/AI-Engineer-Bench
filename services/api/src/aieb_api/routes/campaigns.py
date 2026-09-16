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
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, invalid_request, not_found, stale_revision
from ..idempotency import check_or_reserve, finalize
from ..models import CampaignRow, EntrantRevisionRow, ProtocolRevisionRow, TaskRevisionRow
from ..revisions import validate_stored_manifest
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
    replay = finalize(
        session, scope="POST /v1/campaigns", key=idempotency_key, body=request_body,
        status_code=201, response_body=summary.model_dump(mode="json"),
    )
    return summary if replay is None else CampaignSummary.model_validate(replay)


@router.patch("/{campaign_id}", response_model=CampaignSummary)
def patch_campaign(
    campaign_id: UUID,
    body: CampaignPatchRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignSummary:
    if if_match is None:
        raise invalid_request("If-Match header with the current revision is required")
    try:
        expected_revision = int(if_match.strip('"'))
    except ValueError as exc:
        raise invalid_request("If-Match must be an integer revision") from exc

    # Atomic conditional UPDATE: the state/revision guard is enforced by the database in the
    # same statement that writes the new draft, so two concurrent PATCH requests with the same
    # If-Match cannot both succeed - the second's WHERE clause no longer matches and it falls
    # through to the diagnostic read below instead of silently clobbering the first. RETURNING
    # gives back the authoritative post-write row directly, rather than relying on
    # session.get() and SQLAlchemy's synchronize_session bookkeeping to reflect a raw Core
    # UPDATE back onto an identity-mapped object.
    updated = session.execute(
        update(CampaignRow)
        .where(CampaignRow.id == campaign_id, CampaignRow.state == "draft", CampaignRow.revision == expected_revision)
        .values(draft=body.draft.model_dump(mode="json"), revision=CampaignRow.revision + 1)
        .returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        row = session.get(CampaignRow, campaign_id)
        if row is None:
            raise not_found()
        if row.state != "draft":
            raise conflict("only a draft campaign can be edited")
        raise stale_revision()
    session.commit()
    return _summary(updated)


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

    # Captured now so the final UPDATE's WHERE clause can require the draft to still be at
    # this exact revision: without this, a PATCH that commits between this read and the
    # UPDATE below would be silently discarded - the freeze would still match on state='draft'
    # alone and lock in a snapshot of a draft that is no longer current.
    observed_revision = row.revision
    draft = CampaignDraft.model_validate(row.draft)
    task_rows = session.execute(select(TaskRevisionRow).where(TaskRevisionRow.slug.in_(draft.task_ids))).scalars().all()
    entrant_rows = session.execute(select(EntrantRevisionRow).where(EntrantRevisionRow.slug.in_(draft.entrant_ids))).scalars().all()
    tasks = {r.slug: validate_stored_manifest(TaskRevision, r.manifest, kind="task", row_id=r.id) for r in task_rows}
    entrants = {r.slug: validate_stored_manifest(EntrantRevision, r.manifest, kind="entrant", row_id=r.id) for r in entrant_rows}
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

    # Methodology versions are persisted on first use and thereafter
    # immutable. Reusing a version identifier for different rules would make
    # the public version URL ambiguous, so reject it before freezing.
    protocol_manifest = registry_body.protocol.model_dump(mode="json")
    session.execute(
        pg_insert(ProtocolRevisionRow)
        .values(
            version=registry_body.protocol.id,
            scoring_digest=registry_body.protocol.scoring_digest,
            manifest=protocol_manifest,
        )
        .on_conflict_do_nothing(index_elements=[ProtocolRevisionRow.version])
    )
    protocol_row = session.execute(
        select(ProtocolRevisionRow).where(ProtocolRevisionRow.version == registry_body.protocol.id)
    ).scalar_one()
    if protocol_row.manifest != protocol_manifest or protocol_row.scoring_digest != registry_body.protocol.scoring_digest:
        raise conflict("protocol version already exists with different methodology data")

    # Atomic conditional UPDATE: state AND revision are both guarded in the same statement
    # that writes the frozen manifest, so this cannot lose a concurrent PATCH (see above) and
    # cannot let two concurrent freeze calls both win (only one UPDATE can match this WHERE
    # clause; the other's rowcount is 0).
    updated = session.execute(
        update(CampaignRow)
        .where(CampaignRow.id == campaign_id, CampaignRow.state == "draft", CampaignRow.revision == observed_revision)
        .values(
            state="frozen", manifest_digest=resolved.digest(), cohort_digest=resolved.cohort.digest(),
            resolved=resolved.model_dump(mode="json"), revision=CampaignRow.revision + 1,
        )
        .returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        # Two concurrent freeze calls with the SAME idempotency key both pass check_or_reserve
        # above (neither has committed yet) and then race this UPDATE; the loser's failure here
        # is not necessarily a real conflict - it may be the winner's own commit, matched by
        # key, waiting to be replayed. Re-check the idempotency table (now that the winner, if
        # any, has committed and this session's own transaction can see it under READ
        # COMMITTED) before concluding this is a genuine state/revision conflict.
        replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
        if replay is not None:
            return CampaignSummary.model_validate(replay)
        current = session.get(CampaignRow, campaign_id)
        if current.state != "draft":
            raise conflict(f"cannot freeze a campaign in state {current.state}")
        raise conflict("campaign draft changed concurrently; re-read the campaign and retry freeze")

    summary = _summary(updated)
    replay = finalize(
        session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=summary.model_dump(mode="json"),
    )
    return summary if replay is None else CampaignSummary.model_validate(replay)
