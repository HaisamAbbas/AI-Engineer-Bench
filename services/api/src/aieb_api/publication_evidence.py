"""Build a complete, explicit selection manifest for a publication."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .evidence_integrity import attempt_trace_digest, evidence_digest, evaluation_digest
from .models import AttemptEventRow, AttemptRow, CampaignRow, CandidateRow, EvaluationRow, TrialRow
from .snapshots import snapshot_digest as compute_snapshot_digest

# A campaign may only be published once it has reached a terminal outcome:
# `completed` (full coverage) or `incomplete` (published with disclosed
# incomplete coverage, spec section 28). A draft/frozen/running/paused/
# cancelling campaign is still mutable and must never be published.
_PUBLISHABLE_CAMPAIGN_STATES = {"completed", "incomplete"}

# The only terminal statuses that represent a real scientific verdict. A
# publishable attempt's terminal_status must be one of these AND must equal
# the pinned evaluation's own verdict.
_SCORED_VERDICTS = {"pass", "fail", "contract_violation", "indeterminate"}


def build_evidence_manifest(
    session: Session, campaign_id: UUID, selected_evaluations: dict[UUID, UUID], *, snapshot: dict[str, Any]
) -> dict:
    """Pin every campaign trial to one evaluation or explicitly exclude it,
    and bind the whole selection to the published snapshot.

    `selected_evaluations` maps included trial IDs to the exact evaluation
    IDs approved for publication. Every other campaign trial is represented
    as excluded; unknown trials, cross-trial evaluations, and bad persisted
    digests are rejected before a caller can persist the publication.

    `snapshot` is the exact AnalysisSnapshot dict being published. Its digest
    is embedded in the returned manifest so the snapshot and the selection
    are one immutable, cross-checked unit (review finding #2): the public
    read path refuses to serve a run whose manifest snapshot_digest does not
    match the publication's own snapshot_digest, so the snapshot cannot be
    swapped for a different one after publication without detection. This
    binding does NOT prove the snapshot's rates were originally derived from
    the selected evaluations - that semantic derivation check is deferred to
    ENG-018 - it only guarantees the published pair cannot be altered
    undetected afterward.

    Only a terminal, scientifically-eligible attempt may be included (review
    finding #3): the attempt must be `terminal` (so finalize() has already
    run and will not append a further trace event that invalidates the pinned
    trace digest), its terminal_status must be a real scored verdict rather
    than infrastructure_invalid/cancelled/unresolved, and the selected
    evaluation must carry a non-null verdict.
    """
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None:
        raise ValueError("publication selection references an unknown campaign")
    if campaign.state not in _PUBLISHABLE_CAMPAIGN_STATES:
        raise ValueError("a campaign can only be published after it has completed (or completed with incomplete coverage)")

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
        if attempt.phase != "terminal":
            raise ValueError("publication selection contains a nonterminal attempt")
        if evaluation.verdict is None:
            raise ValueError("publication selection contains an unscored evaluation")
        # The attempt's terminal status must be a real scientific verdict AND
        # must be exactly the verdict of the selected evaluation. A non-verdict
        # terminal status (infrastructure_invalid, cancelled, scorer_error, or
        # any other attribution) is never publishable, and an attempt whose
        # terminal status disagrees with the evaluation being pinned (e.g. a
        # `fail` attempt paired with a `pass` evaluation) is an integrity
        # inconsistency, not a valid selection.
        if attempt.terminal_status not in _SCORED_VERDICTS or attempt.terminal_status != evaluation.verdict:
            raise ValueError("publication selection's terminal status is not the selected evaluation's scored verdict")
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
    return {
        "schema_version": "aieb.published-evidence/v1",
        "campaign_id": str(campaign_id),
        "snapshot_digest": compute_snapshot_digest(snapshot),
        "selections": selections,
    }
