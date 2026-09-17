"""Deadline-first local attempt lifecycle for development vertical slices.

This module deliberately owns lifecycle ordering rather than inheriting it from
Harbor.  It is a deterministic local adapter, not a claim of official sandbox
isolation.  Candidate bytes are collected only after the engineering process
tree has stopped, then reconstructed into a new build allocation.
"""

from __future__ import annotations

import ctypes
import json
import multiprocessing as mp
import os
import select
import shutil
import socket
import signal
import struct
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from multiprocessing.connection import wait as wait_for_process
from pathlib import Path
from threading import Event, Thread
from typing import Callable

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
    engineering_stdout: str = ""
    engineering_stderr: str = ""
    engineering_logs_truncated: bool = False
    evidence_path: Path | None = None

    def add(self, phase: AttemptPhase) -> None:
        self.phases.append(phase.value)


Evaluator = Callable[..., dict[str, object]]

# Seconds VERIFY waits for an evaluator to honour cooperative cancellation
# before its complete process tree is forcibly stopped.
EVALUATOR_CANCEL_GRACE_SECONDS = 5.0
VERIFY_RESULT_MAX_BYTES = 8 * 1024 * 1024
ENGINEERING_LOG_MAX_BYTES = 64 * 1024


def _result_payload(kind: str, value: object) -> bytes:
    """Encode a bounded, non-executable JSON envelope for the result channel."""
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        payload = bytearray()
        for chunk in encoder.iterencode({"kind": kind, "value": value}):
            encoded = chunk.encode("utf-8")
            if len(payload) + len(encoded) > VERIFY_RESULT_MAX_BYTES:
                raise OverflowError(f"evaluator result exceeds {VERIFY_RESULT_MAX_BYTES} serialized bytes")
            payload.extend(encoded)
        return bytes(payload)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        payload = json.dumps(
            {"kind": "scorer_error", "value": f"evaluator result could not be serialized: {exc!r}"[:4096]},
            separators=(",", ":"),
        ).encode("utf-8")
        return payload


def _create_result_channel() -> tuple[object, object]:
    """Create a private result channel suitable for multiprocessing spawn.

    Unix uses a socket pair. Windows uses an anonymous pipe and explicitly
    duplicates only its write handle into the child process; unlike a named
    pipe or filesystem result path, candidate descendants do not inherit it.
    """
    if os.name != "nt":
        receiver, sender = socket.socketpair()
        receiver.setblocking(False)
        return receiver, sender

    import _winapi
    import msvcrt
    from multiprocessing.reduction import DupHandle

    read_handle, write_handle = _winapi.CreatePipe(None, 0)
    try:
        child_endpoint = DupHandle(write_handle, _winapi.DUPLICATE_SAME_ACCESS)
    finally:
        _winapi.CloseHandle(write_handle)
    read_fd = msvcrt.open_osfhandle(read_handle, os.O_RDONLY | os.O_BINARY)
    return os.fdopen(read_fd, "rb", buffering=0), child_endpoint


def _open_child_result_writer(endpoint: object) -> socket.socket | object:
    if isinstance(endpoint, socket.socket):
        return endpoint
    if os.name == "nt":
        import msvcrt

        handle = endpoint.detach()  # multiprocessing.reduction.DupHandle
        writer_fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        return os.fdopen(writer_fd, "wb", buffering=0)
    raise TypeError("unsupported result channel endpoint")


def _send_worker_result(result_writer: socket.socket | object, kind: str, value: object) -> None:
    """Send one length-prefixed JSON result over the private capability channel."""
    payload = _result_payload(kind, value)
    frame = struct.pack("!I", len(payload)) + payload
    if isinstance(result_writer, socket.socket):
        result_writer.sendall(frame)
        return
    offset = 0
    while offset < len(frame):
        offset += os.write(result_writer.fileno(), frame[offset:])


def _decode_worker_result(payload: bytes) -> tuple[str, object]:
    """Parse the deliberately small JSON result schema; never execute data."""
    def reject_constant(value: str) -> object:
        raise ValueError(f"invalid JSON constant: {value}")

    decoded = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    if not isinstance(decoded, dict) or set(decoded) != {"kind", "value"}:
        raise ValueError("worker result has an invalid envelope")
    kind = decoded["kind"]
    if kind not in {"ok", "cancelled", "candidate_unavailable", "scorer_error", "build_error"}:
        raise ValueError("worker result has an invalid kind")
    value = decoded["value"]
    if kind == "ok" and not isinstance(value, dict):
        raise ValueError("evaluator result must be a JSON object")
    if kind != "ok" and value is not None and not isinstance(value, str):
        raise ValueError("worker error result must be a string or null")
    return kind, value


