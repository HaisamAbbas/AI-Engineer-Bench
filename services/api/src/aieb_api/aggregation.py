"""Real campaign aggregation: turn a frozen campaign's persisted trials into
the authoritative `aieb_analysis.summarize()` snapshot.

This is the caller ENG-011's coverage-eligibility gate needed: it derives the
frozen campaign's OWN planned (task, entrant) cells from its resolved manifest
and passes them to `summarize(..., planned_cells=...)`, and it tags every
observation with its frozen per-task category so summarize()'s `per_category`
breakdown is real campaign data. A wholly-missing planned cell (a cell in the
frozen matrix with zero recorded trials) is therefore detected as incomplete
coverage in real aggregation - not only in tests that supply `planned_cells` by
hand. ENG-018's publication flow builds its immutable snapshot from this
function.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from aieb_core.models import EntrantRevision, TaskRevision
from aieb_analysis import TrialObservation, summarize
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AttemptRow,
    CampaignRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    TaskRevisionRow,
    TrialRow,
    UsageReceiptRow,
    UsageRequestRow,
)

# verdict -> `passed` tri-state. `pass` resolves True; `fail` and
# `contract_violation` are definite non-passes (resolved False); `indeterminate`
# has no scientific verdict yet, so it stays unresolved (None) and is excluded
# from scored populations exactly as summarize() itself excludes passed is None.
_VERDICT_TO_PASSED: dict[str, bool | None] = {
    "pass": True,
    "fail": False,
    "contract_violation": False,
    "indeterminate": None,
}
_NONTERMINAL_INVALID = {"infrastructure_invalid", "cancelled"}
# UsageRequestRow.actor_role groupings (per the budget-role model).
_ENGINEER_ROLES = {"engineer"}
_DEV_APPLICATION_ROLES = {"dev_application"}
_VERIFIER_ROLES = {"verifier_application", "verifier_judge"}


class CampaignNotAggregatable(ValueError):
    """The campaign does not exist or has no frozen resolved manifest, so there
    is no authoritative plan to aggregate against."""


def _role_cost(session: Session, attempt_id: UUID, roles: set[str]) -> str | None:
    """Sum receipts for one attempt restricted to the given actor roles.

    Returns a decimal string, or None when NO usage row exists for those roles
    on this attempt - "unknown", not "zero". summarize() treats a None cost as
    missing accounting (so cost-per-resolution reports unavailable rather than a
    fabricated number), which is the honest outcome when a role's spend was
    never recorded."""
    rows = session.execute(
        select(UsageReceiptRow.reported_cost_usd, UsageReceiptRow.estimated_cost_usd)
        .join(UsageRequestRow, UsageRequestRow.id == UsageReceiptRow.usage_request_id)
        .where(UsageRequestRow.attempt_id == attempt_id, UsageRequestRow.actor_role.in_(roles))
    ).all()
    if not rows:
        return None
    total = Decimal("0")
    for reported, estimated in rows:
        cost = reported if reported is not None else estimated
        if cost is not None:
            total += cost
    return format(total, "f")


def _planned_cells_and_categories(
    resolved: dict,
) -> tuple[frozenset[tuple[str, str]], dict[str, str], dict[str, str]]:
    """Derive the frozen matrix's planned (task_id, entrant_id) cells, plus
    per-task category and family maps, from the campaign's own resolved
    manifest - never inferred from which trials happen to exist.

    Planned cells come from the resolved `trials` list when present (the exact
    frozen matrix), correlating each trial's task/entrant digest back to the
    resolved task/entrant manifests; if a resolved manifest predates persisted
    trials, it falls back to the full task x entrant product the planner always
    expands."""
    tasks = [TaskRevision.model_validate(t) for t in resolved.get("tasks", [])]
    entrants = [EntrantRevision.model_validate(e) for e in resolved.get("entrants", [])]
    category_by_task = {task.id: task.category.value if hasattr(task.category, "value") else str(task.category) for task in tasks}
    family_by_task = {task.id: task.family_id for task in tasks}

    task_by_digest = {task.digest(): task.id for task in tasks}
    entrant_by_digest = {entrant.digest(): entrant.id for entrant in entrants}
    planned: set[tuple[str, str]] = set()
    trials = resolved.get("trials")
    if trials:
        for trial in trials:
            task_id = task_by_digest.get(trial["task_digest"])
            entrant_id = entrant_by_digest.get(trial["entrant_digest"])
            if task_id is not None and entrant_id is not None:
                planned.add((task_id, entrant_id))
    else:
        planned = {(task.id, entrant.id) for task in tasks for entrant in entrants}
    return frozenset(planned), category_by_task, family_by_task


def _latest_attempt(session: Session, trial_id: UUID) -> AttemptRow | None:
    return session.execute(
        select(AttemptRow).where(AttemptRow.trial_id == trial_id).order_by(AttemptRow.number.desc()).limit(1)
    ).scalar_one_or_none()


def _latest_evaluation(session: Session, attempt_id: UUID) -> EvaluationRow | None:
    return session.execute(
        select(EvaluationRow)
        .join(CandidateRow, EvaluationRow.candidate_id == CandidateRow.id)
        .where(CandidateRow.attempt_id == attempt_id)
        .order_by(EvaluationRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def campaign_observations(session: Session, campaign_id: UUID) -> tuple[TrialObservation, ...]:
    """One TrialObservation per persisted trial (task, entrant, repetition),
    built from that trial's latest attempt and its evaluation."""
    resolved = None
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is not None:
        resolved = campaign.resolved
    _, category_by_task, family_by_task = _planned_cells_and_categories(resolved or {})

    trials = session.execute(select(TrialRow).where(TrialRow.campaign_id == campaign_id)).scalars().all()
    observations: list[TrialObservation] = []
    for trial in trials:
        task_row = session.get(TaskRevisionRow, trial.task_revision_id)
        entrant_row = session.get(EntrantRevisionRow, trial.entrant_revision_id)
        task_id = task_row.slug if task_row is not None else str(trial.task_revision_id)
        entrant_id = entrant_row.slug if entrant_row is not None else str(trial.entrant_revision_id)
        category = category_by_task.get(task_id)
        family_id = family_by_task.get(task_id, task_id)

        attempt = _latest_attempt(session, trial.id)
        passed: bool | None = None
        execution_valid = False
        engineer_cost = dev_cost = verifier_cost = None
        if attempt is not None:
            execution_valid = attempt.phase == "terminal" and attempt.terminal_status not in _NONTERMINAL_INVALID
            if execution_valid:
                evaluation = _latest_evaluation(session, attempt.id)
                if evaluation is not None and evaluation.verdict is not None:
                    passed = _VERDICT_TO_PASSED.get(evaluation.verdict)
                else:
                    # A terminal, non-invalid attempt without a recorded verdict
                    # is a valid execution that is not (yet) resolved - counted
                    # in coverage/attrition, excluded from scored populations.
                    passed = None
            engineer_cost = _role_cost(session, attempt.id, _ENGINEER_ROLES)
            dev_cost = _role_cost(session, attempt.id, _DEV_APPLICATION_ROLES)
            verifier_cost = _role_cost(session, attempt.id, _VERIFIER_ROLES)
        observations.append(
            TrialObservation(
                task_id=task_id,
                family_id=family_id,
                # No project concept exists in the hosted schema yet; family_id
                # is the best available clustering unit. project_id is unused by
                # summarize() (only paired_project_difference reads it), so this
                # does not affect the published snapshot.
                project_id=family_id,
                entrant_id=entrant_id,
                repetition=trial.repetition,
                passed=passed,
                execution_valid=execution_valid,
                engineer_cost_usd=engineer_cost,
                dev_application_cost_usd=dev_cost,
                verifier_cost_usd=verifier_cost,
                category=category,
            )
        )
    return tuple(observations)


def aggregate_campaign_snapshot(session: Session, campaign_id: UUID) -> dict:
    """Aggregate a frozen campaign's persisted trials into the authoritative
    analysis snapshot, wiring the campaign's OWN planned cells and categories
    through summarize() so incomplete coverage is detected against the real
    frozen matrix (ENG-011 integration gate).

    Raises CampaignNotAggregatable if the campaign has no resolved manifest.
    """
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None or not campaign.resolved:
        raise CampaignNotAggregatable("campaign has no frozen resolved manifest to aggregate")
    resolved = campaign.resolved
    planned_cells, _, _ = _planned_cells_and_categories(resolved)
    # required_repetitions comes from the frozen CampaignDraft (the planner
    # expands range(draft.repetitions)); absent in some hand-built fixtures, in
    # which case summarize() falls back to the observed per-cell count.
    required_repetitions = None
    draft = campaign.draft if isinstance(campaign.draft, dict) else {}
    if isinstance(draft.get("repetitions"), int):
        required_repetitions = draft["repetitions"]

    observations = campaign_observations(session, campaign_id)
    return summarize(observations, required_repetitions=required_repetitions, planned_cells=planned_cells)
