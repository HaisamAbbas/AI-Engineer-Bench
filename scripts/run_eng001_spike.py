"""Run the deterministic ENG-001 Harbor compatibility worker."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from uuid import uuid4

from aieb_runner import ExecutionSpec, ExecutionState
from aieb_runner.backends.harbor import HarborBackend


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "eng001_harbor" / "task"


async def run_spike() -> dict[str, object]:
    backend = HarborBackend()
    capability = await backend.preflight()
    if not capability.ready:
        raise RuntimeError(f"Harbor preflight failed: {capability}")

    trial_name = f"eng001-{uuid4().hex[:10]}"
    handle = await backend.launch(
        ExecutionSpec(
            task_dir=FIXTURE,
            runs_dir=ROOT / ".cache" / "eng001-runs",
            trial_name=trial_name,
            agent_import_path=(
                "aieb_runner.backends.harbor.spike_agent:DeterministicFixtureAgent"
            ),
            agent_timeout_sec=8.0,
        )
    )

    status = await backend.status(handle)
    for _ in range(720):
        if status.state != ExecutionState.RUNNING:
            break
        await asyncio.sleep(0.25)
        status = await backend.status(handle)
    if status.state != ExecutionState.COMPLETED:
        await backend.stop(handle, "spike wall-clock limit")
        raise RuntimeError(f"Harbor trial did not complete: {status}")

    artifacts = await backend.collect(handle)
    result = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
    candidate = artifacts.trial_dir / "artifacts" / "candidate"
    cleanup = await backend.cleanup(handle)
    summary: dict[str, object] = {
        "trial_name": trial_name,
        "trial_dir": str(artifacts.trial_dir),
        "harbor_version": capability.version,
        "state": status.state.value,
        "timeout_exception": result["exception_info"]["exception_type"],
        "reward": result["verifier_result"]["rewards"]["reward"],
        "verifier_environment_mode": result["verifier_environment_mode"],
        "agent_version": result["agent_info"]["version"],
        "usage_coverage": "unknown",
        "new_file": (candidate / "new-file.txt").read_text(encoding="utf-8"),
        "edited_file_contains_marker": "agent-edited"
        in (candidate / "edited-readme.txt").read_text(encoding="utf-8"),
        "late_write_absent": not (candidate / "post-deadline.txt").exists(),
        "artifact_statuses": {
            str(entry["source"]): str(entry["status"])
            for entry in artifacts.manifest
        },
        "cleanup_clean": cleanup.clean,
        "remaining_resource_ids": list(cleanup.remaining_resource_ids),
    }
    expected = {
        "state": "completed",
        "timeout_exception": "AgentTimeoutError",
        "reward": 1.0,
        "verifier_environment_mode": "separate",
        "new_file": "service-ready\n",
        "edited_file_contains_marker": True,
        "late_write_absent": True,
        "cleanup_clean": True,
    }
    mismatches = {
        key: {"expected": value, "actual": summary.get(key)}
        for key, value in expected.items()
        if summary.get(key) != value
    }
    if mismatches:
        raise AssertionError(f"ENG-001 compatibility mismatch: {mismatches}")
    return summary


def main() -> int:
    try:
        summary = asyncio.run(run_spike())
        print(json.dumps(summary, sort_keys=True))
        return 0
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
