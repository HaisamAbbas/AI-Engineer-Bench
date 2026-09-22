"""Bridge from a leased work item to the proven local attempt pipeline.

Deliberately reuses aieb_runner.lifecycle.LocalAttemptRunner unchanged in
its scoring/candidate logic - this ticket adds leasing and recovery around
it, not a second execution or scoring implementation. A real installed
agent remains blocked on ENG-001's authorization gate, so the "engineering
command" here is the same deterministic candidate-variant editor the local
CLI already uses (aieb_cli.main._editor) for development verticals.
"""

from __future__ import annotations

import sys
import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Annotated, Literal, Mapping
from pathlib import Path
from uuid import uuid4

from aieb_core.models import CandidateManifest, EntrantRevision, ExecutionValidity, SubmissionPolicy, TaskRevision
from aieb_runner.artifacts import ArtifactReference, BlobRef, CandidateDiff, StoredCandidate
from aieb_runner.lifecycle import AttemptConfig, AttemptOutcome, CancelledError, EngineeringCommand, LocalAttemptRunner
from aieb_runner.backends.normalization import HarborResultError, normalize_harbor_result
from aieb_runner.backends.base import CandidateArtifacts
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..evidence_integrity import evidence_digest
from ..models import AuditEventRow, AttemptRow, CampaignRow, EntrantRevisionRow, TaskRevisionRow, TrialRow
from . import repository
from .artifact_store import PostgresArtifactStore
from .repository import CandidateOutcome, EvaluationOutcome, LeasedWork

ROOT = Path(__file__).resolve().parents[5]

# Duplicates aieb_cli.main.TASK_RUNTIMES' (source_dir, evaluator_module) pairs.
# services/api must not depend on aieb-cli (the dependency would run backwards
# per the monorepo layout), so this mapping is intentionally kept in sync by
# hand rather than imported; unifying it into aieb-core is a follow-up, not
# blocking this ticket. The third element is the evaluator's attribute
# (qualname) on that module - carried here as a plain string (codex reviewer
# blocker round) so the hosted worker can hand the isolated VERIFY child a
# (module, qualname) identity WITHOUT importing the evaluator module in its
# own parent process, where the worker's unsanitized secrets would be visible
# to evaluator import-time code before any scrub.
TASK_RUNTIMES: dict[str, tuple[str, str, str]] = {
    "rag.document-freshness": ("knowledge_service", "tests.maintainer.rag01.evaluator", "evaluate"),
    "rag.metadata-filter-topk": ("search_service", "tests.maintainer.rag02.evaluator", "evaluate"),
    "rag.citation-current-span": ("citation_service", "tests.maintainer.rag03.evaluator", "evaluate"),
    "rag.embedding-version": ("embedding_service", "tests.maintainer.rag04.evaluator", "evaluate"),
    "ext.missingness": ("missingness_service", "tests.maintainer.ext01.evaluator", "evaluate"),
    "ext.batch-alignment": ("extraction_service", "tests.maintainer.ext02.evaluator", "evaluate"),
    "ext.unit-normalization": ("unit_service", "tests.maintainer.ext03.evaluator", "evaluate"),
    "ext.partial-batch": ("batch_service", "tests.maintainer.ext04.evaluator", "evaluate"),
    "tool.false-completion": ("workflow_service", "tests.maintainer.tool01.evaluator", "evaluate"),
    "tool.idempotent-write": ("write_service", "tests.maintainer.tool02.evaluator", "evaluate"),
    "tool.session-isolation": ("session_service", "tests.maintainer.tool03.evaluator", "evaluate"),
    "tool.corrected-arguments": ("correction_service", "tests.maintainer.tool04.evaluator", "evaluate"),
}


class UnsupportedTaskError(ValueError):
    pass


class StoredCandidateUnavailableError(ValueError):
    """The persisted candidate.stored_candidate for this attempt is missing or
    does not match the shape _serialize_stored_candidate() writes - either a
    pre-ENG015-007 legacy row (the migration's server_default of '{}' for
    existing rows, review finding #4) or corrupted/hand-edited JSON. Raised
    instead of letting a bare KeyError escape from _deserialize_stored_candidate,
    so the caller can route this attempt to infrastructure_invalid instead of
    crashing the worker process."""


def _attempt_env(attempt_id: uuid.UUID, actor_role: str, token: str) -> dict[str, str]:
    """ENG-020 (spec section 37): the attempt variables delivered to a phase subprocess so
    the contestant/verifier code can present its scoped credential to the control plane."""
    return {
        "AIEB_ATTEMPT_ID": str(attempt_id),
        "AIEB_ATTEMPT_ROLE": actor_role,
        "AIEB_ATTEMPT_CREDENTIAL": token,
    }


def _editor_script(
    task_dir: Path, candidate_variant: str, source_dir: str, script_path: Path, delay_seconds: float = 0,
    extra_env: Mapping[str, str] | None = None,
) -> EngineeringCommand:
    delay = f"import time\ntime.sleep({delay_seconds})\n" if delay_seconds > 0 else ""
    if candidate_variant == "baseline":
        script_path.write_text(delay + "pass\n", encoding="utf-8")
    else:
        source = repr(str(task_dir / candidate_variant / "backend.py"))
        script_path.write_text(
            delay + "from pathlib import Path\nimport shutil\n"
            f"shutil.copyfile({source}, Path.cwd() / {source_dir!r} / 'backend.py')\n",
            encoding="utf-8",
        )
    return EngineeringCommand((sys.executable, str(script_path)), 30, extra_env=dict(extra_env or {}))


