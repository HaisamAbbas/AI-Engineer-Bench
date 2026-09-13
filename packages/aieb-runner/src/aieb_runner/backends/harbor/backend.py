"""Thin adapter for the exact Harbor version qualified by ENG-001."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    ResourceMode,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.trial.trial import Trial

from aieb_runner.backends.base import (
    CandidateArtifacts,
    CapabilityCheck,
    CapabilityReport,
    CleanupReport,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
)


HARBOR_VERSION = "0.22.0"


@dataclass
class _RunningTrial:
    spec: ExecutionSpec
    trial: Trial
    task: asyncio.Task[object]


class HarborBackend:
    """Translate AIEB-owned execution calls to Harbor without leaking its models."""

    def __init__(self) -> None:
        self._runs: dict[str, _RunningTrial] = {}

    async def preflight(self) -> CapabilityReport:
        installed_version = version("harbor")
        checks: list[CapabilityCheck] = [
            CapabilityCheck(
                "exact Harbor package",
                installed_version == HARBOR_VERSION,
                f"installed={installed_version}; required={HARBOR_VERSION}",
            )
        ]
        try:
            DockerEnvironment.preflight()
            daemon = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}/{{.ServerVersion}}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            checks.append(CapabilityCheck("Docker daemon", True, daemon))
        except (SystemExit, subprocess.SubprocessError) as exc:
            checks.append(CapabilityCheck("Docker daemon", False, str(exc)))

        resources = DockerEnvironment.resource_capabilities()
        checks.extend(
            (
                CapabilityCheck(
                    "CPU hard limit",
                    resources.cpu_limit,
                    "Harbor Docker resource capability declaration",
                ),
                CapabilityCheck(
                    "memory hard limit",
                    resources.memory_limit,
                    "Harbor Docker resource capability declaration",
                ),
                CapabilityCheck(
                    "usage accounting",
                    None,
                    "entrant-specific; deterministic fixture intentionally reports unknown",
                ),
            )
        )
        return CapabilityReport("harbor-docker", installed_version, tuple(checks))

    async def launch(self, spec: ExecutionSpec) -> ExecutionHandle:
        if any(run.spec.trial_name == spec.trial_name for run in self._runs.values()):
            raise ValueError(f"trial name already active: {spec.trial_name}")
        spec.runs_dir.mkdir(parents=True, exist_ok=True)
        config = TrialConfig(
            task=TaskConfig(path=spec.task_dir.resolve()),
            trial_name=spec.trial_name,
            trials_dir=spec.runs_dir.resolve(),
            agent=AgentConfig(
                import_path=spec.agent_import_path,
                override_timeout_sec=spec.agent_timeout_sec,
            ),
            environment=EnvironmentConfig(
                type="docker",
                delete=True,
                cpu_enforcement_policy=ResourceMode.LIMIT,
                memory_enforcement_policy=ResourceMode.LIMIT,
                override_cpus=spec.cpu_limit,
                override_memory_mb=spec.memory_limit_mb,
            ),
            verifier=VerifierConfig(),
        )
        trial = await Trial.create(config)
        handle = ExecutionHandle(str(uuid4()))
        task = asyncio.create_task(trial.run(), name=f"harbor-{spec.trial_name}")
        self._runs[handle.id] = _RunningTrial(spec=spec, trial=trial, task=task)
        return handle

    def _get(self, handle: ExecutionHandle) -> _RunningTrial:
        try:
            return self._runs[handle.id]
        except KeyError as exc:
            raise KeyError(f"unknown execution handle: {handle.id}") from exc

    async def status(self, handle: ExecutionHandle) -> ExecutionStatus:
        run = self._get(handle)
        if not run.task.done():
            return ExecutionStatus(ExecutionState.RUNNING)
        if run.task.cancelled():
            return ExecutionStatus(ExecutionState.CANCELLED)
        exception = run.task.exception()
        if exception is not None:
            return ExecutionStatus(ExecutionState.FAILED, repr(exception))
        return ExecutionStatus(ExecutionState.COMPLETED)

    async def stop(self, handle: ExecutionHandle, reason: str) -> None:
        run = self._get(handle)
        if run.task.done():
            return
        run.task.cancel(f"AIEB stop: {reason}")
        try:
            await run.task
        except asyncio.CancelledError:
            pass

    async def collect(self, handle: ExecutionHandle) -> CandidateArtifacts:
        run = self._get(handle)
        if not run.task.done():
            raise RuntimeError("cannot collect a running trial")
        if run.task.cancelled():
            raise RuntimeError("cancelled trial has no qualified candidate result")
        exception = run.task.exception()
        if exception is not None:
            raise RuntimeError("Harbor trial failed outside its result envelope") from exception

        trial_dir = run.spec.runs_dir.resolve() / run.spec.trial_name
        result_path = trial_dir / "result.json"
        manifest_path = trial_dir / "artifacts" / "manifest.json"
        if not result_path.is_file() or not manifest_path.is_file():
            raise FileNotFoundError("Harbor did not produce result and artifact manifests")
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, list) or not all(
            isinstance(entry, dict) for entry in raw_manifest
        ):
            raise ValueError("unexpected Harbor artifact manifest shape")
        return CandidateArtifacts(
            trial_dir=trial_dir,
            result_path=result_path,
            manifest_path=manifest_path,
            manifest=tuple(raw_manifest),
        )

    async def cleanup(self, handle: ExecutionHandle) -> CleanupReport:
        run = self._get(handle)
        project_fragments = (
            f"{run.spec.trial_name}__env",
            f"{run.spec.trial_name}__verifier__trial",
        )
        remaining: list[str] = []
        for fragment in project_fragments:
            completed = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name={fragment}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            remaining.extend(line for line in completed.stdout.splitlines() if line)
        return CleanupReport(not remaining, tuple(sorted(set(remaining))))