def _verify_subprocess_entrypoint(
    evaluate: Evaluator, build: Path, stop: "mp.synchronize.Event", result_endpoint: object,
    startup_event: "mp.synchronize.Event",
) -> None:
    """Runs the trusted evaluator in an OWNED, forcibly-killable child
    process (review finding #2: "an uncooperative evaluator remains neither
    terminated nor safely contained" - a Python thread can never be
    preempted, so an evaluator that ignores the cooperative `stop` signal
    could only ever be abandoned, never actually stopped, while it went on
    running arbitrary code - subprocess/network/spend effects included -
    indefinitely). A subprocess can be forcibly terminated regardless of
    whether it cooperates. It establishes a Unix process group before
    evaluator code runs; Windows waits until its parent assigns it to a Job
    Object. Must be module-level so it is picklable for multiprocessing's
    spawn start method."""
    if os.name != "nt":
        os.setsid()
        startup_event.set()
    elif not startup_event.wait(timeout=30):
        return
    result_writer = _open_child_result_writer(result_endpoint)
    try:
        _send_worker_result(result_writer, "ok", _invoke_evaluator(evaluate, build, stop))
    except CancelledError:
        _send_worker_result(result_writer, "cancelled", None)
    except CandidateUnavailableError as exc:
        _send_worker_result(result_writer, "candidate_unavailable", str(exc))
    except Exception as exc:  # noqa: BLE001 - a trusted evaluator's own bug, never a candidate verdict
        _send_worker_result(result_writer, "scorer_error", repr(exc)[:4096])
    finally:
        result_writer.close()


def _build_subprocess_entrypoint(
    frozen_source: Path,
    destination: Path,
    stored: StoredCandidate,
    store: ArtifactStore,
    principal_scope: str,
    result_endpoint: object,
    startup_event: "mp.synchronize.Event",
) -> None:
    """Reconstruct in a killable process so BUILD I/O cannot block cancel."""
    if os.name != "nt":
        os.setsid()
        startup_event.set()
    elif not startup_event.wait(timeout=30):
        return
    result_writer = _open_child_result_writer(result_endpoint)
    try:
        reconstruct_candidate(
            frozen_source=frozen_source,
            destination=destination,
            stored=stored,
            store=store,
            principal_scope=principal_scope,
        )
        _send_worker_result(result_writer, "ok", {})
    except ArtifactError as exc:
        _send_worker_result(result_writer, "build_error", str(exc)[:4096])
    except Exception as exc:  # noqa: BLE001 - storage and host failures are infrastructure failures
        _send_worker_result(result_writer, "build_error", f"{type(exc).__name__}: {exc}"[:4096])
    finally:
        close_store = getattr(store, "close", None)
        if callable(close_store):
            try:
                close_store()
            except Exception:
                pass
        result_writer.close()


@dataclass(frozen=True)
class VerifyRun:
    """Outcome of one isolated (subprocess) VERIFY invocation.

    `cancelled=True` covers BOTH a cooperative stop (the evaluator itself
    raised CancelledError) and a forced kill after the grace period (an
    uncooperative evaluator that had to be terminated) - either way nothing
    it produced is ever scored. Once the owned process group or Job Object is
    stopped, the evaluator and its descendants cannot keep using the build
    allocation, so cleanup is safe."""

    cancelled: bool
    kind: str  # "ok" | "candidate_unavailable" | "scorer_error" | "crashed"
    evaluation: dict[str, object] | None = None
    error: str | None = None


