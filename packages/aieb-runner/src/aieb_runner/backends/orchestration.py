"""Backend-neutral orchestration for one bounded execution.

The worker owns leasing and persistence; this module owns only the lifecycle
common to Harbor (and future backends): capability preflight, launch, bounded
polling, stop-on-timeout, artifact collection, and cleanup.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .base import (
    CandidateArtifacts,
    CapabilityReport,
    CleanupReport,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
)


class ExecutionBackend(Protocol):
    async def preflight(self) -> CapabilityReport: ...
    async def launch(self, spec: ExecutionSpec) -> ExecutionHandle: ...
    async def status(self, handle: ExecutionHandle) -> ExecutionStatus: ...
    async def stop(self, handle: ExecutionHandle, reason: str) -> None: ...
    async def collect(self, handle: ExecutionHandle) -> CandidateArtifacts: ...
    async def cleanup(self, handle: ExecutionHandle) -> CleanupReport: ...


@dataclass(frozen=True)
class BackendRunResult:
    handle: ExecutionHandle
    status: ExecutionStatus
    artifacts: CandidateArtifacts
    cleanup: CleanupReport


class BackendExecutionError(RuntimeError):
    """A backend run failed before producing a trustworthy result."""


async def run_bounded(
    backend: ExecutionBackend,
    spec: ExecutionSpec,
    *,
    poll_seconds: float = 0.25,
    timeout_grace_seconds: float = 10.0,
) -> BackendRunResult:
    """Run one execution with mandatory cleanup and bounded waiting."""

    if poll_seconds <= 0 or timeout_grace_seconds < 0:
        raise ValueError("poll_seconds must be positive and timeout grace nonnegative")
    capability = await backend.preflight()
    if not capability.ready:
        raise BackendExecutionError(f"backend preflight failed: {capability}")

    handle = await backend.launch(spec)
    status: ExecutionStatus
    try:
        deadline = asyncio.get_running_loop().time() + spec.agent_timeout_sec
        status = await backend.status(handle)
        while status.state == ExecutionState.RUNNING:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await backend.stop(handle, "execution deadline exceeded")
                grace_deadline = asyncio.get_running_loop().time() + timeout_grace_seconds
                status = await backend.status(handle)
                while (
                    status.state == ExecutionState.RUNNING
                    and asyncio.get_running_loop().time() < grace_deadline
                ):
                    await asyncio.sleep(min(poll_seconds, max(0.01, grace_deadline - asyncio.get_running_loop().time())))
                    status = await backend.status(handle)
                if status.state == ExecutionState.RUNNING:
                    raise BackendExecutionError("backend remained running after deadline stop")
                break
            await asyncio.sleep(min(poll_seconds, remaining))
            status = await backend.status(handle)

        if status.state not in {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED}:
            raise BackendExecutionError(f"backend returned unsupported terminal state: {status}")
        artifacts = await backend.collect(handle)
    finally:
        cleanup = await backend.cleanup(handle)
        if not cleanup.clean:
            raise BackendExecutionError(
                "backend cleanup was not clean: " + ", ".join(cleanup.remaining_resource_ids)
            )

    return BackendRunResult(handle=handle, status=status, artifacts=artifacts, cleanup=cleanup)
