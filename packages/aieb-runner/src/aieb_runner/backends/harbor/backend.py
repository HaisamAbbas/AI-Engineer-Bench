"""Thin adapter for the exact Harbor version qualified by ENG-001."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import yaml
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import NetworkMode, TaskConfig as HarborTaskConfig
from harbor.models.task.verifier_mode import (
    resolve_step_verifier_mode,
    resolve_task_verifier_mode,
)
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    ResourceMode,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.trial.network_policy import resolve_trial_network_plan
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
    IsolationPolicy,
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


class _ComposeScanError(ValueError):
    """Raised when a compose file cannot be checked safely: unparseable YAML, an unknown
    Compose tag (`!override`/`!merge`/`!reset`, or any other tag a SafeLoader cannot
    construct), an unresolvable required interpolation, or a bind source still containing an
    interpolation marker. The mount scan treats these as escaping by default (fail-closed): a
    task file that cannot be PROVEN safe is refused, never silently skipped."""


def _parse_dotenv(dotenv_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _compose_interpolation_env(compose_file: Path) -> dict[str, str]:
    """Compose interpolation variables for one compose file: its sibling `.env` file plus the
    process environment. Matches Docker Compose precedence: the caller's environment wins over
    the `.env` file. Resolving from `.env` is the security-relevant part - an attacker can
    smuggle `/var/run/docker.sock` through a variable defined in their own `.env`, so the scan
    must see it."""
    dotenv = _parse_dotenv(compose_file.with_name(".env"))
    return {**dotenv, **dict(os.environ)}


_INTERPOLATION_PATTERN = re.compile(r"\$\$|\$(?:\{([^{}\r\n]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_UNSET = object()


def _resolve_interpolation(name_and_op: str, env: dict[str, str]) -> str:
    """Resolve one `${...}` or `$NAME` Compose interpolation expression. Supports the Compose
    forms `${VAR}`, `${VAR:-default}`, `${VAR-default}`, `${VAR:?error}`, `${VAR?error}` and
    `${VAR:+alternative}`. An unset plain variable resolves to "" (Compose semantics); a
    REQUIRED interpolation that is unset raises - a file whose required variables are missing
    is uncheckable, which the fail-closed scan refuses."""
    name = name_and_op
    op = ""
    for candidate in (":-", "-", ":?", "?", ":+", "+"):
        if candidate in name_and_op:
            name, _, word = name_and_op.partition(candidate)
            op = candidate
            break
    if not re.fullmatch(r"[A-Za-z0-9_.]+", name):
        raise _ComposeScanError(f"unsupported interpolation expression {name_and_op!r}")
    raw = env.get(name, _UNSET)
    if op == ":-":
        return word if (raw is _UNSET or raw == "") else raw
    if op == "-":
        return word if raw is _UNSET else raw
    if op == ":?":
        if raw is _UNSET or raw == "":
            raise _ComposeScanError(f"required interpolation ${{{name_and_op}}} is not set")
        return raw
    if op == "?":
        if raw is _UNSET:
            raise _ComposeScanError(f"required interpolation ${{{name_and_op}}} is not set")
        return raw
    if op == ":+":
        return "" if (raw is _UNSET or raw == "") else word
    if op == "+":
        return "" if raw is _UNSET else word
    return "" if raw is _UNSET else raw


def _resolve_interpolations(value: str, env: dict[str, str]) -> str:
    if "$" not in value:
        return value

    def _sub(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        if match.group(2) is not None:
            return "" if env.get(match.group(2), _UNSET) is _UNSET else env[match.group(2)]
        return _resolve_interpolation(match.group(1), env)

    return _INTERPOLATION_PATTERN.sub(_sub, value)


def _read_and_resolve_compose(compose_file: Path) -> dict:
    """Read a compose file, resolve Compose `${VAR}` interpolation (against its sibling `.env`
    and the process environment), then strictly parse it. Any parse/interpolation failure, or
    any tag SafeLoader cannot construct (Compose's `!override`/`!merge`/`!reset`, custom tags),
    raises `_ComposeScanError` so the caller can refuse rather than skip (fail-closed)."""
    text = compose_file.read_text(encoding="utf-8", errors="replace")
    try:
        resolved = _resolve_interpolations(text, _compose_interpolation_env(compose_file))
    except _ComposeScanError:
        raise
    try:
        data = yaml.safe_load(resolved)
    except yaml.YAMLError as exc:
        raise _ComposeScanError(f"invalid YAML or unsupported Compose tag: {exc}") from exc
    if not isinstance(data, dict):
        raise _ComposeScanError("compose file is not a YAML mapping")
    return data


def _sources_from_compose_data(data: dict, compose_file: Path) -> list[str]:
    """Every host-side source path a compose file's services bind-mount, in both the short
    (`"host:container[:mode]"`) and long (`{type: bind, source: ..., target: ...}`) syntaxes.
    A named volume (`"myvolume:/container/path"`, no leading `/`/`./`/`../`/drive letter) is
    Docker-managed, not a host path, and is correctly excluded - it cannot escape anywhere.
    A bind source that STILL contains an interpolation marker after resolution is refused
    (fail-closed): Compose could not have mounted it as a literal path, and a mangled subset
    of it is not something this scan will silently judge."""
    services = data.get("services")
    if not isinstance(services, dict):
        return []
    sources: list[str] = []
    for service in services.values():
        if not isinstance(service, dict):
            continue
        for volume in service.get("volumes") or []:
            if isinstance(volume, str):
                if "$" in volume:
                    raise _ComposeScanError(f"bind mount still contains unresolved interpolation: {volume!r}")
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
                    if "$" in source:
                        raise _ComposeScanError(
                            f"bind mount source still contains unresolved interpolation: {source!r}"
                        )
                    sources.append(source)
    return sources


def _bind_mount_sources(compose_file: Path) -> list[str]:
    """Host-side bind sources for one compose file, with interpolation resolved and the whole
    thing fail-closed: raises `_ComposeScanError` whenever the file cannot be inspected safely.
    See `_read_and_resolve_compose`/`_sources_from_compose_data`."""
    return _sources_from_compose_data(_read_and_resolve_compose(compose_file), compose_file)


def _expand_compose_path_glob(path: Path) -> list[Path]:
    try:
        matches = sorted(path.parent.glob(path.name))
    except (OSError, ValueError):
        return []
    return [candidate for candidate in matches if candidate.is_file()]


def _referenced_compose_files(compose_file: Path, data: dict, seen: set[Path]) -> list[Path]:
    """Files a compose file pulls in via `include:` or service-level `extends: {file: ...}`.
    A task can put the dockidentical-looking socket mount inside an included file that does
    NOT match the `docker-compose*.y*ml`/`compose*.y*ml` globs (e.g. `shared.yaml`) - following
    references makes sure such a mount is still seen. A reference path that still contains an
    interpolation marker is refused."""
    referenced: list[Path] = []
    entries: list[str] = []
    include = data.get("include")
    if isinstance(include, list):
        for entry in include:
            if isinstance(entry, str):
                entries.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
                entries.append(entry["path"])
            elif isinstance(entry, dict) and isinstance(entry.get("path"), (list, tuple)):
                entries.extend(p for p in entry["path"] if isinstance(p, str))
    for entry in entries:
        if "$" in entry:
            raise _ComposeScanError(
                f"include path still contains unresolved interpolation: {entry!r}"
            )
        for candidate in _expand_compose_path_glob(compose_file.parent / entry):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved not in seen:
                referenced.append(candidate)
    for service in data.get("services", {}).values():
        if not isinstance(service, dict):
            continue
        extends = service.get("extends")
        if isinstance(extends, dict):
            file_ref = extends.get("file")
            if isinstance(file_ref, str):
                if "$" in file_ref:
                    raise _ComposeScanError(
                        f"extends file path still contains unresolved interpolation: {file_ref!r}"
                    )
                candidate = compose_file.parent / file_ref
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved not in seen:
                    referenced.append(candidate)
    return referenced


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

    Fail-closed: a compose file that cannot be inspected - unparseable YAML, an unknown tag
    such as `!override`/`!merge`/`!reset`, an unresolvable REQUIRED interpolation, or a bind
    source still containing an interpolation marker - is itself returned as an offense
    ("cannot be safely inspected"), never skipped. Compose `${VAR}` interpolation is resolved
    against each file's sibling `.env` and the process environment before inspection, and
    `include:`/`extends:` references are followed, so a socket mount smuggled through a
    variable or through an un-checked filename (`shared.yaml`) is still seen.

    Note on cross-platform path handling: a Compose file's host-side path describes the real
    Docker host's filesystem, which is always Linux/POSIX even when this check itself happens
    to run on a Windows development machine (Docker Desktop's Linux VM has its own filesystem
    namespace, entirely distinct from the Windows host's paths). A POSIX-absolute source
    ("/var/run/docker.sock", "/etc/shadow") is therefore normalized with `PurePosixPath`, never
    handed to native `Path.resolve()` - on Windows, `Path("/var/run/docker.sock").resolve()`
    silently reinterprets it as `C:\\var\\run\\docker.sock` ("root of the current drive"),
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

    pending: list[Path] = []
    for pattern in _COMPOSE_FILE_GLOBS:
        pending.extend(sorted(task_dir.rglob(pattern)))
    seen: set[Path] = set()
    while pending:
        compose_file = pending.pop(0)
        try:
            compose_resolved = compose_file.resolve()
        except OSError:
            continue
        if compose_resolved in seen:
            continue
        seen.add(compose_resolved)
        try:
            data = _read_and_resolve_compose(compose_file)
        except (OSError, _ComposeScanError) as exc:
            return compose_file, f"compose file that cannot be safely inspected: {exc}"
        try:
            sources = _sources_from_compose_data(data, compose_file)
        except _ComposeScanError as exc:
            return compose_file, f"compose file that cannot be safely inspected: {exc}"
        compose_dir = compose_file.resolve().parent
        for source in sources:
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
        try:
            pending.extend(_referenced_compose_files(compose_file, data, seen))
        except _ComposeScanError as exc:
            return compose_file, f"compose reference that cannot be safely inspected: {exc}"
    return None


def _task_network_offence(
    task_dir: Path,
    agent_cfg: AgentConfig,
    environment_cfg: EnvironmentConfig,
    isolation: IsolationPolicy,
) -> str | None:
    """Compute the network policy Harbor will ACTUALLY enforce for this launch (the same
    `resolve_trial_network_plan` path the trial uses) and refuse whatever violates the AIEB
    egress contract (ADR-12 / spec section 37).

    Context: Harbor's effective phase network policy is driven by the TASK's OWN `task.toml`
    (`[environment]`/`[agent]`/`[verifier]` `network_mode`), whose default is PUBLIC - the
    `extra_allowed_hosts` this adapter merges in only take effect under an allowlist baseline.
    A hostile task that declares PUBLIC (or includes a denied cloud-metadata host in its own
    allowlist) therefore widens or bypasses L3 egress through the very policy mechanism Harbor
    trusts. This is where the codex review's "raw sockets / alternative-configuration bypass"
    concern is genuinely closed: the application-layer `EgressGuardProxy` cannot contain raw
    sockets, so the network LAYER policy must be non-public and metadata-free before the trial
    is allowed to start.

    Returns a human-readable offence reason, or None when the effective policy is within the
    contract. `launch()` raises `UnhardenedBackendError` on a non-None result.

    Note: a task directory with NO `task.toml` is not a loadable Harbor task at all - there is
    nothing to compute a widened policy from, and `Trial.create` will reject it normally right
    after the guards, so no offence is raised here for it."""
    try:
        task_toml = task_dir / "task.toml"
        task_config = HarborTaskConfig.model_validate_toml(task_toml.read_text(encoding="utf-8"))
    except OSError:
        return None
    except Exception as exc:  # noqa: BLE001 - a task we cannot even load cannot be proven safe
        return (
            f"task.toml cannot be validated as a Harbor task ({type(exc).__name__}: {exc}); "
            "its effective network policy cannot be determined - refusing rather than "
            "assuming public egress"
        )

    plans: list[tuple[str, object | None]] = (
        [("single-step", None)]
        if not task_config.steps
        else [(f"step {step.name!r}", step) for step in task_config.steps]
    )
    for label, step_cfg in plans:
        if step_cfg is None:
            verifier_mode = resolve_task_verifier_mode(task_config)
        else:
            verifier_mode = resolve_step_verifier_mode(task_config, step_cfg)
        try:
            plan = resolve_trial_network_plan(
                task_config,
                agent_cfg,
                environment_cfg,
                step_cfg,
                verifier_mode=verifier_mode,
                env_config=None,
            )
        except Exception as exc:  # noqa: BLE001 - uncomputable policy is uncheckable policy
            return (
                f"effective network policy for {label} cannot be computed "
                f"({type(exc).__name__}: {exc})"
            )
        for role, policy in (("agent", plan.agent_phase), ("verifier", plan.verifier_phase)):
            if policy.network_mode == NetworkMode.PUBLIC:
                return (
                    f"{label} {role} phase effective network mode is PUBLIC (task-declared); "
                    "under an egress-managed isolation policy L3 egress cannot be constrained - "
                    "the task must declare no-network or allowlist"
                )
            if policy.network_mode == NetworkMode.ALLOWLIST:
                for host in policy.allowed_hosts:
                    if host in isolation.denied_metadata_hosts:
                        return (
                            f"{label} {role} phase allowlist includes denied cloud-metadata "
                            f"host {host!r}"
                        )
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
                # Per-trial-safe agent configuration (ENG-023 review finding #1):
                # `model_name` and `kwargs` are real `AgentConfig` fields that
                # `AgentFactory.create_agent_from_config` passes straight into the agent
                # class's own `__init__` as ordinary per-instance constructor arguments
                # (see harbor/agents/factory.py) - never a process-wide env var, so
                # concurrent trials (HarborBackend runs each as its own asyncio task; see
                # `launch()` below) never race each other's identity/config.
                model_name=spec.model_name,
                kwargs=dict(spec.agent_kwargs),
                # The AIEB egress allowlist becomes the trial's network-layer allowlist so
                # Harbor's ALLOWLIST enforcement (not just the app-layer proxy) constrains
                # egress. Under a task-declared PUBLIC baseline these are ignored by Harbor
                # (with a warning) - which is exactly the case _task_network_offence refuses
                # below instead of launching.
                extra_allowed_hosts=list(spec.isolation.egress_allowlist),
            ),
            environment=EnvironmentConfig(
                type="docker",
                delete=True,
                cpu_enforcement_policy=ResourceMode.LIMIT,
                memory_enforcement_policy=ResourceMode.LIMIT,
                override_cpus=spec.cpu_limit,
                override_memory_mb=spec.memory_limit_mb,
                extra_allowed_hosts=list(spec.isolation.egress_allowlist),
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
        # Network-layer egress contract: the effective (task-declared) phase policies must be
        # no-network or allowlist and free of denied metadata hosts. Unlike the socket guard,
        # this is NOT gated by a policy flag - an egress-managed allocation is the norm and
        # the app-layer proxy cannot contain raw sockets, so refuse PUBLIC exactly as if it
        # were a hardened-only allocation.
        network_offence = _task_network_offence(
            spec.task_dir, config.agent, config.environment, spec.isolation
        )
        if network_offence is not None:
            egress_guard.close()
            raise UnhardenedBackendError(
                f"HarborBackend cannot launch this task under an egress-managed isolation "
                f"policy: {network_offence}. L3 deny-by-default (ADR-12 / spec section 37) "
                "requires the effective network policy to be no-network or allowlist, never "
                "public."
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