@dataclass(frozen=True)
class BuildRun:
    cancelled: bool
    kind: str  # "ok" | "build_error" | "crashed"
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
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _ExtendedLimitInformation()
        info.basic_limit_information.limit_flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            # multiprocessing.Process wraps its native Windows handle in
            # the platform-specific Popen object.
            process_handle = getattr(getattr(process, "_popen", None), "_handle", None)
        if process_handle is None:
            kernel32.CloseHandle(job)
            raise OSError("owned process has no assignable Windows process handle")
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process_handle)):
            kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        process._aieb_job_handle = job  # type: ignore[attr-defined]

    @staticmethod
    def _close_windows_job(process: subprocess.Popen[str]) -> None:
        job = getattr(process, "_aieb_job_handle", None)
        if job:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(job))
            process._aieb_job_handle = None  # type: ignore[attr-defined]

    @staticmethod
    def _stop_isolated_process_tree(process: object) -> None:
        """Stop an isolated BUILD/VERIFY worker and all its descendants."""
        if os.name == "nt":
            # Closing this KILL_ON_JOB_CLOSE handle terminates the whole job,
            # including children that outlived the evaluator process itself.
            LocalAttemptRunner._close_windows_job(process)  # type: ignore[arg-type]
            if process.is_alive():  # type: ignore[attr-defined]
                process.join(timeout=5)  # type: ignore[attr-defined]
            if process.is_alive():  # type: ignore[attr-defined]
                process.kill()  # type: ignore[attr-defined]
                process.join(timeout=5)  # type: ignore[attr-defined]
            return

        process_group = process.pid  # type: ignore[attr-defined]
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # Give cooperative subprocesses a short opportunity to exit, then
        # escalate against the group even if its original parent has exited.
        # Keep the root process unreaped until both group signals have been
        # sent; reaping it first could release its PID/PGID for reuse.
        time.sleep(0.5)
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.join(timeout=5)  # type: ignore[attr-defined]
        if process.is_alive():  # type: ignore[attr-defined]
            process.join(timeout=5)  # type: ignore[attr-defined]
        if process.is_alive():  # type: ignore[attr-defined]
            process.kill()  # type: ignore[attr-defined]
            process.join(timeout=5)  # type: ignore[attr-defined]

    @staticmethod
    def _stop_tree(process: subprocess.Popen[str]) -> bool:
        """Synchronously stop the owned process and its descendants."""
        def close_streams() -> None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        try:
            if os.name == "nt":
                # Closing the job kills descendants even when a child has escaped
                # the command interpreter's ordinary process tree. A root that
                # already exited may still have left a long-lived child behind.
                LocalAttemptRunner._close_windows_job(process)
                if process.poll() is None:
                    subprocess.run(
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
            process_group = process.pid  # _start uses start_new_session=True
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            # The editor can exit while a candidate server remains in its
            # session. Always signal the group, even when poll() reaped the
            # original process first.
            time.sleep(0.05)
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            close_streams()
            for _ in range(20):
                try:
                    os.killpg(process_group, 0)
                except ProcessLookupError:
                    return True
                except PermissionError:
                    return False
                time.sleep(0.05)
            return False
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
        options: dict[str, object] = {
            "cwd": workspace, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
            "text": True, "encoding": "utf-8", "errors": "replace",
        }
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
    def _drain_output(stream, result: list[tuple[str, bool]]) -> None:
        """Drain a pipe continuously while retaining only a bounded prefix."""
        chunks: list[str] = []
        retained = 0
        truncated = False
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                if truncated:
                    continue
                encoded = chunk.encode("utf-8", errors="replace")
                remaining = ENGINEERING_LOG_MAX_BYTES - retained
                if len(encoded) > remaining:
                    if remaining > 0:
                        chunks.append(encoded[:remaining].decode("utf-8", errors="ignore"))
                    truncated = True
                else:
                    chunks.append(chunk)
                    retained += len(encoded)
        except (OSError, ValueError):
            # Tree teardown closes the pipes if a descendant kept them open.
            pass
        result.append(("".join(chunks), truncated))

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
            "engineering_stdout": outcome.engineering_stdout,
            "engineering_stderr": outcome.engineering_stderr,
            "engineering_logs_truncated": outcome.engineering_logs_truncated,
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
            stdout_capture: list[tuple[str, bool]] = []
            stderr_capture: list[tuple[str, bool]] = []
            stdout_reader = Thread(target=self._drain_output, args=(process.stdout, stdout_capture), daemon=True)
            stderr_reader = Thread(target=self._drain_output, args=(process.stderr, stderr_capture), daemon=True)
            stdout_reader.start()
            stderr_reader.start()
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
                    if process.returncode not in (0, None):
                        outcome.attribution = FailureAttribution.CANDIDATE_BUILD_FAILURE
                        outcome.diagnostics.append(f"engineering command exited {process.returncode}")
                        return outcome
            except subprocess.TimeoutExpired:
                outcome.termination_reason = "deadline"
                outcome.attribution = FailureAttribution.RESOURCE_LIMIT
            finally:
                outcome.add(AttemptPhase.STOP)
                stopped = self._stop_tree(process)
                stdout_reader.join(timeout=5)
                stderr_reader.join(timeout=5)
                if stdout_capture:
                    outcome.engineering_stdout = stdout_capture[0][0]
                    outcome.engineering_logs_truncated |= stdout_capture[0][1]
                if stderr_capture:
                    outcome.engineering_stderr = stderr_capture[0][0]
                    outcome.engineering_logs_truncated |= stderr_capture[0][1]
                if not stopped:
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
                    engineering_stdout=outcome.engineering_stdout,
                    engineering_stderr=outcome.engineering_stderr,
                    engineering_logs_truncated=outcome.engineering_logs_truncated,
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
    def _poll_worker_result(result_receiver: object, received: bytearray) -> tuple[str, object] | str | None:
        """Drain available result bytes without waiting for the child to exit."""
        if isinstance(result_receiver, socket.socket):
            try:
                readable, _, _ = select.select([result_receiver], [], [], 0.1)
                chunk = result_receiver.recv(64 * 1024) if readable else None
            except OSError as exc:
                return f"result channel failed: {exc}"
        else:
            import msvcrt

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.PeekNamedPipe.argtypes = (
                wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
            )
            kernel32.PeekNamedPipe.restype = wintypes.BOOL
            available = wintypes.DWORD()
            handle = msvcrt.get_osfhandle(result_receiver.fileno())
            if not kernel32.PeekNamedPipe(
                wintypes.HANDLE(handle), None, 0, None, ctypes.byref(available), None,
            ):
                error_code = ctypes.get_last_error()
                if error_code == 109:  # ERROR_BROKEN_PIPE
                    return "worker closed the result channel without a complete result"
                return f"result channel failed: Windows error {error_code}"
            if available.value:
                try:
                    chunk = os.read(result_receiver.fileno(), min(available.value, 64 * 1024))
                except OSError as exc:
                    return f"result channel failed: {exc}"
            else:
                chunk = None
        if chunk == b"":
            return "worker closed the result channel without a complete result"
        if chunk:
            received.extend(chunk)
        if len(received) < 4:
            return None
        frame_size = struct.unpack("!I", received[:4])[0]
        if frame_size > VERIFY_RESULT_MAX_BYTES:
            return f"worker result exceeds {VERIFY_RESULT_MAX_BYTES} serialized bytes"
        if len(received) < 4 + frame_size:
            return None
        payload = bytes(received[4 : 4 + frame_size])
        try:
            return _decode_worker_result(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError) as exc:
            return f"invalid worker result: {exc}"

    @staticmethod
    def _run_verify_isolated(evaluate: Evaluator, build: Path, cancel_event: Event | None) -> VerifyRun:
        """Run VERIFY in an owned process tree and receive bounded JSON over
        a private capability channel. Only the child endpoint is passed to
        the evaluator process; no candidate-visible path can replace its
        result."""
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        startup_event = ctx.Event()
        result_receiver, result_endpoint = _create_result_channel()
        process = ctx.Process(
            target=_verify_subprocess_entrypoint,
            args=(evaluate, build, stop_event, result_endpoint, startup_event),
            daemon=True,
        )
        started = False
        tree_stopped = False
        received = bytearray()
        try:
            process.start()
            started = True
            if isinstance(result_endpoint, socket.socket):
                result_endpoint.close()
            if os.name == "nt":
                # The entrypoint waits here before invoking evaluator code,
                # closing the race between Process.start() and Job assignment.
                try:
                    LocalAttemptRunner._attach_windows_kill_job(process)  # type: ignore[arg-type]
                except OSError as exc:
                    process.terminate()
                    process.join(timeout=5)
                    return VerifyRun(cancelled=False, kind="crashed", error=f"could not contain VERIFY process tree: {exc}")
                startup_event.set()
            elif not startup_event.wait(timeout=5):
                return VerifyRun(cancelled=False, kind="crashed", error="VERIFY child failed to establish its process group")

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    stop_event.set()  # cooperative signal - an evaluator honouring it exits promptly
                    process.join(timeout=EVALUATOR_CANCEL_GRACE_SECONDS)
                    LocalAttemptRunner._stop_isolated_process_tree(process)
                    tree_stopped = True
                    return VerifyRun(cancelled=True, kind="cancelled")

                result = LocalAttemptRunner._poll_worker_result(result_receiver, received)
                if isinstance(result, str):
                    LocalAttemptRunner._stop_isolated_process_tree(process)
                    tree_stopped = True
                    return VerifyRun(cancelled=False, kind="crashed", error=result)
                if result is not None:
                    kind, value = result
                    # Evaluators may have launched a candidate server and
                    # returned without stopping it. Tear down the group/job
                    # before the build allocation is cleaned up.
                    LocalAttemptRunner._stop_isolated_process_tree(process)
                    tree_stopped = True
                    if cancel_event is not None and cancel_event.is_set():
                        return VerifyRun(cancelled=True, kind="cancelled")
                    if kind == "ok":
                        return VerifyRun(cancelled=False, kind="ok", evaluation=value)
                    if kind == "cancelled":
                        return VerifyRun(cancelled=True, kind="cancelled")
                    return VerifyRun(cancelled=False, kind=kind, error=value)

                if wait_for_process([process.sentinel], timeout=0):
                    # The child has exited. Bytes it wrote before exiting can
                    # still be buffered in the channel: a large result is
                    # fully written and then the child exits while the parent
                    # has not yet read the tail - especially on a Unix
                    # socketpair, whose send buffer holds only a few hundred KB,
                    # so a multi-MiB result is drained across several reads and
                    # the last chunk routinely remains buffered at exit. Drain
                    # what is left before concluding there is no result;
                    # _poll_worker_result returns the assembled result, or the
                    # "closed" error once the channel reaches EOF, so this
                    # cannot loop forever.
                    while True:
                        drained = LocalAttemptRunner._poll_worker_result(result_receiver, received)
                        if drained is None:
                            continue
                        LocalAttemptRunner._stop_isolated_process_tree(process)
                        tree_stopped = True
                        if cancel_event is not None and cancel_event.is_set():
                            return VerifyRun(cancelled=True, kind="cancelled")
                        if isinstance(drained, str):
                            return VerifyRun(cancelled=False, kind="crashed", error=drained)
                        kind, value = drained
                        if kind == "ok":
                            return VerifyRun(cancelled=False, kind="ok", evaluation=value)
                        if kind == "cancelled":
                            return VerifyRun(cancelled=True, kind="cancelled")
                        return VerifyRun(cancelled=False, kind=kind, error=value)
                time.sleep(0.1)
        finally:
            if started and not tree_stopped:
                LocalAttemptRunner._stop_isolated_process_tree(process)
            result_receiver.close()
            if isinstance(result_endpoint, socket.socket):
                result_endpoint.close()
            elif not started:
                try:
                    import _winapi
                    _winapi.CloseHandle(result_endpoint._handle)
                except (AttributeError, OSError):
                    pass

    @staticmethod
    def _run_build_isolated(
        frozen_source: Path,
        destination: Path,
        stored: StoredCandidate,
        store: ArtifactStore,
        principal_scope: str,
        cancel_event: Event | None,
    ) -> BuildRun:
        """Run candidate reconstruction in a killable process boundary."""
        ctx = mp.get_context("spawn")
        startup_event = ctx.Event()
        result_receiver, result_endpoint = _create_result_channel()
        process = ctx.Process(
            target=_build_subprocess_entrypoint,
            args=(frozen_source, destination, stored, store, principal_scope, result_endpoint, startup_event),
            daemon=True,
        )
        started = False
        tree_stopped = False
        received = bytearray()
        try:
            process.start()
            started = True
            if isinstance(result_endpoint, socket.socket):
                result_endpoint.close()
            if os.name == "nt":
                try:
                    LocalAttemptRunner._attach_windows_kill_job(process)  # type: ignore[arg-type]
                except OSError as exc:
                    process.terminate()
                    process.join(timeout=5)
                    return BuildRun(cancelled=False, kind="crashed", error=f"could not contain BUILD process tree: {exc}")
                startup_event.set()
            elif not startup_event.wait(timeout=5):
                return BuildRun(cancelled=False, kind="crashed", error="BUILD child failed to establish its process group")

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    LocalAttemptRunner._stop_isolated_process_tree(process)
                    tree_stopped = True
                    return BuildRun(cancelled=True, kind="cancelled")
                result = LocalAttemptRunner._poll_worker_result(result_receiver, received)
                if isinstance(result, str):
                    LocalAttemptRunner._stop_isolated_process_tree(process)
                    tree_stopped = True
                    return BuildRun(cancelled=False, kind="crashed", error=result)
                if result is not None:
                    kind, value = result
                    LocalAttemptRunner._stop_isolated_process_tree(process)
                    tree_stopped = True
                    if cancel_event is not None and cancel_event.is_set():
                        return BuildRun(cancelled=True, kind="cancelled")
                    if kind == "ok":
                        return BuildRun(cancelled=False, kind="ok")
                    return BuildRun(cancelled=False, kind="build_error", error=str(value))
                if wait_for_process([process.sentinel], timeout=0):
                    if cancel_event is not None and cancel_event.is_set():
                        LocalAttemptRunner._stop_isolated_process_tree(process)
                        tree_stopped = True
                        return BuildRun(cancelled=True, kind="cancelled")
                    return BuildRun(
                        cancelled=False,
                        kind="crashed",
                        error=f"BUILD subprocess exited (code {process.exitcode}) without reporting a result",
                    )
        except Exception as exc:  # process startup/pickling failures are host failures
            return BuildRun(cancelled=False, kind="crashed", error=f"could not start BUILD subprocess: {exc}")
        finally:
            if started and not tree_stopped:
                LocalAttemptRunner._stop_isolated_process_tree(process)
            result_receiver.close()
            if isinstance(result_endpoint, socket.socket):
                result_endpoint.close()
            elif not started:
                try:
                    import _winapi
                    _winapi.CloseHandle(result_endpoint._handle)
                except (AttributeError, OSError):
                    pass

    def run_verification(
        self, config: AttemptConfig, evaluator: Evaluator, outcome: AttemptOutcome, cancel_event: Event | None = None,
    ) -> AttemptOutcome:
        """BUILD -> VERIFY -> FINALIZE/CLEANUP, given an outcome that already
        carries a collected `candidate` - either from this same process's own
        prior run_engineering() call, or reconstructed from persisted
        artifact-store references by an entirely different worker recovering
        after a crash (ENG015-007). Mutates and returns the same outcome.

        BUILD runs in an owned process group/Windows Job Object, so cancellation
        can terminate reconstruction even while filesystem or PostgreSQL I/O
        is stalled. The parent waits for its process tree to stop before
        removing the build allocation.

        VERIFY runs the ARBITRARY, less-trusted per-task evaluator via
        `_run_verify_isolated`, in an OWNED, forcibly-killable subprocess
        (review finding #2): the evaluator runs in an owned process group on
        Unix and a Windows Job Object on Windows. An evaluator that honours
        the cooperative stop signal exits promptly (as `CancelledError`);
        one that ignores it has the whole process tree terminated after
        EVALUATOR_CANCEL_GRACE_SECONDS. VERIFY does not return until the
        evaluator and its descendants are stopped.

        Cancellation observed during VERIFY means nothing from that phase is
        ever scored or persisted.

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
        try:
            if cancel_event is not None and cancel_event.is_set():
                outcome.execution_validity = ExecutionValidity.CANCELLED
                outcome.termination_reason = "cancelled"
                return outcome

            outcome.add(AttemptPhase.BUILD)
            build_run = self._run_build_isolated(
                config.frozen_source,
                build,
                outcome.candidate,
                self.store,
                config.access_scope,
                cancel_event,
            )
            if build_run.cancelled:
                outcome.execution_validity = ExecutionValidity.CANCELLED
                outcome.termination_reason = "cancelled"
                return outcome
            if build_run.kind != "ok":
                outcome.execution_validity = ExecutionValidity.INFRASTRUCTURE_INVALID
                outcome.attribution = FailureAttribution.HOST_FAILURE
                outcome.diagnostics.append(
                    f"candidate reconstruction failed (storage/reference integrity): {build_run.error}"
                )
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
            # BUILD and VERIFY process trees are stopped before teardown.
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