@dataclass(frozen=True)
class ExecutionResult:
    finalized: bool
    execution_validity: str
    verdict: str | None


def _heartbeat_loop(session_factory: sessionmaker, leased: LeasedWork, worker_id: str, lease_seconds: int, stop: threading.Event) -> None:
    interval = max(lease_seconds / 3, 1)
    while not stop.wait(interval):
        with session_factory() as session:
            if not repository.heartbeat(session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation, lease_seconds=lease_seconds):
                return  # fenced out; nothing more this worker can legitimately do


def _cancellation_poll_loop(
    session_factory: sessionmaker, campaign_id: uuid.UUID, cancel_event: threading.Event, stop: threading.Event, poll_seconds: float,
) -> None:
    """Poll for campaign cancellation while an attempt is engineering, setting the
    shared cancel_event as soon as it is detected - not merely once, at claim
    time. A cancel issued while a long engineering run is already in flight
    must still interrupt it (finding #6); checking only before dispatch stops
    new work but leaves an already-running attempt to finish or hit its own
    deadline regardless."""
    while not stop.wait(poll_seconds):
        if cancel_event.is_set():
            return
        with session_factory() as session:
            if repository.is_campaign_cancelling(session, campaign_id):
                cancel_event.set()
                return


def _serialize_stored_candidate(stored: StoredCandidate) -> dict:
    """StoredCandidate -> plain JSON, so it can be persisted in
    candidate.stored_candidate and reconstructed by a verification phase
    running in an entirely different process (ENG015-007)."""
    return {
        "manifest": stored.manifest.model_dump(mode="json"),
        "file_references": [
            {
                "path": path,
                "reference": {
                    "id": str(reference.id),
                    "blob": {"sha256": reference.blob.sha256, "byte_length": reference.blob.byte_length},
                    "access_scope": reference.access_scope,
                    "visibility": reference.visibility,
                },
            }
            for path, reference in stored.file_references
        ],
        "diffs": [
            {
                "path": entry.path,
                "operation": entry.operation,
                "unified_diff": entry.unified_diff,
                "binary": entry.binary,
                "truncated": entry.truncated,
                "baseline_available": entry.baseline_available,
            }
            for entry in stored.diffs
        ],
        "engineering_stdout": stored.engineering_stdout,
        "engineering_stderr": stored.engineering_stderr,
        "engineering_logs_truncated": stored.engineering_logs_truncated,
    }


class _StoredBlobEnvelope(BaseModel):
    """Strict (ENG-015 review finding #4): this payload is machine-written by
    _serialize_stored_candidate and read back by an independent verification
    process - there is no legitimate producer of extra fields, stringified
    numbers, or empty digests. Lenient defaults (ignore unknown keys, coerce
    "12" to 12) would let hand-edited or corrupted JSON validate that the
    strict writer never emitted, so unknown fields are rejected and types
    must match exactly. Range constraints (nonnegative length, 64-char
    lowercase hex digest) match what the runner's own BlobRef producers
    write, and downstream artifact-store reads re-verify bytes against the
    digest anyway."""

    model_config = ConfigDict(extra="forbid", strict=True)

    sha256: str = Field(min_length=64, max_length=64, pattern=r"[0-9a-f]{64}")
    byte_length: int = Field(ge=0)


class _StoredReferenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Strict everywhere EXCEPT here: _serialize_stored_candidate writes the
    # id as a JSON string ("id": str(reference.id)), so a UUID field under
    # strict=True would reject the writer's own legitimate output. Field-
    # level strict=False re-enables exactly that one documented coercion
    # (well-formed UUID string -> UUID) while every other type stays exact.
    id: Annotated[uuid.UUID, Field(strict=False)]
    blob: _StoredBlobEnvelope
    access_scope: str = Field(min_length=1)
    # A Literal, not `str = Field(pattern=r"public|restricted")` (ENG-015
    # review finding #6): Pydantic's `pattern` constraint uses `re.match`
    # (matches at the START of the string, not the WHOLE string), so the
    # unanchored alternation accepted values like "public-evil" - reproduced
    # directly. A Literal enum can only ever be exactly one of the two real
    # values.
    visibility: Literal["public", "restricted"]


class _StoredFileReferenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1)
    reference: _StoredReferenceEnvelope


class _StoredDiffEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(min_length=1)
    operation: Literal["add", "modify", "delete"]
    unified_diff: str | None
    binary: bool
    truncated: bool
    baseline_available: bool


