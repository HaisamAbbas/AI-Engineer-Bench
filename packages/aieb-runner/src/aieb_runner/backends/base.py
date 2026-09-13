"""Backend-neutral types owned by AIEB, not by an execution dependency."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ExecutionSpec:
    task_dir: Path
    runs_dir: Path
    trial_name: str
    agent_import_path: str
    agent_timeout_sec: float
    cpu_limit: int = 1
    memory_limit_mb: int = 256


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
