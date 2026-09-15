"""Atomic work acquisition, fencing, and reconciliation (ENG-015, spec section 31).

Every state transition here is a single fenced, atomic UPDATE ... WHERE ...
RETURNING statement, the same pattern established for campaign draft/freeze
in ENG-014: the database's own row-level locking enforces the guarantee,
not application-level timing. Workers and the reconciler communicate with
persistence only through this module - no ad hoc queries elsewhere.

ENG015-007 splits what was one leased `engineering` work item covering the
whole attempt into two independently leased phases: `engineering` (produces
and persists a candidate) and `verification` (builds and scores it). Each
has its own lease generation, heartbeat, fencing, and finalize - a crash in
either phase is recovered independently, and a dead verifier never causes
engineering to repeat (the persisted candidate is reused as-is).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aieb_core.models import EntrantRevision, TaskRevision
from aieb_core.models import Trial as TrialContract
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
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
    work_type: str = "engineering"


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


_ATTEMPT_PHASE_FOR_WORK_TYPE = {"engineering": "engineering", "verification": "verifying"}


def claim_work_item(session: Session, *, worker_id: str, work_type: str | None = None, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> LeasedWork | None:
    """Acquire one ready work item atomically. SKIP LOCKED lets two contending
    workers each get a different item (or none) without blocking on each
    other. `work_type=None` (the default) claims whatever is ready across
    both `engineering` and `verification` queues - a real worker pool
    services both phases, not a phase-dedicated one; pass an explicit
    `work_type` only to isolate one phase's queue (as some tests do)."""
    query = select(WorkItemRow.id).where(WorkItemRow.state == "ready")
    if work_type is not None:
        query = query.where(WorkItemRow.type == work_type)
    candidate = query.order_by(WorkItemRow.id).with_for_update(skip_locked=True).limit(1).cte("candidate")
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
        update(AttemptRow).where(AttemptRow.id == leased.attempt_id).values(
            phase=_ATTEMPT_PHASE_FOR_WORK_TYPE.get(leased.type, leased.type), worker_id=worker_id, lease_generation=leased.generation,
        )
    )
    session.commit()
    return LeasedWork(
        work_item_id=leased.id, attempt_id=leased.attempt_id,
        trial_id=session.get(AttemptRow, leased.attempt_id).trial_id, generation=leased.generation, work_type=leased.type,
    )


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
    stored_candidate: dict = field(default_factory=dict)
    """The full StoredCandidate (aieb_runner.artifacts) serialized to JSON -
    manifest plus every changed file's artifact-store reference. Persisted so
    an independently-leased verification phase (a different worker, a
    different process, possibly after this one crashed) can reconstruct the
    candidate from the database alone, without any in-memory state shared
    with whatever produced it. Callers that only exercise the leasing/fencing
    logic itself (not real candidate reconstruction) may leave this empty."""


@dataclass(frozen=True)
class EvaluationOutcome:
    evaluator_id: uuid.UUID
    fixture_id: uuid.UUID
    schedule_digest: str
    verdict: str | None
    result: dict


def _fenced_lease_touch(session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, lease_seconds: int) -> bool:
    """Shared fencing primitive: a real UPDATE extending the lease (doubling as
    an implicit heartbeat), not a plain SELECT - a plain SELECT takes no row
    lock under READ COMMITTED, so a concurrent reconciler sweep could expire
    this same lease and act on it between that check and this transaction's
    own commit, letting an already-abandoned worker's results land anyway. An
    UPDATE here takes the same row lock the reconciler's
    SELECT ... FOR UPDATE SKIP LOCKED contends for, so the two correctly
    serialize: whichever transaction locks the row first commits its
    decision before the other's WHERE clause is even (re-)evaluated."""
    lease_expiry = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    fenced = session.execute(
        update(WorkItemRow)
        .where(WorkItemRow.id == work_item_id, WorkItemRow.worker_id == worker_id, WorkItemRow.generation == generation, WorkItemRow.state == "leased")
        .values(lease_expiry=lease_expiry)
        .returning(WorkItemRow.id)
    ).scalar_one_or_none()
    return fenced is not None


