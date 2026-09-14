"""Atomic work acquisition, fencing, and reconciliation (ENG-015, spec section 31).

Every state transition here is a single fenced, atomic UPDATE ... WHERE ...
RETURNING statement, the same pattern established for campaign draft/freeze
in ENG-014: the database's own row-level locking enforces the guarantee,
not application-level timing. Workers and the reconciler communicate with
persistence only through this module - no ad hoc queries elsewhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aieb_core.models import EntrantRevision, TaskRevision
from aieb_core.models import Trial as TrialContract
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import (
    AttemptRow,
    CampaignRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    TaskRevisionRow,
    TrialRow,
    WorkItemRow,
)

DEFAULT_LEASE_SECONDS = 60


class EnqueueError(ValueError):
    pass


@dataclass(frozen=True)
class LeasedWork:
    work_item_id: uuid.UUID
    attempt_id: uuid.UUID
    trial_id: uuid.UUID
    generation: int


def enqueue_frozen_campaign(session: Session, campaign_id: uuid.UUID) -> int:
    """Create trial/attempt/work_item rows for a frozen campaign's resolved trials.

    A minimal internal capability this ticket needs to make leasing/recovery
    testable end to end. The public POST /campaigns/{id}/start endpoint
    (budget reservations, role checks) is ENG-017's scope and will call this
    same function rather than duplicating trial-expansion logic.
    """
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None or campaign.state != "frozen" or campaign.resolved is None:
        raise EnqueueError("campaign must be frozen with a resolved manifest to enqueue")
    already = session.execute(select(TrialRow.id).where(TrialRow.campaign_id == campaign_id).limit(1)).first()
    if already is not None:
        return 0  # idempotent: already enqueued

    resolved = campaign.resolved
    task_by_digest = {TaskRevision.model_validate(t).digest(): t for t in resolved["tasks"]}
    entrant_by_digest = {EntrantRevision.model_validate(e).digest(): e for e in resolved["entrants"]}

    created = 0
    for trial in resolved["trials"]:
        task = task_by_digest[trial["task_digest"]]
        entrant = entrant_by_digest[trial["entrant_digest"]]
        task_row = session.execute(
            select(TaskRevisionRow).where(TaskRevisionRow.slug == task["id"], TaskRevisionRow.version == task["version"])
        ).scalar_one()
        entrant_row = session.execute(
            select(EntrantRevisionRow).where(
                EntrantRevisionRow.slug == entrant["id"], EntrantRevisionRow.version == entrant["agent_version"]
            )
        ).scalar_one()
        cell_digest = TrialContract.model_validate(trial).digest()
        trial_row = TrialRow(
            id=uuid.UUID(trial["id"]), campaign_id=campaign.id, task_revision_id=task_row.id,
            entrant_revision_id=entrant_row.id, repetition=trial["repetition_index"], cell_digest=cell_digest,
        )
        session.add(trial_row)
        session.flush()
        attempt_row = AttemptRow(trial_id=trial_row.id, number=1, phase="queued", lease_generation=0)
        session.add(attempt_row)
        session.flush()
        session.add(WorkItemRow(attempt_id=attempt_row.id, type="engineering", state="ready"))
        created += 1
    session.commit()
    return created


def claim_work_item(session: Session, *, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> LeasedWork | None:
    """Acquire one ready work item atomically. SKIP LOCKED lets two contending
    workers each get a different item (or none) without blocking on each other."""
    candidate = (
        select(WorkItemRow.id)
        .where(WorkItemRow.type == "engineering", WorkItemRow.state == "ready")
        .order_by(WorkItemRow.id)
        .with_for_update(skip_locked=True)
        .limit(1)
        .cte("candidate")
    )
    lease_expiry = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    leased = session.execute(
        update(WorkItemRow)
        .where(WorkItemRow.id == candidate.c.id)
        .values(state="leased", worker_id=worker_id, generation=WorkItemRow.generation + 1, lease_expiry=lease_expiry)
        .returning(WorkItemRow)
    ).scalar_one_or_none()
    if leased is None:
        session.commit()
        return None
    session.execute(
        update(AttemptRow).where(AttemptRow.id == leased.attempt_id).values(phase="engineering", worker_id=worker_id, lease_generation=leased.generation)
    )
    session.commit()
    return LeasedWork(work_item_id=leased.id, attempt_id=leased.attempt_id, trial_id=session.get(AttemptRow, leased.attempt_id).trial_id, generation=leased.generation)


def heartbeat(session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """Extend the lease. Returns False if this worker/generation has been fenced out -
    the caller must abort immediately rather than continue engineering."""
    lease_expiry = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    result = session.execute(
        update(WorkItemRow)
        .where(WorkItemRow.id == work_item_id, WorkItemRow.worker_id == worker_id, WorkItemRow.generation == generation, WorkItemRow.state == "leased")
        .values(lease_expiry=lease_expiry)
    )
    session.commit()
    return result.rowcount == 1


@dataclass(frozen=True)
class CandidateOutcome:
    tree_digest: str
    manifest_digest: str
    validation_status: str


@dataclass(frozen=True)
class EvaluationOutcome:
    evaluator_id: uuid.UUID
    fixture_id: uuid.UUID
    schedule_digest: str
    verdict: str | None
    result: dict


def record_outcome(
    session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, attempt_id: uuid.UUID,
    candidate: CandidateOutcome, evaluation: EvaluationOutcome | None,
) -> bool:
    """Artifact-first step: persist what was produced BEFORE the work item is marked
    done. If the caller crashes between this commit and finalize(), the reconciler
    finds this row and completes finalization instead of wastefully replacing the
    attempt (EX-03). Fenced the same way as every other transition here, so a
    worker that has already lost its lease cannot record spurious results."""
    fenced = session.execute(
        select(WorkItemRow.id).where(
            WorkItemRow.id == work_item_id, WorkItemRow.worker_id == worker_id, WorkItemRow.generation == generation, WorkItemRow.state == "leased"
        )
    ).scalar_one_or_none()
    if fenced is None:
        return False
    candidate_row = CandidateRow(
        attempt_id=attempt_id, tree_digest=candidate.tree_digest, manifest_digest=candidate.manifest_digest,
        validation_status=candidate.validation_status,
    )
    session.add(candidate_row)
    session.flush()
    if evaluation is not None:
        session.add(
            EvaluationRow(
                candidate_id=candidate_row.id, evaluator_id=evaluation.evaluator_id, fixture_id=evaluation.fixture_id,
                schedule_digest=evaluation.schedule_digest, verdict=evaluation.verdict, result=evaluation.result,
            )
        )
    session.commit()
    return True


def finalize(session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, attempt_id: uuid.UUID, terminal_status: str, done: bool) -> bool:
    """Fenced, atomic transition to a terminal work-item/attempt state. Returns False
    if this worker/generation no longer holds the lease - a stale worker returning
    after reassignment (or a duplicate finalize call) cannot commit results (EX-04)."""
    result = session.execute(
        update(WorkItemRow)
        .where(WorkItemRow.id == work_item_id, WorkItemRow.worker_id == worker_id, WorkItemRow.generation == generation, WorkItemRow.state == "leased")
        .values(state="done" if done else "failed")
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.execute(update(AttemptRow).where(AttemptRow.id == attempt_id).values(phase="terminal", terminal_status=terminal_status))
    session.commit()
    return True


@dataclass(frozen=True)
class ReconciliationSummary:
    resumed: int = 0
    replaced: int = 0
    exhausted: int = 0


def reconcile_expired_leases(session: Session, *, default_max_replacements: int = 2) -> ReconciliationSummary:
    """Recover work items whose lease expired without a fenced finalize.

    If a candidate was already recorded (record_outcome succeeded before the
    worker died), complete finalization from that evidence instead of
    replacing the attempt - no re-execution, no duplicate scoring (EX-03).
    Otherwise the attempt is replaced, up to the frozen campaign's
    max_replacements policy; beyond that the trial is left unresolved rather
    than silently retried forever.
    """
    resumed = replaced = exhausted = 0
    expired = session.execute(
        select(WorkItemRow)
        .where(WorkItemRow.type == "engineering", WorkItemRow.state == "leased", WorkItemRow.lease_expiry < func.now())
        .with_for_update(skip_locked=True)
    ).scalars().all()
    for item in expired:
        attempt = session.get(AttemptRow, item.attempt_id)
        candidate = session.execute(select(CandidateRow).where(CandidateRow.attempt_id == attempt.id)).scalar_one_or_none()
        if candidate is not None:
            evaluation = session.execute(select(EvaluationRow).where(EvaluationRow.candidate_id == candidate.id)).scalar_one_or_none()
            item.state = "done" if evaluation is not None and evaluation.verdict is not None else "failed"
            attempt.phase = "terminal"
            attempt.terminal_status = evaluation.verdict if evaluation is not None else "infrastructure_invalid"
            resumed += 1
            continue

        item.state = "failed"
        attempt.phase = "terminal"
        attempt.terminal_status = "infrastructure_invalid"
        trial = session.get(TrialRow, attempt.trial_id)
        campaign = session.get(CampaignRow, trial.campaign_id)
        max_replacements = (
            campaign.resolved["protocol"]["max_replacements"] if campaign is not None and campaign.resolved else default_max_replacements
        )
        attempt_count = session.execute(select(func.count()).select_from(AttemptRow).where(AttemptRow.trial_id == trial.id)).scalar_one()
        if attempt_count - 1 < max_replacements:
            new_attempt = AttemptRow(trial_id=trial.id, number=attempt_count + 1, phase="queued", lease_generation=0)
            session.add(new_attempt)
            session.flush()
            session.add(WorkItemRow(attempt_id=new_attempt.id, type="engineering", state="ready"))
            replaced += 1
        else:
            exhausted += 1
    session.commit()
    return ReconciliationSummary(resumed=resumed, replaced=replaced, exhausted=exhausted)


def cancel_campaign(session: Session, campaign_id: uuid.UUID) -> bool:
    """Stop new dispatch. Already-leased work items are left to finish or expire
    naturally; teardown_orphans() cleans up anything left behind by a killed worker."""
    result = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state.in_(("frozen", "running"))).values(state="cancelling")
    )
    session.commit()
    return result.rowcount == 1


def maybe_complete_cancellation(session: Session, campaign_id: uuid.UUID) -> bool:
    """Once no work remains ready or leased for a cancelling campaign, mark it
    fully cancelled rather than leaving it stuck in 'cancelling' forever."""
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None or campaign.state != "cancelling":
        return False
    outstanding = session.execute(
        select(func.count())
        .select_from(WorkItemRow)
        .join(AttemptRow, AttemptRow.id == WorkItemRow.attempt_id)
        .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .where(TrialRow.campaign_id == campaign_id, WorkItemRow.state.in_(("ready", "leased")))
    ).scalar_one()
    if outstanding > 0:
        return False
    campaign.state = "cancelled"
    session.commit()
    return True


def is_campaign_cancelling(session: Session, campaign_id: uuid.UUID) -> bool:
    state = session.execute(select(CampaignRow.state).where(CampaignRow.id == campaign_id)).scalar_one_or_none()
    return state in ("cancelling", "cancelled")


def teardown_orphans(session: Session) -> list[uuid.UUID]:
    """Return attempt IDs whose lease has expired and are still 'leased' - callers
    use this to identify local work-root directories a killed worker left behind,
    before reconcile_expired_leases() transitions their state."""
    rows = session.execute(
        select(WorkItemRow.attempt_id).where(
            WorkItemRow.type == "engineering", WorkItemRow.state == "leased", WorkItemRow.lease_expiry < func.now()
        )
    ).scalars().all()
    return [attempt_id for attempt_id in rows if attempt_id is not None]
