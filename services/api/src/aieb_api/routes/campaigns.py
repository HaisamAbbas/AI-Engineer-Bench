"""Campaign lifecycle routes: draft editing, freeze, plan (exact matrix
materialization), start, pause/resume, cancel, approval, progress.

Draft editing uses optimistic revision control (If-Match). Freezing is
immutable: any change after freeze must create a new campaign (spec
section 13/14). V2-GAP-004 adds the orchestration sequence on top of the
frozen manifest - freeze -> plan -> approve -> start - each a replay-safe,
role-gated transition whose legal moves are the shared state machine in
aieb_api.orchestration (mirrored by a PostgreSQL trigger).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from aieb_core.models import CampaignDraft, EntrantRevision, TaskRevision
from aieb_core.planner import PlanningError, Registry, freeze_campaign, resolve_campaign
from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import admission, budgets, orchestration
from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, forbidden, invalid_request, not_found, stale_revision
from ..idempotency import check_or_reserve, finalize, principal_scope
from ..models import (
    AttemptRow,
    AuditEventRow,
    BudgetReservationRow,
    CampaignRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    ProtocolRevisionRow,
    ReviewRow,
    TaskRevisionRow,
    TrialRow,
    User,
)
from ..revisions import validate_stored_manifest
from ..schemas import (
    BudgetReservationSummary,
    CampaignApproveRequest,
    CampaignApprovalSummary,
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
    MatrixPreviewTrial,
    ResumeCampaignRequest,
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
        matrix_digest=row.matrix_digest,
    )


@router.post("", response_model=CampaignSummary, status_code=201)
def create_campaign(
    body: CampaignCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignSummary:
    request_body = body.model_dump(mode="json")
    scope = principal_scope("POST /v1/campaigns", str(_current_user_id(session, identity)))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
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
        session, scope=scope, key=idempotency_key, body=request_body,
        status_code=201, response_body=summary.model_dump(mode="json"),
    )
    return summary if replay is None else CampaignSummary.model_validate(replay)


@router.patch("/{campaign_id}", response_model=CampaignSummary)
def patch_campaign(
    campaign_id: UUID,
    body: CampaignPatchRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignSummary:
    if if_match is None:
        raise invalid_request("If-Match header with the current revision is required")
    try:
        expected_revision = int(if_match.strip('"'))
    except ValueError as exc:
        raise invalid_request("If-Match must be an integer revision") from exc

    # PATCH is a mutation like any other (spec section 22): a network-lost
    # response must be replayable with the same key instead of risking a
    # double edit. Optimistic concurrency (If-Match) and the idempotency
    # record commit in ONE transaction below, so a replayed draft edit and
    # its stored response are atomic with the business write.
    request_body = {"draft": body.draft.model_dump(mode="json"), "if_match": expected_revision}
    scope = principal_scope(f"PATCH /v1/campaigns/{campaign_id}", str(_current_user_id(session, identity)))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return CampaignSummary.model_validate(cached)

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
        # The conditional UPDATE may have waited for an identical request to
        # commit. Recheck its atomically stored response before reporting stale
        # state/revision; a true competing edit still gets the usual conflict.
        cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
        if cached is not None:
            return CampaignSummary.model_validate(cached)
        row = session.get(CampaignRow, campaign_id)
        if row is None:
            raise not_found()
        if row.state != "draft":
            raise conflict("only a draft campaign can be edited")
        raise stale_revision()
    summary = _summary(updated)
    replay = finalize(
        session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=summary.model_dump(mode="json"),
    )
    return summary if replay is None else CampaignSummary.model_validate(replay)


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
    scope = principal_scope(f"POST /v1/campaigns/{campaign_id}/freeze", str(_current_user_id(session, identity)))
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
    registry = _registry_for(session, draft, registry_body)
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
    """Resolve explicit stored-version pins without discarding historical rows.

    Legacy unpinned requests fail closed when a slug is ambiguous. Preview and
    freeze use the same pins; the resolved manifests retain the chosen versions.
    """
    def _unique_revision(model, slugs, kind, pins):
        if set(pins) - set(slugs):
            raise invalid_request(f"{kind} version pins include references outside the draft")
        rows = session.execute(select(model).where(model.slug.in_(slugs))).scalars().all()
        by_slug: dict[str, list] = {}
        for row in rows:
            by_slug.setdefault(row.slug, []).append(row)
        resolved = {}
        selected_rows = []
        for slug in slugs:
            versions = by_slug.get(slug, [])
            if slug in pins:
                versions = [row for row in versions if row.version == pins[slug]]
            if not versions:
                raise invalid_request(f"unresolved {kind} reference: {slug}")
            if len(versions) > 1:
                raise conflict(
                    f"{kind} {slug} has {len(versions)} stored revisions; "
                    f"supply an explicit {kind} version pin"
                )
            selected = versions[0]
            selected_rows.append(selected)
            contract = TaskRevision if model is TaskRevisionRow else EntrantRevision
            resolved[slug] = validate_stored_manifest(contract, selected.manifest, kind=kind, row_id=selected.id)
        if model is TaskRevisionRow:
            # A frozen task identity is not an admitted task.  Preview and
            # freeze share this exact resolver, so neither can place a
            # pending/failed/rejected revision into a campaign matrix.
            admission.require_release_eligible(session, [row.id for row in selected_rows])
        return resolved

    return Registry(
        tasks=_unique_revision(TaskRevisionRow, draft.task_ids, "task", registry_body.task_versions),
        entrants=_unique_revision(EntrantRevisionRow, draft.entrant_ids, "entrant", registry_body.entrant_versions),
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
    so an operator can preview coverage/cost before committing to a freeze.

    Deliberately NOT idempotency-keyed: this POST writes nothing, so it is a
    read in disguise (the OpenAPI mutation audit lists it as non-persisting)
    and there is no response to replay."""
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
    trials = [
        MatrixPreviewTrial(
            trial_id=trial.id, task_id=task_id_by_digest[trial.task_digest],
            entrant_id=entrant_id_by_digest[trial.entrant_digest],
            repetition_index=trial.repetition_index, order_index=trial.order_index,
        )
        for trial in resolved.trials
    ]
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
        cells=cells, trials=trials,
    )


