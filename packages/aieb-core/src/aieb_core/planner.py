"""Pure campaign reference resolution and immutable trial-matrix expansion."""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from .canonical import content_hash
from .models import BudgetProfile, CampaignDraft, Cohort, EntrantRevision, ProtocolRevision, ResolvedCampaign, TaskRevision, Trial


class PlanningError(ValueError):
    """A draft cannot safely become a frozen campaign."""


@dataclass(frozen=True)
class Registry:
    tasks: dict[str, TaskRevision]
    entrants: dict[str, EntrantRevision]
    cohorts: dict[str, Cohort]
    protocols: dict[str, ProtocolRevision]
    budgets: dict[str, BudgetProfile]


def _resolve(mapping: dict[str, object], identifier: str, kind: str) -> object:
    try:
        return mapping[identifier]
    except KeyError as exc:
        raise PlanningError(f"unresolved {kind} reference: {identifier}") from exc


def _validate_compatibility(cohort: Cohort, task: TaskRevision, entrant: EntrantRevision, budget: BudgetProfile) -> None:
    if cohort.budget_profile_id != budget.id:
        raise PlanningError("cohort budget reference does not resolve to supplied budget")
    if cohort.id not in task.profile_compatibility:
        raise PlanningError(f"task {task.id} is incompatible with cohort {cohort.id}")
    if task.application.dependency_mode != cohort.dependency_mode:
        raise PlanningError(f"task {task.id} dependency mode differs from cohort")
    if not set(cohort.required_capabilities).issubset(entrant.capabilities):
        raise PlanningError(f"entrant {entrant.id} lacks required cohort capabilities")
    if task.environment.engineer_cpu > budget.engineer_cpu or task.environment.engineer_memory_mb > budget.engineer_memory_mb:
        raise PlanningError(f"task {task.id} exceeds budget-profile resources")


def resolve_campaign(draft: CampaignDraft, registry: Registry) -> ResolvedCampaign:
    cohort = _resolve(registry.cohorts, draft.cohort_id, "cohort")
    assert isinstance(cohort, Cohort)
    protocol = _resolve(registry.protocols, cohort.protocol_id, "protocol")
    budget = _resolve(registry.budgets, cohort.budget_profile_id, "budget")
    assert isinstance(protocol, ProtocolRevision) and isinstance(budget, BudgetProfile)
    tasks = tuple(_resolve(registry.tasks, task_id, "task") for task_id in draft.task_ids)
    entrants = tuple(_resolve(registry.entrants, entrant_id, "entrant") for entrant_id in draft.entrant_ids)
    assert all(isinstance(task, TaskRevision) for task in tasks)
    assert all(isinstance(entrant, EntrantRevision) for entrant in entrants)
    for task in tasks:
        for entrant in entrants:
            _validate_compatibility(cohort, task, entrant, budget)

    draft_digest = content_hash(draft)
    cells = [(task, entrant, repetition) for task in tasks for entrant in entrants for repetition in range(draft.repetitions)]
    random.Random(draft.order_seed).shuffle(cells)
    campaign_id = uuid5(NAMESPACE_URL, f"aieb:campaign:{draft_digest}")
    trials = tuple(
        Trial(
            id=uuid5(NAMESPACE_URL, f"aieb:trial:{draft_digest}:{task.digest()}:{entrant.digest()}:{repetition}"),
            campaign_digest=draft_digest,
            task_digest=task.digest(), entrant_digest=entrant.digest(), cohort_digest=cohort.digest(),
            repetition_index=repetition, order_index=index,
        )
        for index, (task, entrant, repetition) in enumerate(cells)
    )
    if len({trial.id for trial in trials}) != len(trials):
        raise PlanningError("duplicate deterministic trial identity")
    return ResolvedCampaign(
        id=campaign_id, draft_digest=draft_digest, cohort=cohort, protocol=protocol, budget=budget,
        tasks=tasks, entrants=entrants, trials=trials,
    )


def freeze_campaign(draft: CampaignDraft, registry: Registry) -> ResolvedCampaign:
    """Resolve all references now; the returned immutable object is dispatch input."""
    return resolve_campaign(draft, registry)


def assert_comparable(left: ResolvedCampaign, right: ResolvedCampaign) -> None:
    if left.cohort.digest() != right.cohort.digest() or left.protocol.digest() != right.protocol.digest():
        raise PlanningError("campaigns belong to incompatible cohorts or protocols")
