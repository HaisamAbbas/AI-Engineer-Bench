"""Publication preparation, independent approval, and retained public exports."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import aggregation, signing
from ..auth import Identity, require_role, resolve_roles
from ..db import get_session
from ..errors import conflict, forbidden, invalid_request, not_found
from ..evidence_integrity import evidence_digest
from ..idempotency import check_or_reserve, finalize, principal_scope
from ..models import (
    AttemptEventRow, AttemptRow, AuditEventRow, CampaignRow, CandidateRow, CorrectionRunRow, EvaluationRow, PublicationPreparationRow,
    PublicationRow, ReviewRow, TrialRow,
)
from ..publication_evidence import build_evidence_manifest
from ..publication_export import build_publication_export
from ..schemas import (
    PublicationExport, PublicationPreparationDetail, PublicationPreparationSummary, PublicationPrepareRequest,
    PublicationReviewRequest, PublicationSignature, PublicationWithdrawRequest,
)
from ..snapshots import snapshot_digest
from .campaigns import _current_user_id

router = APIRouter(prefix="/v1", tags=["publications"])

# The `required_trace_coverage` contract: when the frozen protocol demands
# trace coverage, EVERY selected (published) attempt must have a recorded
# phase-lifecycle trace covering BOTH executed phases - a fenced
# `phase.started` event with payload phase `engineering` and one with payload
# phase `verification`. "Some event exists" is NOT coverage: an engineering
# trace alone proves nothing about verification instrumentation.
_TRACE_REQUIRED_PHASES = ("engineering", "verification")


def _summary(row: PublicationPreparationRow) -> PublicationPreparationSummary:
    return PublicationPreparationSummary(
        id=row.id, campaign_id=row.campaign_id, status=row.status,
        snapshot_digest=row.snapshot_digest, evidence_manifest_digest=row.evidence_manifest_digest,
        review_kind=row.review_kind, supersedes_publication_id=row.supersedes_publication_id,
        published_publication_id=row.published_publication_id, created_at=row.created_at.isoformat(),
        publication_class=row.publication_class,
    )


def _selected_evaluations(session: Session, campaign_id: UUID) -> dict[UUID, UUID]:
    # Match aggregation exactly: first scored attempt, with its latest evaluation.
    # Do not choose a later/better evaluation when the authoritative one is invalid.
    selected = {}
    attempts = session.execute(
        select(AttemptRow).join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .where(TrialRow.campaign_id == campaign_id).order_by(AttemptRow.number, AttemptRow.id)
    ).scalars()
    for attempt in attempts:
        if attempt.trial_id in selected or attempt.phase != "terminal":
            continue
        if attempt.terminal_status not in aggregation._VALID_TERMINAL_STATUSES:
            continue
        evaluation = aggregation._latest_evaluation(session, attempt.id)
        if evaluation is not None and evaluation.verdict == attempt.terminal_status:
            selected[attempt.trial_id] = evaluation.id
    return selected


def _prepared_data(
    session: Session, campaign_id: UUID, correction_run_id: UUID | None = None,
    publication_class: str = "ranked",
) -> tuple[dict, dict]:
    try:
        snapshot = aggregation.aggregate_campaign_snapshot(session, campaign_id, correction_run_id)
        if publication_class == "non_ranked":
            # A non-ranking publication must NEVER be treated as the canonical
            # ranked release by any consumer: force complete_for_rank false
            # INTO the hashed/signed snapshot (not merely a display label), and
            # disclose why. review recomputes with the same class, so the
            # digest comparison still holds.
            snapshot = {
                **snapshot,
                "complete_for_rank": False,
                "limitations": [
                    *snapshot.get("limitations", []),
                    "This publication is explicitly non-ranking: it may not be used as a canonical ranked "
                    "release (it was prepared with publication_class=non_ranked).",
                ],
            }
        manifest = build_evidence_manifest(session, campaign_id,
            {trial: row.id for trial, row in aggregation.selected_campaign_evaluations(session, campaign_id, correction_run_id).items()},
            snapshot=snapshot)
    except ValueError as exc:
        raise conflict(str(exc)) from exc
    return snapshot, manifest


def _prior_publication(session: Session, campaign_id: UUID, prior_id: UUID | None) -> PublicationRow | None:
    if prior_id is None:
        return None
    prior = session.execute(select(PublicationRow).where(PublicationRow.id == prior_id).with_for_update()).scalar_one_or_none()
    if prior is None or prior.campaign_id != campaign_id:
        raise invalid_request("superseded publication must belong to this campaign")
    if prior.status != "published":
        raise conflict("only the current published snapshot may be superseded")
    return prior


def _selected_attempt_ids(session: Session, campaign_id: UUID) -> tuple[int, list[UUID], int]:
    """(planned trials, selected attempt ids, selected trial count) using the
    SAME authoritative selection aggregation publishes."""
    from ..aggregation import CampaignNotAggregatable, selected_campaign_evaluations

    campaign = session.get(CampaignRow, campaign_id)
    resolved = campaign.resolved if campaign is not None else None
    planned = len(resolved.get("trials", [])) if isinstance(resolved, dict) and isinstance(resolved.get("trials"), list) else 0
    try:
        selected = selected_campaign_evaluations(session, campaign_id)
    except CampaignNotAggregatable as exc:
        raise conflict(f"campaign cannot be aggregated for publication: {exc}") from exc
    attempt_ids = list(session.scalars(
        select(CandidateRow.attempt_id).where(
            CandidateRow.id.in_([evaluation.candidate_id for evaluation in selected.values()]),
        )
    ))
    return planned, attempt_ids, len(selected)


def _missing_trace_phases(session: Session, attempt_ids: list[UUID]) -> list[tuple[str, str]]:
    """Per selected attempt, which required phases lack a recorded
    `phase.started` trace event. Empty means the contract holds."""
    if not attempt_ids:
        return []
    events = session.execute(
        select(AttemptEventRow.attempt_id, AttemptEventRow.payload)
        .where(AttemptEventRow.attempt_id.in_(attempt_ids), AttemptEventRow.event_type == "phase.started")
    ).all()
    phases_by_attempt: dict[UUID, set[str]] = {}
    for attempt_id, payload in events:
        phase = payload.get("phase") if isinstance(payload, dict) else None
        if isinstance(phase, str):
            phases_by_attempt.setdefault(attempt_id, set()).add(phase)
    missing: list[tuple[str, str]] = []
    for attempt_id in attempt_ids:
        recorded = phases_by_attempt.get(attempt_id, set())
        for phase in _TRACE_REQUIRED_PHASES:
            if phase not in recorded:
                missing.append((str(attempt_id), phase))
    return missing


def _publication_eligibility_error(session: Session, campaign: CampaignRow, snapshot: dict, publication_class: str) -> str | None:
    """Prompt 14 publication eligibility gates, enforced from the FROZEN
    protocol manifest and the authoritative selection - not from client
    claims. Returns a conflict message, or None when the campaign is eligible.

    1. Cohort coverage: a `ranked` publication requires the campaign to be
       complete with the full planned cohort resolved; `complete_for_rank`
       must honestly reflect that. `non_ranked` publications are the only way
       to publish an incomplete snapshot, and they are labelled so they can
       never become the canonical ranked release.
    2. `protocol.required_trace_coverage`: every selected attempt must carry
       the phase trace contract (see `_TRACE_REQUIRED_PHASES`).
    3. `protocol.hard_cost_ranking`: the coverage disclosure must establish
       hard-cost eligibility; an estimated-accounting snapshot is not a
       hard-cost-ranked publication.
    """
    resolved = campaign.resolved if isinstance(campaign.resolved, dict) else {}
    protocol = resolved.get("protocol")
    if not isinstance(protocol, dict):
        return "the frozen campaign has no protocol manifest to enforce eligibility against"
    planned, attempt_ids, resolved_count = _selected_attempt_ids(session, campaign.id)

    if publication_class == "ranked":
        if planned == 0 or resolved_count < planned:
            return (
                f"cohort coverage is incomplete ({resolved_count} of {planned} planned trials resolved); "
                "a ranked publication requires a complete cohort - prepare an explicitly non_ranked "
                "publication to publish a disclosed incomplete snapshot"
            )
        if campaign.state != "completed":
            return f"campaign state is {campaign.state}; a ranked publication requires a completed campaign"
        if snapshot.get("complete_for_rank") is not True:
            return "the snapshot is not complete_for_rank; a ranked publication requires a complete-cohort snapshot"
    else:
        if snapshot.get("complete_for_rank") is True and (planned == 0 or resolved_count < planned):
            return "a snapshot claiming complete_for_rank cannot be published with incomplete cohort coverage"

    if protocol.get("required_trace_coverage"):
        missing = _missing_trace_phases(session, attempt_ids)
        if missing:
            attempt, phase = missing[0]
            return (
                f"protocol requires trace coverage but {len(missing)} required phase trace(s) are missing "
                f"(first: attempt {attempt} lacks a recorded '{phase}' phase start); "
                "missing evidence cannot be published as covered"
            )

    if protocol.get("hard_cost_ranking"):
        disclosure = snapshot.get("coverage_disclosure") if isinstance(snapshot, dict) else None
        hard_eligible = disclosure.get("hard_cost_eligible") if isinstance(disclosure, dict) else None
        if hard_eligible is not True:
            return (
                "protocol requires hard_cost_ranking, but hard-cost eligibility is not established by the "
                "coverage disclosure (provider accounting is estimated, not hard); this snapshot cannot be "
                "published as a hard-cost-ranked result"
            )
    return None


@router.post("/campaigns/{campaign_id}/publications/prepare", response_model=PublicationPreparationSummary)
def prepare_publication(
    campaign_id: UUID, body: PublicationPrepareRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("operator")), session: Session = Depends(get_session),
) -> PublicationPreparationSummary:
    campaign = session.execute(select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()).scalar_one_or_none()
    if campaign is None:
        raise not_found()
    if campaign.state not in {"completed", "incomplete"}:
        raise conflict("publication requires a terminal campaign")
    scope = principal_scope(
        f"POST /v1/campaigns/{campaign_id}/publications/prepare", str(_current_user_id(session, identity)))
    request_body = body.model_dump(mode="json")
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return PublicationPreparationSummary.model_validate(cached)
    if body.correction_run_id is not None:
        run = session.get(CorrectionRunRow, body.correction_run_id)
        if run is None or run.campaign_id != campaign_id:
            raise invalid_request("correction run must belong to this campaign")
        if run.status != "completed":
            raise conflict("correction run must be completed before preparing a publication")
    _prior_publication(session, campaign_id, body.supersedes_publication_id)
    snapshot, manifest = _prepared_data(session, campaign_id, body.correction_run_id, body.publication_class)
    # Prompt 14 eligibility gates run at PREPARE time, against the exact
    # snapshot that will be signed - and are re-verified at approval below.
    eligibility_error = _publication_eligibility_error(session, campaign, snapshot, body.publication_class)
    if eligibility_error is not None:
        raise conflict(eligibility_error)
    snapshot_hash, manifest_hash = snapshot_digest(snapshot), evidence_digest(manifest)
    if body.supersedes_publication_id and body.correction_run_id is None:
        raise conflict("superseding requires the correction run that justifies it")
    row = session.execute(select(PublicationPreparationRow).where(
        PublicationPreparationRow.campaign_id == campaign_id,
        PublicationPreparationRow.status == "prepared",
        PublicationPreparationRow.snapshot_digest == snapshot_hash,
        PublicationPreparationRow.evidence_manifest_digest == manifest_hash,
        PublicationPreparationRow.supersedes_publication_id == body.supersedes_publication_id,
        PublicationPreparationRow.correction_reason == body.correction_reason,
        PublicationPreparationRow.publication_class == body.publication_class,
        PublicationPreparationRow.prepared_by_user_id == _current_user_id(session, identity),
    )).scalar_one_or_none()
    if row is None:
        row = PublicationPreparationRow(
            campaign_id=campaign_id, prepared_by_user_id=_current_user_id(session, identity),
            snapshot=snapshot, snapshot_digest=snapshot_hash, evidence_manifest=manifest,
            evidence_manifest_digest=manifest_hash, supersedes_publication_id=body.supersedes_publication_id,
            correction_reason=body.correction_reason, status="prepared",
            publication_class=body.publication_class,
        )
        session.add(row)
        session.flush()
    summary = _summary(row)
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=summary.model_dump(mode="json"))
    return summary if replay is None else PublicationPreparationSummary.model_validate(replay)


@router.get("/publications/preparations/{preparation_id}", response_model=PublicationPreparationDetail)
def get_preparation(
    preparation_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> PublicationPreparationDetail:
    row = session.get(PublicationPreparationRow, preparation_id)
    if row is None:
        raise not_found()
    if snapshot_digest(row.snapshot) != row.snapshot_digest or evidence_digest(row.evidence_manifest) != row.evidence_manifest_digest:
        raise conflict("prepared evidence failed its integrity check")
    campaign = session.get(CampaignRow, row.campaign_id)
    user_id = _current_user_id(session, identity)
    reason = None
    if row.status != "prepared":
        reason = "This preparation has already been reviewed."
    elif user_id in {row.prepared_by_user_id, campaign.created_by_user_id}:
        reason = "The preparer and campaign creator cannot approve their own publication."
    elif not set(resolve_roles(session, identity)) & {"reviewer", "administrator"}:
        reason = "A reviewer or administrator role is required."
    return PublicationPreparationDetail(preparation=_summary(row), snapshot=row.snapshot,
        evidence_manifest=row.evidence_manifest, correction_reason=row.correction_reason,
        can_approve=reason is None, approval_blocked_reason=reason)


@router.post("/publications/preparations/{preparation_id}/review", response_model=PublicationPreparationSummary)
def review_publication(
    preparation_id: UUID, body: PublicationReviewRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("reviewer", "administrator")), session: Session = Depends(get_session),
) -> PublicationPreparationSummary:
    # Lock campaign first, consistently with prepare.
    campaign_id = session.execute(select(PublicationPreparationRow.campaign_id).where(
        PublicationPreparationRow.id == preparation_id,
    )).scalar_one_or_none()
    if campaign_id is None:
        raise not_found()
    reviewer_id = _current_user_id(session, identity)
    scope = principal_scope(f"POST /v1/publications/preparations/{preparation_id}/review", str(reviewer_id))
    request_body = body.model_dump(mode="json")
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return PublicationPreparationSummary.model_validate(cached)
    campaign = session.execute(select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()).scalar_one()
    row = session.execute(select(PublicationPreparationRow).where(
        PublicationPreparationRow.id == preparation_id,
    ).with_for_update()).scalar_one()
    if row.status != "prepared":
        raise conflict("preparation has already been reviewed")
    if body.decision == "approve" and reviewer_id in {row.prepared_by_user_id, campaign.created_by_user_id}:
        raise forbidden("preparer and campaign creator cannot approve their own publication")
    # review_kind is derived SERVER-SIDE from recorded identities; the client
    # never selects it. The reviewer may attest organizational independence -
    # that attestation is an INPUT to the derivation, not the label itself.
    # Server policy (recorded in the review evidence and audit trail):
    #   - reviewer is the preparer or campaign creator: approval forbidden
    #     outright (official self-approval stays prohibited);
    #   - distinct identity + attestation of organizational independence:
    #     `independent`;
    #   - distinct identity WITHOUT attestation: `single_maintainer` - a
    #     technically-distinct OIDC identity does not by itself prove
    #     organizational independence, so the honest label is kept.
    review_kind = "independent" if body.independence_attestation else "single_maintainer"
    if body.decision == "approve":
        # Prompt 14 eligibility gates are re-verified at approval time against
        # the prepared snapshot: a gate that passed at prepare can no longer
        # be bypassed by approving a stale or mutated preparation.
        eligibility_error = _publication_eligibility_error(session, campaign, row.snapshot, row.publication_class)
        if eligibility_error is not None:
            raise conflict(eligibility_error)
        # Recover the correction identity from the exact pinned evaluations,
        # never from whichever correction run most recently completed.
        correction_ids = set()
        for selection in row.evidence_manifest.get("selections", []):
            if selection.get("included"):
                evaluation = session.get(EvaluationRow, UUID(selection["evaluation_id"]))
                if evaluation is None:
                    raise conflict("prepared evaluation no longer exists")
                if evaluation.correction_run_id is not None:
                    correction_ids.add(evaluation.correction_run_id)
        if len(correction_ids) > 1:
            raise conflict("preparation mixes correction runs")
        snapshot, manifest = _prepared_data(session, campaign_id, next(iter(correction_ids), None), row.publication_class)
        if (snapshot_digest(row.snapshot) != row.snapshot_digest
                or evidence_digest(row.evidence_manifest) != row.evidence_manifest_digest
                or snapshot_digest(snapshot) != row.snapshot_digest
                or evidence_digest(manifest) != row.evidence_manifest_digest):
            raise conflict("prepared evidence has changed; prepare again before approval")
        prior = _prior_publication(session, campaign_id, row.supersedes_publication_id)
        current = session.execute(select(PublicationRow.id).where(
            PublicationRow.campaign_id == campaign_id, PublicationRow.status == "published",
        )).scalars().all()
        if current and current != ([prior.id] if prior else []):
            raise conflict("a current publication must be explicitly superseded")
        now = datetime.now(timezone.utc)
        signed = signing.sign_manifest({
            "schema_version": signing.SIGNED_MANIFEST_SCHEMA_VERSION,
            "campaign_id": str(campaign_id), "snapshot_digest": row.snapshot_digest,
            "evidence_manifest_digest": row.evidence_manifest_digest,
            "reviewer_id": str(reviewer_id), "review_kind": review_kind,
            "publication_class": row.publication_class,
            "supersedes_id": str(prior.id) if prior else None, "created_at": now.isoformat(),
        })
        publication = PublicationRow(
            campaign_id=campaign_id, snapshot=row.snapshot, snapshot_digest=row.snapshot_digest,
            evidence_manifest=row.evidence_manifest, reviewer_id=reviewer_id,
            review_kind=review_kind, supersedes_id=prior.id if prior else None,
            reason=row.correction_reason, status="published", created_at=now,
            publication_class=row.publication_class,
            manifest_signature=signed.manifest_signature, signing_public_key=signed.signing_public_key,
            signing_key_id=signed.signing_key_id, signed_manifest=signed.signed_manifest,
        )
        session.add(publication)
        session.flush()
        if prior is not None:
            prior.status = "superseded"
        row.status = "published"
        row.published_publication_id = publication.id
        session.add(AuditEventRow(
            actor_user_id=reviewer_id, target_type="publication", target_id=publication.id,
            action="publication_published", evidence={"preparation_id": str(row.id), "review_kind": review_kind,
                                                      "independence_attestation": body.independence_attestation},
        ))
    else:
        row.status = "rejected"
    row.review_kind = review_kind
    session.add(ReviewRow(
        reviewer_id=reviewer_id, target_type="publication_preparation", target_id=row.id,
        decision=body.decision, evidence={"notes": body.notes, "review_kind": review_kind,
                                          "independence_attestation": body.independence_attestation},
    ))
    summary = _summary(row)
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=summary.model_dump(mode="json"))
    return summary if replay is None else PublicationPreparationSummary.model_validate(replay)


@router.post("/publications/{publication_id}/withdraw", response_model=PublicationExport)
def withdraw_publication(
    publication_id: UUID, body: PublicationWithdrawRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("reviewer", "administrator")), session: Session = Depends(get_session),
) -> PublicationExport:
    if not body.reason.strip():
        raise invalid_request("withdrawal requires a reason")
    row = session.execute(select(PublicationRow).where(PublicationRow.id == publication_id).with_for_update()).scalar_one_or_none()
    if row is None:
        raise not_found()
    scope = principal_scope(f"POST /v1/publications/{publication_id}/withdraw", str(_current_user_id(session, identity)))
    request_body = {"reason": body.reason}
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return PublicationExport.model_validate(cached)
    if row.status != "withdrawn":
        # The withdrawal rationale is recorded SEPARATELY from the correction
        # reason (`reason`): a withdrawal must never overwrite the frozen
        # correction/supersession rationale. The immutability trigger enforces
        # that withdrawal_reason can only be SET on the published -> withdrawn
        # transition, never rewritten afterwards.
        row.status = "withdrawn"
        row.withdrawal_reason = body.reason
        session.add(AuditEventRow(
            actor_user_id=_current_user_id(session, identity), target_type="publication", target_id=row.id,
            action="publication_withdrawn", evidence={"reason": body.reason},
        ))
    export = build_publication_export(session, publication_id)
    replay = finalize(session, scope=scope, key=idempotency_key, body=request_body,
        status_code=200, response_body=export.model_dump(mode="json"))
    return export if replay is None else PublicationExport.model_validate(replay)


@router.get("/publications/{publication_id}/export", response_model=PublicationExport)
def export_publication(publication_id: UUID, session: Session = Depends(get_session)) -> PublicationExport:
    return build_publication_export(session, publication_id)


@router.get("/publications/{publication_id}/signature", response_model=PublicationSignature)
def publication_signature(publication_id: UUID, session: Session = Depends(get_session)) -> PublicationSignature:
    row = session.get(PublicationRow, publication_id)
    if row is None or not all((row.signed_manifest, row.manifest_signature, row.signing_public_key, row.signing_key_id)):
        raise not_found()
    return PublicationSignature(
        publication_id=row.id, signed_manifest=row.signed_manifest, manifest_signature=row.manifest_signature,
        signing_public_key=row.signing_public_key, signing_key_id=row.signing_key_id, review_kind=row.review_kind,
    )

