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

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Sequence

from aieb_core.models import EntrantRevision, TaskRevision
from aieb_core.models import Trial as TrialContract
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AttemptCredentialRow,
    AttemptEventRow,
    AttemptModelIdentityRow,
    AttemptRow,
    CampaignRow,
    CandidateRow,
    EntrantRevisionRow,
    EvaluationRow,
    KillSwitchRow,
    MetricCounterRow,
    SystemFenceRow,
    TaskRevisionRow,
    TrialRow,
    UsageReceiptRow,
    UsageRequestRow,
    User,
    WorkItemRow,
    WorkerArtifactBlobRow,
    WorkerArtifactReferenceRow,
)
from ..evidence_integrity import evidence_digest
from .metrics import inc_counter

DEFAULT_LEASE_SECONDS = 60


def record_metric_counter(session: Session, name: str, amount: int = 1) -> None:
    """Atomically persist a worker/API-wide monotonic observability counter."""
    if amount < 0:
        raise ValueError("metric counters may only increase")
    statement = pg_insert(MetricCounterRow).values(name=name, value=amount)
    statement = statement.on_conflict_do_update(
        index_elements=[MetricCounterRow.name],
        set_={"value": MetricCounterRow.value + amount, "updated_at": func.now()},
    )
    session.execute(statement)

# ENG-020 auto-pause (spec sections 39/48): distinct from the global kill switch below.
AUTO_PAUSE_INFRASTRUCTURE_FAILURE_THRESHOLD = 3

# ENG-020 scoped credentials (spec section 37): candidate- and verifier-role, per attempt.
CREDENTIAL_ROLES = ("candidate", "verifier")
DEFAULT_CREDENTIAL_TTL_SECONDS = 3600

# ENG-020 credential issuance is scoped by the WORK-ITEM TYPE that holds the caller's lease
# (codex-audit finding 3, second review round): only an `engineering` work item may mint the
# candidate-role credential, and only a `verification` or `regrade` item may mint the
# verifier-role credential. An item whose type is unknown mints nothing. This closes the
# cross-role hole where an engineering item carrying a verifier request (or a verification
# item a candidate request) would happily mint the wrong actor_role.
_WORK_ITEM_TYPE_ROLES = {
    "engineering": ("candidate",),
    "verification": ("verifier",),
    "regrade": ("verifier",),
}


@dataclass(frozen=True)
class IssuedAttemptCredential:
    """The plaintext token plus its expiry - the ONLY place the plaintext ever
    exists after generation: it is returned once to the caller (delivered to the
    subprocess environment) and only its sha256 hash is persisted."""

    token: str
    expires_at: datetime


class LeaseFenceError(RuntimeError):
    """Raised by issue_attempt_credential when the caller does NOT hold the work-item lease
    it claims for this attempt (worker/generation mismatch, item not leased to them, or lease
    already expired). The credential row is left untouched - a stale or fenced worker must
    never issue, rotate, or revoke a live credential. The caller (a worker) must treat this
    exactly like heartbeat fencing: it no longer legitimately owns the work item, so it must
    stop; the reconciler - which is what fenced it - recovers the item."""


@dataclass(frozen=True)
class AttemptCredentialStatus:
    actor_role: str
    issued_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    valid: bool
    # Lease-fence identity (codex-audit finding 3): which lease issued the live credential.
    work_item_id: uuid.UUID | None = None
    worker_id: str | None = None
    lease_generation: int | None = None
    # Restore-drill fence epoch (gap 4): the system fence epoch the live credential was
    # (re)issued under - dead as soon as the operator advances the epoch after a restore.
    lease_epoch: int | None = None


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
    # ENG-020 restore-drill fencing (gap 4): the system fence epoch this lease was claimed under.
    lease_epoch: int | None = None


def current_fence_epoch(session: Session, *, share_lock: bool = False) -> int:
    """ENG-020 restore-drill fencing (gap 4): the CURRENT system fence epoch. Every fenced
    lease/credential operation requires the row's stored `lease_epoch` to equal this value, so a
    database restore followed by advance_fence_epoch() orphans every pre-restore lease and
    credential immediately.

    With `share_lock=True` the singleton row is locked FOR SHARE for the caller's WHOLE
    transaction (deciding-review finding 1): advance_fence_epoch() takes the same row's EXCLUSIVE
    lock, so PostgreSQL serializes the two - an advance cannot commit between a fenced
    operation's epoch READ and its lease/credential mutation (a stale worker's pre-advance read
    can never be followed by a post-advance commit), and a fenced operation can never observe a
    half-advanced epoch. Access itself is shared, so parallel workers do not contend; only the
    operator's exclusive advance waits. Every fenced lease/credential operation passes
    `share_lock=True` as its first step. Without the flag this is a plain read (informational /
    intros in tests and the drill, never a fencing decision). Reads the singleton row;
    self-heals if it is absent (a fresh schema created without the migration's seed, e.g. a test
    harness) by creating it at 0."""
    stmt = select(SystemFenceRow).where(SystemFenceRow.id == 1)
    if share_lock:
        stmt = stmt.with_for_update(read=True)  # PostgreSQL FOR SHARE - conflicts with the advance's FOR UPDATE
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        session.add(SystemFenceRow(id=1, lease_fence_epoch=0))
        session.flush()
        return 0
    return row.lease_fence_epoch


def advance_fence_epoch(
    session: Session, *, reason: str, activated_by_user_id: uuid.UUID | None = None, commit: bool = True,
) -> int:
    """ENG-020 restore-drill fencing (gap 4): the OPERATOR's post-restore step. Atomically
    increments the singleton fence epoch under its row lock, returning the NEW epoch. Once this
    commits, every lease/credential stamped with an earlier epoch fails every fenced operation
    at first touch - BEFORE the reconciler's next poll - and the reconciler's sweep treats
    those same stale-epoch leases as orphaned even while their lease_expiry is still in the
    future. Monotonic by construction (only ever += 1, serialized by SELECT ... FOR UPDATE).
    """
    row = session.execute(select(SystemFenceRow).where(SystemFenceRow.id == 1).with_for_update()).scalar_one_or_none()
    if row is None:
        raise RuntimeError("system_fence singleton row (id=1) is missing; run migrations before advancing the fence")
    now = datetime.now(timezone.utc)
    row.lease_fence_epoch += 1
    row.reason = reason
    row.activated_by_user_id = activated_by_user_id
    row.activated_at = now
    if commit:
        session.commit()
    else:
        session.flush()
    return row.lease_fence_epoch


