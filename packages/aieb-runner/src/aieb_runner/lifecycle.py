"""Deadline-first local attempt lifecycle for development vertical slices.

This module deliberately owns lifecycle ordering rather than inheriting it from
Harbor.  It is a deterministic local adapter, not a claim of official sandbox
isolation.  Candidate bytes are collected only after the engineering process
tree has stopped, then reconstructed into a new build allocation.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue as queue_module
import shutil
import signal
import subprocess
import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event, Thread
from typing import Callable
from uuid import uuid4

from aieb_core.models import ExecutionValidity, SubmissionPolicy, Verdict

from .artifacts import (
    ArtifactError,
    ArtifactStore,
    CollectionLimits,
    StoredCandidate,
    collect_candidate,
    reconstruct_candidate,
)


class CandidateUnavailableError(Exception):
    """An evaluator raises this - and only this - to report that the
    candidate application itself is unreachable or crashed (failed to start,
    stopped responding, etc). Deliberately distinct from Python's built-in
    RuntimeError: relying on that generic, widely-raised type to mean
    specifically "the candidate is broken" risked misattributing a trusted
    evaluator's own unrelated bug (which could just as easily raise a bare
    RuntimeError) as a scored candidate failure instead of a scorer error."""


class CancelledError(BaseException):
    """Raised BY an evaluator (or BUILD/VERIFY phase worker) to honour the
    cooperative stop event every evaluator now receives: cancellation is not
    a scorer error and must never be classified as one. Derives from
    BaseException rather than Exception for the same reason KeyboardInterrupt
    does - a broad `except Exception` around a trusted evaluator must not
    swallow a cancellation and misreport it as a scorer failure."""


def _invoke_evaluator(evaluate: Evaluator, build: Path, stop: Event) -> dict[str, object]:
    """Call a trusted evaluator with the cancellation-contract stop event,
    transparently supporting evaluators still written against the original
    single-argument signature (dev-suite evaluators predate ENG-015's
    cooperative cancellation contract, and are digest-pinned - they keep the
    legacy contract). Uses inspect.signature (not try/except TypeError) so a
    genuine TypeError raised INSIDE the evaluator is never mistaken for a
    signature mismatch - that would misroute a real scorer error into a
    fabricated second argument. Only parameters that can actually receive a
    SECOND POSITIONAL argument qualify: a keyword-only parameter (rag01's
    `seed=4107`) does not, and passing stop positionally to such an evaluator
    would raise TypeError - exactly the misclassification this helper
    exists to prevent."""
    import inspect

    try:
        parameters = inspect.signature(evaluate).parameters
    except (TypeError, ValueError):
        return evaluate(build, stop)
    kinds = {parameter.kind for parameter in parameters.values()}
    if inspect.Parameter.VAR_POSITIONAL in kinds:
        return evaluate(build, stop)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return evaluate(build, stop)
    return evaluate(build)


class AttemptPhase(StrEnum):
    PROVISION = "provision"
    ENGINEER = "engineer"
    STOP = "stop"
    COLLECT = "collect"
    BUILD = "build"
    VERIFY = "verify"
    FINALIZE = "finalize"
    CLEANUP = "cleanup"


class FailureAttribution(StrEnum):
    NONE = "none"
    CANDIDATE_BUILD_FAILURE = "candidate_build_failure"
    CANDIDATE_RUNTIME_FAILURE = "candidate_runtime_failure"
    CONFIGURATION_FAILURE = "configuration_failure"
    RESOURCE_LIMIT = "resource_limit"
    PROVIDER_OUTAGE = "provider_outage"
    HOST_FAILURE = "host_failure"
    SCORER_ERROR = "scorer_error"
    SUBMISSION_CONTRACT_VIOLATION = "submission_contract_violation"
    TEARDOWN_FAILURE = "teardown_failure"


@dataclass(frozen=True)
class EngineeringCommand:
    argv: tuple[str, ...]
    deadline_sec: float


@dataclass(frozen=True)
class AttemptConfig:
    attempt_id: str
    frozen_source: Path
    work_root: Path
    base_revision_digest: str
    submission: SubmissionPolicy
    engineering: EngineeringCommand
    access_scope: str
    collection_limits: CollectionLimits = CollectionLimits()


@dataclass(frozen=True)
class ReplacementPolicy:
    """Declared cap for replacing only infrastructure-invalid allocations."""

    max_infrastructure_replacements: int = 0


@dataclass
class AttemptOutcome:
    attempt_id: str
    phases: list[str] = field(default_factory=list)
    execution_validity: ExecutionValidity = ExecutionValidity.INFRASTRUCTURE_INVALID
    verdict: Verdict | None = None
    attribution: FailureAttribution = FailureAttribution.NONE
    termination_reason: str | None = None
    retryable: bool = False
    candidate: StoredCandidate | None = None
    evaluation: dict[str, object] | None = None
    cleanup_clean: bool = False
    diagnostics: list[str] = field(default_factory=list)
    evidence_path: Path | None = None

    def add(self, phase: AttemptPhase) -> None:
        self.phases.append(phase.value)


Evaluator = Callable[..., dict[str, object]]

# Seconds _run_cancelable waits after cancellation for an evaluator that
# honours the cooperative stop event before its thread is abandoned.
EVALUATOR_CANCEL_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class PhaseRun:
    """Outcome of one BUILD phase run under cancellation polling.

    `completed` means the phase worker finished and its result may be used.
    `abandoned` means cancellation fired, the worker was given
    EVALUATOR_CANCEL_GRACE_SECONDS to honour the cooperative stop event, and
    did NOT - its daemon thread is still running and may still be reading the
    phase's allocation. The caller must then not delete that allocation (see
    run_verification's finally block) and must discard whatever the abandoned
    thread eventually writes.

    `cancelled` means cancellation was OBSERVED while this phase's worker was
    still running - independent of `completed`/`abandoned` (review finding
    #1, seventh pass/ENG015 review): a worker that ignores the cooperative
    stop event but happens to finish naturally DURING the grace window
    previously came back as `completed=True, abandoned=False` with no signal
    that cancellation had ever fired, so its late result was silently scored
    anyway. `cancelled` is set the instant cancellation is observed, before
    we even know whether the worker will honour it or race past the grace
    period - callers must treat ANY `cancelled=True` result as discardable,
    never conditioned on `completed`."""

    completed: bool
    abandoned: bool = False
    cancelled: bool = False


def _verify_subprocess_entrypoint(evaluate: Evaluator, build: Path, stop: "mp.synchronize.Event", result_queue: "mp.Queue") -> None:
    """Runs the trusted evaluator in an OWNED, forcibly-killable child
    process (review finding #2: "an uncooperative evaluator remains neither
    terminated nor safely contained" - a Python thread can never be
    preempted, so an evaluator that ignores the cooperative `stop` signal
    could only ever be abandoned, never actually stopped, while it went on
    running arbitrary code - subprocess/network/spend effects included -
    indefinitely). A subprocess CAN be forcibly terminated
    (Process.terminate()/kill()) regardless of whether it cooperates, which
    is what `LocalAttemptRunner._run_verify_isolated` does after the grace
    period. Must be a module-level function (not a closure) so it is
    picklable for `multiprocessing`'s spawn start method; reports its
    outcome back through `result_queue` since nothing is shared with the
    parent across the process boundary."""
    try:
        result_queue.put(("ok", _invoke_evaluator(evaluate, build, stop)))
    except CancelledError:
        result_queue.put(("cancelled", None))
    except CandidateUnavailableError as exc:
        result_queue.put(("candidate_unavailable", str(exc)))
    except Exception as exc:  # noqa: BLE001 - a trusted evaluator's own bug, never a candidate verdict
        result_queue.put(("scorer_error", repr(exc)))


@dataclass(frozen=True)
class VerifyRun:
    """Outcome of one isolated (subprocess) VERIFY invocation.

    `cancelled=True` covers BOTH a cooperative stop (the evaluator itself
    raised CancelledError) and a forced kill after the grace period (an
    uncooperative evaluator that had to be terminated) - either way nothing
    it produced is ever scored. Unlike the thread-based `PhaseRun`, there is
    no "abandoned, leave in place" state: once terminated (or killed) and
    joined, the child process is verifiably dead, so cleanup is always safe
    (review finding #2's core fix - real containment, not merely discarding
    a still-running thread's eventual result)."""

    cancelled: bool
    kind: str  # "ok" | "candidate_unavailable" | "scorer_error" | "crashed"
    evaluation: dict[str, object] | None = None
    error: str | None = None


class LocalAttemptRunner:
    """Run a deterministic editable-workspace attempt and fresh replay.

    The engineering command is a test seam, not proof of installed-agent
    compatibility.  A real entrant must be routed through the qualified Harbor
    adapter only after ENG-001's authorization gate is met.
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    @staticmethod
    def _copy_frozen(source: Path, destination: Path) -> None:
        if not source.is_dir():
            raise ValueError(f"frozen source is not a directory: {source}")
        shutil.copytree(source, destination)

    @staticmethod
    def _attach_windows_kill_job(process: subprocess.Popen[str]) -> None:
        """Kill the complete owned Windows process tree when the job closes."""
        if os.name != "nt":
            return

        class _IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "read_operation_count", "write_operation_count", "other_operation_count",
                "read_transfer_count", "write_transfer_count", "other_transfer_count",
            )]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("per_process_user_time_limit", ctypes.c_longlong),
                ("per_job_user_time_limit", ctypes.c_longlong),
                ("limit_flags", wintypes.DWORD),
                ("minimum_working_set_size", ctypes.c_size_t),
                ("maximum_working_set_size", ctypes.c_size_t),
                ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority_class", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("basic_limit_information", _BasicLimitInformation),
                ("io_info", _IoCounters),
                ("process_memory_limit", ctypes.c_size_t),
                ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory_used", ctypes.c_size_t),
                ("peak_job_memory_used", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _ExtendedLimitInformation()
        info.basic_limit_information.limit_flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        process._aieb_job_handle = job  # type: ignore[attr-defined]

    @staticmethod
    def _close_windows_job(process: subprocess.Popen[str]) -> None:
        job = getattr(process, "_aieb_job_handle", None)
        if job:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job)
            process._aieb_job_handle = None  # type: ignore[attr-defined]

    @staticmethod
    def _stop_tree(process: subprocess.Popen[str]) -> bool:
        """Synchronously stop the owned process and its descendants."""
        def close_streams() -> None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        if process.poll() is not None:
            LocalAttemptRunner._close_windows_job(process)
            close_streams()
            return True
        try:
            if os.name == "nt":
                # Closing the job kills descendants even when a child has escaped
                # the command interpreter's ordinary process tree.
                LocalAttemptRunner._close_windows_job(process)
                completed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                process.wait(timeout=5)
                # Job close may already have killed the root before taskkill
                # observes it; process termination, not taskkill's lookup code,
                # is the authoritative condition here.
                stopped = process.poll() is not None
                close_streams()
                return stopped
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
            close_streams()
            return True
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                close_streams()
                return False
            stopped = process.poll() is not None
            close_streams()
            return stopped

    @staticmethod
    def _start(command: EngineeringCommand, workspace: Path) -> subprocess.Popen[str]:
        options: dict[str, object] = {"cwd": workspace, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command.argv, **options)  # type: ignore[arg-type]
        try:
            LocalAttemptRunner._attach_windows_kill_job(process)
        except OSError:
            process.kill()
            process.wait(timeout=5)
            raise
        return process

    @staticmethod
    def _write_evidence(outcome: AttemptOutcome, path: Path) -> None:
        candidate = outcome.candidate
        value: dict[str, object] = {
            "attempt_id": outcome.attempt_id,
            "phases": outcome.phases,
            "execution_validity": outcome.execution_validity.value,
            "verdict": outcome.verdict.value if outcome.verdict else None,
            "attribution": outcome.attribution.value,
            "termination_reason": outcome.termination_reason,
            "retryable": outcome.retryable,
            "cleanup_clean": outcome.cleanup_clean,
            "diagnostics": outcome.diagnostics,
            "candidate_manifest": candidate.manifest.model_dump(mode="json") if candidate else None,
            "evaluation": outcome.evaluation,
        }
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _finalize(self, outcome: AttemptOutcome, attempt_root: Path, evidence: Path, allocations: tuple[Path, ...]) -> None:
        """Shared terminal bookkeeping: write evidence, remove the given
        writable allocations, compute cleanup_clean/retryable, write evidence
        again reflecting the final state. Used both by run_engineering() (an
        early exit with no candidate - the attempt is already terminal, no
        verification follows) and by run_verification() (the true end of the
        pipeline) - never by a successful run_engineering() handoff, which is
        not yet a terminal outcome for the attempt."""
        outcome.add(AttemptPhase.FINALIZE)
        try:
            if attempt_root.exists():
                self._write_evidence(outcome, evidence)
            outcome.add(AttemptPhase.CLEANUP)
            for allocation in allocations:
                if allocation.exists():
                    shutil.rmtree(allocation)
            outcome.cleanup_clean = all(not allocation.exists() for allocation in allocations)
        except OSError as exc:
            outcome.attribution = FailureAttribution.TEARDOWN_FAILURE
            outcome.execution_validity = ExecutionValidity.INFRASTRUCTURE_INVALID
            outcome.verdict = None
            outcome.cleanup_clean = False
            outcome.diagnostics.append(f"allocation cleanup failed: {exc}")
        finally:
            outcome.retryable = outcome.attribution in {
                FailureAttribution.PROVIDER_OUTAGE,
                FailureAttribution.HOST_FAILURE,
                FailureAttribution.SCORER_ERROR,
                FailureAttribution.TEARDOWN_FAILURE,
            }
            if attempt_root.exists():
                self._write_evidence(outcome, evidence)
                outcome.evidence_path = evidence

    def run_engineering(self, config: AttemptConfig, cancel_event: Event | None = None) -> AttemptOutcome:
        """PROVISION -> ENGINEER -> STOP -> COLLECT.

        Returns an outcome that is either already terminal (attribution set,
        `candidate` still None - configuration/host/teardown/cancellation/
        contract-violation failure; no verification follows, the attempt is
        finalized) or has `candidate` populated and ready for
        run_verification(). That call may happen in this same process
        (see run()) or independently - a different worker, a different
        process, even after this one has exited or crashed - since the
        collected candidate is already durably persisted in the artifact
        store keyed by content, and verification reconstructs from
        `config.frozen_source` plus that store, never from this call's own
        `engineer` workspace (ENG-015's leasing split, ENG015-007). That
        workspace is torn down before returning either way, since nothing
        downstream reads it.
        """
        outcome = AttemptOutcome(config.attempt_id)
        attempt_root = config.work_root / config.attempt_id
        engineer = attempt_root / "engineer"
        evidence = attempt_root / "attempt.json"
        process: subprocess.Popen[str] | None = None
        cancelled = False
        try:
            outcome.add(AttemptPhase.PROVISION)
            attempt_root.mkdir(parents=True, exist_ok=False)
            if config.engineering.deadline_sec <= 0 or not config.engineering.argv:
                outcome.attribution = FailureAttribution.CONFIGURATION_FAILURE
                outcome.diagnostics.append("engineering command and positive deadline are required")
                return outcome
            self._copy_frozen(config.frozen_source, engineer)

            outcome.add(AttemptPhase.ENGINEER)
            try:
                process = self._start(config.engineering, engineer)
            except OSError as exc:
                outcome.attribution = FailureAttribution.HOST_FAILURE
                outcome.diagnostics.append(f"unable to launch engineering process: {exc}")
                return outcome
            try:
                # A bounded poll loop (rather than one blocking communicate(timeout=...))
                # lets an external cancellation event interrupt engineering before the
                # deadline, without changing observed behavior when cancel_event is None.
                deadline_at = time.monotonic() + config.engineering.deadline_sec
                poll_interval = min(0.2, config.engineering.deadline_sec)
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        break
                    if time.monotonic() >= deadline_at:
                        raise subprocess.TimeoutExpired(config.engineering.argv, config.engineering.deadline_sec)
                    time.sleep(poll_interval)
                if cancelled:
                    outcome.termination_reason = "cancelled"
                else:
                    process.communicate(timeout=max(poll_interval, 1))
                    if process.returncode not in (0, None):
                        outcome.attribution = FailureAttribution.CANDIDATE_BUILD_FAILURE
                        outcome.diagnostics.append(f"engineering command exited {process.returncode}")
                        return outcome
            except subprocess.TimeoutExpired:
                outcome.termination_reason = "deadline"
                outcome.attribution = FailureAttribution.RESOURCE_LIMIT
            finally:
                outcome.add(AttemptPhase.STOP)
                if not self._stop_tree(process):
                    outcome.attribution = FailureAttribution.TEARDOWN_FAILURE
                    outcome.diagnostics.append("owned engineering process tree could not be confirmed stopped")
                    return outcome
            if cancelled:
                outcome.execution_validity = ExecutionValidity.CANCELLED
                return outcome

            # The stop phase is complete before any candidate filesystem read.
            outcome.add(AttemptPhase.COLLECT)
            try:
                outcome.candidate = collect_candidate(
                    frozen_source=config.frozen_source,
                    workspace=engineer,
                    submission=config.submission,
                    base_revision_digest=config.base_revision_digest,
                    store=self.store,
                    access_scope=config.access_scope,
                    limits=config.collection_limits,
                )
            except ArtifactError as exc:
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.CONTRACT_VIOLATION
                outcome.attribution = FailureAttribution.SUBMISSION_CONTRACT_VIOLATION
                outcome.diagnostics.append(str(exc))
                return outcome
            return outcome
        finally:
            if outcome.candidate is None:
                self._finalize(outcome, attempt_root, evidence, (engineer,))
            else:
                try:
                    if engineer.exists():
                        shutil.rmtree(engineer)
                except OSError as exc:
                    # Collection succeeded, but the workspace this attempt owned
                    # could not be torn down - the same teardown-failure
                    # attribution the original single-call pipeline would have
                    # given if this cleanup had failed at its very end. No
                    # verification follows; this is terminal now, not a handoff.
                    outcome.attribution = FailureAttribution.TEARDOWN_FAILURE
                    outcome.execution_validity = ExecutionValidity.INFRASTRUCTURE_INVALID
                    outcome.diagnostics.append(f"engineer allocation cleanup failed: {exc}")
                    outcome.candidate = None
                    self._finalize(outcome, attempt_root, evidence, (engineer,))

    @staticmethod
    def _run_cancelable(fn: Callable[[Event], None], cancel_event: Event | None, poll_seconds: float = 0.2) -> PhaseRun:
        """Run `fn` (assigning into variables it closes over) on a background
        thread and return a `PhaseRun`: `completed=True` once it finishes, or
        `completed=False, abandoned=False/True` as soon as `cancel_event`
        fires first - whichever happens first.

        Cancellation contract: `fn` receives a dedicated stop Event and is
        REQUIRED to check it periodically between any two units of work and
        return promptly (as `CancelledError`) once it is set. That is the
        genuinely cooperative way to stop an in-process evaluator the runner
        owns no subprocess handle for (review finding #1: a Python thread
        cannot be preempted, so a `fn` that ignores the stop event can never
        be forcibly terminated in-process - the alternative is an abandoned
        daemon thread, which this version only tolerates under a bounded
        grace and never tears down work under). An evaluator that needs hard
        external termination must run its work in a subprocess the caller
        owns and kills instead (exactly what run_engineering does).

        On cancellation the worker gets EVALUATOR_CANCEL_GRACE_SECONDS to
        honour the stop event: if it does, the thread is joined cleanly
        (`abandoned=False`); if it does not, the thread is left as a daemon
        (`abandoned=True`) and the CALLER must leave any allocation that
        thread may still be reading in place for host-side reconciliation.

        Either way a cancelled attempt is never scored from a late
        completion: the result the worker writes after cancellation is
        discarded by the caller (run_verification stops reading phase
        results as soon as `completed` is False)."""
        thread = Thread(target=fn, args=(cancel_event if cancel_event is not None else Event(),), daemon=True)
        thread.start()
        while thread.is_alive():
            if cancel_event is not None and cancel_event.is_set():
                thread.join(timeout=EVALUATOR_CANCEL_GRACE_SECONDS)
                # cancelled=True unconditionally (review finding #1): a
                # worker that ignores `stop` but happens to finish naturally
                # within the grace window still counted as `completed=True`
                # here previously, with nothing recording that cancellation
                # had fired - its late, unrequested result was then silently
                # scored by the caller. Whether the thread went on to finish
                # (`completed`) or had to be abandoned (`abandoned`) is now a
                # SEPARATE fact from whether it must be discarded.
                return PhaseRun(completed=not thread.is_alive(), abandoned=thread.is_alive(), cancelled=True)
            thread.join(timeout=poll_seconds)
        return PhaseRun(completed=True, abandoned=False, cancelled=False)

    @staticmethod
    def _run_verify_isolated(evaluate: Evaluator, build: Path, cancel_event: Event | None) -> VerifyRun:
        """Run the trusted evaluator in an OWNED, forcibly-killable child
        process (review finding #2): unlike `_run_cancelable`'s in-process
        daemon thread (which can never be preempted and can only ever be
        "abandoned"), a subprocess that ignores the cooperative stop signal
        past EVALUATOR_CANCEL_GRACE_SECONDS is genuinely terminated here -
        `terminate()`, escalating to `kill()` if it somehow survives - and
        then joined, so by the time this returns the process is verifiably
        dead. There is therefore no "leave it running, hope it stops"
        state for VERIFY any more: cancellation always results in a
        confirmed-dead process, and the caller (run_verification) can always
        safely tear down the build allocation afterward - the abandoned-
        thread teardown race this method exists to close only ever applied
        because a thread could not be killed; a process can.
        """
        ctx = mp.get_context("spawn")
        result_queue: mp.Queue = ctx.Queue()
        stop_event = ctx.Event()
        process = ctx.Process(target=_verify_subprocess_entrypoint, args=(evaluate, build, stop_event, result_queue), daemon=True)
        process.start()
        try:
            while process.is_alive():
                if cancel_event is not None and cancel_event.is_set():
                    stop_event.set()  # cooperative signal - an evaluator honouring it exits promptly
                    process.join(timeout=EVALUATOR_CANCEL_GRACE_SECONDS)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    return VerifyRun(cancelled=True, kind="cancelled")
                process.join(timeout=0.2)
            if cancel_event is not None and cancel_event.is_set():
                # The process exited naturally right around when cancellation
                # fired, before the loop above observed it - still discard
                # unconditionally (review finding #1): a race that lets a
                # cancelled attempt's result through is exactly the defect
                # being fixed here, regardless of which side of the check it
                # would have landed on.
                return VerifyRun(cancelled=True, kind="cancelled")
            try:
                kind, payload = result_queue.get(timeout=5)
            except queue_module.Empty:
                return VerifyRun(cancelled=False, kind="crashed", error=f"evaluator subprocess exited (code {process.exitcode}) without reporting a result")
            if kind == "ok":
                return VerifyRun(cancelled=False, kind="ok", evaluation=payload)
            if kind == "cancelled":
                return VerifyRun(cancelled=True, kind="cancelled")
            return VerifyRun(cancelled=False, kind=kind, error=payload)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            result_queue.close()

    def run_verification(
        self, config: AttemptConfig, evaluator: Evaluator, outcome: AttemptOutcome, cancel_event: Event | None = None,
    ) -> AttemptOutcome:
        """BUILD -> VERIFY -> FINALIZE/CLEANUP, given an outcome that already
        carries a collected `candidate` - either from this same process's own
        prior run_engineering() call, or reconstructed from persisted
        artifact-store references by an entirely different worker recovering
        after a crash (ENG015-007). Mutates and returns the same outcome.

        Cancellation (review findings #1/#2): `cancel_event` interrupts BOTH
        BUILD and VERIFY while they are running, not merely before/after
        each phase.

        BUILD runs `reconstruct_candidate` (trusted, internal code) via
        `_run_cancelable` on an in-process thread: an evaluator is not
        involved here, only this runner's own reconstruction logic, which
        currently does not itself check the cooperative stop signal - so a
        cancellation during BUILD always waits out EVALUATOR_CANCEL_GRACE_SECONDS
        and, since a thread can never be preempted, the thread is then
        abandoned (`phase_abandoned`) and its allocation is deliberately left
        on disk rather than deleted while it may still be reading/writing -
        see the `finally` block below.

        VERIFY runs the ARBITRARY, less-trusted per-task evaluator via
        `_run_verify_isolated`, in an OWNED, forcibly-killable subprocess
        (review finding #2 - "an uncooperative evaluator remains neither
        terminated nor safely contained": a daemon thread can only ever be
        abandoned, never actually stopped, so it could keep running
        arbitrary subprocess/network/spend side effects indefinitely). An
        evaluator that honours the cooperative stop signal exits promptly
        (as `CancelledError`); one that ignores it is genuinely terminated
        (`terminate()`, escalating to `kill()`) once
        EVALUATOR_CANCEL_GRACE_SECONDS elapses. Either way the subprocess is
        confirmed dead before `_run_verify_isolated` returns, so VERIFY never
        sets `phase_abandoned` - real containment, not merely discarding a
        still-running worker's eventual result.

        In both cases, cancellation observed at all means nothing from that
        phase is ever scored or persisted - see `PhaseRun.cancelled` and
        `VerifyRun.cancelled`.

        Any `ArtifactError` during BUILD is classified INFRASTRUCTURE_INVALID
        here, never a candidate contract violation (review finding #3): by
        this phase, `collect_candidate` has already accepted the candidate as
        submission-policy-compliant during ENGINEER; any error reconstructing
        it from the artifact store now (a missing/corrupted blob, a tampered
        reference, a byte-for-byte mismatch against the frozen manifest) is a
        storage/reference integrity failure, not something the candidate
        itself did wrong.
        """
        attempt_root = config.work_root / config.attempt_id
        build = attempt_root / "build"
        evidence = attempt_root / "attempt.json"
        attempt_root.mkdir(parents=True, exist_ok=True)
        phase_abandoned = False
        try:
            if cancel_event is not None and cancel_event.is_set():
                outcome.execution_validity = ExecutionValidity.CANCELLED
                outcome.termination_reason = "cancelled"
                return outcome

            outcome.add(AttemptPhase.BUILD)
            build_result: dict[str, object] = {}

            def _do_build(stop: Event) -> None:
                try:
                    reconstruct_candidate(
                        frozen_source=config.frozen_source,
                        destination=build,
                        stored=outcome.candidate,
                        store=self.store,
                        principal_scope=config.access_scope,
                    )
                except CancelledError:
                    # Honouring the cooperative stop event ends this thread
                    # normally (threading swallows the exception), so the
                    # completed phase must still be classifiable as cancelled
                    # by the main flow - record it explicitly.
                    build_result["cancelled"] = True
                except ArtifactError as exc:
                    build_result["error"] = exc

            build_run = self._run_cancelable(_do_build, cancel_event)
            phase_abandoned = build_run.abandoned
            # build_run.cancelled is checked FIRST and unconditionally
            # (review finding #1): cancellation having been observed at all
            # discards this phase's result outright, regardless of whether
            # the worker also happened to finish (`completed`) within the
            # grace window - see PhaseRun's docstring.
            if build_run.cancelled or not build_run.completed or build_result.get("cancelled"):
                outcome.execution_validity = ExecutionValidity.CANCELLED
                outcome.termination_reason = "cancelled"
                return outcome
            if "error" in build_result:
                outcome.execution_validity = ExecutionValidity.INFRASTRUCTURE_INVALID
                outcome.attribution = FailureAttribution.HOST_FAILURE
                outcome.diagnostics.append(f"candidate reconstruction failed (storage/reference integrity): {build_result['error']}")
                return outcome

            if cancel_event is not None and cancel_event.is_set():
                outcome.execution_validity = ExecutionValidity.CANCELLED
                outcome.termination_reason = "cancelled"
                return outcome

            outcome.add(AttemptPhase.VERIFY)
            # Runs in an OWNED, forcibly-killable subprocess, not an
            # in-process daemon thread (review finding #2): a
            # cancellation-ignoring evaluator is genuinely terminated after
            # grace, never merely abandoned - see _run_verify_isolated.
            verify_run = self._run_verify_isolated(evaluator, build, cancel_event)
            if verify_run.cancelled:
                outcome.execution_validity = ExecutionValidity.CANCELLED
                outcome.termination_reason = "cancelled"
                return outcome
            if verify_run.kind == "candidate_unavailable":
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.FAIL
                outcome.attribution = FailureAttribution.CANDIDATE_RUNTIME_FAILURE
                outcome.diagnostics.append(str(verify_run.error))
                return outcome
            if verify_run.kind in ("scorer_error", "crashed"):
                outcome.attribution = FailureAttribution.SCORER_ERROR
                outcome.diagnostics.append(f"trusted evaluator failed: {verify_run.error}")
                return outcome
            outcome.evaluation = verify_run.evaluation
            if not bool(outcome.evaluation.get("pass")):
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.FAIL
            else:
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.PASS
            return outcome
        finally:
            # Teardown race (review finding #1/#2): deleting the build
            # allocation while BUILD's phase thread may still be reading it
            # corrupts the worker's read and this process's own cleanup
            # bookkeeping. Only an ABANDONED BUILD thread - one that ignored
            # the cooperative stop event past its grace period - can still be
            # running here: VERIFY runs in a subprocess that is always
            # confirmed dead (terminated/killed and joined) by the time
            # _run_verify_isolated returns, so it never sets phase_abandoned
            # and never needs this preservation.
            #
            # Deciding this on `phase_abandoned` ALONE - never also
            # requiring `build.exists()` at this exact instant - matters
            # because `reconstruct_candidate` (BUILD's worker) walks the
            # FROZEN SOURCE tree before it ever creates `build` itself
            # (aieb_runner.artifacts.reconstruct_candidate): an abandoned
            # thread that hasn't reached that mkdir yet reads as
            # `build.exists() == False` at the moment we check, so the old
            # `phase_abandoned and build.exists()` condition fell through to
            # the "safe to clean up" branch, reported cleanup_clean=True, and
            # then the still-running abandoned thread created and started
            # writing into `build` AFTER finalization had already declared
            # cleanup complete - a real teardown race independent of the one
            # fixed above. An abandoned thread's allocation (however much of
            # it exists, now or later) is always left for host-side
            # reconciliation instead.
            if phase_abandoned:
                self._finalize(outcome, attempt_root, evidence, ())
                outcome.cleanup_clean = False  # deliberately NOT torn down - see above
                outcome.diagnostics.append(
                    "cancelled verification abandoned its evaluator past the stop-event grace period; "
                    "the build allocation is left in place for host-side reconciliation"
                )
                self._write_evidence(outcome, evidence)
            else:
                self._finalize(outcome, attempt_root, evidence, (build,))

    def run(self, config: AttemptConfig, evaluator: Evaluator, cancel_event: Event | None = None) -> AttemptOutcome:
        """Convenience wrapper preserving the original single-call contract for
        existing (local CLI) callers: engineering and verification happen in
        this same process/call, back to back. ENG-015's hosted worker instead
        calls run_engineering() and run_verification() from two
        independently-leased PostgreSQL work items (ENG015-007) - potentially
        different processes, with a real gap between them where either side
        can crash and be recovered independently."""
        outcome = self.run_engineering(config, cancel_event)
        if outcome.candidate is None:
            return outcome
        return self.run_verification(config, evaluator, outcome, cancel_event)

    def run_with_replacements(
        self,
        configs: tuple[AttemptConfig, ...],
        evaluator: Evaluator,
        policy: ReplacementPolicy,
    ) -> tuple[AttemptOutcome, ...]:
        """Run declared replacement allocations without erasing earlier evidence.

        Candidate and resource-limit outcomes are never retried.  The caller
        supplies distinct attempt IDs so every allocation has an immutable
        evidence record even when it is replaced for infrastructure reasons.
        """
        if policy.max_infrastructure_replacements < 0:
            raise ValueError("replacement limit cannot be negative")
        outcomes: list[AttemptOutcome] = []
        for index, config in enumerate(configs):
            if index > policy.max_infrastructure_replacements:
                break
            outcome = self.run(config, evaluator)
            outcomes.append(outcome)
            if not outcome.retryable:
                break
        return tuple(outcomes)
