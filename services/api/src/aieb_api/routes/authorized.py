"""GET /v1/trials/{id} and GET /v1/artifacts/{ref}/download (authorized reads).

Artifact authorization is reference-scoped: an unauthorized private
reference returns 404, never a 403 that would confirm existence (API-02).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Identity, get_identity
from ..db import get_session
from ..errors import not_found
from ..models import ArtifactRefRow, ArtifactRow, AttemptRow, TrialRow

router = APIRouter(prefix="/v1", tags=["authorized"])


@router.get("/trials/{trial_id}")
def get_trial(trial_id: UUID, identity: Identity = Depends(get_identity), session: Session = Depends(get_session)) -> dict:
    row = session.get(TrialRow, trial_id)
    if row is None:
        raise not_found()
    latest_attempt = session.execute(
        select(AttemptRow).where(AttemptRow.trial_id == trial_id).order_by(AttemptRow.number.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "id": str(row.id),
        "campaign_id": str(row.campaign_id),
        "task_revision_id": str(row.task_revision_id),
        "entrant_revision_id": str(row.entrant_revision_id),
        "repetition": row.repetition,
        "latest_attempt": None
        if latest_attempt is None
        else {"number": latest_attempt.number, "phase": latest_attempt.phase, "terminal_status": latest_attempt.terminal_status},
    }


@router.get("/artifacts/{artifact_ref_id}/download")
def download_artifact(
    artifact_ref_id: UUID, identity: Identity = Depends(get_identity), session: Session = Depends(get_session)
) -> dict:
    ref = session.get(ArtifactRefRow, artifact_ref_id)
    if ref is None:
        raise not_found()
    if ref.visibility == "private" and str(ref.owner_user_id) != identity.subject and "administrator" not in identity.roles:
        raise not_found()  # API-02: deny without confirming the private reference exists
    artifact = session.get(ArtifactRow, ref.artifact_id)
    if artifact is None:
        raise not_found()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    return {
        "artifact_ref_id": str(ref.id),
        "content_digest": artifact.content_digest,
        "media_type": artifact.media_type,
        "size": artifact.size,
        "expires_at": expires_at.isoformat(),
    }