class KillSwitchBarrierError(RuntimeError):
    """ENG-020 gap 4 (re-review round 2): the operator fence advance REQUIRES the global kill
    switch to be ACTIVE, atomically with the epoch bump. Raised - with the epoch NOT advanced -
    when the barrier is missing or a concurrent deactivation won the race first."""


def advance_fence_epoch_with_barrier(
    session: Session, *, reason: str, activated_by_user_id: uuid.UUID | None = None, commit: bool = True,
) -> tuple[int, int]:
    """ENG-020 gap 4 (re-review rounds 2 + 3): the OPERATOR advance, made ATOMIC with the
    kill-switch barrier. ONE transaction (1) locks the kill_switch singleton row FOR UPDATE - the
    same lock activate/deactivate_kill_switch take - (2) confirms it is ACTIVE, raising
    KillSwitchBarrierError with the epoch untouched otherwise, then (3) bumps the fence epoch
    under its own FOR UPDATE lock and (4) commits ONCE (`commit=False` leaves both locks open for
    the caller's single outer commit). A concurrent deactivation therefore either wins the
    kill_switch row lock FIRST - this call then sees active=False and refuses with NO epoch
    change - or BLOCKS on that row until this advance has fully committed, so the barrier can
    never disappear between the check and the mutation.

    Returns the (previous_epoch, new_epoch) pair, BOTH read under these same locks (round-3
    blocker: the operator CLI previously read `before` outside this transaction, so a concurrent
    command that committed between that unlocked read and this bump made its own honestly
    committed transition look like a FAILURE - "expected epoch 0 -> 1, observed 2" - recreating
    the "reports failure after its own mutation committed" hazard this epoch exists to remove, and
    inviting a retry that would double-advance the fence). `activated_by_user_id`, when given,
    must reference an existing `users` row - surfaced as ValueError BEFORE any lock or epoch
    change so an operator typo fails cleanly instead of as a raw IntegrityError (the FK would
    catch it too, after the mutation had already been attempted)."""
    kill_switch = session.execute(select(KillSwitchRow).where(KillSwitchRow.id == 1).with_for_update()).scalar_one_or_none()
    if kill_switch is None:
        raise RuntimeError("kill_switch singleton row (id=1) is missing; run migrations before advancing the fence")
    if not kill_switch.active:
        raise KillSwitchBarrierError(
            "the kill switch is not active: the restore runbook requires it (no new dispatch) "
            "before the system fence epoch may advance; nothing was advanced"
        )
    if activated_by_user_id is not None and session.get(User, activated_by_user_id) is None:
        raise ValueError(
            f"activated_by_user_id {activated_by_user_id} does not reference an existing users row; "
            "nothing was advanced"
        )
    fence = session.execute(select(SystemFenceRow).where(SystemFenceRow.id == 1).with_for_update()).scalar_one_or_none()
    if fence is None:
        raise RuntimeError("system_fence singleton row (id=1) is missing; run migrations before advancing the fence")
    before = fence.lease_fence_epoch
    return before, advance_fence_epoch(session, reason=reason, activated_by_user_id=activated_by_user_id, commit=commit)


def _fenced_work_item_where(*, work_item_id: uuid.UUID, worker_id: str, generation: int, lease_fence_epoch: int):
    """Shared WHERE predicates for every fenced lease operation (ENG-015 fencing plus the
    ENG-020 restore-drill fence epoch): the row must still be leased to this worker/generation
    AND stamped with the CURRENT fence epoch (so advancing the epoch after a database restore
    fences a pre-restore lease even while its lease_expiry is still in the future)."""
    return and_(
        WorkItemRow.id == work_item_id,
        WorkItemRow.worker_id == worker_id,
        WorkItemRow.generation == generation,
        WorkItemRow.state == "leased",
        WorkItemRow.lease_epoch == lease_fence_epoch,
    )


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
    # The caller owns the transaction: start must persist the queue, reservation,
    # running state and replayable response together, or roll all of them back.
    session.flush()
    return created


_ATTEMPT_PHASE_FOR_WORK_TYPE = {"engineering": "engineering", "verification": "verifying", "regrade": "verifying"}


