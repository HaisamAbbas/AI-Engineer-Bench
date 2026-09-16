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
from aieb_core.models import EntrantRevision, TaskRevision

from ..auth import Identity, get_identity, require_role, resolve_roles
from ..db import get_session
from ..errors import not_found, service_unavailable
from ..models import (
    ArtifactRefRow,
    ArtifactRow,
    AttemptRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    PublicationRow,
    TaskRevisionRow,
    TrialRow,
    User,
)
from ..revisions import validate_stored_manifest
from ..schemas import PrivateRunEvidence, PublicRequirementCheck, PublicRunEvidence, RunAttemptSummary, RunFileDiff
from ..worker.runner_bridge import StoredCandidateUnavailableError, _deserialize_stored_candidate

router = APIRouter(prefix="/v1", tags=["authorized"])


@router.get("/trials/{trial_id}", response_model=PrivateRunEvidence)
def get_trial(
    trial_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> PrivateRunEvidence:
    row = session.get(TrialRow, trial_id)
    if row is None:
        raise not_found()
    latest_attempt = session.execute(
        select(AttemptRow).where(AttemptRow.trial_id == trial_id).order_by(AttemptRow.number.desc()).limit(1)
    ).scalar_one_or_none()
    candidate = None
    evaluation = None
    if latest_attempt is not None:
        candidate = session.execute(
            select(CandidateRow).where(CandidateRow.attempt_id == latest_attempt.id).order_by(CandidateRow.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if candidate is not None:
            evaluation = session.execute(
                select(EvaluationRow).where(EvaluationRow.candidate_id == candidate.id)
                .order_by(EvaluationRow.created_at.desc()).limit(1)
            ).scalar_one_or_none()
    checks, diagnostics = _private_result_projection(evaluation.result if evaluation is not None else None)
    diffs: list[RunFileDiff] = []
    engineering_stdout = None
    engineering_stderr = None
    engineering_logs_truncated = False
    if candidate is not None:
        try:
            stored = _deserialize_stored_candidate(candidate.stored_candidate)
        except StoredCandidateUnavailableError as exc:
            raise service_unavailable("stored candidate evidence is malformed") from exc
        if stored.manifest.digest() != candidate.manifest_digest or stored.manifest.full_tree_hash != candidate.tree_digest:
            raise service_unavailable("stored candidate evidence failed its recorded digest check")
        diffs = [
            RunFileDiff(
                path=entry.path,
                operation=entry.operation,
                unified_diff=entry.unified_diff,
                binary=entry.binary,
                truncated=entry.truncated,
                baseline_available=entry.baseline_available,
            )
            for entry in stored.diffs
        ]
        engineering_stdout = stored.engineering_stdout
        engineering_stderr = stored.engineering_stderr
        engineering_logs_truncated = stored.engineering_logs_truncated
    return PrivateRunEvidence(
        id=row.id,
        campaign_id=row.campaign_id,
        task_revision_id=row.task_revision_id,
        entrant_revision_id=row.entrant_revision_id,
        repetition=row.repetition,
        latest_attempt=_attempt_summary(latest_attempt),
        verdict=evaluation.verdict if evaluation is not None else None,
        checks=checks,
        diagnostics=diagnostics,
        engineering_stdout=engineering_stdout,
        engineering_stderr=engineering_stderr,
        engineering_logs_truncated=engineering_logs_truncated,
        diffs=diffs,
    )


@router.get("/public/trials/{trial_id}", response_model=PublicRunEvidence)
def get_public_trial(trial_id: UUID, session: Session = Depends(get_session)) -> PublicRunEvidence:
    """Public redacted evidence exists only after the trial's campaign has
    been published. The projection is a whitelist and never includes source
    text, raw evaluator diagnostics, fixture data, billing, or artifact IDs."""
    trial = session.get(TrialRow, trial_id)
    if trial is None:
        raise not_found()
    publication = session.execute(
        select(PublicationRow)
        .where(PublicationRow.campaign_id == trial.campaign_id, PublicationRow.status.in_(("published", "superseded")))
        .order_by(PublicationRow.created_at.desc(), PublicationRow.id.desc()).limit(1)
    ).scalar_one_or_none()
    if publication is None:
        raise not_found()
    task_row = session.get(TaskRevisionRow, trial.task_revision_id)
    entrant_row = session.get(EntrantRevisionRow, trial.entrant_revision_id)
    if task_row is None or entrant_row is None:
        raise service_unavailable("published run is missing its frozen task or entrant revision")
    task = validate_stored_manifest(TaskRevision, task_row.manifest, kind="task", row_id=task_row.id)
    entrant = validate_stored_manifest(EntrantRevision, entrant_row.manifest, kind="entrant", row_id=entrant_row.id)
    latest_attempt = _latest_attempt(session, trial_id)
    evaluation = None
    if latest_attempt is not None:
        candidate = session.execute(
            select(CandidateRow).where(CandidateRow.attempt_id == latest_attempt.id).order_by(CandidateRow.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if candidate is not None:
            evaluation = session.execute(
                select(EvaluationRow).where(EvaluationRow.candidate_id == candidate.id)
                .order_by(EvaluationRow.created_at.desc()).limit(1)
            ).scalar_one_or_none()
    raw_checks = evaluation.result.get("checks") if evaluation is not None and isinstance(evaluation.result, dict) else None
    checks = [
        PublicRequirementCheck(
            requirement_id=requirement.id,
            passed=raw_checks.get(requirement.id) if isinstance(raw_checks, dict) and type(raw_checks.get(requirement.id)) is bool else None,
        )
        for requirement in task.requirements
    ]
    verdict = evaluation.verdict if evaluation is not None and evaluation.verdict in {"pass", "fail", "contract_violation", "indeterminate"} else None
    return PublicRunEvidence(
        trial_id=trial.id,
        publication_id=publication.id,
        task_id=task.id,
        task_version=task.version,
        entrant_id=entrant.id,
        entrant_version=entrant_row.version,
        repetition=trial.repetition,
        attempt=_attempt_summary(latest_attempt),
        verdict=verdict,
        checks=checks,
    )


def _latest_attempt(session: Session, trial_id: UUID) -> AttemptRow | None:
    return session.execute(
        select(AttemptRow).where(AttemptRow.trial_id == trial_id)
        .order_by(AttemptRow.number.desc()).limit(1)
    ).scalar_one_or_none()


def _attempt_summary(attempt: AttemptRow | None) -> RunAttemptSummary | None:
    if attempt is None:
        return None
    return RunAttemptSummary(number=attempt.number, phase=attempt.phase, terminal_status=attempt.terminal_status)


def _private_result_projection(result: object) -> tuple[dict[str, bool] | None, dict[str, str] | None]:
    if not isinstance(result, dict):
        return None, None
    raw_checks = result.get("checks")
    checks = {key: value for key, value in raw_checks.items() if isinstance(key, str) and type(value) is bool} if isinstance(raw_checks, dict) else None
    raw_diagnostics = result.get("diagnostics")
    diagnostics: dict[str, str] | None = None
    if isinstance(raw_diagnostics, dict):
        diagnostics = {}
        remaining = 64 * 1024
        for key, value in raw_diagnostics.items():
            if not isinstance(key, str) or not isinstance(value, str) or remaining <= 0:
                continue
            text = value[:remaining]
            diagnostics[key[:256]] = text
            remaining -= len(text.encode("utf-8", errors="replace"))
    return checks, diagnostics


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
