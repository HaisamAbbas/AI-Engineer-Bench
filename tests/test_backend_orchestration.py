from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

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
from aieb_runner.backends.orchestration import BackendExecutionError, run_bounded


class FakeBackend:
    def __init__(self, states: list[ExecutionState], *, clean: bool = True) -> None:
        self.states = iter(states)
        self.stopped = False
        self.cleaned = False
        self.clean = clean

    async def preflight(self) -> CapabilityReport:
        return CapabilityReport("fake", "test", (CapabilityCheck("fake", True, "test"),))

    async def launch(self, spec: ExecutionSpec) -> ExecutionHandle:
        return ExecutionHandle("fake-handle")

    async def status(self, handle: ExecutionHandle) -> ExecutionStatus:
        try:
            state = next(self.states)
        except StopIteration:
            state = ExecutionState.COMPLETED
        return ExecutionStatus(state)

    async def stop(self, handle: ExecutionHandle, reason: str) -> None:
        self.stopped = True

    async def collect(self, handle: ExecutionHandle) -> CandidateArtifacts:
        return CandidateArtifacts(Path("."), Path("result.json"), Path("manifest.json"), ())

    async def cleanup(self, handle: ExecutionHandle) -> CleanupReport:
        self.cleaned = True
        return CleanupReport(self.clean, () if self.clean else ("fake-resource",))


def _spec(timeout: float = 1.0) -> ExecutionSpec:
    return ExecutionSpec(Path("."), Path("."), "trial", "agent:Class", timeout)


class BackendOrchestrationTests(unittest.TestCase):
    def test_completed_execution_collects_and_cleans(self) -> None:
        backend = FakeBackend([ExecutionState.RUNNING, ExecutionState.COMPLETED])
        result = asyncio.run(run_bounded(backend, _spec(), poll_seconds=0.001))
        self.assertEqual(result.status.state, ExecutionState.COMPLETED)
        self.assertTrue(backend.cleaned)
        self.assertFalse(backend.stopped)

    def test_deadline_stops_before_collecting(self) -> None:
        backend = FakeBackend([ExecutionState.RUNNING, ExecutionState.CANCELLED])
        result = asyncio.run(
            run_bounded(backend, _spec(timeout=0.0), poll_seconds=0.001, timeout_grace_seconds=0.1)
        )
        self.assertEqual(result.status.state, ExecutionState.CANCELLED)
        self.assertTrue(backend.stopped)
        self.assertTrue(backend.cleaned)

    def test_dirty_cleanup_is_not_a_valid_result(self) -> None:
        backend = FakeBackend([ExecutionState.COMPLETED], clean=False)
        with self.assertRaises(BackendExecutionError):
            asyncio.run(run_bounded(backend, _spec(), poll_seconds=0.001))
        self.assertTrue(backend.cleaned)


if __name__ == "__main__":
    unittest.main()