class _StoredCandidateEnvelope(BaseModel):
    """The complete shape _serialize_stored_candidate() writes, validated as
    a whole rather than accessed field-by-field with plain dict indexing and
    `uuid.UUID(...)` (review finding #4): a manually-parsed malformed entry -
    e.g. `"id": []` - previously escaped as a bare, uncaught `AttributeError`
    from `uuid.UUID()` ('list' object has no attribute 'replace'), not the
    typed `StoredCandidateUnavailableError` every other malformed-input path
    already raised. Every field here is typed (including `id: uuid.UUID`), so
    any structural or type mismatch anywhere in the payload - including
    nested references - surfaces as one well-defined `pydantic.ValidationError`
    instead of whatever built-in exception a hand-rolled accessor happens to
    raise for that particular kind of corruption."""

    model_config = ConfigDict(extra="forbid", strict=True)

    manifest: dict
    # Deliberately NO min_length: a candidate with zero allowed file changes
    # is a legitimate, scored outcome (collect_candidate emits an empty
    # references list and the pipeline records/verdicts it), so requiring at
    # least one reference would regress that path to infrastructure_invalid.
    file_references: list[_StoredFileReferenceEnvelope]
    # Optional on read for candidates persisted before this display-only field
    # existed; new writers always include it.
    diffs: list[_StoredDiffEnvelope] = Field(default_factory=list)
    engineering_stdout: str = ""
    engineering_stderr: str = ""
    engineering_logs_truncated: bool = False


def _deserialize_stored_candidate(data: dict) -> StoredCandidate:
    """Reverses _serialize_stored_candidate(). Raises StoredCandidateUnavailableError
    (never a bare KeyError/TypeError/AttributeError) for anything that isn't a
    well-formed serialized StoredCandidate - in particular the '{}' a legacy
    pre-ENG015-007 candidate row carries, and any malformed nested field
    (review finding #4). The envelope is STRICT (extra="forbid", strict=True)
    with constrained digests, visibility, nonnegative lengths and non-empty
    path/reference lists, and path uniqueness is enforced here to mirror
    CandidateManifest's own uniqueness rule - the documentation's strictness
    claim is now accurate for unknown fields, coercion and value domains,
    not just structural shape."""
    try:
        envelope = _StoredCandidateEnvelope.model_validate(data)
        manifest = CandidateManifest.model_validate(envelope.manifest)
    except ValidationError as exc:
        raise StoredCandidateUnavailableError(f"stored_candidate is malformed: {exc}") from exc
    paths = [entry.path for entry in envelope.file_references]
    if len(set(paths)) != len(paths):
        raise StoredCandidateUnavailableError("stored_candidate is malformed: duplicate file reference paths")
    file_references = tuple(
        (
            entry.path,
            ArtifactReference(
                id=entry.reference.id,
                blob=BlobRef(sha256=entry.reference.blob.sha256, byte_length=entry.reference.blob.byte_length),
                access_scope=entry.reference.access_scope,
                visibility=entry.reference.visibility,
            ),
        )
        for entry in envelope.file_references
    )
    diffs = tuple(CandidateDiff(**entry.model_dump()) for entry in envelope.diffs)
    return StoredCandidate(
        manifest=manifest,
        file_references=file_references,
        diffs=diffs,
        engineering_stdout=envelope.engineering_stdout,
        engineering_stderr=envelope.engineering_stderr,
        engineering_logs_truncated=envelope.engineering_logs_truncated,
    )


