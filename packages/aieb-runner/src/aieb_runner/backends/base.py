"""Backend-neutral types owned by AIEB, not by an execution dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class ExecutionState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CapabilityCheck:
    name: str
    supported: bool | None
    evidence: str


@dataclass(frozen=True)
class CapabilityReport:
    backend: str
    version: str
    checks: tuple[CapabilityCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.supported is not False for check in self.checks)


# Cloud-metadata endpoints every backend must deny by construction (ADR-12 / spec section 37).
# IPv4 link-local (AWS/GCP/Azure/DigitalOcean all resolve their metadata service here) plus the
# IPv6 link-local equivalent some providers also expose it under.
DEFAULT_DENIED_METADATA_HOSTS: tuple[str, ...] = ("169.254.169.254", "fd00:ec2::254")


@dataclass(frozen=True)
class IsolationPolicy:
    """The trust-boundary contract every `ExecutionBackend.launch()` must enforce or refuse
    (ADR-12). Deny-by-default: an empty `egress_allowlist` means no outbound network access at
    all, not unrestricted access. A backend that cannot enforce a field it is asked to enforce
    must raise from `launch()`, never launch and merely log a warning - "not hardened" must be
    unusable as if it were hardened, not just disclosed as such.

    Deliberately does NOT declare a `scoped_credential_id`/per-attempt-credential field: that
    identity belongs to the RUNNER/control-plane layer, not to a sandbox backend's launch
    policy. Per-attempt short-lived scoped credentials ARE implemented (ENG-020, spec section
    37): the API worker issues a candidate- and verifier-role token per attempt - fenced to the
    issuing work-item lease, revoked on phase end and on lease recovery, delivered to the
    engineering/verifying subprocess environments via `extra_env`/`attempt_vars` - and each
    attempt's process can authenticate to exactly the control-plane capability its role is
    entitled to. See DECISIONS.md ADR-12 and
    `docs/implementation/evidence/ENG-020/operations-review.md`.
    """

    egress_allowlist: tuple[str, ...] = ()
    denied_metadata_hosts: tuple[str, ...] = DEFAULT_DENIED_METADATA_HOSTS
    deny_docker_socket: bool = True
    hardened_isolation_required: bool = False


class UnhardenedBackendError(RuntimeError):
    """Raised when a backend that cannot provide hardened isolation is asked to launch an
    allocation that requires it. This must stop the launch, not merely warn about it (ADR-12)."""


@dataclass(frozen=True)
class ExecutionSpec:
    task_dir: Path
    runs_dir: Path
    trial_name: str
    agent_import_path: str
    agent_timeout_sec: float
    cpu_limit: int = 1
    memory_limit_mb: int = 256
    isolation: IsolationPolicy = field(default_factory=IsolationPolicy)


@dataclass(frozen=True)
class ExecutionHandle:
    id: str


@dataclass(frozen=True)
class ExecutionStatus:
    state: ExecutionState
    detail: str | None = None


@dataclass(frozen=True)
class CandidateArtifacts:
    trial_dir: Path
    result_path: Path
    manifest_path: Path
    manifest: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class CleanupReport:
    clean: bool
    remaining_resource_ids: tuple[str, ...]


class ExecutionBackend(Protocol):
    async def preflight(self) -> CapabilityReport: ...

    async def launch(self, spec: ExecutionSpec) -> ExecutionHandle: ...

    async def status(self, handle: ExecutionHandle) -> ExecutionStatus: ...

    async def stop(self, handle: ExecutionHandle, reason: str) -> None: ...

    async def collect(self, handle: ExecutionHandle) -> CandidateArtifacts: ...

    async def cleanup(self, handle: ExecutionHandle) -> CleanupReport: ...
