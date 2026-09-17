"""Redacted, self-verifiable publication export bundle (ENG-018).

The export carries ONLY public data: the exact published snapshot (byte-for-
byte), the frozen cohort/task/entrant manifest identity, the signed manifest,
and per-trial PUBLIC run evidence produced by the same whitelist redaction the
public run-evidence route uses. It never includes candidate source, engineering
logs, evaluator diagnostics, costs/receipts, or artifact references.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from .errors import not_found, service_unavailable
from .models import CampaignRow, PublicationRow, TrialRow
from .routes.authorized import public_run_evidence_for, verified_published_manifest
from .routes.results import _frozen_manifest_data
from .schemas import IncludedEvidenceSelection, PublicationExport, PublicationSignature
from .snapshots import snapshot_digest as compute_snapshot_digest


def build_publication_export(session: Session, publication_id: UUID) -> PublicationExport:
    publication = session.get(PublicationRow, publication_id)
    if publication is None:
        raise not_found()
    if compute_snapshot_digest(publication.snapshot) != publication.snapshot_digest:
        raise service_unavailable("published snapshot does not match its recorded digest")
    manifest = verified_published_manifest(publication)
    campaign = session.get(CampaignRow, publication.campaign_id)
    cohort, frozen_tasks, frozen_entrants = _frozen_manifest_data(campaign)

    runs = []
    for selection in manifest.selections:
        if not isinstance(selection, IncludedEvidenceSelection):
            continue
        trial = session.get(TrialRow, selection.trial_id)
        if trial is None:
            raise service_unavailable("published run selection references a trial that no longer exists")
        runs.append(public_run_evidence_for(session, trial, publication, selection))

    signature = None
    if publication.manifest_signature and publication.signed_manifest and publication.signing_public_key:
        signature = PublicationSignature(
            publication_id=publication.id,
            signed_manifest=publication.signed_manifest,
            manifest_signature=publication.manifest_signature,
            signing_public_key=publication.signing_public_key,
            signing_key_id=publication.signing_key_id or "",
            review_kind=publication.review_kind,
        )
    notice = "this snapshot has been withdrawn; it remains addressable but is not canonical" if publication.status == "withdrawn" else None
    if publication.publication_class == "non_ranked":
        rank_notice = "this publication is explicitly non-ranking and is excluded from canonical ranks"
        notice = f"{notice}; {rank_notice}" if notice else rank_notice
    return PublicationExport(
        publication_id=publication.id,
        campaign_id=publication.campaign_id,
        status=publication.status,
        snapshot_digest=publication.snapshot_digest,
        snapshot=publication.snapshot,
        cohort=cohort,
        frozen_tasks=frozen_tasks,
        frozen_entrants=frozen_entrants,
        signature=signature,
        runs=runs,
        notice=notice,
        publication_class=publication.publication_class,
        correction_reason=publication.reason,
        withdrawal_reason=publication.withdrawal_reason,
    )
