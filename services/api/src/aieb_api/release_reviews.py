"""Strict persistence helpers for independent campaign/release reviews.

The caller supplies the human decision and reason, while the server derives
the target's subject identity and exact evidence digest.  This keeps an API
client from choosing a convenient reviewer subject or binding a review to a
different release artifact.
"""
from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .evidence_integrity import evidence_digest
from .errors import conflict, invalid_request
from .models import CampaignRow, IndependentReviewRow, PublicationPreparationRow, RoleBinding

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _reviewer_role_binding(session: Session, reviewer_id: UUID) -> RoleBinding:
    """Resolve and retain the global grant used for this review."""
    binding = session.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == reviewer_id,
            RoleBinding.role.in_(("reviewer", "administrator")),
            RoleBinding.scope == "global",
        ).order_by(RoleBinding.created_at.asc(), RoleBinding.id.asc()).limit(1)
    ).scalar_one_or_none()
    if binding is None:
        raise conflict("reviewer has no global reviewer or administrator role binding")
    return binding


def campaign_review_digest(campaign: CampaignRow) -> str:
    if not campaign.manifest_digest or not campaign.cohort_digest or not campaign.matrix_digest:
        raise conflict("campaign is not fully frozen/planned; review evidence cannot be bound")
    return evidence_digest({
        "schema_version": "aieb.release-review/campaign-v1",
        "campaign_id": str(campaign.id),
        "manifest_digest": campaign.manifest_digest,
        "cohort_digest": campaign.cohort_digest,
        "matrix_digest": campaign.matrix_digest,
    })


def publication_review_digest(preparation: PublicationPreparationRow) -> str:
    return evidence_digest({
        "schema_version": "aieb.release-review/publication-v1",
        "preparation_id": str(preparation.id),
        "snapshot_digest": preparation.snapshot_digest,
        "evidence_manifest_digest": preparation.evidence_manifest_digest,
    })


def record_release_review(
    session: Session,
    *,
    target_type: str,
    target_id: UUID,
    reviewer_id: UUID,
    decision: str,
    scope: str,
    evidence_digest_value: str,
    independence_declaration: bool,
    reason: str,
) -> IndependentReviewRow:
    """Validate and persist one immutable independent release review.

    Both decisions require an explicit independence declaration.  A rejection
    is still a genuine review and must not be represented as an approval with
    missing provenance.  The target is locked and the expected subject/digest
    are derived from it before insertion.
    """
    if target_type not in {"campaign_approval", "publication_preparation"}:
        raise invalid_request("unsupported release review target")
    if decision not in {"approve", "reject"}:
        raise invalid_request("review decision must be approve or reject")
    if not independence_declaration:
        raise invalid_request("an independent release review requires an explicit independence declaration")
    if not scope or not scope.strip():
        raise invalid_request("review scope must not be blank")
    if not reason or not reason.strip():
        raise invalid_request("review reason must not be blank")
    if not _HEX64.fullmatch(evidence_digest_value or ""):
        raise invalid_request("review evidence_digest must be a lowercase SHA-256 digest")

    campaign: CampaignRow | None = None
    if target_type == "campaign_approval":
        target = session.execute(
            select(CampaignRow).where(CampaignRow.id == target_id).with_for_update()
        ).scalar_one_or_none()
        if target is None:
            raise conflict("campaign review target does not exist")
        campaign = target
        subject_id = target.created_by_user_id
        if subject_id is None:
            raise conflict("campaign has no recorded creator; independent review cannot be established")
        expected_digest = campaign_review_digest(target)
    else:
        # Match the API's campaign -> preparation lock order.  The campaign
        # creator is part of publication-review independence, so it must be
        # locked and checked even when the preparer is a different user.
        campaign_id = session.execute(
            select(PublicationPreparationRow.campaign_id).where(
                PublicationPreparationRow.id == target_id
            )
        ).scalar_one_or_none()
        if campaign_id is None:
            raise conflict("publication preparation review target does not exist")
        campaign = session.execute(
            select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
        ).scalar_one_or_none()
        if campaign is None:
            raise conflict("publication review campaign does not exist")
        target = session.execute(
            select(PublicationPreparationRow).where(PublicationPreparationRow.id == target_id).with_for_update()
        ).scalar_one_or_none()
        if target is None:
            raise conflict("publication preparation review target does not exist")
        if target.campaign_id != campaign.id:
            raise conflict("publication preparation campaign identity changed during review")
        subject_id = target.prepared_by_user_id
        expected_digest = publication_review_digest(target)

    if reviewer_id == subject_id or (campaign is not None and reviewer_id == campaign.created_by_user_id):
        raise conflict("the reviewer cannot be the release preparer or campaign creator")
    if evidence_digest_value != expected_digest:
        raise invalid_request("review evidence_digest does not match the immutable target evidence")
    binding = _reviewer_role_binding(session, reviewer_id)
    existing = session.execute(
        select(IndependentReviewRow).where(
            IndependentReviewRow.target_type == target_type,
            IndependentReviewRow.target_id == target_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict("this release target has already been reviewed")

    row = IndependentReviewRow(
        target_type=target_type,
        target_id=target_id,
        reviewer_user_id=reviewer_id,
        reviewer_role_binding_id=binding.id,
        subject_user_id=subject_id,
        scope=scope.strip(),
        decision=decision,
        evidence_digest=evidence_digest_value,
        independence_declaration=True,
        reason=reason.strip(),
    )
    session.add(row)
    session.flush()
    return row
