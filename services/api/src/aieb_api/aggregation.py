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

from decimal import Decimal
from uuid import UUID

from aieb_core.models import EntrantRevision, TaskRevision, Trial
from pydantic import ValidationError
from aieb_analysis import TrialObservation, summarize
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AttemptEventRow,
    AttemptRow,
    CampaignRow,
    CandidateRow,
    CorrectionRunRow,
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
_VALID_TERMINAL_STATUSES = frozenset(_VERDICT_TO_PASSED)
# UsageRequestRow.actor_role groupings (per the budget-role model).
_ENGINEER_ROLES = {"engineer"}
_DEV_APPLICATION_ROLES = {"dev_application"}
_VERIFIER_ROLES = {"verifier_application", "verifier_judge"}


class CampaignNotAggregatable(ValueError):
    """The campaign lacks an authoritative plan or persisted trial identities
    do not exactly match that frozen plan."""


def _role_cost(session: Session, attempt_id: UUID, roles: set[str]) -> str | None:
    """Sum receipts for one attempt restricted to the given actor roles.

    Returns None if the role has no requests, a request has no receipt yet,
    or any physical retry has neither reported nor estimated cost. Partial
    accounting is not a complete total; an explicit zero remains zero.
    """
    rows = session.execute(
        select(UsageReceiptRow.reported_cost_usd, UsageReceiptRow.estimated_cost_usd)
        .select_from(UsageRequestRow)
        .outerjoin(UsageReceiptRow, UsageRequestRow.id == UsageReceiptRow.usage_request_id)
        .where(UsageRequestRow.attempt_id == attempt_id, UsageRequestRow.actor_role.in_(roles))
    ).all()
    if not rows:
        return None
    total = Decimal("0")
    for reported, estimated in rows:
        cost = reported if reported is not None else estimated
        if cost is None:
            return None
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


def _validated_campaign_trials(session: Session, campaign_id: UUID) -> tuple[dict, list[TrialRow]]:
    """Require a bijection with the frozen matrix before reading any outcomes.

    Revision UUIDs are not stored in the frozen contract: the enqueuer resolves
    them by slug/version. Check those keys AND canonical manifest digests, not
    just the mutable trial foreign keys or denormalized digest columns.
    """
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None or not isinstance(campaign.resolved, dict):
        raise CampaignNotAggregatable("campaign has no frozen resolved manifest to aggregate")
    resolved = campaign.resolved
    try:
        if any(not isinstance(resolved.get(key), list) or not resolved[key] for key in ("tasks", "entrants", "trials")):
            raise ValueError("missing frozen matrix")
        tasks = [TaskRevision.model_validate(value) for value in resolved["tasks"]]
        entrants = [EntrantRevision.model_validate(value) for value in resolved["entrants"]]
        planned = [Trial.model_validate(value) for value in resolved["trials"]]
    except (ValidationError, ValueError, TypeError) as exc:
        raise CampaignNotAggregatable("invalid or missing frozen trial matrix") from exc
    task_by_digest = {task.digest(): task for task in tasks}
    entrant_by_digest = {entrant.digest(): entrant for entrant in entrants}
    planned_by_id = {trial.id: trial for trial in planned}
    cells = {(trial.task_digest, trial.entrant_digest, trial.repetition_index) for trial in planned}
    if (len(planned_by_id) != len(planned) or len(cells) != len(planned)
            or len({task.id for task in tasks}) != len(tasks)
            or len({entrant.id for entrant in entrants}) != len(entrants)):
        raise CampaignNotAggregatable("duplicate identities in frozen trial matrix")
    if any(trial.task_digest not in task_by_digest or trial.entrant_digest not in entrant_by_digest for trial in planned):
        raise CampaignNotAggregatable("frozen trial references an unplanned revision")

    rows = session.execute(select(TrialRow).where(TrialRow.campaign_id == campaign_id)).scalars().all()
    if len(rows) != len(planned) or {row.id for row in rows} != set(planned_by_id):
        raise CampaignNotAggregatable("persisted trials do not match frozen matrix: missing or extra trial IDs")
    for row in rows:
        frozen = planned_by_id[row.id]
        if row.repetition != frozen.repetition_index or row.cell_digest != frozen.digest():
            raise CampaignNotAggregatable(f"trial {row.id} repetition or cell digest differs from frozen identity")
        task = task_by_digest[frozen.task_digest]
        entrant = entrant_by_digest[frozen.entrant_digest]
        task_row = session.get(TaskRevisionRow, row.task_revision_id)
        entrant_row = session.get(EntrantRevisionRow, row.entrant_revision_id)
        if (task_row is None or entrant_row is None
                or (task_row.slug, task_row.version) != (task.id, task.version)
                or (entrant_row.slug, entrant_row.version) != (entrant.id, entrant.agent_version)):
            raise CampaignNotAggregatable(f"trial {row.id} revision differs from frozen identity")
        try:
            task_digest = TaskRevision.model_validate(task_row.manifest).digest()
            entrant_digest = EntrantRevision.model_validate(entrant_row.manifest).digest()
        except (ValidationError, TypeError) as exc:
            raise CampaignNotAggregatable(f"trial {row.id} has an invalid persisted revision") from exc
        if task_digest != frozen.task_digest or entrant_digest != frozen.entrant_digest:
            raise CampaignNotAggregatable(f"trial {row.id} revision digest differs from frozen identity")
    return resolved, rows