def _state_response(session: Session, campaign: CampaignRow, *, notice: str | None = None) -> CampaignStateResponse:
    return CampaignStateResponse(
        campaign=_summary(campaign), reservation=_reservation_summary(session, campaign.id), notice=notice,
        draft=validate_stored_manifest(CampaignDraft, campaign.draft, kind="campaign draft", row_id=campaign.id) if campaign.state == "draft" else None,
    )


@router.post("/{campaign_id}/plan", response_model=CampaignStateResponse)
def plan_campaign(
    campaign_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """frozen -> planned: expand the EXACT cell matrix from the frozen release
    manifest and pin it (V2-GAP-004 plan sections 1/3/4).

    Every `task_revision x entrant_revision x repetition_index` cell becomes a
    persisted trial row with the plan-required per-cell identity (the
    deterministic trial id plus its contract cell digest), frozen order, worst-
    case budget allocation, planned wall-clock deadline, and initial
    `planned` status. The campaign's `matrix_digest` - a canonical digest over
    the full cell-identity set - is recorded here and re-verified before start
    and at aggregation, so a matrix altered after planning can never run.

    Expansion is idempotent (an already-materialized matrix is left as-is and
    re-verified) and shares the transaction with the state transition, so a
    failure cannot leave half a matrix or a planned campaign without cells.
    Operator-authorized, replay-safe, and never public: `POST /preview` remains
    the read-only dry run for drafts.
    """
    row = session.execute(
        select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    scope = principal_scope(f"POST /v1/campaigns/{campaign_id}/plan", str(_current_user_id(session, identity)))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body={})
    if cached is not None:
        return CampaignStateResponse.model_validate(cached)
    if row.state != "frozen":
        raise conflict(f"only a frozen campaign can be planned, not one in state {row.state}")
    if not isinstance(row.resolved, dict) or not row.resolved.get("trials"):
        raise conflict("campaign has no frozen resolved manifest matrix to plan")

    # Materialize cells from the FROZEN manifest, then verify the persisted
    # matrix exactly equals it (membership, identities, digests) BEFORE the
    # transition. The unique (campaign, task, entrant, repetition) constraint
    # and the deterministic trial ids make duplicates impossible; anything
    # unexpected rolls back both the rows and this plan.
    try:
        repository.materialize_frozen_matrix(session, campaign_id)  # flushed, not committed
        matrix_digest = orchestration.verify_matrix_or_raise(session, row)
    except orchestration.MatrixMismatchError as exc:
        raise invalid_request(f"campaign matrix could not be planned: {exc}") from exc

    updated = session.execute(
        update(CampaignRow)
        .where(CampaignRow.id == campaign_id, CampaignRow.state == "frozen", CampaignRow.revision == row.revision)
        .values(state="planned", matrix_digest=matrix_digest, revision=CampaignRow.revision + 1)
        .returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        replay = check_or_reserve(session, scope=scope, key=idempotency_key, body={})
        if replay is not None:
            return CampaignStateResponse.model_validate(replay)
        current = session.get(CampaignRow, campaign_id)
        if current is None:
            raise not_found()
        raise conflict(f"cannot plan a campaign in state {current.state}")
    session.flush()
    response = _state_response(
        session, updated,
        notice="Cell matrix materialized from the frozen manifest and pinned by matrix digest.",
    )
    replay = finalize(session, scope=scope, key=idempotency_key, body={}, status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else CampaignStateResponse.model_validate(replay)


@router.post("/{campaign_id}/start", response_model=CampaignStateResponse)
def start_campaign(
    campaign_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """approved -> running: verify the exact matrix, reserve the declared
    budget (estimated, not a hard provider hold), enqueue first attempts for
    every cell, then flip to running. All writes, including the idempotent
    response, share one transaction.

    Server-side gates (V2-GAP-004 plan sections 3-5): the campaign must be
    `approved` - reachable only through freeze -> plan -> independent
    approval, so start cannot bypass the approval sequence - the persisted
    matrix must exactly equal the frozen manifest matrix, the worst-case
    reservation must be known (a missing role cap or environment bound is
    refused, never silently dropped from the total), and it must not exceed
    the authorized cap (`AIEB_BUDGET_CAP_USD`) when one is configured."""
    row = session.execute(
        select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    scope = principal_scope(f"POST /v1/campaigns/{campaign_id}/start", str(_current_user_id(session, identity)))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body={})
    if cached is not None:
        return CampaignStateResponse.model_validate(cached)
    if row.state != "approved":
        raise conflict(
            f"only an approved campaign can be started, not one in state {row.state} "
            "(freeze, plan the matrix, then obtain independent approval first)"
        )
    try:
        orchestration.verify_matrix_or_raise(session, row)
    except orchestration.MatrixMismatchError as exc:
        raise conflict(str(exc)) from exc
    try:
        budgets.reserve_campaign_budget(session, row)
    except budgets.BudgetError as exc:
        raise conflict(str(exc)) from exc
    repository.enqueue_campaign_attempts(session, campaign_id)  # flushed, not committed
    updated = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state == "approved")
        .values(state="running").returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        replay = check_or_reserve(session, scope=scope, key=idempotency_key, body={})
        if replay is not None:
            return CampaignStateResponse.model_validate(replay)
        current = session.get(CampaignRow, campaign_id)
        raise conflict(f"cannot start a campaign in state {current.state}")
    session.flush()
    response = _state_response(
        session, updated,
        notice="Trials are enqueued and dispatching. Budget is reserved as an estimate, not a hard provider cap.",
    )
    replay = finalize(session, scope=scope, key=idempotency_key, body={}, status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else CampaignStateResponse.model_validate(replay)


def _transition(
    session: Session, campaign_id: UUID, *, frm: tuple[str, ...], to: str, notice: str,
    idempotency_scope: str, idempotency_key: str | None,
) -> CampaignStateResponse:
    """A replay-safe campaign state transition: the state write and the
    idempotency record commit together, so a network-lost response can be
    retried with the SAME key and receive the stored result (spec section
    33/API-01 - every mutation, not only create/freeze/start, is replay-safe).
    """
    request_body: dict[str, str] = {"action": to}
    cached = check_or_reserve(session, scope=idempotency_scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return CampaignStateResponse.model_validate(cached)
    updated = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state.in_(frm))
        .values(state=to).returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        current = session.get(CampaignRow, campaign_id)
        if current is None:
            raise not_found()
        if current.state == to:
            session.flush()
            response = _state_response(session, current, notice="Already in the requested state.")
            replay = finalize(session, scope=idempotency_scope, key=idempotency_key, body=request_body,
                status_code=200, response_body=response.model_dump(mode="json"))
            return response if replay is None else CampaignStateResponse.model_validate(replay)
        raise conflict(f"cannot transition a campaign in state {current.state}")
    session.flush()
    response = _state_response(session, updated, notice=notice)
    replay = finalize(session, scope=idempotency_scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else CampaignStateResponse.model_validate(replay)


@router.post("/{campaign_id}/pause", response_model=CampaignStateResponse)
def pause_campaign(
    campaign_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """running -> paused: leased work finishes, but no NEW work dispatches for
    this campaign until it is resumed."""
    return _transition(
        session, campaign_id, frm=("running",), to="paused",
        notice="Paused: in-flight leased work will finish; no new trials will be dispatched until resumed.",
        idempotency_scope=principal_scope(
            f"POST /v1/campaigns/{campaign_id}/pause", str(_current_user_id(session, identity))),
        idempotency_key=idempotency_key,
    )


@router.post("/{campaign_id}/resume", response_model=CampaignStateResponse)
def resume_campaign(
    campaign_id: UUID,
    body: ResumeCampaignRequest = ResumeCampaignRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """ENG-020 (spec sections 39/48): a campaign the auto-pause mechanism paused requires
    `acknowledge_auto_pause=True` to resume - a plain resume (as for a manual pause) is
    refused, so the operator review the mechanism exists for cannot be skipped by habit."""
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None:
        raise not_found()
    if campaign.state == "paused" and campaign.auto_paused and not body.acknowledge_auto_pause:
        raise forbidden(
            "this campaign was paused automatically after repeated infrastructure failures "
            f"({campaign.auto_pause_reason}); resuming requires acknowledge_auto_pause=true "
            "after operator review"
        )
    response = _transition(
        session, campaign_id, frm=("paused",), to="running", notice="Resumed: trial dispatch continues.",
        idempotency_scope=principal_scope(
            f"POST /v1/campaigns/{campaign_id}/resume", str(_current_user_id(session, identity))),
        idempotency_key=idempotency_key,
    )
    if campaign.auto_paused and response.campaign.state == "running":
        campaign.auto_paused = False
        campaign.auto_pause_reason = None
        campaign.consecutive_infrastructure_failures = 0
        session.commit()
    return response


@router.post("/{campaign_id}/cancel", response_model=CampaignStateResponse)
def cancel_campaign_route(
    campaign_id: UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")),
    session: Session = Depends(get_session),
) -> CampaignStateResponse:
    """frozen/planned/approved/running/paused -> cancelling: stop new dispatch,
    keep the budget reservation ACTIVE until the drain completes. Releasing at
    request time would book the estimate back as available while leased work
    was still capable of billing spend; the reservation is released only when
    no ready/leased work remains. Already-leased work finishes or expires
    naturally; the worker (or the idle sweep) finalizes the campaign to
    `cancelled` once nothing remains. A drain with NOTHING outstanding - e.g.
    cancelling a planned campaign that was never started, or one whose last
    item just finished - is finalized to `cancelled` directly in THIS
    transaction, so it can never sit stuck in `cancelling` with no work item
    left to trigger the worker's completion path."""
    scope = principal_scope(f"POST /v1/campaigns/{campaign_id}/cancel", str(_current_user_id(session, identity)))
    request_body = {"action": "cancelling"}
    # Reserve/validate the key FIRST for every path below: a keyless mutation is
    # rejected before any state change, and every branch commits its stored
    # replay response with the business write.
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return CampaignStateResponse.model_validate(cached)
    row = session.execute(select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()).scalar_one_or_none()
    if row is None:
        raise not_found()
    if row.state in ("cancelling", "cancelled"):
        # A cancel arriving after the transition (or after the drain finished) is
        # an idempotent success, not a conflict. If the campaign is still
        # cancelling, this is also the checkpoint that finalizes a drain whose
        # last item already completed.
        if row.state == "cancelling":
            repository.maybe_complete_cancellation(session, campaign_id, commit=False)
        current = session.get(CampaignRow, campaign_id)
        response = _state_response(
            session, current,
            notice=("Cancellation complete: nothing remained to drain; the reservation is released."
                    if current.state == "cancelled" else
                    "Cancellation already in progress: no new trials dispatch; the reservation is released "
                    "once leased work finishes or expires."),
        )
        replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
            status_code=200, response_body=response.model_dump(mode="json"))
        return response if replay is None else CampaignStateResponse.model_validate(replay)
    updated = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state.in_(orchestration.CANCELLABLE_STATES))
        .values(state="cancelling").returning(CampaignRow)
    ).scalar_one_or_none()
    if updated is None:
        current = session.get(CampaignRow, campaign_id)
        raise conflict(f"cannot cancel a campaign in state {current.state}")
    # A drain with NOTHING outstanding completes in the SAME transaction as the
    # transition: no work item will ever trigger the worker's completion path
    # for it (a frozen campaign was never enqueued; a drained one has no items
    # left), so leaving it `cancelling` would strand it forever.
    if not repository.has_outstanding_work(session, campaign_id):
        updated.state = "cancelled"
        budgets.set_reservation_status(session, campaign_id, "released")
        session.flush()
        notice = "Cancelled immediately: the campaign had no ready or leased work to drain; the reservation is released."
    else:
        session.flush()
        notice = ("Cancelling: no new trials dispatch; leased work finishes or expires; the reservation is released "
                  "only after the drain completes.")
    response = _state_response(session, updated, notice=notice)
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=response.model_dump(mode="json"))
    return response if replay is None else CampaignStateResponse.model_validate(replay)


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


_CAMPAIGN_APPROVAL_TARGET = "campaign_approval"


@router.get("/{campaign_id}/approval", response_model=CampaignApprovalSummary)
def campaign_approval(
    campaign_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> CampaignApprovalSummary:
    """The campaign's approval fact. An unapproved frozen campaign reads
    `approved: false` - distinct from missing; the operator CLI's `run`
    gate refuses before ever issuing a start."""
    row = session.get(CampaignRow, campaign_id)
    if row is None:
        raise not_found()
    review = session.execute(
        select(ReviewRow).where(
            ReviewRow.target_type == _CAMPAIGN_APPROVAL_TARGET,
            ReviewRow.target_id == campaign_id,
            ReviewRow.decision == "approve",
        ).order_by(ReviewRow.created_at.desc(), ReviewRow.id.desc()).limit(1)
    ).scalar_one_or_none()
    if review is None:
        return CampaignApprovalSummary(campaign_id=campaign_id, approved=False)
    return CampaignApprovalSummary(
        campaign_id=campaign_id, approved=True, approved_by_user_id=review.reviewer_id,
        approved_at=review.created_at.isoformat().replace("+00:00", "Z"),
        reason=review.evidence.get("reason"), review_id=review.id,
    )


@router.post("/{campaign_id}/approve", response_model=CampaignApprovalSummary)
def approve_campaign(
    campaign_id: UUID,
    body: CampaignApproveRequest = CampaignApproveRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> CampaignApprovalSummary:
    """Reviewer-only independent approval of a PLANNED campaign.

    Anti-bypass rules:
    - reviewer must be distinct from the campaign's creator (no self-approval);
    - the campaign must be `planned` - i.e. frozen AND its exact cell matrix
      materialized and digest-pinned first - so what is approved is an
      executable matrix, not an abstract draft, and a running/ended campaign
      cannot subsequently be 'approved';
    - the recorded approval binds the exact frozen manifest, cohort, and
      matrix digests, so an approval can never be read as covering different
      content;
    - a replay-safe mutation like everything else: same key replays the same
      summary, a reused key with different body is 409.
    Approval records a durable decision AND transitions planned -> approved
    (the plan's campaign_approved state); actually dispatching work remains
    POST .../start with its own matrix/budget gates."""
    row = session.execute(
        select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    reviewer_id = session.execute(
        select(User.id).where(User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject)
    ).scalar_one_or_none()
    if reviewer_id is None:
        raise forbidden("approval requires a provisioned reviewer")
    if row.created_by_user_id is not None and reviewer_id == row.created_by_user_id:
        raise forbidden("the campaign creator cannot approve their own campaign")
    request_body = body.model_dump(mode="json")
    scope = principal_scope(f"POST /v1/campaigns/{campaign_id}/approve", str(reviewer_id))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return CampaignApprovalSummary.model_validate(cached)
    if row.state != "planned":
        raise conflict(
            f"only a planned campaign can be approved, not one in state {row.state} "
            "(freeze, then plan the exact matrix, before seeking approval)"
        )
    now = datetime.now(timezone.utc)
    bound_identities = {
        "manifest_digest": row.manifest_digest,
        "cohort_digest": row.cohort_digest,
        "matrix_digest": row.matrix_digest,
    }
    review = ReviewRow(
        reviewer_id=reviewer_id, target_type=_CAMPAIGN_APPROVAL_TARGET, target_id=campaign_id,
        decision="approve", created_at=now,
        evidence={"reason": body.reason, "campaign_id": str(campaign_id), **bound_identities},
    )
    session.add(review)
    session.flush()
    approved = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state == "planned")
        .values(state="approved", revision=CampaignRow.revision + 1).returning(CampaignRow)
    ).scalar_one_or_none()
    if approved is None:
        # The FOR UPDATE lock above serializes concurrent approvers, so this
        # can only be a replay of an approval that just committed.
        replay = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
        if replay is not None:
            return CampaignApprovalSummary.model_validate(replay)
        current = session.get(CampaignRow, campaign_id)
        raise conflict(f"cannot approve a campaign in state {current.state}")
    session.add(AuditEventRow(
        actor_user_id=reviewer_id, target_type="campaign", target_id=campaign_id,
        action="campaign_approved", created_at=now,
        evidence={"review_id": str(review.id), "reason": body.reason, **bound_identities},
    ))
    session.flush()
    summary = CampaignApprovalSummary(
        campaign_id=campaign_id, approved=True, approved_by_user_id=reviewer_id,
        approved_at=now.isoformat().replace("+00:00", "Z"), reason=body.reason, review_id=review.id,
    )
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
                      status_code=200, response_body=summary.model_dump(mode="json"))
    return summary if replay is None else CampaignApprovalSummary.model_validate(replay)


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
