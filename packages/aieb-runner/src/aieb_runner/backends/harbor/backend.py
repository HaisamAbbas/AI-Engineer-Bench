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
    UnhardenedBackendError,
)
from aieb_runner.backends.egress_proxy import EgressGuardProxy


HARBOR_VERSION = "0.22.0"

# ADR-12: this backend runs Harbor's Docker environment - a non-privileged container on the
# host kernel, not a VM or equivalent hardened isolation. It must never be used for an
# allocation that requires hardened isolation; `launch()` refuses that outright rather than
# silently running it anyway.
PROVIDES_HARDENED_ISOLATION = False


@dataclass
class _RunningTrial:
    spec: ExecutionSpec
    trial: Trial
    task: asyncio.Task[object]
    egress_guard: EgressGuardProxy | None


def _find_docker_socket_mount(task_dir: Path) -> Path | None:
    """Real check, not tautological: Harbor's Docker environment builds the container from the
    TASK's own environment definition (`task_dir/environment/docker-compose.yaml` in every
    fixture this repository has), which is where a task could actually introduce a Docker
    socket mount - the `EnvironmentConfig` this adapter constructs itself never sets `mounts`
    or `extra_docker_compose`, so checking that object (as an earlier version of this function
    did) could never find anything regardless of what any real task defines. Scans every
    docker-compose*.y*ml this task directory contains for a literal docker.sock reference.
    Returns the offending file, or None if none mounts it."""
    for compose_file in task_dir.rglob("docker-compose*.y*ml"):
        try:
            contents = compose_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "docker.sock" in contents:
            return compose_file
    return None


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
        if spec.isolation.hardened_isolation_required and not PROVIDES_HARDENED_ISOLATION:
            raise UnhardenedBackendError(
                "HarborBackend runs a non-privileged Docker container on the host kernel, "
                "not a VM or equivalent hardened isolation (ADR-12); it refuses to launch an "
                "allocation marked hardened_isolation_required=True rather than running it "
                "anyway. No hardened backend is available - the 'Official VM provider' "
                "decision remains deferred (see DECISIONS.md)."
            )
        if any(run.spec.trial_name == spec.trial_name for run in self._runs.values()):
            raise ValueError(f"trial name already active: {spec.trial_name}")
        spec.runs_dir.mkdir(parents=True, exist_ok=True)

        # Deny-by-default egress (ADR-12/spec section 37): every outbound request from inside
        # the container is routed through a guard proxy that denies and logs anything not on
        # the isolation policy's allowlist, including cloud-metadata hosts unconditionally.
        egress_guard = EgressGuardProxy(spec.isolation, bind_host="0.0.0.0", advertised_host="host.docker.internal")
        env = {**egress_guard.env_vars()}

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
                env=env,
            ),
            verifier=VerifierConfig(),
        )
        if spec.isolation.deny_docker_socket:
            offending_file = _find_docker_socket_mount(spec.task_dir)
            if offending_file is not None:
                egress_guard.close()
                raise UnhardenedBackendError(
                    f"isolation policy denies the Docker socket, but {offending_file} appears "
                    "to mount it - refusing to launch (ADR-12 / spec section 37 - no contestant "
                    "Docker socket)"
                )
        try:
            trial = await Trial.create(config)
        except Exception:
            egress_guard.close()
            raise
        handle = ExecutionHandle(str(uuid4()))
        task = asyncio.create_task(trial.run(), name=f"harbor-{spec.trial_name}")
        self._runs[handle.id] = _RunningTrial(spec=spec, trial=trial, task=task, egress_guard=egress_guard)
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
        if run.egress_guard is not None:
            run.egress_guard.close()
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
