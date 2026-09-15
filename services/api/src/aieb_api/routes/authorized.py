"""GET /v1/trials/{id} and GET /v1/artifacts/{ref}/download (authorized reads).

Artifact authorization is reference-scoped: an unauthorized private
reference returns 404, never a 403 that would confirm existence (API-02).

Trial records include campaign/task/entrant internals and attempt phase
data that spec section 3 restricts to operator/reviewer/administrator
roles ("raw run artifacts as assigned", "assigned evaluation evidence");
a submitter or bare visitor identity is not sufficient. Per-trial
ownership scoping (a submitter seeing only their own campaign's trials)
is deferred - campaigns have no owner column yet - so this is a role
gate, not the finer scoped-private-record behavior spec names as a
future refinement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Identity, get_identity, require_role, resolve_roles
from ..db import get_session
from ..errors import not_found
from ..models import ArtifactRefRow, ArtifactRow, AttemptRow, TrialRow, User

router = APIRouter(prefix="/v1", tags=["authorized"])


@router.get("/trials/{trial_id}")
def get_trial(
    trial_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> dict:
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
    if ref.visibility == "private":
        # ref.owner_user_id is the internal users.id UUID; identity.subject is the
        # raw OIDC subject claim - these are different value spaces and must not
        # be compared directly (an earlier version of this check compared them as
        # strings, which could never match, so ownership-based access silently
        # never worked). Resolve identity to its users.id row first.
        user_id = session.execute(
            select(User.id).where(User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject)
        ).scalar_one_or_none()
        if ref.owner_user_id != user_id and "administrator" not in resolve_roles(session, identity):
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
