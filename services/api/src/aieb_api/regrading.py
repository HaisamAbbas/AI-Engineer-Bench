"""Pinned, staging-only re-evaluation of retained candidates."""
from __future__ import annotations

import copy
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .evidence_integrity import evidence_digest
from .models import (
    AttemptEventRow, AttemptRow, CampaignRow, CandidateRow, CorrectionRunRow,
    EvaluatorRevisionRow, FixtureRevisionRow, TaskRevisionRow,
    TrialRow, WorkItemRow, WorkerArtifactReferenceRow,
)


def installed_scoring_bundle(session: Session, campaign_id: UUID) -> dict:
    """Pin installed evaluator packages (including fixtures) and shared harness."""
    import hashlib
    from .worker.runner_bridge import ROOT, TASK_RUNTIMES

    tasks = session.execute(select(TaskRevisionRow).join(TrialRow).where(
        TrialRow.campaign_id == campaign_id,
    )).scalars().unique().all()
    closures = {}
    for task in tasks:
        runtime = TASK_RUNTIMES.get(task.slug)
        if runtime is None:
            raise ValueError("campaign task has no installed trusted evaluator")
        package = ROOT.joinpath(*runtime[1].split(".")[:-1])
        files = sorted(package.glob("*.py")) + [ROOT / "tests" / "maintainer" / "common.py"]
        closures[task.slug] = {
            str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.is_file()
        }
    if not closures:
        raise ValueError("campaign has no retained trial matrix")
    return {"schema_version": "aieb.installed-scoring/v1", "tasks": closures}


def correction_for_attempt(session: Session, attempt_id: UUID) -> CorrectionRunRow | None:
    event = session.execute(select(AttemptEventRow).where(
        AttemptEventRow.attempt_id == attempt_id, AttemptEventRow.event_type == "regrade.requested",
    )).scalar_one_or_none()
    return session.get(CorrectionRunRow, UUID(event.payload["correction_run_id"])) if event else None


def enqueue_regrade(session: Session, campaign: CampaignRow, *, scoring_digest: str, reason: str, user_id: UUID) -> CorrectionRunRow:
    from .aggregation import selected_campaign_evaluations
    from .worker.repository import append_attempt_event
    from .worker.runner_bridge import _deserialize_stored_candidate

    bundle_digest = evidence_digest(installed_scoring_bundle(session, campaign.id))
    if scoring_digest != bundle_digest:
        raise ValueError("scoring digest does not identify the installed trusted evaluator bundle")
    if session.execute(select(CorrectionRunRow.id).where(
        CorrectionRunRow.campaign_id == campaign.id, CorrectionRunRow.status == "running",
    )).first():
        raise ValueError("a correction run is already active")
    originals = selected_campaign_evaluations(session, campaign.id)
    if not originals:
        raise ValueError("no original scored candidates are retained")
    evaluator = session.execute(select(EvaluatorRevisionRow).where(
        EvaluatorRevisionRow.code_digest == bundle_digest,
    )).scalars().first()
    if evaluator is None:
        evaluator = EvaluatorRevisionRow(code_digest=bundle_digest, contract_version="installed-scoring/v1")
        session.add(evaluator)
    fixture_digest = evidence_digest({"fixture_bundle": bundle_digest})
    fixture = session.execute(select(FixtureRevisionRow).where(FixtureRevisionRow.digest == fixture_digest)).scalar_one_or_none()
    if fixture is None:
        fixture = FixtureRevisionRow(digest=fixture_digest, visibility="restricted", family_id="installed-staging-bundle")
        session.add(fixture)
    session.flush()
    run = CorrectionRunRow(campaign_id=campaign.id, requested_by_user_id=user_id,
        corrected_evaluator_id=evaluator.id, corrected_fixture_id=fixture.id,
        scoring_correction_digest=bundle_digest, reason=reason, status="running")
    session.add(run)
    session.flush()
    for trial_id, evaluation in originals.items():
        source = session.get(CandidateRow, evaluation.candidate_id)
        if source.validation_status != "valid" or evidence_digest(source.stored_candidate) != source.stored_candidate_digest:
            raise ValueError("retained candidate evidence is invalid")
        _deserialize_stored_candidate(source.stored_candidate)
        number = session.execute(select(func.max(AttemptRow.number)).where(AttemptRow.trial_id == trial_id)).scalar_one() + 1
        attempt = AttemptRow(trial_id=trial_id, number=number, phase="queued")
        session.add(attempt)
        session.flush()
        stored = copy.deepcopy(source.stored_candidate)
        references = []
        for entry in stored.get("file_references", []):
            raw = entry["reference"]
            old = session.get(WorkerArtifactReferenceRow, UUID(raw["id"]))
            if old is None or old.candidate_id != source.id or old.blob_sha256 != raw["blob"]["sha256"]:
                raise ValueError("retained candidate artifact is unavailable")
            reference = WorkerArtifactReferenceRow(id=uuid4(), blob_sha256=old.blob_sha256,
                access_scope=str(attempt.id), visibility=old.visibility)
            raw["id"], raw["access_scope"] = str(reference.id), str(attempt.id)
            references.append(reference)
        candidate = CandidateRow(attempt_id=attempt.id, tree_digest=source.tree_digest,
            manifest_digest=source.manifest_digest, validation_status="valid", stored_candidate=stored)
        session.add(candidate)
        session.flush()
        for reference in references:
            reference.candidate_id = candidate.id
            session.add(reference)
        append_attempt_event(session, attempt_id=attempt.id, event_type="regrade.requested",
            payload={"correction_run_id": str(run.id), "source_candidate_id": str(source.id)})
        session.add(WorkItemRow(attempt_id=attempt.id, type="regrade", state="ready"))
    session.flush()
    return run


def complete_correction_runs(session: Session) -> None:
    """Terminalize runs only after every retained-candidate work item drains."""
    runs = session.execute(select(CorrectionRunRow).where(
        CorrectionRunRow.status == "running",
    ).with_for_update()).scalars().all()
    for run in runs:
        attempts = session.execute(select(AttemptRow).join(AttemptEventRow).where(
            AttemptEventRow.event_type == "regrade.requested",
            AttemptEventRow.payload["correction_run_id"].astext == str(run.id),
        )).scalars().all()
        if not attempts or any(a.phase != "terminal" for a in attempts):
            continue
        run.status = "completed" if all(a.terminal_status in {"pass", "fail", "contract_violation", "indeterminate"} for a in attempts) else "failed"
    session.commit()