def record_candidate(
    session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, attempt_id: uuid.UUID,
    candidate: CandidateOutcome, lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> uuid.UUID | None:
    """Artifact-first step of the engineering phase: persist the collected
    candidate BEFORE the engineering work item is marked done. If the caller
    crashes between this commit and advance_to_verification(), the
    reconciler finds this row and advances straight to verification instead
    of wastefully repeating engineering (EX-03, extended by ENG015-007 to the
    engineering/verification boundary specifically).

    Returns the new candidate row's id, or None if this worker/generation no
    longer holds the lease (fenced out - the caller must not proceed).

    Idempotent under an ambiguous commit outcome (review finding #5): if a
    caller retries after a commit that actually succeeded but whose
    acknowledgement was lost (a dropped connection, a killed worker that
    restarts and replays the same call), `uq_candidate_attempt_tree` turns
    the retry's insert into an IntegrityError rather than a second row - that
    error is caught here and the already-recorded row's id is returned
    instead of raising, so a retry lands on the same identity a fresh insert
    would have, rather than crashing the caller."""
    if not _fenced_lease_touch(session, work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_seconds=lease_seconds):
        session.rollback()
        return None
    candidate_row = CandidateRow(
        attempt_id=attempt_id, tree_digest=candidate.tree_digest, manifest_digest=candidate.manifest_digest,
        validation_status=candidate.validation_status, stored_candidate=candidate.stored_candidate,
    )
    session.add(candidate_row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing_id = session.execute(
            select(CandidateRow.id).where(CandidateRow.attempt_id == attempt_id, CandidateRow.tree_digest == candidate.tree_digest)
        ).scalar_one_or_none()
        if existing_id is None:
            raise
        return existing_id
    session.commit()
    return candidate_row.id


def advance_to_verification(session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, attempt_id: uuid.UUID) -> bool:
    """Marks the engineering work item done (its candidate is already durably
    persisted by record_candidate - this is the engineering phase's own
    terminal transition, not the whole attempt's) and enqueues a new,
    independently-leased `verification` work item for the SAME attempt - not
    a new attempt, just the next phase of this one. Fenced the same way as
    every other transition here: a stale engineering worker returning after
    its lease was reassigned must not be able to advance a replacement
    attempt's work item."""
    result = session.execute(
        update(WorkItemRow)
        .where(WorkItemRow.id == work_item_id, WorkItemRow.worker_id == worker_id, WorkItemRow.generation == generation, WorkItemRow.state == "leased")
        .values(state="done")
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.execute(update(AttemptRow).where(AttemptRow.id == attempt_id).values(phase="verifying"))
    session.add(WorkItemRow(attempt_id=attempt_id, type="verification", state="ready"))
    session.commit()
    return True


def record_evaluation(
    session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, candidate_id: uuid.UUID,
    evaluation: EvaluationOutcome, lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """Artifact-first step of the verification phase: persist the evaluation
    verdict BEFORE the verification work item is marked done, mirroring
    record_candidate's role in the engineering phase. If the caller crashes
    between this commit and finalize(), the reconciler finds this row and
    finalizes from it rather than repeating verification.

    Idempotent the same way record_candidate() is (review finding #5): a
    retry after an ambiguous commit hits `uq_evaluation_plan_digest` instead
    of inserting a duplicate row; that IntegrityError is caught and treated
    as success (the evaluation this call wanted recorded already is) rather
    than raised."""
    if not _fenced_lease_touch(session, work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_seconds=lease_seconds):
        session.rollback()
        return False
    session.add(
        EvaluationRow(
            candidate_id=candidate_id, evaluator_id=evaluation.evaluator_id, fixture_id=evaluation.fixture_id,
            schedule_digest=evaluation.schedule_digest, verdict=evaluation.verdict, result=evaluation.result,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.execute(
            select(EvaluationRow.id).where(
                EvaluationRow.candidate_id == candidate_id,
                EvaluationRow.evaluator_id == evaluation.evaluator_id,
                EvaluationRow.fixture_id == evaluation.fixture_id,
                EvaluationRow.schedule_digest == evaluation.schedule_digest,
            )
        ).scalar_one_or_none()
        return existing is not None
    return True


@dataclass(frozen=True)
class LoadedCandidate:
    candidate_id: uuid.UUID
    stored_candidate: dict
    tree_digest: str
    manifest_digest: str


def load_stored_candidate(session: Session, attempt_id: uuid.UUID) -> LoadedCandidate | None:
    """Read back the persisted candidate for an attempt - id, its serialized
    StoredCandidate JSON, and the authoritative digests CandidateRow itself
    recorded when the engineering phase committed - so an independently-leased
    verification phase can both reconstruct the candidate AND check that the
    reconstructed manifest actually matches the identity this row claims
    (review finding #3), rather than trusting stored_candidate JSON blindly.
    Returns None if no candidate has been recorded (should not happen:
    verification is only ever enqueued after record_candidate succeeds),
    letting the caller fail safe rather than crash on a KeyError."""
    row = session.execute(
        select(CandidateRow).where(CandidateRow.attempt_id == attempt_id).order_by(CandidateRow.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return LoadedCandidate(
        candidate_id=row.id, stored_candidate=row.stored_candidate, tree_digest=row.tree_digest, manifest_digest=row.manifest_digest,
    )


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
    advanced: int = 0
    requeued: int = 0
    orphaned_attempt_ids: tuple[uuid.UUID, ...] = ()


def reconcile_expired_leases(session: Session, *, default_max_replacements: int = 2) -> ReconciliationSummary:
    """Recover work items whose lease expired without a fenced finalize.
    Handles both work-item types, each recovered according to what that
    phase's own artifact-first evidence shows (ENG015-007):

    - `engineering`, candidate already persisted (record_candidate succeeded
      before the worker died): advance straight to verification - engineering
      is never repeated once its output already exists (EX-03).
    - `engineering`, no candidate: replaced with a brand new attempt, up to
      the frozen campaign's max_replacements policy; beyond that the trial
      is left unresolved rather than silently retried forever.
    - `verification`, evaluation already persisted (record_evaluation
      succeeded before the verifier died): finalize from that evidence - no
      re-verification, no duplicate scoring.
    - `verification`, no evaluation: requeue a fresh `verification` work item
      for the SAME attempt and candidate - a dead verifier never causes
      engineering to repeat - up to the same max_replacements cap; beyond
      that the trial is left unresolved.

    `orphaned_attempt_ids` names every attempt this same locked pass touched
    - the caller (reconciler.py) uses that list, not a separate later read,
    to know which local work directories (`engineer/`, `build/`, whichever
    exists) are now safe to delete; deleting both unconditionally is safe
    even when only one exists. A row is only "expired" here if it is still
    lease_expiry < now() at the moment this SELECT ... FOR UPDATE SKIP LOCKED
    actually acquires the row lock: a concurrent heartbeat extending the same
    lease either commits first (this row is then simply excluded from
    `expired`, since FOR UPDATE re-reads the current committed row) or blocks
    behind this transaction's lock and then correctly fails its own fenced
    check once this transaction has committed the row as 'failed'. Either
    way, a lease that is genuinely still being renewed can never be the
    source of an orphaned-attempt_id in this list.
    """
    resumed = replaced = exhausted = advanced = requeued = 0
    orphaned: list[uuid.UUID] = []
    expired = session.execute(
        select(WorkItemRow)
        .where(WorkItemRow.type.in_(("engineering", "verification")), WorkItemRow.state == "leased", WorkItemRow.lease_expiry < func.now())
        .with_for_update(skip_locked=True)
    ).scalars().all()
    for item in expired:
        attempt = session.get(AttemptRow, item.attempt_id)
        trial = session.get(TrialRow, attempt.trial_id)
        campaign = session.get(CampaignRow, trial.campaign_id)
        max_replacements = (
            campaign.resolved["protocol"]["max_replacements"] if campaign is not None and campaign.resolved else default_max_replacements
        )
        candidate = session.execute(select(CandidateRow).where(CandidateRow.attempt_id == attempt.id)).scalar_one_or_none()

        if item.type == "engineering":
            if candidate is not None:
                # Artifact-first: the candidate was already durably persisted
                # before this worker died - resume by moving on to
                # verification, never repeat engineering.
                item.state = "done"
                attempt.phase = "verifying"
                session.add(WorkItemRow(attempt_id=attempt.id, type="verification", state="ready"))
                advanced += 1
                orphaned.append(attempt.id)
                continue
            item.state = "failed"
            attempt.phase = "terminal"
            attempt.terminal_status = "infrastructure_invalid"
            orphaned.append(attempt.id)
            attempt_count = session.execute(select(func.count()).select_from(AttemptRow).where(AttemptRow.trial_id == trial.id)).scalar_one()
            if attempt_count - 1 < max_replacements:
                new_attempt = AttemptRow(trial_id=trial.id, number=attempt_count + 1, phase="queued", lease_generation=0)
                session.add(new_attempt)
                session.flush()
                session.add(WorkItemRow(attempt_id=new_attempt.id, type="engineering", state="ready"))
                replaced += 1
            else:
                exhausted += 1
            continue

        # item.type == "verification"
        evaluation = (
            session.execute(select(EvaluationRow).where(EvaluationRow.candidate_id == candidate.id)).scalar_one_or_none()
            if candidate is not None else None
        )
        if evaluation is not None and evaluation.verdict is not None:
            item.state = "done"
            attempt.phase = "terminal"
            attempt.terminal_status = evaluation.verdict
            resumed += 1
            orphaned.append(attempt.id)
            continue
        item.state = "failed"
        orphaned.append(attempt.id)
        verification_attempts = session.execute(
            select(func.count()).select_from(WorkItemRow).where(WorkItemRow.attempt_id == attempt.id, WorkItemRow.type == "verification")
        ).scalar_one()
        if verification_attempts - 1 < max_replacements:
            # Retry verification in place - same attempt, same persisted
            # candidate - a dead verifier never causes engineering to repeat.
            session.add(WorkItemRow(attempt_id=attempt.id, type="verification", state="ready"))
            requeued += 1
        else:
            attempt.phase = "terminal"
            attempt.terminal_status = "infrastructure_invalid"
            exhausted += 1
    session.commit()
    return ReconciliationSummary(
        resumed=resumed, replaced=replaced, exhausted=exhausted, advanced=advanced, requeued=requeued, orphaned_attempt_ids=tuple(orphaned),
    )


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
