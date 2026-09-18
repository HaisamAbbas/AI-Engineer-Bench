"""Thin adapter for the exact Harbor version qualified by ENG-001."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import yaml
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


_COMPOSE_FILE_GLOBS = (
    "docker-compose*.y*ml",  # legacy convention (every existing fixture in this repository)
    "compose*.y*ml",  # Docker's current canonical convention (compose.yaml/compose.yml)
)


# The repo-relative root of the hidden evaluator fixtures (spec section 37's "hidden fixture";
# a task's public environment definition must never mount from here - this is where a real
# answer key/held-out reference implementation lives, e.g. tests/maintainer/rag01/fixture.py).
# Resolved defensively: a real deployed install of this package may not have this directory at
# all (it only exists inside this monorepo checkout), in which case that ONE named check is
# skipped and the general escape check below still applies.
try:
    _REPO_ROOT_FOR_HIDDEN_FIXTURE_CHECK: Path | None = Path(__file__).resolve().parents[6]
    if not (_REPO_ROOT_FOR_HIDDEN_FIXTURE_CHECK / "tests" / "maintainer").is_dir():
        _REPO_ROOT_FOR_HIDDEN_FIXTURE_CHECK = None
except IndexError:
    _REPO_ROOT_FOR_HIDDEN_FIXTURE_CHECK = None


def _bind_mount_sources(compose_file: Path) -> list[str]:
    """Every host-side source path a compose file's services bind-mount, in both the short
    (`"host:container[:mode]"`) and long (`{type: bind, source: ..., target: ...}`) syntaxes.
    A named volume (`"myvolume:/container/path"`, no leading `/`/`./`/`../`/drive letter) is
    Docker-managed, not a host path, and is correctly excluded - it cannot escape anywhere."""
    try:
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8", errors="replace"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    services = data.get("services")
    if not isinstance(services, dict):
        return []
    sources: list[str] = []
    for service in services.values():
        if not isinstance(service, dict):
            continue
        for volume in service.get("volumes") or []:
            if isinstance(volume, str):
                parts = volume.split(":")
                # A Windows drive-letter host path ("C:\foo:/container") splits into an extra
                # part at its own drive-letter colon ("C", "\foo", "/container") - reassemble
                # the first two parts as one path before treating parts[0] as the source, or a
                # Windows-authored compose file's host path would be silently truncated to "C".
                if len(parts) >= 2 and len(parts[0]) == 1 and parts[1][:1] in ("\\", "/"):
                    parts = [parts[0] + ":" + parts[1], *parts[2:]]
                if len(parts) >= 2 and _looks_like_host_path(parts[0]):
                    sources.append(parts[0])
            elif isinstance(volume, dict) and volume.get("type", "bind") == "bind":
                source = volume.get("source")
                if isinstance(source, str):
                    sources.append(source)
    return sources


def _looks_like_host_path(candidate: str) -> bool:
    return candidate.startswith(("/", "./", "../", "~")) or (len(candidate) >= 2 and candidate[1] == ":")


def _is_posix_absolute(source: str) -> bool:
    # A leading "/" is POSIX-absolute UNLESS it's actually a Windows drive-relative form
    # ("/c/...", not used by Compose) - Compose host paths are POSIX (the container side is
    # always Linux, and Docker Desktop's Linux VM has its own filesystem namespace distinct
    # from the Windows host's), so treat any "/"-leading source as POSIX regardless of which
    # OS this check itself happens to run on. A drive-letter path ("C:\..." or "C:/...") is
    # NOT POSIX-absolute even though `Path(...).is_absolute()` would say True for it here on
    # native Windows.
    return source.startswith("/")


def _find_unauthorized_host_mount(task_dir: Path) -> tuple[Path, str] | None:
    """Real check, not tautological: Harbor's Docker environment builds the container from the
    TASK's own environment definition, which is where a task could actually introduce an
    unauthorized mount - the `EnvironmentConfig` this adapter constructs itself never sets
    `mounts` or `extra_docker_compose`, so checking that object (as an earlier version of this
    function did) could never find anything regardless of what any real task defines.

    Covers, as one principle rather than three separate ad hoc rules: "no contestant Docker
    socket" and "no evaluator answer-key mount" (spec sections 37/43) and, more generally, any
    attempted host-path access outside the task's own directory - a task legitimately never
    needs to bind-mount anything from outside itself, so ANY bind-mount source resolving
    outside `task_dir` is refused, with the Docker socket and this repo's hidden-fixture root
    called out explicitly for a clearer error when they are the specific offender.

    Scans every docker-compose*.y*ml AND compose*.y*ml (Docker's current canonical filename,
    with no `docker-` prefix) this task directory contains, parsed as real YAML (not a
    substring heuristic) so a relative bind-mount source resolves correctly against the
    compose file's own directory before the containment check.

    Note on cross-platform path handling: a Compose file's host-side path describes the real
    Docker host's filesystem, which is always Linux/POSIX even when this check itself happens
    to run on a Windows development machine (Docker Desktop's Linux VM has its own filesystem
    namespace, entirely distinct from the Windows host's paths). A POSIX-absolute source
    ("/var/run/docker.sock", "/etc/shadow") is therefore normalized with `PurePosixPath`, never
    handed to native `Path.resolve()` - on Windows, `Path("/var/run/docker.sock").resolve()`
    silently reinterprets it as `C:\var\run\docker.sock` ("root of the current drive"),
    destroying the exact comparison this check depends on. A POSIX-absolute source can never
    be "inside" `task_dir` (a real task directory is never itself POSIX-rooted at `/`), so it
    is unconditionally treated as escaping. Only relative and Windows-drive-style sources are
    resolved with native `Path` semantics, against the compose file's own directory.

    Returns (offending file, reason), or None if nothing escapes."""
    from posixpath import normpath as posix_normpath

    task_dir_resolved = task_dir.resolve()
    hidden_root = None
    if _REPO_ROOT_FOR_HIDDEN_FIXTURE_CHECK is not None:
        hidden_root = (_REPO_ROOT_FOR_HIDDEN_FIXTURE_CHECK / "tests" / "maintainer").resolve()

    for pattern in _COMPOSE_FILE_GLOBS:
        for compose_file in task_dir.rglob(pattern):
            compose_dir = compose_file.resolve().parent
            for source in _bind_mount_sources(compose_file):
                if _is_posix_absolute(source):
                    normalized_posix = posix_normpath(source)
                    if normalized_posix == "/var/run/docker.sock":
                        return compose_file, "mounts the Docker socket"
                    return compose_file, f"mounts a host path outside the task directory ({normalized_posix})"

                resolved_source = Path(source).resolve() if Path(source).is_absolute() else (compose_dir / source).resolve()
                if hidden_root is not None and (resolved_source == hidden_root or hidden_root in resolved_source.parents):
                    return compose_file, "mounts the hidden evaluator fixture directory (tests/maintainer/)"
                if resolved_source != task_dir_resolved and task_dir_resolved not in resolved_source.parents:
                    return compose_file, f"mounts a host path outside the task directory ({resolved_source})"
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
            # The flag is named after its flagship case (spec section 37's "no contestant
            # Docker socket"), but the check it gates is the general "no unauthorized host
            # mount" rule - Docker socket, the hidden evaluator fixture tree, and host access
            # generally are the same underlying vector (see _find_unauthorized_host_mount).
            offense = _find_unauthorized_host_mount(spec.task_dir)
            if offense is not None:
                offending_file, reason = offense
                egress_guard.close()
                raise UnhardenedBackendError(
                    f"{offending_file} {reason} - refusing to launch (ADR-12 / spec section 37: "
                    "no contestant Docker socket or evaluator answer-key mount, no host access "
                    "outside the task's own directory)"
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
