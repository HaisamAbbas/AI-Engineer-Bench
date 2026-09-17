"""POST /v1/campaigns, PATCH /v1/campaigns/{id}, POST /v1/campaigns/{id}/freeze.

Draft editing uses optimistic revision control (If-Match). Freezing is
immutable: any change after freeze must create a new campaign (spec
section 13/14).
"""

from __future__ import annotations

from uuid import UUID

from aieb_core.models import CampaignDraft, EntrantRevision, TaskRevision
from aieb_core.planner import PlanningError, Registry, freeze_campaign, resolve_campaign
from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import budgets
from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, invalid_request, not_found, stale_revision
from ..idempotency import check_or_reserve, finalize
from ..models import (
    AttemptRow,
    BudgetReservationRow,
    CampaignRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    ProtocolRevisionRow,
    TaskRevisionRow,
    TrialRow,
    User,
)
from ..revisions import validate_stored_manifest
from ..schemas import (
    BudgetReservationSummary,
    CampaignCreateRequest,
    CampaignPatchRequest,
    CampaignProgress,
    CampaignStateCount,
    CampaignStateResponse,
    CampaignSummary,
    FreezeRegistry,
    InvalidAttemptEntry,
    MatrixPreview,
    MatrixPreviewCell,
)
from ..worker import repository

router = APIRouter(prefix="/v1/campaigns", tags=["campaigns"])


def _current_user_id(session: Session, identity: Identity) -> UUID | None:
    return session.execute(
        select(User.id).where(User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject)
    ).scalar_one_or_none()


def _reservation_row(session: Session, campaign_id: UUID) -> BudgetReservationRow | None:
    return session.execute(
        select(BudgetReservationRow).where(BudgetReservationRow.campaign_id == campaign_id)
    ).scalar_one_or_none()


def _reservation_summary(session: Session, campaign_id: UUID) -> BudgetReservationSummary | None:
    data = budgets.reservation_summary(_reservation_row(session, campaign_id))
    return BudgetReservationSummary.model_validate(data) if data is not None else None


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

    row = CampaignRow(
        name=body.name, state="draft", draft=body.draft.model_dump(mode="json"), revision=0,
        created_by_user_id=_current_user_id(session, identity),
    )
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


def _registry_for(session: Session, draft: CampaignDraft, registry_body: FreezeRegistry) -> Registry:
    task_rows = session.execute(select(TaskRevisionRow).where(TaskRevisionRow.slug.in_(draft.task_ids))).scalars().all()
    entrant_rows = session.execute(select(EntrantRevisionRow).where(EntrantRevisionRow.slug.in_(draft.entrant_ids))).scalars().all()
    tasks = {r.slug: validate_stored_manifest(TaskRevision, r.manifest, kind="task", row_id=r.id) for r in task_rows}
    entrants = {r.slug: validate_stored_manifest(EntrantRevision, r.manifest, kind="entrant", row_id=r.id) for r in entrant_rows}
    return Registry(
        tasks=tasks, entrants=entrants,
        cohorts={registry_body.cohort.id: registry_body.cohort},
        protocols={registry_body.protocol.id: registry_body.protocol},
        budgets={registry_body.budget.id: registry_body.budget},
    )


@router.post("/{campaign_id}/preview", response_model=MatrixPreview)
def preview_matrix(
    campaign_id: UUID,
    registry_body: FreezeRegistry,
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> MatrixPreview:
    """Exact trial matrix a freeze WOULD produce for this draft + registry,
    computed by the pure planner and NEVER persisted. Works on a draft campaign
    so an operator can preview coverage/cost before committing to a freeze."""
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    if row.state != "draft":
        raise conflict(f"a matrix preview is only meaningful for a draft campaign, not state {row.state}")
    draft = CampaignDraft.model_validate(row.draft)
    try:
        resolved = resolve_campaign(draft, _registry_for(session, draft, registry_body))
    except PlanningError as exc:
        raise invalid_request(f"campaign cannot be planned: {exc}") from exc
    task_id_by_digest = {task.digest(): task.id for task in resolved.tasks}
    entrant_id_by_digest = {entrant.digest(): entrant.id for entrant in resolved.entrants}
    counts: dict[tuple[str, str], int] = {}
    for trial in resolved.trials:
        key = (task_id_by_digest[trial.task_digest], entrant_id_by_digest[trial.entrant_digest])
        counts[key] = counts.get(key, 0) + 1
    cells = [
        MatrixPreviewCell(task_id=task_id, entrant_id=entrant_id, repetitions=n)
        for (task_id, entrant_id), n in sorted(counts.items())
    ]
    return MatrixPreview(
        campaign_id=campaign_id, trial_count=len(resolved.trials),
        cohort_id=resolved.cohort.id, protocol_id=resolved.protocol.id,
        budget_profile_id=resolved.budget.id,
        reserved_budget_usd=budgets.reserved_amount_from_resolved(resolved.model_dump(mode="json")),
        cells=cells,
    )


def _state_response(session: Session, campaign: CampaignRow, *, notice: str | None = None) -> CampaignStateResponse:
    return CampaignStateResponse(
        campaign=_summary(campaign), reservation=_reservation_summary(session, campaign.id), notice=notice,
        draft=validate_stored_manifest(CampaignDraft, campaign.draft, kind="campaign draft", row_id=campaign.id) if campaign.state == "draft" else None,
    )


@router.post("/{campaign_id}/start", response_model=CampaignStateResponse)
def start_campaign(
    campaign_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """frozen -> running: reserve the declared budget (estimated, not a hard
    provider hold), enqueue the frozen trial matrix, then flip to running."""
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    scope = f"POST /v1/campaigns/{campaign_id}/start"
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body={})
    if cached is not None:
        return CampaignStateResponse.model_validate(cached)
    if row.state != "frozen":
        raise conflict(f"only a frozen campaign can be started, not one in state {row.state}")
    budgets.reserve_campaign_budget(session, row)
    repository.enqueue_frozen_campaign(session, campaign_id)  # commits the reservation + trial rows
    updated = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state == "frozen")
        .values(state="running").returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        replay = check_or_reserve(session, scope=scope, key=idempotency_key, body={})
        if replay is not None:
            return CampaignStateResponse.model_validate(replay)
        current = session.get(CampaignRow, campaign_id)
        raise conflict(f"cannot start a campaign in state {current.state}")
    session.commit()
    response = _state_response(
        session, updated,
        notice="Trials are enqueued and dispatching. Budget is reserved as an estimate, not a hard provider cap.",
    )
    replay = finalize(session, scope=scope, key=idempotency_key, body={}, status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else CampaignStateResponse.model_validate(replay)


def _transition(session: Session, campaign_id: UUID, *, frm: tuple[str, ...], to: str, notice: str) -> CampaignStateResponse:
    updated = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state.in_(frm))
        .values(state=to).returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        current = session.get(CampaignRow, campaign_id)
        if current is None:
            raise not_found()
        if current.state == to:
            session.commit()
            return _state_response(session, current, notice="Already in the requested state.")
        raise conflict(f"cannot transition a campaign in state {current.state}")
    session.commit()
    return _state_response(session, updated, notice=notice)


