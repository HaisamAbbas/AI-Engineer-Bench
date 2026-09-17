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
import json
import re

from aieb_core.models import EntrantRevision, TaskRevision
from aieb_core.models import Trial as TrialContract
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AttemptEventRow,
    AttemptRow,
    CampaignRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    TaskRevisionRow,
    TrialRow,
    WorkItemRow,
    WorkerArtifactBlobRow,
    WorkerArtifactReferenceRow,
)
from ..evidence_integrity import evidence_digest

DEFAULT_LEASE_SECONDS = 60


def append_attempt_event(session: Session, *, attempt_id: uuid.UUID, event_type: str, payload: dict[str, str | int | bool | None]) -> None:
    """Append one ordered, immutable, public-safe lifecycle observation."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", event_type):
        raise ValueError("attempt event type is invalid")
    if any(
        not isinstance(key, str) or len(key) > 128
        or not (value is None or type(value) in (str, int, bool))
        or isinstance(value, str) and len(value) > 2048
        for key, value in payload.items()
    ):
        raise ValueError("attempt event payload is invalid")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 8192:
        raise ValueError("attempt event payload exceeds the size limit")
    sequence = session.execute(
        select(func.coalesce(func.max(AttemptEventRow.sequence), -1) + 1).where(AttemptEventRow.attempt_id == attempt_id)
    ).scalar_one()
    session.add(AttemptEventRow(attempt_id=attempt_id, sequence=sequence, event_type=event_type, payload=payload))


class EnqueueError(ValueError):
    pass


class CandidateConflictError(RuntimeError):
    """A retried record_candidate() call's payload (manifest_digest,
    validation_status, or stored_candidate) differs from the candidate
    already persisted under the identical (attempt_id, tree_digest)
    identity - a real integrity conflict, not a safe idempotent replay of
    the same write (review finding #2). Raised rather than silently
    returning the mismatched existing row's id, which would misrepresent
    what this call's caller believes was actually recorded."""


class EvaluationConflictError(RuntimeError):
    """A retried record_evaluation() call recomputed a DIFFERENT verdict or
    result under the identical (candidate_id, evaluator_id, fixture_id,
    schedule_digest) identity already persisted (review finding #3).
    Deterministic evaluation means an honest replay produces byte-identical
    output; divergence is evaluator nondeterminism or an integrity anomaly,
    not idempotent input. The first persisted evaluation remains
    authoritative (the first valid scored attempt is final, spec section 16);
    the caller surfaces this via a durable audit event and infrastructure-
    invalid finalization instead of silently swallowing the mismatch."""

    def __init__(self, message: str, *, persisted_verdict: str | None, persisted_result: dict | None, reported_verdict: str | None, reported_result: dict | None, schedule_digest: str) -> None:
        super().__init__(message)
        self.persisted_verdict = persisted_verdict
        self.persisted_result = persisted_result
        self.reported_verdict = reported_verdict
        self.reported_result = reported_result
        self.schedule_digest = schedule_digest


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


def append_attempt_event_fenced(
    session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, attempt_id: uuid.UUID,
    event_type: str, payload: dict[str, str | int | bool | None], lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """Append a lifecycle event ONLY while this worker/generation still holds
    the lease, in the same fenced transaction as the lease touch (review
    finding #6).

    Trace writes are authoritative evidence, so a stale worker returning after
    its lease was reassigned must not be able to mutate the trace. The fenced
    UPDATE takes the same work_item row lock the reconciler's
    SELECT ... FOR UPDATE SKIP LOCKED contends for, so an already-fenced
    worker's event write is refused (returns False, nothing committed) instead
    of racing in after reassignment. Returns True when the event was appended
    and committed. Sequence collisions between genuinely concurrent writers
    are a hard error at the database (uq_attempt_event_sequence), never a
    silently overwritten event.
    """
    if not _fenced_lease_touch(session, work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_seconds=lease_seconds):
        session.rollback()
        return False
    append_attempt_event(session, attempt_id=attempt_id, event_type=event_type, payload=payload)
    session.commit()
    return True


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

    Idempotent under an ambiguous commit outcome (review finding #5/#2): if a
    caller retries after a commit that actually succeeded but whose
    acknowledgement was lost (a dropped connection, a killed worker that
    restarts and replays the same call), `uq_candidate_attempt_tree` turns
    the retry's insert into an IntegrityError rather than a second row. The
    existing row's FULL payload (manifest_digest, validation_status,
    stored_candidate) is compared against this call's - not just the
    (attempt_id, tree_digest) identity the unique constraint itself checks -
    since two different candidates could in principle share a tree_digest
    collision, or a bug elsewhere could commit inconsistent fields under the
    same identity. A byte-for-byte match is a safe idempotent replay and
    returns the existing row's id; any mismatch is a genuine integrity
    conflict and raises CandidateConflictError rather than silently
    returning an id whose actual persisted content differs from what this
    call believed it was recording."""
    if not _fenced_lease_touch(session, work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_seconds=lease_seconds):
        session.rollback()
        return None
    candidate_row = CandidateRow(
        attempt_id=attempt_id, tree_digest=candidate.tree_digest, manifest_digest=candidate.manifest_digest,
        validation_status=candidate.validation_status, stored_candidate=candidate.stored_candidate,
        stored_candidate_digest=evidence_digest(candidate.stored_candidate),
    )
    session.add(candidate_row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.execute(
            select(CandidateRow).where(CandidateRow.attempt_id == attempt_id, CandidateRow.tree_digest == candidate.tree_digest)
        ).scalar_one_or_none()
        if existing is None:
            raise
        if (
            existing.manifest_digest != candidate.manifest_digest
            or existing.validation_status != candidate.validation_status
            or existing.stored_candidate != candidate.stored_candidate
            or existing.stored_candidate_digest != evidence_digest(candidate.stored_candidate)
        ):
            raise CandidateConflictError(
                f"attempt {attempt_id} tree_digest {candidate.tree_digest} is already recorded with different content"
            )
        return existing.id
    append_attempt_event(
        session,
        attempt_id=attempt_id,
        event_type="candidate.collected",
        payload={
            "changed_files": len(candidate.stored_candidate.get("file_references", [])),
            "engineering_output_captured": bool(candidate.stored_candidate.get("engineering_stdout") or candidate.stored_candidate.get("engineering_stderr")),
        },
    )
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


@dataclass(frozen=True)
class RecordedEvaluation:
    """The AUTHORITATIVE persisted evaluation after a record_evaluation()
    call - always reflecting what is actually committed in the database,
    whether this call wrote it just now (`newly_recorded=True`) or it was
    already there under the same identity from an earlier call
    (`newly_recorded=False`). Callers MUST finalize using `verdict`/`result`
    from here, never from their own locally-computed outcome (review finding
    #2): a retried verification that recomputes a DIFFERENT verdict for the
    same (candidate_id, evaluator_id, fixture_id, schedule_digest) identity
    must not let that later, unpersisted computation override the first one
    actually written - the first persisted evaluation is the one that
    counts, by the same artifact-first principle as everything else here."""

    evaluation_id: uuid.UUID
    verdict: str | None
    result: dict | None
    newly_recorded: bool


def record_evaluation(
    session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, candidate_id: uuid.UUID,
    evaluation: EvaluationOutcome, lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> RecordedEvaluation | None:
    """Artifact-first step of the verification phase: persist the evaluation
    verdict BEFORE the verification work item is marked done, mirroring
    record_candidate's role in the engineering phase. If the caller crashes
    between this commit and finalize(), the reconciler finds this row and
    finalizes from it rather than repeating verification.

    Returns None only if this worker/generation no longer holds the lease
    (fenced out). Otherwise ALWAYS returns the authoritative persisted
    RecordedEvaluation - a retry after an ambiguous commit hits
    `uq_evaluation_plan_digest` instead of inserting a duplicate row; that
    IntegrityError is caught, and the row already there is returned as the
    authoritative result (review finding #2) rather than the caller's own
    (possibly different) retry payload being silently treated as if it had
    been written.

    Idempotency vs conflict (review finding #3): a retry whose verdict AND
    result match the persisted row is a genuine idempotent replay and
    returns it as-is. A retry that recomputes a DIFFERENT verdict or result
    under the same identity is evaluator nondeterminism or an integrity
    anomaly, never idempotent input: the persisted row stays authoritative
    (the first valid scored attempt is final), and the mismatch is raised as
    EvaluationConflictError so the caller can surface it as a durable audit
    event instead of laundering it through the replay path (record_candidate
    already does the equivalent via CandidateConflictError)."""
    if not _fenced_lease_touch(session, work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_seconds=lease_seconds):
        session.rollback()
        return None
    row = EvaluationRow(
        candidate_id=candidate_id, evaluator_id=evaluation.evaluator_id, fixture_id=evaluation.fixture_id,
        schedule_digest=evaluation.schedule_digest, verdict=evaluation.verdict, result=evaluation.result,
    )
    session.add(row)
    attempt_id = session.execute(select(CandidateRow.attempt_id).where(CandidateRow.id == candidate_id)).scalar_one()
    append_attempt_event(
        session, attempt_id=attempt_id, event_type="evaluation.recorded",
        payload={"verdict": evaluation.verdict or "unscored", "checks": len(evaluation.result.get("checks", {})) if isinstance(evaluation.result, dict) and isinstance(evaluation.result.get("checks"), dict) else 0},
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.execute(
            select(EvaluationRow).where(
                EvaluationRow.candidate_id == candidate_id,
                EvaluationRow.evaluator_id == evaluation.evaluator_id,
                EvaluationRow.fixture_id == evaluation.fixture_id,
                EvaluationRow.schedule_digest == evaluation.schedule_digest,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        if existing.verdict != evaluation.verdict or existing.result != evaluation.result:
            raise EvaluationConflictError(
                f"evaluation identity (candidate {candidate_id}, evaluator {evaluation.evaluator_id}, "
                f"fixture {evaluation.fixture_id}, schedule {evaluation.schedule_digest}) is already recorded "
                "with a different verdict or result",
                persisted_verdict=existing.verdict,
                persisted_result=existing.result,
                reported_verdict=evaluation.verdict,
                reported_result=evaluation.result,
                schedule_digest=evaluation.schedule_digest,
            ) from None
        return RecordedEvaluation(evaluation_id=existing.id, verdict=existing.verdict, result=existing.result, newly_recorded=False)
    return RecordedEvaluation(evaluation_id=row.id, verdict=row.verdict, result=row.result, newly_recorded=True)


@dataclass(frozen=True)
class LoadedCandidate:
    candidate_id: uuid.UUID
    stored_candidate: dict
    stored_candidate_digest: str
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
        candidate_id=row.id, stored_candidate=row.stored_candidate, stored_candidate_digest=row.stored_candidate_digest,
        tree_digest=row.tree_digest, manifest_digest=row.manifest_digest,
    )


def attach_candidate_references(session: Session, *, attempt_id: uuid.UUID, candidate_id: uuid.UUID) -> int:
    """Promote the artifact references an engineering phase created for this
    attempt from anonymous staging to committed evidence (review finding #2:
    the previous schema had no candidate linkage, so nothing distinguished a
    candidate's live references from an orphaned staging leftover).

    Called right after record_candidate() commits, in the same fenced lease:
    - sets worker_artifact_reference.candidate_id on every reference whose
      access_scope matches this attempt (the engineering phase passes
      str(attempt_id) as the store's access_scope);
    - flips each referenced blob's retention_class from 'staging' to
      'evidence' and clears its staged_until, so the 24-hour orphan purge
      (purge_expired_worker_artifacts) can NEVER delete a blob a committed
      candidate still references (spec section 37).

    Returns the number of references claimed. Legacy references predating
    the candidate_id column match by access_scope exactly as before.
    """
    refs = session.execute(
        select(WorkerArtifactReferenceRow).where(
            WorkerArtifactReferenceRow.candidate_id.is_(None),
            WorkerArtifactReferenceRow.access_scope == str(attempt_id),
        )
    ).scalars().all()
    claimed = 0
    for ref in refs:
        ref.candidate_id = candidate_id
        claimed += 1
        blob = session.get(WorkerArtifactBlobRow, ref.blob_sha256)
        if blob is not None and blob.retention_class == "staging":
            blob.retention_class = "evidence"
            blob.staged_until = None
    session.flush()
    return claimed


def purge_expired_worker_artifacts(session: Session, *, now: datetime | None = None) -> int:
    """Delete staging worker_artifact_blob rows whose staged_until has passed,
    implementing spec section 37's "orphaned unreferenced staging objects
    expire after 24 hours by default; committed evidence is never deleted by
    that cleanup job".

    Guardrails:
    - only blobs with retention_class = 'staging' are ever considered;
    - 'evidence' blobs (claimed by a committed candidate via
      attach_candidate_references) are invisible to this job by class, and
      that claim is exactly what protects "shared blobs with remaining live
      references" (spec section 38) - see below.

    ANY worker_artifact_reference row still pointing at an expired STAGING
    blob is, by construction, an unclaimed/orphaned one (review finding #4):
    attach_candidate_references always flips a blob's retention_class to
    'evidence' in the SAME call that sets candidate_id on a reference, so a
    blob that is still 'staging' can never have a claimed (candidate_id-set)
    reference - every reference on it is orphaned. A prior version treated
    ANY reference (claimed or not) as protection and skipped deletion
    entirely, but `collect_candidate` always creates a blob AND a reference
    together in the same call - a real orphan (a worker that died between
    collection and record_candidate, or one that was never going to be
    claimed at all) therefore ALWAYS has a reference, so that version never
    purged a single real orphan in practice. Fixed: an expired staging
    blob's own (necessarily orphaned) references are deleted first (the
    'worker_artifact_reference.blob_sha256' FK requires this order), then
    the blob itself.

    Returns the number of blobs removed. Runs from the reconciler, so a
    candidate collected but never recorded (its worker died before
    record_candidate) is reclaimed by this job instead of accumulating in
    the control-plane database indefinitely (review finding #2, kept
    correct for real by review finding #4).
    """
    current_time = now if now is not None else datetime.now(timezone.utc)
    expired = session.execute(
        select(WorkerArtifactBlobRow)
        .where(
            WorkerArtifactBlobRow.retention_class == "staging",
            WorkerArtifactBlobRow.staged_until.isnot(None),
            WorkerArtifactBlobRow.staged_until < current_time,
        )
        .with_for_update(skip_locked=True)
    ).scalars().all()
    removed = 0
    for blob in expired:
        orphan_references = session.execute(
            select(WorkerArtifactReferenceRow).where(WorkerArtifactReferenceRow.blob_sha256 == blob.sha256)
        ).scalars().all()
        for reference in orphan_references:
            session.delete(reference)
        # Flush the reference deletes before deleting the blob: there is no
        # declared ORM relationship() between these two mapped classes (only
        # a plain ForeignKey column), so the unit of work does not know to
        # order these deletes by that dependency on its own - an unflushed
        # delete(blob) queued alongside them can otherwise be emitted first
        # and violate the FK.
        session.flush()
        session.delete(blob)
        removed += 1
    session.commit()
    return removed


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
    append_attempt_event(
        session, attempt_id=attempt_id, event_type="attempt.terminal",
        payload={"terminal_status": terminal_status, "completed": done},
    )
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
    worker_artifacts_purged: int = 0


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
                # Claim this candidate's artifact references as committed
                # evidence, exactly as execute_leased_engineering does right
                # after record_candidate() in the normal (non-crashed) path
                # (review finding #4): a worker that dies between committing
                # record_candidate and calling attach_candidate_references
                # otherwise leaves those references permanently
                # candidate_id=NULL / retention_class='staging' even though a
                # real candidate now exists and verification is about to
                # consume it - vulnerable to the 24-hour staging purge ever
                # running before anyone claims them.
                attach_candidate_references(session, attempt_id=attempt.id, candidate_id=candidate.id)
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
        resumed=resumed, replaced=replaced, exhausted=exhausted, advanced=advanced, requeued=requeued,
        orphaned_attempt_ids=tuple(orphaned),
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
