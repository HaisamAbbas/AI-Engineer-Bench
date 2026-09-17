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
from decimal import Decimal
from pathlib import PurePosixPath
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from aieb_core.models import EntrantRevision, TaskRevision
from aieb_runner.artifacts import ArtifactAccessDenied, ArtifactIntegrityError, ArtifactNotFound, ArtifactReference, BlobRef

from ..auth import Identity, get_identity, require_role, resolve_roles
from ..db import get_session
from ..errors import not_found, service_unavailable
from ..models import (
    ArtifactRefRow,
    ArtifactRow,
    AttemptEventRow,
    AttemptRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    PublicationRow,
    TaskRevisionRow,
    TrialRow,
    User,
    WorkerArtifactBlobRow,
    WorkerArtifactReferenceRow,
    UsageReceiptRow,
    UsageRequestRow,
)
from ..evidence_integrity import attempt_trace_digest, evidence_digest, evaluation_digest, task_revision_digest
from ..revisions import validate_stored_manifest
from ..schemas import (
    PrivateRunEvidence, PublishedEvidenceManifest, PublicRequirementCheck, PublicRunEvidence,
    RunArtifact, RunAttemptSummary, RunFileDiff, RunTraceEvent, RunUsage,
)
from ..worker.runner_bridge import StoredCandidateUnavailableError, _deserialize_stored_candidate
from ..worker.artifact_store import PostgresArtifactStore
from .. import db

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
    attempts = session.execute(
        select(AttemptRow).where(AttemptRow.trial_id == trial_id).order_by(AttemptRow.number.desc())
    ).scalars().all()
    latest_attempt = attempts[0] if attempts else None
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
    artifacts: list[RunArtifact] = []
    if candidate is not None:
        stored = _verified_candidate(candidate)
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
        artifacts = _private_artifacts(session, candidate, latest_attempt, candidate.stored_candidate)
    total_evaluations = session.execute(
        select(func.count(EvaluationRow.id)).select_from(EvaluationRow)
        .join(CandidateRow, EvaluationRow.candidate_id == CandidateRow.id)
        .join(AttemptRow, CandidateRow.attempt_id == AttemptRow.id)
        .where(AttemptRow.trial_id == trial_id)
    ).scalar_one()
    invalid_attempt = latest_attempt is not None and latest_attempt.terminal_status in {"infrastructure_invalid", "cancelled"}
    if candidate is not None and candidate.validation_status != "valid":
        invalid_attempt = True
    if invalid_attempt:
        evaluation_state = "invalid"
    elif evaluation is not None:
        evaluation_state = "current"
    elif total_evaluations:
        evaluation_state = "superseded"
    else:
        evaluation_state = "unscored"
    if evaluation_state in {"invalid", "superseded"}:
        checks, diagnostics = None, None
    trace_state, trace = _attempt_trace_projection(session, latest_attempt, evaluation.result if evaluation else None)
    usage = _usage_projection(evaluation.result if evaluation else None, public=False)
    if usage is None and latest_attempt is not None:
        usage = _receipt_usage_projection(session, latest_attempt.id, public=False)
    task_row = session.get(TaskRevisionRow, row.task_revision_id)
    entrant_row = session.get(EntrantRevisionRow, row.entrant_revision_id)
    return PrivateRunEvidence(
        id=row.id,
        campaign_id=row.campaign_id,
        task_revision_id=row.task_revision_id,
        entrant_revision_id=row.entrant_revision_id,
        repetition=row.repetition,
        latest_attempt=_attempt_summary(latest_attempt),
        verdict=evaluation.verdict if evaluation is not None and evaluation_state == "current" else None,
        checks=checks,
        diagnostics=diagnostics,
        engineering_stdout=engineering_stdout,
        engineering_stderr=engineering_stderr,
        engineering_logs_truncated=engineering_logs_truncated,
        diffs=diffs,
        candidate_id=candidate.id if candidate is not None else None,
        evaluation_id=evaluation.id if evaluation is not None else None,
        evaluation_state=evaluation_state,
        superseded_evaluation_count=max(0, total_evaluations - (1 if evaluation is not None else 0)),
        trace_state=trace_state,
        trace=trace,
        usage=usage,
        configuration=_configuration_projection(task_row, entrant_row),
        artifacts=artifacts,
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
    if publication is None or publication.evidence_manifest is None or publication.evidence_manifest_digest is None:
        raise not_found()
    if evidence_digest(publication.evidence_manifest) != publication.evidence_manifest_digest:
        raise service_unavailable("published run selection failed its recorded digest check")
    try:
        published_manifest = PublishedEvidenceManifest.model_validate(publication.evidence_manifest)
    except Exception as exc:
        raise service_unavailable("published run selection is malformed") from exc
    if published_manifest.campaign_id != trial.campaign_id:
        raise service_unavailable("published run selection belongs to a different campaign")
    selections = [selection for selection in published_manifest.selections if selection.trial_id == trial_id]
    if len(selections) != 1 or not selections[0].included:
        raise not_found()
    selection = selections[0]
    attempt = session.get(AttemptRow, selection.attempt_id) if selection.attempt_id else None
    if selection.attempt_id is not None and attempt is None:
        raise service_unavailable("published attempt selection no longer exists")
    if attempt is not None and attempt.trial_id != trial_id:
        raise service_unavailable("published attempt does not belong to the selected trial")
    candidate = session.get(CandidateRow, selection.candidate_id) if selection.candidate_id else None
    if selection.candidate_id is not None and candidate is None:
        raise service_unavailable("published candidate selection no longer exists")
    if candidate is not None:
        if attempt is None or candidate.attempt_id != attempt.id:
            raise service_unavailable("published candidate does not belong to the selected attempt")
        _verified_candidate(candidate)
        if selection.candidate_digest != candidate.stored_candidate_digest:
            raise service_unavailable("published candidate differs from the selected candidate digest")
    elif selection.evaluation_id is not None:
        raise service_unavailable("published evaluation has no selected candidate")
    evaluation = session.get(EvaluationRow, selection.evaluation_id) if selection.evaluation_id else None
    if selection.evaluation_id is not None and evaluation is None:
        raise service_unavailable("published evaluation selection no longer exists")
    if evaluation is not None:
        if candidate is None or evaluation.candidate_id != candidate.id:
            raise service_unavailable("published evaluation does not belong to the selected candidate")
        actual_evaluation_digest = evaluation_digest(
            candidate_id=evaluation.candidate_id, evaluator_id=evaluation.evaluator_id, fixture_id=evaluation.fixture_id,
            schedule_digest=evaluation.schedule_digest, verdict=evaluation.verdict, result=evaluation.result,
        )
        if actual_evaluation_digest != evaluation.evaluation_digest or selection.evaluation_digest != evaluation.evaluation_digest:
            raise service_unavailable("published evaluation differs from its selected evidence digest")
    elif selection.evaluation_digest is not None:
        raise service_unavailable("published evaluation digest has no selected evaluation")
    task_row = session.get(TaskRevisionRow, trial.task_revision_id)
    entrant_row = session.get(EntrantRevisionRow, trial.entrant_revision_id)
    if task_row is None or entrant_row is None:
        raise service_unavailable("published run is missing its frozen task or entrant revision")
    if task_revision_digest(task_row.manifest, task_row.ticket_text) != task_row.revision_digest:
        raise service_unavailable("published task ticket or manifest failed its identity digest check")
    task = validate_stored_manifest(TaskRevision, task_row.manifest, kind="task", row_id=task_row.id)
    entrant = validate_stored_manifest(EntrantRevision, entrant_row.manifest, kind="entrant", row_id=entrant_row.id)
    raw_checks = evaluation.result.get("checks") if evaluation is not None and isinstance(evaluation.result, dict) else None
    checks = [
        PublicRequirementCheck(
            requirement_id=requirement.id,
            passed=raw_checks.get(requirement.id) if isinstance(raw_checks, dict) and type(raw_checks.get(requirement.id)) is bool else None,
        )
        for requirement in task.requirements
    ]
    verdict = evaluation.verdict if evaluation is not None and evaluation.verdict in {"pass", "fail", "contract_violation", "indeterminate"} else None
    trace_state, trace = _attempt_trace_projection(
        session, attempt, evaluation.result if evaluation else None,
        expected_digest=selection.trace_digest, include_result_trace=False,
    )
    public_usage = _usage_projection(evaluation.result if evaluation else None, public=True)
    if public_usage is None and attempt is not None:
        public_usage = _receipt_usage_projection(session, attempt.id, public=True)
    configuration = _configuration_projection(task_row, entrant_row)
    return PublicRunEvidence(
        trial_id=trial.id,
        publication_id=publication.id,
        task_id=task.id,
        task_version=task.version,
        entrant_id=entrant.id,
        entrant_version=entrant_row.version,
        repetition=trial.repetition,
        attempt=_attempt_summary(attempt),
        verdict=verdict,
        checks=checks,
        evaluation_id=evaluation.id if evaluation is not None else None,
        evaluation_state=("invalid" if attempt is not None and attempt.terminal_status in {"infrastructure_invalid", "cancelled"} else "current") if evaluation is not None else "unscored",
        trace_state=trace_state,
        trace=trace,
        usage=public_usage,
        configuration=configuration,
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


def _verified_candidate(candidate: CandidateRow):
    if evidence_digest(candidate.stored_candidate) != candidate.stored_candidate_digest:
        raise service_unavailable("stored candidate display evidence failed its content digest check")
    try:
        stored = _deserialize_stored_candidate(candidate.stored_candidate)
    except StoredCandidateUnavailableError as exc:
        raise service_unavailable("stored candidate evidence is malformed") from exc
    if stored.manifest.digest() != candidate.manifest_digest or stored.manifest.full_tree_hash != candidate.tree_digest:
        raise service_unavailable("stored candidate evidence failed its recorded manifest digest check")
    return stored


def _trace_projection(result: object) -> tuple[str, list[RunTraceEvent]]:
    if not isinstance(result, dict) or not isinstance(result.get("trace"), list):
        return "unavailable", []
    events: list[RunTraceEvent] = []
    remaining = 64 * 1024
    for index, raw in enumerate(result["trace"][:500]):
        if not isinstance(raw, dict):
            continue
        event_type = raw.get("event_type")
        sequence = raw.get("sequence", index)
        payload = raw.get("payload", {})
        if not isinstance(event_type, str) or not isinstance(sequence, int) or isinstance(sequence, bool) or not isinstance(payload, dict):
            continue
        safe_payload = {
            key[:128]: value for key, value in payload.items()
            if isinstance(key, str) and (value is None or isinstance(value, (str, int, bool)))
        }
        encoded_size = sum(len(key.encode("utf-8")) + len(str(value).encode("utf-8", errors="replace")) for key, value in safe_payload.items())
        if encoded_size > remaining:
            break
        remaining -= encoded_size
        events.append(RunTraceEvent(sequence=sequence, event_type=event_type[:128], payload=safe_payload))
    if not events:
        return "unavailable", []
    complete = result.get("trace_complete") is True and len(result["trace"]) <= 500
    return ("complete" if complete else "partial"), events


def _attempt_trace_projection(
    session: Session, attempt: AttemptRow | None, result: object, *, expected_digest: str | None = None,
    include_result_trace: bool = True,
) -> tuple[str, list[RunTraceEvent]]:
    if attempt is None:
        return _trace_projection(result) if include_result_trace else ("unavailable", [])
    rows = session.execute(
        select(AttemptEventRow).where(AttemptEventRow.attempt_id == attempt.id).order_by(AttemptEventRow.sequence)
    ).scalars().all()
    event_data = [{"sequence": row.sequence, "event_type": row.event_type, "payload": row.payload} for row in rows]
    if expected_digest is not None and attempt_trace_digest(event_data) != expected_digest:
        raise service_unavailable("published observable trace differs from its selected trace digest")
    events = [
        RunTraceEvent(sequence=row.sequence, event_type=row.event_type, payload=row.payload, created_at=row.created_at.isoformat())
        for row in rows
    ]
    result_state, result_events = _trace_projection(result) if include_result_trace else ("unavailable", [])
    offset = len(events)
    events.extend(
        RunTraceEvent(sequence=offset + index, event_type=item.event_type, payload=item.payload, created_at=item.created_at)
        for index, item in enumerate(result_events)
    )
    if result_state == "complete":
        return "complete", events
    if events:
        return "partial", events
    return "unavailable", []


def _usage_projection(result: object, *, public: bool) -> RunUsage | None:
    if not isinstance(result, dict) or not isinstance(result.get("usage"), dict):
        return None
    raw = result["usage"]
    input_tokens = raw.get("input_tokens") if type(raw.get("input_tokens")) is int and raw["input_tokens"] >= 0 else None
    output_tokens = raw.get("output_tokens") if type(raw.get("output_tokens")) is int and raw["output_tokens"] >= 0 else None
    cost = raw.get("cost_usd") if isinstance(raw.get("cost_usd"), str) else None
    if input_tokens is None and output_tokens is None and (public or cost is None):
        return None
    return RunUsage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=None if public else cost)


def _receipt_usage_projection(session: Session, attempt_id: UUID, *, public: bool) -> RunUsage | None:
    rows = session.execute(
        select(
            UsageReceiptRow.input_tokens, UsageReceiptRow.output_tokens,
            UsageReceiptRow.reported_cost_usd, UsageReceiptRow.estimated_cost_usd,
        ).join(UsageRequestRow, UsageRequestRow.id == UsageReceiptRow.usage_request_id)
        .where(UsageRequestRow.attempt_id == attempt_id)
    ).all()
    if not rows:
        return None
    input_values = [row.input_tokens for row in rows if row.input_tokens is not None]
    output_values = [row.output_tokens for row in rows if row.output_tokens is not None]
    costs = [row.reported_cost_usd if row.reported_cost_usd is not None else row.estimated_cost_usd for row in rows]
    costs = [cost for cost in costs if cost is not None]
    total_cost = sum(costs, Decimal("0")) if costs else None
    return RunUsage(
        input_tokens=sum(input_values) if input_values else None,
        output_tokens=sum(output_values) if output_values else None,
        cost_usd=None if public or total_cost is None else format(total_cost, "f"),
    )


def _configuration_projection(task_row: TaskRevisionRow | None, entrant_row: EntrantRevisionRow | None) -> dict:
    if task_row is None or entrant_row is None:
        return {}
    if task_revision_digest(task_row.manifest, task_row.ticket_text) != task_row.revision_digest:
        raise service_unavailable("run task ticket or manifest failed its identity digest check")
    task = validate_stored_manifest(TaskRevision, task_row.manifest, kind="task", row_id=task_row.id)
    entrant = validate_stored_manifest(EntrantRevision, entrant_row.manifest, kind="entrant", row_id=entrant_row.id)
    return {
        "task_revision_id": str(task_row.id),
        "task_version": task.version,
        "entrant_revision_id": str(entrant_row.id),
        "entrant_version": entrant_row.version,
        "track": entrant.track,
        "agent_implementation": entrant.agent_implementation,
        "agent_version": entrant.agent_version,
        "requested_model": entrant.engineer_model.requested_model,
        "reported_model": entrant.engineer_model.reported_model,
        "capabilities": list(entrant.capabilities),
        "settings_digest": entrant.engineer_model.settings_digest,
    }


def _private_artifacts(session: Session, candidate: CandidateRow, attempt: AttemptRow | None, data: dict) -> list[RunArtifact]:
    if attempt is None:
        return []
    output: list[RunArtifact] = []
    for file_ref in data.get("file_references", []):
        if not isinstance(file_ref, dict) or not isinstance(file_ref.get("reference"), dict):
            continue
        raw = file_ref["reference"]
        try:
            ref_id = UUID(raw["id"])
            digest = raw["blob"]["sha256"]
            size = raw["blob"]["byte_length"]
        except (KeyError, ValueError, TypeError):
            continue
        ref = session.get(WorkerArtifactReferenceRow, ref_id)
        blob = session.get(WorkerArtifactBlobRow, digest) if isinstance(digest, str) else None
        if ref is None or blob is None or ref.candidate_id != candidate.id or ref.access_scope != str(attempt.id):
            continue
        if ref.blob_sha256 != digest or blob.byte_length != size:
            raise service_unavailable("candidate artifact reference failed its recorded metadata check")
        output.append(RunArtifact(artifact_ref_id=ref.id, path=str(file_ref.get("path", "artifact")), content_digest=digest, size=size))
    return output


@router.get("/trials/{trial_id}/artifacts/{artifact_ref_id}/download")
def download_candidate_artifact(
    trial_id: UUID,
    artifact_ref_id: UUID,
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> Response:
    del identity
    trial = session.get(TrialRow, trial_id)
    if trial is None:
        raise not_found()
    attempt = _latest_attempt(session, trial_id)
    if attempt is None:
        raise not_found()
    candidate = session.execute(
        select(CandidateRow).where(CandidateRow.attempt_id == attempt.id).order_by(CandidateRow.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if candidate is None:
        raise not_found()
    _verified_candidate(candidate)
    raw_refs = candidate.stored_candidate.get("file_references", [])
    raw_item = next((item for item in raw_refs if isinstance(item, dict) and isinstance(item.get("reference"), dict)
                     and item["reference"].get("id") == str(artifact_ref_id)), None)
    ref = session.get(WorkerArtifactReferenceRow, artifact_ref_id)
    if raw_item is None or ref is None or ref.candidate_id != candidate.id or ref.access_scope != str(attempt.id):
        raise not_found()
    raw_reference = raw_item["reference"]
    try:
        artifact_ref = ArtifactReference(
            id=artifact_ref_id,
            blob=BlobRef(sha256=raw_reference["blob"]["sha256"], byte_length=raw_reference["blob"]["byte_length"]),
            access_scope=raw_reference["access_scope"], visibility=raw_reference["visibility"],
        )
        if ref.blob_sha256 != artifact_ref.blob.sha256:
            raise service_unavailable("candidate artifact reference metadata changed")
        store = PostgresArtifactStore(db.session_factory())
        content = store.read(artifact_ref, principal_scope=str(attempt.id))
    except ArtifactNotFound as exc:
        raise not_found() from exc
    except (ArtifactAccessDenied, ArtifactIntegrityError) as exc:
        raise service_unavailable("candidate artifact failed its access or integrity check") from exc
    name = re.sub(r"[^A-Za-z0-9._-]", "_", PurePosixPath(str(raw_item.get("path", "artifact"))).name) or "artifact"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-SHA256": artifact_ref.blob.sha256},
    )


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