def execute_leased_engineering(
    session_factory: sessionmaker, leased: LeasedWork, *, worker_id: str, candidate_variant: str = "reference",
    work_root: Path, lease_seconds: int = repository.DEFAULT_LEASE_SECONDS, cancel_event: threading.Event | None = None,
    engineering_delay_seconds: float = 0,
) -> ExecutionResult:
    """Run one leased `engineering` work item: PROVISION->ENGINEER->STOP->COLLECT
    only (LocalAttemptRunner.run_engineering()), then persist the collected
    candidate and hand off to an independently-leased `verification` work
    item (ENG015-007) - this call never builds or scores the candidate
    itself. A cancellation or any failure with no candidate collected is
    terminal here directly (no verification follows).

    Execution happens entirely outside any database transaction; a
    background thread heartbeats the lease on its own session while it
    runs. `engineering_delay_seconds` is a test seam only, letting a
    controlled-failure test kill the process mid-engineering before its
    (otherwise near-instant) deterministic editor would have finished.
    """
    work_root = Path(work_root)
    with session_factory() as session:
        trial = session.get(TrialRow, leased.trial_id)
        task_row = session.get(TaskRevisionRow, trial.task_revision_id)
        task_slug = task_row.slug
        task_version = task_row.version
        campaign_id = trial.campaign_id

    runtime = TASK_RUNTIMES.get(task_slug)
    if runtime is None:
        raise UnsupportedTaskError(f"task {task_slug} has no supported local evaluator")
    source_dir, _evaluator_module, _evaluator_qualname = runtime

    task_dir = ROOT / "suites" / "dev" / task_slug
    attempt_work_root = work_root / str(leased.attempt_id)
    attempt_work_root.mkdir(parents=True, exist_ok=True)
    script = attempt_work_root / "deterministic-editor.py"
    store = PostgresArtifactStore(session_factory)
    runner = LocalAttemptRunner(store)

    if cancel_event is None:
        cancel_event = threading.Event()

    # ENG-020 (spec section 37), lease-fenced (codex-audit finding 3): issue the candidate-role
    # scoped credential under THIS lease - the worker_id/generation triple must still match the
    # live leased row or issuance is refused (LeaseFenceError), failing safe exactly like
    # heartbeat fencing. The token is delivered to the engineering subprocess environment.
    # Revoked when this phase ends - the credential dies even before its expiry and never
    # outlives the phase that used it; if THIS worker crashes, the reconciler's lease sweep
    # revokes it (a dead worker's token is never usable for the rest of its TTL).
    try:
        with session_factory() as session:
            credential = repository.issue_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="candidate",
                work_item_id=leased.work_item_id, worker_id=worker_id, lease_generation=leased.generation,
            )
    except repository.LeaseFenceError:
        return ExecutionResult(finalized=False, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID.value, verdict=None)

    engineering = _editor_script(
        task_dir, candidate_variant, source_dir, script, delay_seconds=engineering_delay_seconds,
        extra_env=_attempt_env(leased.attempt_id, "candidate", credential.token),
    )

    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(session_factory, leased, worker_id, lease_seconds, stop_heartbeat), daemon=True,
    )
    heartbeat_thread.start()
    stop_cancel_poll = threading.Event()
    cancel_poll_interval = max(lease_seconds / 6, 1)
    cancel_poll_thread = threading.Thread(
        target=_cancellation_poll_loop, args=(session_factory, campaign_id, cancel_event, stop_cancel_poll, cancel_poll_interval), daemon=True,
    )
    cancel_poll_thread.start()
    try:
        outcome = runner.run_engineering(
            AttemptConfig(
                attempt_id=f"attempt-{leased.attempt_id.hex[:8]}-{uuid4().hex[:8]}",
                frozen_source=task_dir / "repo",
                work_root=attempt_work_root / "runs",
                base_revision_digest="1" * 64,
                submission=SubmissionPolicy(include=(f"{source_dir}/**",), protected=("dev_tests/**",), max_artifact_bytes=52_428_800),
                engineering=engineering,
                access_scope=str(leased.attempt_id),
            ),
            cancel_event=cancel_event,
        )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)
        stop_cancel_poll.set()
        cancel_poll_thread.join(timeout=5)
        with session_factory() as session:
            repository.revoke_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="candidate",
                work_item_id=leased.work_item_id, worker_id=worker_id, lease_generation=leased.generation,
            )

    if outcome.execution_validity == ExecutionValidity.CANCELLED:
        with session_factory() as session:
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="cancelled", done=False,
            )
        return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=None)

    if outcome.candidate is None:
        # No candidate was ever collected (configuration/host/teardown failure, or a
        # deadline hit before any allowed file changed) - nothing to record artifact-first;
        # go straight to a fenced finalize so the reconciler can replace this attempt.
        with session_factory() as session:
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="infrastructure_invalid", done=False,
            )
        return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=None)

    # Candidate collected: persist it (artifact-first), then hand the attempt
    # off to an independently-leased verification work item. This call is
    # done - it never builds or scores the candidate itself.
    stored = _serialize_stored_candidate(outcome.candidate)
    with session_factory() as session:
        candidate = CandidateOutcome(
            tree_digest=outcome.candidate.manifest.full_tree_hash,
            manifest_digest=outcome.candidate.manifest.digest(),
            validation_status="valid",
            stored_candidate=stored,
        )
        try:
            candidate_id = repository.record_candidate(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, candidate=candidate, lease_seconds=lease_seconds,
            )
        except repository.CandidateConflictError:
            # A genuine integrity conflict (review finding #2): a retried
            # record_candidate call whose payload differs from what is
            # already persisted under the same (attempt_id, tree_digest)
            # identity. Fail safe to infrastructure_invalid rather than
            # crash the worker process over what is, in practice, a rare
            # anomaly worth investigating, not a routine failure mode.
            with session_factory() as finalize_session:
                finalized = repository.finalize(
                    finalize_session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                    attempt_id=leased.attempt_id, terminal_status="infrastructure_invalid", done=False,
                )
            return ExecutionResult(finalized=finalized, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID.value, verdict=None)
        if candidate_id is None:
            return ExecutionResult(finalized=False, execution_validity=outcome.execution_validity.value, verdict=None)
        # Claim this candidate's artifact references as committed evidence
        # (review finding #2): ties worker_artifact_reference.candidate_id to
        # this candidate and flips the referenced blobs' retention_class from
        # 'staging' to 'evidence', so the reconciler's 24-hour orphan purge
        # can never delete bytes a committed candidate still references
        # (spec section 37). Runs inside the same fenced lease window.
        repository.attach_candidate_references(session, attempt_id=leased.attempt_id, candidate_id=candidate_id)
        advanced = repository.advance_to_verification(
            session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation, attempt_id=leased.attempt_id,
        )
    return ExecutionResult(finalized=advanced, execution_validity=outcome.execution_validity.value, verdict=None)


