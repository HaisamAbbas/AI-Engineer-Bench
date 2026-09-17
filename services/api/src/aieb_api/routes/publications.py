"""Publication preparation, independent approval, and retained public exports."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import aggregation, signing
from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, forbidden, invalid_request, not_found
from ..evidence_integrity import evidence_digest
from ..models import (
    AttemptRow, AuditEventRow, CampaignRow, PublicationPreparationRow,
    PublicationRow, ReviewRow, TrialRow,
)
from ..publication_evidence import build_evidence_manifest
from ..publication_export import build_publication_export
from ..schemas import (
    PublicationExport, PublicationPreparationSummary, PublicationPrepareRequest,
    PublicationReviewRequest, PublicationSignature, PublicationWithdrawRequest,
)
from ..snapshots import snapshot_digest
from .campaigns import _current_user_id

router = APIRouter(prefix="/v1", tags=["publications"])


def _summary(row: PublicationPreparationRow) -> PublicationPreparationSummary:
    return PublicationPreparationSummary(
        id=row.id, campaign_id=row.campaign_id, status=row.status,
        snapshot_digest=row.snapshot_digest, evidence_manifest_digest=row.evidence_manifest_digest,
        review_kind=row.review_kind, supersedes_publication_id=row.supersedes_publication_id,
        published_publication_id=row.published_publication_id, created_at=row.created_at.isoformat(),
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


def _prepared_data(session: Session, campaign_id: UUID) -> tuple[dict, dict]:
    try:
        snapshot = aggregation.aggregate_campaign_snapshot(session, campaign_id)
        manifest = build_evidence_manifest(session, campaign_id, _selected_evaluations(session, campaign_id), snapshot=snapshot)
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


@router.post("/campaigns/{campaign_id}/publications/prepare", response_model=PublicationPreparationSummary)
def prepare_publication(
    campaign_id: UUID, body: PublicationPrepareRequest,
    identity: Identity = Depends(require_role("operator")), session: Session = Depends(get_session),
) -> PublicationPreparationSummary:
    campaign = session.execute(select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()).scalar_one_or_none()
    if campaign is None:
        raise not_found()
    if campaign.state not in {"completed", "incomplete"}:
        raise conflict("publication requires a terminal campaign")
    if body.correction_run_id is not None:
        raise conflict("corrected publication requires the regrade integration")
    if body.supersedes_publication_id and not (body.correction_reason or "").strip():
        raise invalid_request("superseding a publication requires a correction reason")
    _prior_publication(session, campaign_id, body.supersedes_publication_id)
    snapshot, manifest = _prepared_data(session, campaign_id)
    snapshot_hash, manifest_hash = snapshot_digest(snapshot), evidence_digest(manifest)
    row = session.execute(select(PublicationPreparationRow).where(
        PublicationPreparationRow.campaign_id == campaign_id,
        PublicationPreparationRow.status == "prepared",
        PublicationPreparationRow.snapshot_digest == snapshot_hash,
        PublicationPreparationRow.evidence_manifest_digest == manifest_hash,
        PublicationPreparationRow.supersedes_publication_id == body.supersedes_publication_id,
        PublicationPreparationRow.correction_reason == body.correction_reason,
        PublicationPreparationRow.prepared_by_user_id == _current_user_id(session, identity),
    )).scalar_one_or_none()
    if row is None:
        row = PublicationPreparationRow(
            campaign_id=campaign_id, prepared_by_user_id=_current_user_id(session, identity),
            snapshot=snapshot, snapshot_digest=snapshot_hash, evidence_manifest=manifest,
            evidence_manifest_digest=manifest_hash, supersedes_publication_id=body.supersedes_publication_id,
            correction_reason=body.correction_reason, status="prepared",
        )
        session.add(row)
    session.commit()
    return _summary(row)


@router.post("/publications/preparations/{preparation_id}/review", response_model=PublicationPreparationSummary)
def review_publication(
    preparation_id: UUID, body: PublicationReviewRequest,
    identity: Identity = Depends(require_role("reviewer", "administrator")), session: Session = Depends(get_session),
) -> PublicationPreparationSummary:
    # Lock campaign first, consistently with prepare.
    campaign_id = session.execute(select(PublicationPreparationRow.campaign_id).where(
        PublicationPreparationRow.id == preparation_id,
    )).scalar_one_or_none()
    if campaign_id is None:
        raise not_found()
    campaign = session.execute(select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()).scalar_one()
    row = session.execute(select(PublicationPreparationRow).where(
        PublicationPreparationRow.id == preparation_id,
    ).with_for_update()).scalar_one()
    if row.status != "prepared":
        raise conflict("preparation has already been reviewed")
    reviewer_id = _current_user_id(session, identity)
    if body.decision == "approve" and reviewer_id in {row.prepared_by_user_id, campaign.created_by_user_id}:
        raise forbidden("preparer and campaign creator cannot approve their own publication")
    if body.decision == "approve":
        snapshot, manifest = _prepared_data(session, campaign_id)
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
            "reviewer_id": str(reviewer_id), "review_kind": body.review_kind,
            "supersedes_id": str(prior.id) if prior else None, "created_at": now.isoformat(),
        })
        publication = PublicationRow(
            campaign_id=campaign_id, snapshot=row.snapshot, snapshot_digest=row.snapshot_digest,
            evidence_manifest=row.evidence_manifest, reviewer_id=reviewer_id,
            review_kind=body.review_kind, supersedes_id=prior.id if prior else None,
            reason=row.correction_reason, status="published", created_at=now,
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
            action="publication_published", evidence={"preparation_id": str(row.id), "review_kind": body.review_kind},
        ))
    else:
        row.status = "rejected"
    row.review_kind = body.review_kind
    session.add(ReviewRow(
        reviewer_id=reviewer_id, target_type="publication_preparation", target_id=row.id,
        decision=body.decision, evidence={"notes": body.notes, "review_kind": body.review_kind},
    ))
    session.commit()
    return _summary(row)


@router.post("/publications/{publication_id}/withdraw", response_model=PublicationExport)
def withdraw_publication(
    publication_id: UUID, body: PublicationWithdrawRequest,
    identity: Identity = Depends(require_role("reviewer", "administrator")), session: Session = Depends(get_session),
) -> PublicationExport:
    if not body.reason.strip():
        raise invalid_request("withdrawal requires a reason")
    row = session.execute(select(PublicationRow).where(PublicationRow.id == publication_id).with_for_update()).scalar_one_or_none()
    if row is None:
        raise not_found()
    if row.status != "withdrawn":
        row.status, row.reason = "withdrawn", body.reason
        session.add(AuditEventRow(
            actor_user_id=_current_user_id(session, identity), target_type="publication", target_id=row.id,
            action="publication_withdrawn", evidence={"reason": body.reason},
        ))
    session.commit()
    return build_publication_export(session, publication_id)


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