@router.post("/{campaign_id}/pause", response_model=CampaignStateResponse)
def pause_campaign(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """running -> paused: leased work finishes, but no NEW work dispatches for
    this campaign until it is resumed."""
    return _transition(
        session, campaign_id, frm=("running",), to="paused",
        notice="Paused: in-flight leased work will finish; no new trials will be dispatched until resumed.",
    )


@router.post("/{campaign_id}/resume", response_model=CampaignStateResponse)
def resume_campaign(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    return _transition(
        session, campaign_id, frm=("paused",), to="running", notice="Resumed: trial dispatch continues.",
    )


@router.post("/{campaign_id}/cancel", response_model=CampaignStateResponse)
def cancel_campaign_route(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """frozen/running/paused -> cancelling: stop new dispatch and release the
    budget reservation. Already-leased work finishes or expires naturally; the
    worker finalizes the campaign to `cancelled` once nothing remains."""
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    if row.state in ("cancelling", "cancelled"):
        return _state_response(session, row, notice="Cancellation already in progress or complete.")
    updated = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state.in_(("frozen", "running", "paused")))
        .values(state="cancelling").returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        current = session.get(CampaignRow, campaign_id)
        raise conflict(f"cannot cancel a campaign in state {current.state}")
    budgets.set_reservation_status(session, campaign_id, "released")
    session.commit()
    return _state_response(
        session, updated,
        notice="Cancelling: no new trials dispatch; leased work finishes or expires; the reservation is released and the campaign becomes cancelled once no work remains.",
    )


@router.get("/{campaign_id}", response_model=CampaignStateResponse)
def get_campaign(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    return _state_response(session, row)


@router.get("/{campaign_id}/progress", response_model=CampaignProgress)
def campaign_progress(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> CampaignProgress:
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    planned = len(row.resolved.get("trials", [])) if isinstance(row.resolved, dict) else 0
    observed = session.execute(select(func.count()).select_from(TrialRow).where(TrialRow.campaign_id == campaign_id)).scalar_one()

    def _counts(column, extra_join=False):
        query = (
            select(column, func.count())
            .select_from(AttemptRow)
            .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
            .where(TrialRow.campaign_id == campaign_id)
            .group_by(column)
        )
        return [CampaignStateCount(state=str(value), count=count) for value, count in session.execute(query).all() if value is not None]

    attempts_by_phase = _counts(AttemptRow.phase)
    attempts_by_terminal = _counts(AttemptRow.terminal_status)
    from ..models import WorkItemRow

    work_items = session.execute(
        select(WorkItemRow.state, func.count())
        .select_from(WorkItemRow)
        .join(AttemptRow, AttemptRow.id == WorkItemRow.attempt_id)
        .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .where(TrialRow.campaign_id == campaign_id)
        .group_by(WorkItemRow.state)
    ).all()
    return CampaignProgress(
        campaign_id=campaign_id, state=row.state, planned_trials=planned, observed_trials=observed,
        attempts_by_phase=attempts_by_phase, attempts_by_terminal_status=attempts_by_terminal,
        work_items_by_state=[CampaignStateCount(state=str(s), count=c) for s, c in work_items],
    )


@router.get("/{campaign_id}/invalid-attempts", response_model=list[InvalidAttemptEntry])
def campaign_invalid_attempts(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> list[InvalidAttemptEntry]:
    if session.get(CampaignRow, campaign_id) is None:
        raise not_found()
    rows = session.execute(
        select(AttemptRow.trial_id, AttemptRow.id, AttemptRow.number, AttemptRow.terminal_status)
        .select_from(AttemptRow)
        .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .where(TrialRow.campaign_id == campaign_id, AttemptRow.terminal_status.in_(("infrastructure_invalid", "cancelled")))
        .order_by(AttemptRow.created_at)
    ).all()
    return [
        InvalidAttemptEntry(trial_id=trial_id, attempt_id=attempt_id, attempt_number=number, terminal_status=status)
        for trial_id, attempt_id, number, status in rows
    ]