def execute_leased_verification(
    session_factory: sessionmaker, leased: LeasedWork, *, worker_id: str, work_root: Path,
    lease_seconds: int = repository.DEFAULT_LEASE_SECONDS, cancel_event: threading.Event | None = None,
) -> ExecutionResult:
    """Run one leased `verification` work item: BUILD->VERIFY
    (LocalAttemptRunner.run_verification()) against a candidate an
    engineering phase already collected and persisted - possibly in a
    different process, possibly a different worker, possibly long since
    exited (ENG015-007). Persists the evaluation (artifact-first, mirroring
    engineering's own record_candidate step) before finalizing.

    A campaign cancelled after this attempt's engineering phase already
    handed off (review finding #1) must not let a claimed verification item
    go on to produce and finalize a score: this mirrors
    execute_leased_engineering's own cancel_event plumbing - a background
    poll thread sets the shared cancel_event as soon as cancellation is
    observed, and LocalAttemptRunner.run_verification() checks it at the
    BUILD/VERIFY phase boundary.
    """
    work_root = Path(work_root)
    with session_factory() as session:
        trial = session.get(TrialRow, leased.trial_id)
        task_row = session.get(TaskRevisionRow, trial.task_revision_id)
        task_slug = task_row.slug
        task_version = task_row.version
        campaign_id = trial.campaign_id
        from ..regrading import correction_for_attempt, installed_scoring_bundle
        correction = correction_for_attempt(session, leased.attempt_id) if leased.work_type == "regrade" else None
        if leased.work_type == "regrade" and (
            correction is None or correction.status != "running"
            or correction.scoring_correction_digest != evidence_digest(installed_scoring_bundle(session, campaign_id))
        ):
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="scorer_error", done=False,
            )
            return ExecutionResult(finalized=finalized, execution_validity="infrastructure_invalid", verdict=None)

    # ENG-020 (spec section 37), lease-fenced (codex-audit finding 3, second review round):
    # issue the verifier-role scoped credential under THIS lease FIRST, then CONSUME the
    # credential-gated candidate capability with it (finding 3: verification must actually
    # authenticate to the persisted candidate, not reach it through a bare worker DB session).
    # A stale worker's issue is refused (LeaseFenceError -> handle below). Revoked when this
    # phase ends, on any infra abort below, or by the reconciler if THIS worker crashes.
    try:
        with session_factory() as session:
            credential = repository.issue_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="verifier",
                work_item_id=leased.work_item_id, worker_id=worker_id, lease_generation=leased.generation,
            )
    except repository.LeaseFenceError:
        return ExecutionResult(finalized=False, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID.value, verdict=None)

    def _abort_infra() -> ExecutionResult:
        """Revoke the just-issued verifier credential and finalize the lease as an
        infrastructure failure - every infra abort AFTER issuance must kill the credential it
        minted, exactly as the phase-ending finally does (codex finding 3)."""
        with session_factory() as session:
            repository.revoke_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="verifier",
                work_item_id=leased.work_item_id, worker_id=worker_id, lease_generation=leased.generation,
            )
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="infrastructure_invalid", done=False,
            )
        return ExecutionResult(finalized=finalized, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID.value, verdict=None)

    try:
        with session_factory() as session:
            loaded = repository.load_stored_candidate_authorized(
                session, attempt_id=leased.attempt_id, actor_role="verifier", token=credential.token,
            )
    except repository.CredentialDeniedError:
        # Just-issued credential should verify; if the gate somehow refuses, fail safe.
        return _abort_infra()

    if loaded is None:
        # Should never happen in practice - verification is only ever enqueued
        # right after record_candidate succeeds - but fail safe rather than
        # crash if it somehow does.
        return _abort_infra()

    if evidence_digest(loaded.stored_candidate) != loaded.stored_candidate_digest:
        return _abort_infra()

    try:
        deserialized_candidate = _deserialize_stored_candidate(loaded.stored_candidate)
    except StoredCandidateUnavailableError:
        # Missing/legacy/malformed stored_candidate (review finding #4): fail
        # safe to infrastructure_invalid rather than raising a bare KeyError
        # out of a worker process.
        return _abort_infra()

    # The persisted candidate is checked against CandidateRow's own
    # authoritative digests before anything is built or scored under its
    # identity (review finding #3): stored_candidate JSON that has been
    # corrupted, hand-edited, or otherwise diverged from the row that
    # recorded it must not be silently evaluated as if it still matched.
    if (
        deserialized_candidate.manifest.full_tree_hash != loaded.tree_digest
        or deserialized_candidate.manifest.digest() != loaded.manifest_digest
    ):
        return _abort_infra()

    runtime = TASK_RUNTIMES.get(task_slug)
    if runtime is None:
        # No supported local evaluator - the verifier credential was already issued above, so
        # kill it before raising so this path can never leak a live scoped identity.
        with session_factory() as session:
            repository.revoke_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="verifier",
                work_item_id=leased.work_item_id, worker_id=worker_id, lease_generation=leased.generation,
            )
        raise UnsupportedTaskError(f"task {task_slug} has no supported local evaluator")
    source_dir, evaluator_module, evaluator_qualname = runtime
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # The evaluator is NEVER imported in this worker parent process (codex
    # reviewer blocker round): importlib.import_module(evaluator_module) would run
    # the evaluator's module-level code against the worker's unsanitized
    # environment (AIEB_DATABASE_URL, CI tokens, ...) BEFORE the isolated child -
    # or even its scrub - exists. Carry the (module, qualname) identity as plain
    # strings and let LocalAttemptRunner._run_verify_isolated spawn the child,
    # which is the FIRST process to import the module, and only after
    # os.environ is scrubbed (codex-audit finding 4). ROOT is still injected into
    # sys.path so the spawn child inherits it and can find tests.maintainer.*.
    evaluate_identity = (evaluator_module, evaluator_qualname)

    task_dir = ROOT / "suites" / "dev" / task_slug
    attempt_work_root = work_root / str(leased.attempt_id)
    attempt_work_root.mkdir(parents=True, exist_ok=True)
    store = PostgresArtifactStore(session_factory)
    runner = LocalAttemptRunner(store)

    attempt_id = f"attempt-{leased.attempt_id.hex[:8]}-{uuid4().hex[:8]}"
    config = AttemptConfig(
        attempt_id=attempt_id,
        frozen_source=task_dir / "repo",
        work_root=attempt_work_root / "runs",
        base_revision_digest="1" * 64,
        submission=SubmissionPolicy(include=(f"{source_dir}/**",), protected=("dev_tests/**",), max_artifact_bytes=52_428_800),
        engineering=EngineeringCommand((sys.executable, "-c", "pass"), 1),  # unused by run_verification
        access_scope=str(leased.attempt_id),
    )
    outcome = AttemptOutcome(attempt_id=attempt_id, candidate=deserialized_candidate)

    if cancel_event is None:
        cancel_event = threading.Event()

    # The verifier credential was issued EARLIER (right after the lease/task plumbing, before
    # any candidate read) and is delivered as attempt variables into the isolated VERIFY
    # subprocess so the trusted evaluator can prove its scoped identity to the control plane.
    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(session_factory, leased, worker_id, lease_seconds, stop_heartbeat), daemon=True,
    )
    heartbeat_thread.start()
    stop_cancel_poll = threading.Event()
    cancel_poll_interval = max(lease_seconds / 6, 1)
    cancel_poll_thread = threading.Thread(
        target=_cancellation_poll_loop, args=(session_factory, campaign_id, cancel_event, stop_cancel_poll, cancel_poll_interval), daemon=True,
    )
    cancel_poll_thread.start()
    try:
        outcome = runner.run_verification(
            config, None, outcome, cancel_event=cancel_event,
            attempt_vars=_attempt_env(leased.attempt_id, "verifier", credential.token),
            evaluate_identity=evaluate_identity,
        )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)
        stop_cancel_poll.set()
        cancel_poll_thread.join(timeout=5)
        with session_factory() as session:
            repository.revoke_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="verifier",
                work_item_id=leased.work_item_id, worker_id=worker_id, lease_generation=leased.generation,
            )

    if outcome.execution_validity == ExecutionValidity.CANCELLED:
        with session_factory() as session:
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="cancelled", done=False,
            )
        return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=None)

    with session_factory() as session:
        recorded_evaluation: repository.RecordedEvaluation | None = None
        if outcome.evaluation is not None:
            evaluator_id = task_row_evaluator_id(session, task_slug, task_version)
            fixture_id = ensure_fixture_row(session, task_slug)
            evaluation = EvaluationOutcome(
                evaluator_id=correction.corrected_evaluator_id if correction else evaluator_id,
                fixture_id=correction.corrected_fixture_id if correction else fixture_id,
                schedule_digest=evidence_digest({"candidate": outcome.candidate.manifest.digest(),
                    "correction": correction.scoring_correction_digest}) if correction else outcome.candidate.manifest.digest(),
                verdict=outcome.verdict.value if outcome.verdict else None,
                result=outcome.evaluation,
                correction_run_id=correction.id if correction else None,
            )
            try:
                recorded_evaluation = repository.record_evaluation(
                    session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                    candidate_id=loaded.candidate_id, evaluation=evaluation, lease_seconds=lease_seconds,
                )
            except repository.EvaluationConflictError as conflict:
                # Evaluator nondeterminism / integrity anomaly (review finding
                # #3): a retry recomputed a DIFFERENT verdict or result under
                # the exact (candidate_id, evaluator_id, fixture_id,
                # schedule_digest) identity already persisted. The first
                # persisted evaluation stays authoritative - the first valid
                # scored attempt is final (spec section 16) - but the
                # divergence is never silently swallowed: it is recorded as a
                # durable audit event and this attempt is finalized
                # infrastructure_invalid so the anomaly is investigated rather
                # than laundered through an idempotent-replay path.
                with session_factory() as audit_session:
                    audit_session.add(
                        AuditEventRow(
                            target_type="evaluation",
                            target_id=loaded.candidate_id,
                            action="evaluation_conflict",
                            evidence={
                                "attempt_id": str(leased.attempt_id),
                                "work_item_id": str(leased.work_item_id),
                                "worker_id": worker_id,
                                "persisted_verdict": conflict.persisted_verdict,
                                "reported_verdict": conflict.reported_verdict,
                                "schedule_digest": conflict.schedule_digest,
                                "diagnostic": "a retried verification produced a different result for an already-recorded evaluation identity",
                            },
                        )
                    )
                    audit_session.commit()
                finalized = repository.finalize(
                    session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                    attempt_id=leased.attempt_id, terminal_status="infrastructure_invalid", done=False,
                )
                return ExecutionResult(finalized=finalized, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID.value, verdict=None)
            if recorded_evaluation is None:
                return ExecutionResult(finalized=False, execution_validity=outcome.execution_validity.value, verdict=outcome.verdict.value if outcome.verdict else None)

        # Finalize using the AUTHORITATIVE persisted evaluation's verdict
        # when one was recorded (review finding #2) - never this call's own
        # in-memory outcome.verdict, which could differ from what an earlier
        # retry already committed under the same identity. Only fall back to
        # the local outcome when no evaluation was ever recorded at all (a
        # crashed/unresolved verdict path - CandidateUnavailableError or a
        # scorer error - where there is nothing persisted to defer to).
        if recorded_evaluation is not None:
            terminal_status = recorded_evaluation.verdict if recorded_evaluation.verdict else outcome.attribution.value
            done = recorded_evaluation.verdict is not None
            reported_verdict = recorded_evaluation.verdict
        else:
            terminal_status = outcome.verdict.value if outcome.verdict else outcome.attribution.value
            done = outcome.verdict is not None
            reported_verdict = outcome.verdict.value if outcome.verdict else None
        finalized = repository.finalize(
            session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
            attempt_id=leased.attempt_id, terminal_status=terminal_status, done=done,
        )
    return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=reported_verdict)


