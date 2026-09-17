"""Build a complete, explicit selection manifest for a publication."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .evidence_integrity import attempt_trace_digest, evidence_digest, evaluation_digest
from .models import AttemptEventRow, AttemptRow, CandidateRow, EvaluationRow, TrialRow


def build_evidence_manifest(session: Session, campaign_id: UUID, selected_evaluations: dict[UUID, UUID]) -> dict:
    """Pin every campaign trial to one evaluation or explicitly exclude it.

    `selected_evaluations` maps included trial IDs to the exact evaluation
    IDs approved for publication. Every other campaign trial is represented
    as excluded; unknown trials, cross-trial evaluations, and bad persisted
    digests are rejected before a caller can persist the publication.
    """
    trials = session.execute(select(TrialRow).where(TrialRow.campaign_id == campaign_id)).scalars().all()
    by_id = {trial.id: trial for trial in trials}
    unknown = set(selected_evaluations) - set(by_id)
    if unknown:
        raise ValueError("publication selection contains a trial outside this campaign")

    selections = []
    for trial in trials:
        evaluation_id = selected_evaluations.get(trial.id)
        if evaluation_id is None:
            selections.append({"trial_id": str(trial.id), "included": False})
            continue
        evaluation = session.get(EvaluationRow, evaluation_id)
        candidate = session.get(CandidateRow, evaluation.candidate_id) if evaluation is not None else None
        attempt = session.get(AttemptRow, candidate.attempt_id) if candidate is not None else None
        if evaluation is None or candidate is None or attempt is None or attempt.trial_id != trial.id:
            raise ValueError("selected evaluation does not belong to its publication trial")
        if candidate.validation_status != "valid":
            raise ValueError("publication selection contains an invalid candidate")
        if attempt.terminal_status in {"infrastructure_invalid", "cancelled"}:
            raise ValueError("publication selection contains an invalid or cancelled attempt")
        if evidence_digest(candidate.stored_candidate) != candidate.stored_candidate_digest:
            raise ValueError("selected candidate display evidence failed its content digest check")
        actual_eval_digest = evaluation_digest(
            candidate_id=evaluation.candidate_id, evaluator_id=evaluation.evaluator_id,
            fixture_id=evaluation.fixture_id, schedule_digest=evaluation.schedule_digest,
            verdict=evaluation.verdict, result=evaluation.result,
        )
        if actual_eval_digest != evaluation.evaluation_digest:
            raise ValueError("selected evaluation failed its recorded evidence digest check")
        events = session.execute(
            select(AttemptEventRow).where(AttemptEventRow.attempt_id == attempt.id).order_by(AttemptEventRow.sequence)
        ).scalars().all()
        trace_digest = attempt_trace_digest([
            {"sequence": event.sequence, "event_type": event.event_type, "payload": event.payload}
            for event in events
        ])
        selections.append({
            "trial_id": str(trial.id),
            "included": True,
            "attempt_id": str(attempt.id),
            "candidate_id": str(candidate.id),
            "evaluation_id": str(evaluation.id),
            "candidate_digest": candidate.stored_candidate_digest,
            "evaluation_digest": evaluation.evaluation_digest,
            "trace_digest": trace_digest,
        })
    return {"schema_version": "aieb.published-evidence/v1", "campaign_id": str(campaign_id), "selections": selections}