def claim_work_item(session: Session, *, worker_id: str, work_type: str | None = None, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> LeasedWork | None:
    """Acquire one ready work item atomically. SKIP LOCKED lets two contending
    workers each get a different item (or none) without blocking on each
    other. `work_type=None` (the default) claims whatever is ready across
    both `engineering` and `verification` queues - a real worker pool
    services both phases, not a phase-dedicated one; pass an explicit
    `work_type` only to isolate one phase's queue (as some tests do)."""
    if is_kill_switch_active(session):
        # ENG-020 (spec sections 39/48): the global kill switch stops ALL new dispatch
        # platform-wide, independent of any one campaign's own state. Cancellation/regrade
        # queue eligibility below is irrelevant once this is active - nothing new claims.
        session.commit()
        return None
    # Only running campaigns dispatch ordinary work. Cancellation claims drain
    # through the worker's cancellation path; regrades run against terminal
    # campaigns. Draft/frozen/paused work must never become executable merely
    # because a ready row exists. Keep row locking scoped to work_item.
    eligible = (
        select(WorkItemRow.id)
        .join(AttemptRow, AttemptRow.id == WorkItemRow.attempt_id)
        .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .join(CampaignRow, CampaignRow.id == TrialRow.campaign_id)
        .where(
            CampaignRow.state.in_(("running", "cancelling", "cancelled"))
            | ((WorkItemRow.type == "regrade") & CampaignRow.state.in_(("completed", "incomplete")))
        )
    )
    query = select(WorkItemRow.id).where(WorkItemRow.state == "ready", WorkItemRow.id.in_(eligible))
    if work_type is not None:
        query = query.where(WorkItemRow.type == work_type)
    candidate = query.order_by(WorkItemRow.id).with_for_update(skip_locked=True).limit(1).cte("candidate")
    heartbeat_at = datetime.now(timezone.utc)
    lease_expiry = heartbeat_at + timedelta(seconds=lease_seconds)
    lease_fence_epoch = current_fence_epoch(session, share_lock=True)
    leased = session.execute(
        update(WorkItemRow)
        .where(WorkItemRow.id == candidate.c.id)
        .values(
            state="leased", worker_id=worker_id, generation=WorkItemRow.generation + 1,
            lease_expiry=lease_expiry, last_heartbeat_at=heartbeat_at, lease_epoch=lease_fence_epoch,
        )
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
        lease_epoch=leased.lease_epoch,
    )


def heartbeat(session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """Extend the lease. Returns False if this worker/generation has been fenced out -
    the caller must abort immediately rather than continue engineering."""
    heartbeat_at = datetime.now(timezone.utc)
    lease_expiry = heartbeat_at + timedelta(seconds=lease_seconds)
    result = session.execute(
        update(WorkItemRow)
        .where(_fenced_work_item_where(work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_fence_epoch=current_fence_epoch(session, share_lock=True)))
        .values(lease_expiry=lease_expiry, last_heartbeat_at=heartbeat_at)
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
    correction_run_id: uuid.UUID | None = None


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
    heartbeat_at = datetime.now(timezone.utc)
    lease_expiry = heartbeat_at + timedelta(seconds=lease_seconds)
    fenced = session.execute(
        update(WorkItemRow)
        .where(_fenced_work_item_where(work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_fence_epoch=current_fence_epoch(session, share_lock=True)))
        .values(lease_expiry=lease_expiry, last_heartbeat_at=heartbeat_at)
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
        .where(_fenced_work_item_where(work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_fence_epoch=current_fence_epoch(session, share_lock=True)))
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
        correction_run_id=evaluation.correction_run_id,
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


class CredentialDeniedError(RuntimeError):
    """Raised by load_stored_candidate_authorized when the presented scoped credential is
    not a valid live credential for the exact (attempt_id, actor_role) requested - the
    credential-gated candidate read is just as scoped as the endpoint that exposes it. The
    presenter (a worker in the verification phase, or the endpoint's verifier route) must
    actually HOLD a valid credential to consume the capability, never a bare DB session."""


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


def load_stored_candidate_authorized(
    session: Session, *, attempt_id: uuid.UUID, actor_role: str, token: str,
) -> LoadedCandidate | None:
    """CREDENTIAL-GATED candidate read (codex-audit finding 3, second review round): the
    persisted-candidate capability - the SAME gate the HTTP endpoint enforces - requires a
    valid live scoped credential for exactly this attempt and exactly this role, verified by
    the exact same verify_attempt_credential check before any candidate row is returned.
    Returns None when no candidate is persisted (caller fails safe); raises
    CredentialDeniedError when the presented credential is not a valid live one for this
    attempt/role - a worker's bare database session can never bypass the gate by calling
    load_stored_candidate directly. Verification consumes this capability: it issues its
    verifier credential first, then reads the candidate THROUGH the credential."""
    if not verify_attempt_credential(session, attempt_id=attempt_id, actor_role=actor_role, token=token):
        raise CredentialDeniedError(
            f"credential-gated candidate read refused: no valid live {actor_role!r} credential for attempt {attempt_id}"
        )
    return load_stored_candidate(session, attempt_id)


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
    if removed:
        record_metric_counter(session, "aieb_reconciler_worker_artifacts_purged_total", removed)
    session.commit()
    return removed


def finalize(session: Session, *, work_item_id: uuid.UUID, worker_id: str, generation: int, attempt_id: uuid.UUID, terminal_status: str, done: bool) -> bool:
    """Fenced, atomic transition to a terminal work-item/attempt state. Returns False
    if this worker/generation no longer holds the lease - a stale worker returning
    after reassignment (or a duplicate finalize call) cannot commit results (EX-04)."""
    result = session.execute(
        update(WorkItemRow)
        .where(_fenced_work_item_where(work_item_id=work_item_id, worker_id=worker_id, generation=generation, lease_fence_epoch=current_fence_epoch(session, share_lock=True)))
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
    """Recover work items whose lease EXPIRED without a fenced finalize OR whose lease is
    stamped with an OLDER fence epoch than the current one (gap 4: the operator advances the
    system fence epoch immediately after restoring the database from a backup, and every
    pre-restore lease - even one whose lease_expiry is still in the future, which a still-
    heartbeating stale worker would otherwise keep renewable forever - is swept here on the
    next poll).
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
    lease_fence_epoch = current_fence_epoch(session, share_lock=True)
    expired = session.execute(
        select(WorkItemRow)
        .where(
            WorkItemRow.type.in_(("engineering", "verification", "regrade")),
            WorkItemRow.state == "leased",
            (WorkItemRow.lease_expiry < func.now()) | (WorkItemRow.lease_epoch != lease_fence_epoch),
        )
        .with_for_update(skip_locked=True)
    ).scalars().all()
    for item in expired:
        attempt = session.get(AttemptRow, item.attempt_id)
        # Lease-recovery gate for scoped credentials (codex-audit finding 3): the worker that
        # held this item is DEAD (its lease lapsed without a fenced finalize), so any credential
        # it issued is desalting immediately. Without this sweep a crashed worker's token stayed
        # usable up to its 1h TTL by whoever held it. Participation in this same locked,
        # single-commit transaction (commit=False) makes revocation atomic with the recovery.
        revoke_attempt_credentials(session, attempt_id=attempt.id, commit=False)
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
            inc_counter("aieb_attempt_infrastructure_invalid_total")
            record_metric_counter(session, "aieb_attempt_infrastructure_invalid_total")
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

        # item.type == "verification" | "regrade" (codex-audit finding 3, second review round):
        # an expired regrade item is swept identically to verification - its verifier credential
        # was already revoked at the top of this loop, and recovery either resumes from a
        # recorded evaluation or requeues a fresh item of the same type in place.
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
            select(func.count()).select_from(WorkItemRow).where(WorkItemRow.attempt_id == attempt.id, WorkItemRow.type == item.type)
        ).scalar_one()
        if verification_attempts - 1 < max_replacements:
            # Retry verification in place - same attempt, same persisted
            # candidate - a dead verifier never causes engineering to repeat.
            session.add(WorkItemRow(attempt_id=attempt.id, type=item.type, state="ready"))
            requeued += 1
        else:
            attempt.phase = "terminal"
            attempt.terminal_status = "infrastructure_invalid"
            inc_counter("aieb_attempt_infrastructure_invalid_total")
            record_metric_counter(session, "aieb_attempt_infrastructure_invalid_total")
            exhausted += 1
    session.commit()
    return ReconciliationSummary(
        resumed=resumed, replaced=replaced, exhausted=exhausted, advanced=advanced, requeued=requeued,
        orphaned_attempt_ids=tuple(orphaned),
    )


def is_kill_switch_active(session: Session) -> bool:
    """Fails closed: a missing singleton row (id=1) is treated as active (blocks
    dispatch) rather than inactive, matching the fail-closed behavior of the
    status route and the activate/deactivate paths."""
    row = session.get(KillSwitchRow, 1)
    return row is None or row.active


def activate_kill_switch(session: Session, *, activated_by_user_id: uuid.UUID | None, reason: str, commit: bool = True) -> int:
    """ENG-020 (spec sections 39/48): stop ALL new dispatch immediately and request bounded
    teardown of active work by cancelling every non-terminal campaign - reusing the existing,
    already-tested cancellation/drain machinery (in-flight work finishes or is cooperatively
    interrupted, then reconciled) rather than a second, novel teardown mechanism. Distinct
    from per-campaign auto-pause: this is global and not tied to any one campaign's health.
    Returns the number of campaigns whose teardown was requested by this call.

    Raises ValueError if the switch is already active - the check and the state
    transition are atomic under the singleton row's FOR UPDATE lock, so concurrent
    callers are serialized by PostgreSQL rather than by application-level timing.

    When `commit=False`, the kill_switch row and all campaign cancellations are staged (flushed)
    but NOT committed - the caller owns the outer transaction so the state transition can be
    committed atomically with an idempotency record (the route's API-01 transaction rule)."""
    row = session.execute(select(KillSwitchRow).where(KillSwitchRow.id == 1).with_for_update()).scalar_one_or_none()
    if row is None:
        raise RuntimeError("kill_switch singleton row (id=1) is missing; run migrations before changing the kill switch")
    if row.active:
        raise ValueError("the kill switch is already active")
    row.active = True
    row.reason = reason
    row.activated_by_user_id = activated_by_user_id
    row.activated_at = datetime.now(timezone.utc)
    session.flush()
    campaign_ids = session.execute(
        select(CampaignRow.id).where(CampaignRow.state.in_(("frozen", "running", "paused")))
    ).scalars().all()
    # commit=False here regardless of the outer `commit`: committing per-campaign would
    # release the FOR UPDATE lock taken above before all campaigns are cancelled, letting
    # a concurrent deactivate race in mid-teardown. The single commit/flush below covers
    # the row activation and every cancellation atomically.
    requested = sum(1 for campaign_id in campaign_ids if cancel_campaign(session, campaign_id, commit=False))
    if commit:
        session.commit()
    else:
        session.flush()
    return requested


def deactivate_kill_switch(session: Session, commit: bool = True) -> bool:
    """Clears the flag only - it does NOT resume any campaign the kill switch drove to
    'cancelling'/'cancelled', and it does NOT clear any campaign's own `auto_paused` flag.
    Both are separate, deliberate operator decisions (spec sections 39/48).

    Returns True if the switch was active and is now deactivated, False if it was
    already inactive (idempotent no-op). The check and transition are atomic under
    the singleton row's FOR UPDATE lock.

    When `commit=False`, the kill_switch row is staged (flushed) but NOT committed - the
    caller owns the outer transaction so the state transition can be committed atomically
    with an idempotency record (the route's API-01 transaction rule)."""
    row = session.execute(select(KillSwitchRow).where(KillSwitchRow.id == 1).with_for_update()).scalar_one_or_none()
    if row is None:
        raise RuntimeError("kill_switch singleton row (id=1) is missing; run migrations before changing the kill switch")
    if not row.active:
        if commit:
            session.commit()
        else:
            session.flush()
        return False
    row.active = False
    if commit:
        session.commit()
    else:
        session.flush()
    return True


# ---------------------------------------------------------------------------
# ENG-023 usage-accounting closure: real, authoritative UsageRequestRow /
# UsageReceiptRow / AttemptModelIdentityRow writers. These are the ONLY
# production code path (as of this change) that ever inserts a real usage
# row - previously `UsageRequestRow(` was constructed only in this module's
# own class definition and in test fixtures (a genuine, pre-existing,
# cross-track gap; see docs/implementation/evidence/ENG-023/README.md).
#
# `aieb_runner` (and specifically model_loop.py) intentionally has no
# dependency on `aieb_api` - these functions are the `aieb_api`-side half of
# that seam. A caller (a worker, or a smoke script standing in for one)
# collects plain-dict usage-receipt/identity payloads from the loop's
# `usage_sink` callback and passes them here to actually persist them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageReceiptInput:
    """One physical provider call's usage, keyed to its logical `request_id`
    (several physical retries of the same logical request share a
    `request_id` and are distinguished by `physical_retry`)."""

    request_id: str
    physical_retry: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    reported_cost_usd: str | None = None
    estimated_cost_usd: str | None = None


class UsageRequestConflictError(RuntimeError):
    """A retried record_usage_receipts() call reported a DIFFERENT attempt_id
    for a request_id already recorded under the same (actor_role, request_id)
    identity (`uq_usage_request_scoped_identity`) - the same idiom
    record_candidate() uses for `CandidateConflictError`: a genuine identity
    conflict, never silently laundered through the idempotent-replay path."""


class UsageReceiptConflictError(RuntimeError):
    """A retried record_usage_receipts() call reported DIFFERENT token/cost
    figures for a (usage_request_id, physical_retry) pair already recorded
    (`uq_usage_receipt_scoped_identity`) - mirrors UsageRequestConflictError
    above and CandidateConflictError's established idiom."""


def record_usage_receipts(
    session: Session,
    *,
    attempt_id: uuid.UUID | None,
    actor_role: str,
    receipts: Sequence[UsageReceiptInput],
    commit: bool = True,
) -> list[uuid.UUID]:
    """Persist real `UsageRequestRow`/`UsageReceiptRow` rows for one
    attempt/role: one `UsageRequestRow` per distinct `request_id` among
    `receipts`, and one `UsageReceiptRow` per physical retry under it.

    Idempotent the same way `record_candidate`/`record_evaluation` already
    are in this module: an insert that collides with an existing unique
    identity is not immediately treated as an error. Instead, the existing
    row is re-read and compared field-for-field against what this call would
    have written - a byte-for-byte match is a safe replay of an already-
    committed call (e.g. a retried worker after a dropped connection) and is
    accepted silently; any real mismatch raises a conflict error rather than
    misrepresenting what is actually persisted.

    Returns the list of `UsageRequestRow.id`s touched (newly inserted or
    confirmed-identical existing), in the order their `request_id`s first
    appear in `receipts`.
    """
    by_request: dict[str, list[UsageReceiptInput]] = {}
    for receipt in receipts:
        by_request.setdefault(receipt.request_id, []).append(receipt)

    request_ids: list[uuid.UUID] = []
    for request_id, group in by_request.items():
        usage_request = UsageRequestRow(actor_role=actor_role, request_id=request_id, attempt_id=attempt_id)
        session.add(usage_request)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.execute(
                select(UsageRequestRow).where(
                    UsageRequestRow.actor_role == actor_role, UsageRequestRow.request_id == request_id
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            if existing.attempt_id != attempt_id:
                raise UsageRequestConflictError(
                    f"usage_request (actor_role={actor_role!r}, request_id={request_id!r}) is already "
                    f"recorded with attempt_id={existing.attempt_id!r}, not {attempt_id!r}"
                )
            usage_request = existing
        request_ids.append(usage_request.id)

        for receipt in group:
            receipt_row = UsageReceiptRow(
                usage_request_id=usage_request.id,
                physical_retry=receipt.physical_retry,
                reported_cost_usd=receipt.reported_cost_usd,
                estimated_cost_usd=receipt.estimated_cost_usd,
                input_tokens=receipt.input_tokens,
                output_tokens=receipt.output_tokens,
            )
            session.add(receipt_row)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing_receipt = session.execute(
                    select(UsageReceiptRow).where(
                        UsageReceiptRow.usage_request_id == usage_request.id,
                        UsageReceiptRow.physical_retry == receipt.physical_retry,
                    )
                ).scalar_one_or_none()
                if existing_receipt is None:
                    raise
                if (
                    existing_receipt.reported_cost_usd != receipt.reported_cost_usd
                    or existing_receipt.estimated_cost_usd != receipt.estimated_cost_usd
                    or existing_receipt.input_tokens != receipt.input_tokens
                    or existing_receipt.output_tokens != receipt.output_tokens
                ):
                    raise UsageReceiptConflictError(
                        f"usage_receipt (usage_request_id={usage_request.id!r}, "
                        f"physical_retry={receipt.physical_retry!r}) is already recorded with different content"
                    )
    if commit:
        session.commit()
    else:
        session.flush()
    return request_ids


class ModelIdentityConflictError(RuntimeError):
    """A retried record_model_identity() call reported DIFFERENT model
    identity/coverage fields for an (attempt_id, actor_role) pair already
    recorded (`uq_attempt_model_identity_attempt_role`) - same idiom as
    UsageRequestConflictError above."""


def record_model_identity(
    session: Session,
    *,
    attempt_id: uuid.UUID | None,
    actor_role: str,
    requested_model: str,
    reported_model: str | None,
    settings_digest: str | None,
    coverage_label: str,
    commit: bool = True,
) -> uuid.UUID:
    """Persist the real, authoritative `AttemptModelIdentityRow` for one
    attempt/role - which model was requested vs actually reported, the
    settings_digest it was verified against, and the disclosed
    coverage_label. Idempotent on (attempt_id, actor_role), exactly like
    record_usage_receipts above: a byte-identical replay returns the
    existing row's id; a genuine mismatch raises ModelIdentityConflictError.
    """
    row = AttemptModelIdentityRow(
        attempt_id=attempt_id,
        actor_role=actor_role,
        requested_model=requested_model,
        reported_model=reported_model,
        settings_digest=settings_digest,
        coverage_label=coverage_label,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.execute(
            select(AttemptModelIdentityRow).where(
                AttemptModelIdentityRow.attempt_id == attempt_id, AttemptModelIdentityRow.actor_role == actor_role
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        if (
            existing.requested_model != requested_model
            or existing.reported_model != reported_model
            or existing.settings_digest != settings_digest
            or existing.coverage_label != coverage_label
        ):
            raise ModelIdentityConflictError(
                f"attempt_model_identity (attempt_id={attempt_id!r}, actor_role={actor_role!r}) is already "
                "recorded with different content"
            )
        row = existing
    if commit:
        session.commit()
    else:
        session.flush()
    return row.id


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_attempt_credential(
    session: Session, *, attempt_id: uuid.UUID, actor_role: str, work_item_id: uuid.UUID, worker_id: str, lease_generation: int,
    ttl_seconds: int = DEFAULT_CREDENTIAL_TTL_SECONDS,
) -> IssuedAttemptCredential:
    """ENG-020 (spec section 37), lease-fenced (codex-audit finding 3): issue a short-lived,
    per-role, per-attempt token. The unique (attempt_id, actor_role) constraint means a fresh
    issue ROTATES the previous credential for that role (one live credential per role at a
    time), and the plaintext is returned exactly once - the caller delivers it to a subprocess
    environment; only the sha256 hash is stored.

    Issuance is FENCED to the caller's work-item lease: it succeeds only while the
    (work_item_id, worker_id, lease_generation) triple matches the live leased row - FOR THIS
    SAME ATTEMPT - with an unexpired lease, under that row's lock. A stale worker whose lease
    expired or was reassigned (generation bumped by a new claimant) gets LeaseFenceError and
    the credential, current or otherwise, is untouched. The fence identity is stored on the
    row, so the audit can attribute any accepted token to the exact lease that minted it.
    Issuance is additionally scoped by WORK-ITEM TYPE (codex-audit finding 3, second review
    round): only an `engineering` lease may issue the candidate-role credential, only a
    `verification`/`regrade` lease the verifier-role credential - a request whose cross-attempt
    or cross-role identity does not line up is refused, never minted. Worker (candidate/
    verifier) calls only; callers that must impersonate an operator cannot mint one of these
    because that is an entirely different, role-checked token class."""
    if actor_role not in CREDENTIAL_ROLES:
        raise ValueError(f"unknown actor_role {actor_role!r}; expected one of {CREDENTIAL_ROLES}")
    lease_fence_epoch = current_fence_epoch(session, share_lock=True)
    lease = session.execute(
        select(WorkItemRow)
        .where(
            WorkItemRow.id == work_item_id,
            WorkItemRow.attempt_id == attempt_id,
            WorkItemRow.worker_id == worker_id,
            WorkItemRow.generation == lease_generation,
            WorkItemRow.state == "leased",
            WorkItemRow.lease_expiry > datetime.now(timezone.utc),
            WorkItemRow.lease_epoch == lease_fence_epoch,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if lease is None:
        session.rollback()
        raise LeaseFenceError(
            "credential issuance refused: this worker/generation no longer holds the work-item lease "
            f"for this attempt (work_item={work_item_id}, attempt={attempt_id}, worker={worker_id!r}, generation={lease_generation})"
        )
    allowed_roles = _WORK_ITEM_TYPE_ROLES.get(lease.type, ())
    if actor_role not in allowed_roles:
        session.rollback()
        raise ValueError(
            f"credential issuance refused: work item type {lease.type!r} may only mint "
            f"{allowed_roles or 'no'} role credential(s), not {actor_role!r}"
        )
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)
    row = session.execute(
        select(AttemptCredentialRow)
        .where(AttemptCredentialRow.attempt_id == attempt_id, AttemptCredentialRow.actor_role == actor_role)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        session.add(
            AttemptCredentialRow(
                attempt_id=attempt_id,
                actor_role=actor_role,
                token_hash=_hash_token(token),
                expires_at=expires_at,
                work_item_id=work_item_id,
                worker_id=worker_id,
                lease_generation=lease_generation,
                lease_epoch=lease_fence_epoch,
            )
        )
    else:
        row.token_hash = _hash_token(token)
        row.expires_at = expires_at
        row.revoked_at = None
        row.work_item_id = work_item_id
        row.worker_id = worker_id
        row.lease_generation = lease_generation
        row.lease_epoch = lease_fence_epoch
    append_attempt_event(session, attempt_id=attempt_id, event_type=f"credential.{actor_role}.issued", payload={"ttl_seconds": ttl_seconds, "work_item_id": str(work_item_id), "worker_id": worker_id})
    session.commit()
    return IssuedAttemptCredential(token=token, expires_at=expires_at)


def verify_attempt_credential(session: Session, *, attempt_id: uuid.UUID, actor_role: str, token: str) -> bool:
    """ENG-020 (spec section 37): role-scoped, attempt-scoped proof of identity for a presented
    token - the check succeeds only when the exact (attempt_id, actor_role) row exists, is not
    revoked, is not expired, is stamped with the CURRENT fence epoch (gap 4: a credential minted
    before a database restore is dead at first use once the fence epoch was advanced), and hashes
    to the presented token. This is what a trusted VERIFY subprocess (or candidate code) uses to
    authenticate to the control plane as that attempt's verifier (or candidate), never as the
    operator or any other attempt."""
    if actor_role not in CREDENTIAL_ROLES:
        return False
    row = session.execute(
        select(AttemptCredentialRow)
        .where(
            AttemptCredentialRow.attempt_id == attempt_id,
            AttemptCredentialRow.actor_role == actor_role,
            AttemptCredentialRow.lease_epoch == current_fence_epoch(session, share_lock=True),
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    if row.revoked_at is not None:
        return False
    if row.expires_at <= datetime.now(timezone.utc):
        return False
    return secrets.compare_digest(row.token_hash, _hash_token(token))


def _mark_credentials_revoked(session: Session, attempt_id: uuid.UUID, *, actor_roles: Sequence[str], commit: bool) -> int:
    """Internal: flip revoked_at on the matching live credential rows for an attempt. With
    commit=False this participates in the CALLER's transaction (the reconciler must revoke
    both roles inside its single recovery pass); the public wrappers commit."""
    now = datetime.now(timezone.utc)
    updated = session.execute(
        update(AttemptCredentialRow)
        .where(
            AttemptCredentialRow.attempt_id == attempt_id,
            AttemptCredentialRow.actor_role.in_(actor_roles),
            AttemptCredentialRow.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    if commit:
        session.commit()
    return updated.rowcount


def revoke_attempt_credential(
    session: Session, *, attempt_id: uuid.UUID, actor_role: str,
    work_item_id: uuid.UUID, worker_id: str, lease_generation: int,
) -> bool:
    """Revoke the live credential for (attempt_id, actor_role) - what a worker does when its
    phase ends, so the scoped identity dies even before its expiry. FENCED by the issuer's
    stored lease identity (codex-audit finding 3, second review round): only the exact
    (work_item_id, worker_id, lease_generation) under which the credential row was issued may
    revoke it. A stale worker whose delayed finally arrives AFTER a new worker already rotated
    the token now NO-OPS - the current worker's fresh token is untouched - since the row's
    fence identity now belongs to the replacement lease. Idempotent: revoking an
    already-revoked/missing/non-matching credential returns False and changes nothing. The
    reconciler's wholesale sweep uses the separate, unconditional revoke_attempt_credentials
    when the lease actually died."""
    if actor_role not in CREDENTIAL_ROLES:
        return False
    now = datetime.now(timezone.utc)
    updated = session.execute(
        update(AttemptCredentialRow)
        .where(
            AttemptCredentialRow.attempt_id == attempt_id,
            AttemptCredentialRow.actor_role == actor_role,
            AttemptCredentialRow.revoked_at.is_(None),
            AttemptCredentialRow.work_item_id == work_item_id,
            AttemptCredentialRow.worker_id == worker_id,
            AttemptCredentialRow.lease_generation == lease_generation,
            AttemptCredentialRow.lease_epoch == current_fence_epoch(session, share_lock=True),
        )
        .values(revoked_at=now)
    )
    session.commit()
    return updated.rowcount == 1


def revoke_attempt_credentials(session: Session, *, attempt_id: uuid.UUID, commit: bool = True) -> int:
    """Revoke EVERY live credential for the attempt (both candidate and verifier) - what lease
    recovery calls when the work item died without a normal-path revoke (worker crash, lease
    expiry, kill-switch teardown, reconciler sweep), so a dead worker's token is NEVER usable
    for the remainder of its TTL. Returns the number of rows revoked. Non-committing assembly
    for callers that must revoke inside their own transaction (commit=False)."""
    return _mark_credentials_revoked(session, attempt_id, actor_roles=list(CREDENTIAL_ROLES), commit=commit)


def attempt_credential_status(session: Session, *, attempt_id: uuid.UUID, actor_role: str) -> AttemptCredentialStatus:
    """Read-only status of a credential row (or that no credential exists) - for evidence and
    tests; carries no secret, only the hash's presence. `valid` must match what
    verify_attempt_credential() would actually accept (deciding-review finding 2): expiry and
    revocation are necessary but not sufficient - a row minted under an EARLIER fence epoch is
    stale and must report invalid, exactly as verify refuses it at first use, lest operators/tests
    trust a status that no live verification would honor."""
    row = session.execute(
        select(AttemptCredentialRow)
        .where(AttemptCredentialRow.attempt_id == attempt_id, AttemptCredentialRow.actor_role == actor_role)
    ).scalar_one_or_none()
    if row is None:
        return AttemptCredentialStatus(actor_role=actor_role, issued_at=None, expires_at=None, revoked_at=None, valid=False)
    now = datetime.now(timezone.utc)
    current = current_fence_epoch(session)
    valid = row.revoked_at is None and row.expires_at > now and row.lease_epoch == current
    return AttemptCredentialStatus(
        actor_role=row.actor_role, issued_at=row.issued_at, expires_at=row.expires_at, revoked_at=row.revoked_at, valid=valid,
        work_item_id=row.work_item_id, worker_id=row.worker_id, lease_generation=row.lease_generation, lease_epoch=row.lease_epoch,
    )


def record_infrastructure_outcome(session: Session, campaign_id: uuid.UUID, execution_validity: str) -> bool:
    """ENG-020 auto-pause (spec sections 39/48): AUTO_PAUSE_INFRASTRUCTURE_FAILURE_THRESHOLD
    consecutive infrastructure failures pause a running campaign automatically, distinct from
    and independent of the global kill switch above. A scored candidate outcome (valid
    execution, whatever verdict) is NOT an infrastructure failure and resets the counter to
    zero - an ordinary task/candidate failure alone must never trigger this. `auto_paused`
    distinguishes this from a manual operator pause so resume can require explicit review
    (see `routes/campaigns.py::resume_campaign`), not a same click as a manual pause's resume.
    Returns True iff this call transitioned the campaign to paused."""
    campaign = session.execute(
        select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
    ).scalar_one_or_none()
    if campaign is None:
        return False
    if execution_validity != "infrastructure_invalid":
        if campaign.consecutive_infrastructure_failures != 0:
            campaign.consecutive_infrastructure_failures = 0
            session.commit()
        else:
            session.commit()
        return False
    campaign.consecutive_infrastructure_failures += 1
    paused = False
    if (
        campaign.consecutive_infrastructure_failures >= AUTO_PAUSE_INFRASTRUCTURE_FAILURE_THRESHOLD
        and campaign.state == "running"
    ):
        campaign.state = "paused"
        campaign.auto_paused = True
        campaign.auto_pause_reason = (
            f"{AUTO_PAUSE_INFRASTRUCTURE_FAILURE_THRESHOLD} consecutive infrastructure failures - "
            "operator review required before resume"
        )
        paused = True
    session.commit()
    return paused


def cancel_campaign(session: Session, campaign_id: uuid.UUID, commit: bool = True) -> bool:
    """Stop new dispatch. Already-leased work items are left to finish or expire
    naturally; teardown_orphans() cleans up anything left behind by a killed worker.

    Includes 'paused' (fixed 2026-09-18): a paused campaign can still hold outstanding
    leased work, and `activate_kill_switch`'s "every non-terminal campaign" teardown request
    needs it cancellable too - THIS HELPER previously silently no-opped (rowcount 0) for a
    paused campaign, the exact gap a kill switch activated during a pause would have missed.
    `routes/campaigns.py::cancel_campaign_route` was never affected: it issues its own inline
    UPDATE (not a call to this function) whose WHERE clause already included 'paused' - ENG-017's
    accepted cancel-from-paused behavior was correct and unbroken throughout. This was a bug in
    this standalone helper's narrower WHERE clause, not a regression in the HTTP route."""
    result = session.execute(
        update(CampaignRow).where(CampaignRow.id == campaign_id, CampaignRow.state.in_(("frozen", "running", "paused"))).values(state="cancelling")
    )
    if commit:
        session.commit()
    else:
        session.flush()
    return result.rowcount == 1


def _outstanding_work_count(session: Session, campaign_id: uuid.UUID) -> int:
    """Ready or leased work items for a campaign - the work a worker could
    still pick up or is currently executing."""
    return session.execute(
        select(func.count())
        .select_from(WorkItemRow)
        .join(AttemptRow, AttemptRow.id == WorkItemRow.attempt_id)
        .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
        .where(TrialRow.campaign_id == campaign_id, WorkItemRow.state.in_(("ready", "leased")))
    ).scalar_one()


def has_outstanding_work(session: Session, campaign_id: uuid.UUID) -> bool:
    """True iff the campaign still has ready or leased work items. Used by the
    cancel route to finalize a zero-work drain in the caller's transaction."""
    return _outstanding_work_count(session, campaign_id) > 0


def maybe_complete_cancellation(session: Session, campaign_id: uuid.UUID, *, commit: bool = True) -> bool:
    """Once no work remains ready or leased for a cancelling campaign, mark it
    fully cancelled rather than leaving it stuck in 'cancelling' forever."""
    campaign = session.execute(
        select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if campaign is None or campaign.state != "cancelling":
        return False
    if _outstanding_work_count(session, campaign_id) > 0:
        return False
    campaign.state = "cancelled"
    # The reservation stays reserved for the whole drain and is released only
    # now, when nothing ready/leased remains that could still bill spend.
    from .. import budgets
    budgets.set_reservation_status(session, campaign_id, "released")
    if commit:
        session.commit()
    else:
        # HTTP callers commit settlement together with their replay response.
        session.flush()
    return True


def _finalize_terminal_campaign(session: Session, campaign: CampaignRow) -> None:
    """Transition a campaign with no outstanding work to its terminal state
    and settle its reservation exactly once (cancelling -> cancelled with the
    reservation released; running/paused -> completed/incomplete with the
    reservation consumed)."""
    if campaign.state == "cancelling":
        campaign.state = "cancelled"
        from .. import budgets
        budgets.set_reservation_status(session, campaign.id, "released")
        return
    total_trials = session.execute(
        select(func.count()).select_from(TrialRow).where(TrialRow.campaign_id == campaign.id)
    ).scalar_one()
    from ..aggregation import CampaignNotAggregatable, selected_campaign_evaluations

    try:
        resolved_trials = len(selected_campaign_evaluations(session, campaign.id))
    except CampaignNotAggregatable:
        resolved_trials = 0
    campaign.state = "completed" if total_trials > 0 and resolved_trials == total_trials else "incomplete"
    from .. import budgets
    budgets.set_reservation_status(session, campaign.id, "consumed")


def maybe_complete_campaign(session: Session, campaign_id: uuid.UUID) -> bool:
    """Once a running OR paused campaign has no work left ready or leased,
    transition it to a terminal state: `completed` if every planned trial has
    a valid scored terminal attempt, otherwise `incomplete` (spec: incomplete
    coverage is a first-class, publishable outcome).

    Paused campaigns are included: pause only stops NEW dispatch, and a
    paused campaign whose last leased item just finished has no work left to
    trigger this completion from the worker's after-processing path - the
    idle sweep reaches it here instead. Without this, such a campaign could
    never leave `paused` (resuming creates no new work), and its reservation
    would stay `active` forever.

    Resolution is decided by the SAME selection aggregation publishes
    (`selected_campaign_evaluations`): the first terminal attempt whose
    terminal status equals its evaluation's verdict, with `indeterminate`
    never resolving a trial - counting any non-null evaluation (or accepting
    a verdict/terminal-status mismatch) as complete would let an unresolved
    or inconsistent campaign publish a canonical complete rank. This state
    remains a coarse operator-facing signal; publication recomputes the
    authoritative completeness. Marks the budget reservation `consumed`."""
    campaign = session.execute(
        select(CampaignRow).where(CampaignRow.id == campaign_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if campaign is None or campaign.state not in ("running", "paused"):
        return False
    if _outstanding_work_count(session, campaign_id) > 0:
        return False
    _finalize_terminal_campaign(session, campaign)
    session.commit()
    return True


def finalize_stalled_campaigns(session: Session, *, limit: int = 16) -> list[uuid.UUID]:
    """Bounded, concurrency-safe sweep for campaigns whose lifecycle stalled
    with no work left to trigger their completion: `cancelling` drains whose
    last item finished (or which never had work items at all), and
    `running`/`paused` campaigns whose final leased item completed while the
    worker was between after-processing checkpoints (or while paused).

    Candidates are claimed with FOR UPDATE SKIP LOCKED so two idle workers
    sweeping concurrently claim DIFFERENT campaigns, never block each other,
    and never finalize the same row twice; the outstanding-work count is then
    re-checked under the row lock, so a work item a reconciler requeued
    between the candidate SELECT and this transaction can never be raced into
    a premature terminal state. Returns the ids this sweep finalized."""
    if limit <= 0:
        raise ValueError("sweep limit must be positive")
    stalled = session.execute(
        select(CampaignRow).where(
            CampaignRow.state.in_(("cancelling", "running", "paused")),
            ~exists(
                select(WorkItemRow.id)
                .join(AttemptRow, AttemptRow.id == WorkItemRow.attempt_id)
                .join(TrialRow, TrialRow.id == AttemptRow.trial_id)
                .where(WorkItemRow.state.in_(("ready", "leased")), TrialRow.campaign_id == CampaignRow.id)
            ),
        ).order_by(CampaignRow.id).with_for_update(skip_locked=True).limit(limit)
        .execution_options(populate_existing=True)
    ).scalars().all()
    finalized: list[uuid.UUID] = []
    for campaign in stalled:
        campaign_id = campaign.id
        if _outstanding_work_count(session, campaign_id) > 0:
            continue  # re-checked under the row lock: work reappeared, leave it alone
        _finalize_terminal_campaign(session, campaign)
        finalized.append(campaign_id)
    if finalized:
        session.commit()
    return finalized


def is_campaign_cancelling(session: Session, campaign_id: uuid.UUID) -> bool:
    state = session.execute(select(CampaignRow.state).where(CampaignRow.id == campaign_id)).scalar_one_or_none()
    return state in ("cancelling", "cancelled")