def execute_leased_harbor_engineering(
    session_factory: sessionmaker,
    leased: LeasedWork,
    *,
    worker_id: str,
    work_root: Path,
    lease_seconds: int = repository.DEFAULT_LEASE_SECONDS,
    cancel_event: threading.Event | None = None,
) -> ExecutionResult:
    """Execute one engineering lease through the explicitly selected Harbor backend.

    Harbor owns the agent/container lifecycle here; candidate normalization and
    persistence still use the same AIEB artifact/repository path as the local
    runner. Verification remains a separate AIEB lease and replays the retained
    candidate, so selecting Harbor cannot bypass the independent verification
    and fencing contract.
    """
    from .harbor_dispatch import DispatchIntegrityError, build_frozen_cell_payload, dispatch_cell
    from aieb_runner.backends.harbor.backend import HarborBackend

    if leased.work_type != "engineering":
        # Verification/regrade continue through the authoritative replay path.
        return execute_leased_work(
            session_factory, leased, worker_id=worker_id, work_root=work_root,
            lease_seconds=lease_seconds, cancel_event=cancel_event,
        )
    with session_factory() as session:
        started = repository.append_attempt_event_fenced(
            session, work_item_id=leased.work_item_id, worker_id=worker_id,
            generation=leased.generation, attempt_id=leased.attempt_id,
            event_type="phase.started", payload={"phase": "engineering"},
            lease_seconds=lease_seconds,
        )
        if not started:
            return ExecutionResult(False, ExecutionValidity.INFRASTRUCTURE_INVALID.value, None)
        trial = session.get(TrialRow, leased.trial_id)
        attempt = session.get(AttemptRow, leased.attempt_id)
        campaign = session.get(CampaignRow, trial.campaign_id) if trial else None
        task_row = session.get(TaskRevisionRow, trial.task_revision_id) if trial else None
        entrant_row = session.get(EntrantRevisionRow, trial.entrant_revision_id) if trial else None
        if not trial or not attempt or not campaign or not task_row or not entrant_row:
            return ExecutionResult(False, ExecutionValidity.INFRASTRUCTURE_INVALID.value, None)
        resolved = campaign.resolved if isinstance(campaign.resolved, dict) else None
        try:
            task = TaskRevision.model_validate(task_row.manifest)
            entrant = EntrantRevision.model_validate(entrant_row.manifest)
            agent_import_path = entrant.agent_implementation
            if ":" not in agent_import_path:
                raise DispatchIntegrityError("entrant agent_implementation is not an importable Harbor agent path")
            payload = build_frozen_cell_payload(
                resolved or {}, trial_id=str(trial.id), attempt_number=attempt.number,
                campaign_id=str(campaign.id), manifest_digest=campaign.manifest_digest or "",
            )
        except Exception as exc:  # noqa: BLE001 - malformed frozen input is infrastructure evidence
            session.rollback()
            with session_factory() as final_session:
                finalized = repository.finalize(
                    final_session, work_item_id=leased.work_item_id, worker_id=worker_id,
                    generation=leased.generation, attempt_id=leased.attempt_id,
                    terminal_status="infrastructure_invalid", done=False,
                )
            return ExecutionResult(finalized, ExecutionValidity.INFRASTRUCTURE_INVALID.value, None)
        task_dir = ROOT / "suites" / "dev" / task.slug
        frozen_source = task_dir / "repo"
        runs_dir = Path(work_root) / str(leased.attempt_id) / "harbor"
        store = PostgresArtifactStore(session_factory)

    if cancel_event is None:
        cancel_event = threading.Event()
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(session_factory, leased, worker_id, lease_seconds, heartbeat_stop),
        daemon=True,
    )
    heartbeat.start()
    try:
        backend = HarborBackend()
        outcome = asyncio.run(dispatch_cell(
            payload, launcher=backend, task_dir=task_dir, runs_dir=runs_dir,
            agent_import_path=agent_import_path, cancel_event=cancel_event,
        ))
        artifacts = CandidateArtifacts(
            trial_dir=Path(outcome.trial_dir or ""),
            result_path=Path(outcome.result_path or ""),
            manifest_path=Path(outcome.trial_dir or "") / "artifacts" / "manifest.json",
            manifest=outcome.manifest_entries,
        )
        normalized = normalize_harbor_result(
            artifacts=artifacts, frozen_source=frozen_source,
            submission=task.submission, base_revision_digest=task.source.repository_digest,
            store=store, access_scope=str(leased.attempt_id),
        )
    except (Exception,) as exc:  # noqa: BLE001 - backend/normalization failures are infra outcomes
        with session_factory() as final_session:
            finalized = repository.finalize(
                final_session, work_item_id=leased.work_item_id, worker_id=worker_id,
                generation=leased.generation, attempt_id=leased.attempt_id,
                terminal_status="cancelled" if cancel_event.is_set() else "infrastructure_invalid",
                done=False,
            )
        return ExecutionResult(finalized, "cancelled" if cancel_event.is_set() else ExecutionValidity.INFRASTRUCTURE_INVALID.value, None)
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=5)

    stored = _serialize_stored_candidate(normalized.candidate)
    with session_factory() as session:
        try:
            candidate_id = repository.record_candidate(
                session, work_item_id=leased.work_item_id, worker_id=worker_id,
                generation=leased.generation, attempt_id=leased.attempt_id,
                candidate=CandidateOutcome(
                    tree_digest=normalized.candidate.manifest.full_tree_hash,
                    manifest_digest=normalized.candidate.manifest.digest(),
                    validation_status="valid", stored_candidate=stored,
                ), lease_seconds=lease_seconds,
            )
        except repository.CandidateConflictError:
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id,
                generation=leased.generation, attempt_id=leased.attempt_id,
                terminal_status="infrastructure_invalid", done=False,
            )
            return ExecutionResult(finalized, ExecutionValidity.INFRASTRUCTURE_INVALID.value, None)
        if candidate_id is None:
            return ExecutionResult(False, ExecutionValidity.INFRASTRUCTURE_INVALID.value, None)
        repository.attach_candidate_references(session, attempt_id=leased.attempt_id, candidate_id=candidate_id)
        advanced = repository.advance_to_verification(
            session, work_item_id=leased.work_item_id, worker_id=worker_id,
            generation=leased.generation, attempt_id=leased.attempt_id,
        )
    return ExecutionResult(advanced, ExecutionValidity.VALID.value, None)


