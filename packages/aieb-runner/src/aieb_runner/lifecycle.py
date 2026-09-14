"""Deadline-first local attempt lifecycle for development vertical slices.

This module deliberately owns lifecycle ordering rather than inheriting it from
Harbor.  It is a deterministic local adapter, not a claim of official sandbox
isolation.  Candidate bytes are collected only after the engineering process
tree has stopped, then reconstructed into a new build allocation.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
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


Evaluator = Callable[[Path], dict[str, object]]


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

    def run(self, config: AttemptConfig, evaluator: Evaluator) -> AttemptOutcome:
        outcome = AttemptOutcome(config.attempt_id)
        attempt_root = config.work_root / config.attempt_id
        engineer = attempt_root / "engineer"
        build = attempt_root / "build"
        evidence = attempt_root / "attempt.json"
        process: subprocess.Popen[str] | None = None
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
                process.communicate(timeout=config.engineering.deadline_sec)
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

            outcome.add(AttemptPhase.BUILD)
            try:
                reconstruct_candidate(
                    frozen_source=config.frozen_source,
                    destination=build,
                    stored=outcome.candidate,
                    store=self.store,
                    principal_scope=config.access_scope,
                )
            except ArtifactError as exc:
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.CONTRACT_VIOLATION
                outcome.attribution = FailureAttribution.CANDIDATE_BUILD_FAILURE
                outcome.diagnostics.append(str(exc))
                return outcome

            outcome.add(AttemptPhase.VERIFY)
            try:
                outcome.evaluation = evaluator(build)
            except RuntimeError as exc:
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.FAIL
                outcome.attribution = FailureAttribution.CANDIDATE_RUNTIME_FAILURE
                outcome.diagnostics.append(str(exc))
                return outcome
            except Exception as exc:  # trusted scorer failure must never become a verdict
                outcome.attribution = FailureAttribution.SCORER_ERROR
                outcome.diagnostics.append(f"trusted evaluator failed: {exc!r}")
                return outcome
            if not bool(outcome.evaluation.get("pass")):
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.FAIL
            else:
                outcome.execution_validity = ExecutionValidity.VALID
                outcome.verdict = Verdict.PASS
            return outcome
        finally:
            outcome.add(AttemptPhase.FINALIZE)
            try:
                if attempt_root.exists():
                    self._write_evidence(outcome, evidence)
                # Preserve the immutable artifact references and attempt JSON; only
                # allocations carrying writable candidate state are removed.
                outcome.add(AttemptPhase.CLEANUP)
                for allocation in (engineer, build):
                    if allocation.exists():
                        shutil.rmtree(allocation)
                outcome.cleanup_clean = not engineer.exists() and not build.exists()
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