def _latest_evaluation(session: Session, attempt_id: UUID) -> EvaluationRow | None:
    return session.execute(
        select(EvaluationRow)
        .join(CandidateRow, EvaluationRow.candidate_id == CandidateRow.id)
        .where(CandidateRow.attempt_id == attempt_id)
        .order_by(EvaluationRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def selected_campaign_evaluations(
    session: Session, campaign_id: UUID, correction_run_id: UUID | None = None,
) -> dict[UUID, EvaluationRow]:
    """Select original definitive results, overridden only by the requested run.

    Corrections from other runs never resolve an original trial. A corrected
    indeterminate verdict overrides the original too: uncertainty cannot be
    hidden by falling back to an earlier pass.
    """
    _, trials = _validated_campaign_trials(session, campaign_id)
    if correction_run_id is not None:
        run = session.get(CorrectionRunRow, correction_run_id)
        if run is None or run.campaign_id != campaign_id or run.status != "completed":
            raise CampaignNotAggregatable("correction run must be completed and belong to this campaign")
    selected: dict[UUID, EvaluationRow] = {}
    for trial in trials:
        attempts = session.execute(select(AttemptRow).where(
            AttemptRow.trial_id == trial.id,
        ).order_by(AttemptRow.number)).scalars().all()
        corrected: list[EvaluationRow] = []
        for attempt in attempts:
            evaluations = session.execute(select(EvaluationRow).join(CandidateRow).where(
                CandidateRow.attempt_id == attempt.id,
            ).order_by(EvaluationRow.created_at.desc(), EvaluationRow.id.desc())).scalars().all()
            matching = [e for e in evaluations if correction_run_id is not None and e.correction_run_id == correction_run_id]
            if matching:
                evaluation = matching[0]
                if (len(matching) != 1 or attempt.phase != "terminal"
                        or attempt.terminal_status not in _VALID_TERMINAL_STATUSES
                        or evaluation.verdict != attempt.terminal_status):
                    raise CampaignNotAggregatable("correction has ambiguous or invalid scored evidence")
                corrected.append(evaluation)
            original = next((e for e in evaluations if e.correction_run_id is None), None)
            if (trial.id not in selected and original is not None and attempt.phase == "terminal"
                    and original.verdict == attempt.terminal_status
                    and _VERDICT_TO_PASSED.get(original.verdict) is not None):
                selected[trial.id] = original
        if len(corrected) > 1:
            raise CampaignNotAggregatable("correction run has multiple results for one trial")
        if corrected:
            selected[trial.id] = corrected[0]
    return selected


def campaign_observations(
    session: Session, campaign_id: UUID, correction_run_id: UUID | None = None,
) -> tuple[TrialObservation, ...]:
    """One observation per persisted attempt, retaining replacement spend.

    Only the first valid scored attempt resolves a trial (never the best or
    latest attempt). Later attempts remain in accounting/attrition but have
    no scored verdict. A trial with no attempts is checked by planned_cells,
    not invented as an infrastructure failure or unknown-cost execution.
    """
    resolved, trials = _validated_campaign_trials(session, campaign_id)
    selected = selected_campaign_evaluations(session, campaign_id, correction_run_id)
    selected_attempts = {
        session.get(CandidateRow, evaluation.candidate_id).attempt_id: evaluation
        for evaluation in selected.values()
    }
    _, category_by_task, family_by_task = _planned_cells_and_categories(resolved)
    observations: list[TrialObservation] = []
    for trial in trials:
        task_row = session.get(TaskRevisionRow, trial.task_revision_id)
        entrant_row = session.get(EntrantRevisionRow, trial.entrant_revision_id)
        task_id = task_row.slug if task_row is not None else str(trial.task_revision_id)
        entrant_id = entrant_row.slug if entrant_row is not None else str(trial.entrant_revision_id)
        category = category_by_task.get(task_id)
        family_id = family_by_task.get(task_id, task_id)

        attempts = session.execute(
            select(AttemptRow).where(AttemptRow.trial_id == trial.id).order_by(AttemptRow.number)
        ).scalars().all()
        for attempt in attempts:
            execution_valid = attempt.phase == "terminal" and attempt.terminal_status in _VALID_TERMINAL_STATUSES
            evaluation = selected_attempts.get(attempt.id)
            passed = _VERDICT_TO_PASSED.get(evaluation.verdict) if evaluation is not None else None
            observations.append(
                TrialObservation(
                    task_id=task_id,
                    family_id=family_id,
                    # The hosted schema has no project identity; family is the
                    # available clustering unit (unused by summarize itself).
                    project_id=family_id,
                    entrant_id=entrant_id,
                    repetition=trial.repetition,
                    passed=passed,
                    execution_valid=execution_valid,
                    engineer_cost_usd=_role_cost(session, attempt.id, _ENGINEER_ROLES),
                    dev_application_cost_usd=_role_cost(session, attempt.id, _DEV_APPLICATION_ROLES),
                    verifier_cost_usd=_role_cost(session, attempt.id, _VERIFIER_ROLES),
                    # Hosted persistence has no authoritative deadline flag.
                    deadline=None,
                    category=category,
                )
            )
    return tuple(observations)


def _coverage_disclosure(session: Session, campaign_id: UUID) -> dict:
    """Disclose recorded evidence, not a claim of complete instrumentation.

    Every persisted attempt is included, including replacements and corrections.
    No request identities, receipt values, or trace payloads enter public data.
    A present lifecycle event does not prove full action-trace coverage; a known
    receipt does not prove every provider request was metered.
    """
    attempts = session.execute(
        select(AttemptRow).join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .where(TrialRow.campaign_id == campaign_id).order_by(AttemptRow.id)
    ).scalars().all()
    roles = ("engineer", "dev_application", "verifier_application", "verifier_judge")
    disclosures = []
    for attempt in attempts:
        costs = {}
        for role in roles:
            receipts = session.execute(
                select(UsageReceiptRow.reported_cost_usd, UsageReceiptRow.estimated_cost_usd)
                .select_from(UsageRequestRow)
                .outerjoin(UsageReceiptRow, UsageReceiptRow.usage_request_id == UsageRequestRow.id)
                .where(UsageRequestRow.attempt_id == attempt.id, UsageRequestRow.actor_role == role)
            ).all()
            if not receipts or any(reported is None and estimated is None for reported, estimated in receipts):
                costs[role] = "unknown"
            elif any(reported is None for reported, _ in receipts):
                costs[role] = "estimated"
            else:
                costs[role] = "reported"
        has_events = session.execute(select(AttemptEventRow.id).where(
            AttemptEventRow.attempt_id == attempt.id,
        ).limit(1)).scalar_one_or_none() is not None
        disclosures.append({
            "attempt_id": str(attempt.id),
            "trace": "present" if has_events else "missing",
            "cost_by_role": costs,
        })
    return {
        "schema_version": "aieb.coverage-disclosure/v1",
        "attempts": disclosures,
        "hard_cost_eligible": False,
        "limitations": [
            "Trace presence records lifecycle evidence, not complete action-trace coverage.",
            "Cost labels describe recorded requests and retries only; absent role accounting is unknown, not zero.",
            "Complete provider accounting and hard budget enforcement are not established by this publication path.",
        ],
    }


def aggregate_campaign_snapshot(
    session: Session, campaign_id: UUID, correction_run_id: UUID | None = None,
) -> dict:
    """Aggregate a frozen campaign's persisted trials into the authoritative
    analysis snapshot, wiring the campaign's OWN planned cells and categories
    through summarize() so incomplete coverage is detected against the real
    frozen matrix (ENG-011 integration gate).

    Raises CampaignNotAggregatable if the campaign has no resolved manifest.
    """
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None or not campaign.resolved:
        raise CampaignNotAggregatable("campaign has no frozen resolved manifest to aggregate")
    observations = campaign_observations(session, campaign_id, correction_run_id)
    resolved = campaign.resolved
    planned_cells, _, _ = _planned_cells_and_categories(resolved)
    # required_repetitions comes from the frozen CampaignDraft (the planner
    # expands range(draft.repetitions)); absent in some hand-built fixtures, in
    # which case summarize() falls back to the observed per-cell count.
    required_repetitions = None
    draft = campaign.draft if isinstance(campaign.draft, dict) else {}
    if isinstance(draft.get("repetitions"), int):
        required_repetitions = draft["repetitions"]

    snapshot = summarize(observations, required_repetitions=required_repetitions, planned_cells=planned_cells)
    snapshot["coverage_disclosure"] = _coverage_disclosure(session, campaign_id)
    return snapshot