def execute_leased_work(
    session_factory: sessionmaker, leased: LeasedWork, *, worker_id: str, candidate_variant: str = "reference",
    work_root: Path, lease_seconds: int = repository.DEFAULT_LEASE_SECONDS, cancel_event: threading.Event | None = None,
    engineering_delay_seconds: float = 0,
) -> ExecutionResult:
    """Dispatch a leased work item to the executor for its phase (ENG015-007).
    A generic worker pool claims whatever is ready across both queues
    (repository.claim_work_item's default), so the same worker loop
    (worker/loop.py) is unchanged: it just calls this once per claimed item,
    regardless of which phase that item happens to be."""
    with session_factory() as session:
        # Fenced (review finding #6): the phase.started trace write must not
        # land if this worker was already fenced out (its lease reassigned) -
        # the earlier unfenced append let a stale worker mutate the
        # authoritative trace after reassignment. If the fence refuses, this
        # worker no longer owns the item; do not proceed to execute it.
        started = repository.append_attempt_event_fenced(
            session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
            attempt_id=leased.attempt_id, event_type="phase.started", payload={"phase": leased.work_type},
            lease_seconds=lease_seconds,
        )
    if not started:
        return ExecutionResult(finalized=False, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID.value, verdict=None)
    if leased.work_type in {"verification", "regrade"}:
        return execute_leased_verification(
            session_factory, leased, worker_id=worker_id, work_root=work_root, lease_seconds=lease_seconds, cancel_event=cancel_event,
        )
    return execute_leased_engineering(
        session_factory, leased, worker_id=worker_id, candidate_variant=candidate_variant, work_root=work_root,
        lease_seconds=lease_seconds, cancel_event=cancel_event, engineering_delay_seconds=engineering_delay_seconds,
    )


def task_row_evaluator_id(session, task_slug: str, task_version: str) -> uuid.UUID:
    row = session.execute(select(TaskRevisionRow).where(TaskRevisionRow.slug == task_slug, TaskRevisionRow.version == task_version)).scalar_one()
    return row.evaluator_id


def ensure_fixture_row(session, task_slug: str) -> uuid.UUID:
    import hashlib

    from ..models import FixtureRevisionRow

    digest = hashlib.sha256(f"aieb:worker-fixture:{task_slug}".encode("utf-8")).hexdigest()
    existing = session.execute(select(FixtureRevisionRow).where(FixtureRevisionRow.digest == digest)).scalar_one_or_none()
    if existing is not None:
        return existing.id
    row = FixtureRevisionRow(digest=digest, visibility="restricted", family_id=task_slug)
    session.add(row)
    session.flush()
    return row.id
