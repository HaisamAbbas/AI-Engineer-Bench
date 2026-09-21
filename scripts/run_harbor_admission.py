"""Run one or more development admission trials through the pinned Harbor adapter.

This command is intentionally fail-closed: a package without task.toml,
instruction.md, environment, or a declared agent entrypoint is rejected. It
produces evidence only and never changes release/admission state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from aieb_runner import ExecutionSpec, ExecutionState
from aieb_runner.backends.harbor import HarborBackend

ROOT = Path(__file__).resolve().parents[1]


def _validate_package(task_dir: Path) -> None:
    required = ("task.toml", "instruction.md", "environment")
    missing = [name for name in required if not (task_dir / name).exists()]
    if missing:
        raise ValueError(f"Harbor admission package is incomplete: {', '.join(missing)}")


async def run_trial(task_dir: Path, agent_import_path: str, timeout: int) -> dict[str, object]:
    _validate_package(task_dir)
    backend = HarborBackend()
    capability = await backend.preflight()
    if not capability.ready:
        raise RuntimeError(f"Harbor preflight failed: {capability}")
    handle = await backend.launch(ExecutionSpec(
        task_dir=task_dir,
        runs_dir=ROOT / ".cache" / "harbor-admission",
        trial_name=f"admission-{uuid4().hex[:12]}",
        agent_import_path=agent_import_path,
        agent_timeout_sec=timeout,
    ))
    try:
        status = await backend.status(handle)
        for _ in range(timeout * 4 + 1):
            if status.state != ExecutionState.RUNNING:
                break
            await asyncio.sleep(0.25)
            status = await backend.status(handle)
        if status.state == ExecutionState.RUNNING:
            await backend.stop(handle, "admission timeout")
            raise RuntimeError("Harbor admission trial exceeded its deadline")
        artifacts = await backend.collect(handle)
        result = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
        return {
            "state": status.state.value,
            "reward": result.get("verifier_result", {}).get("rewards", {}).get("reward"),
            "trial_dir": str(artifacts.trial_dir),
            "harbor_version": capability.version,
            "development_only": True,
        }
    finally:
        cleanup = await backend.cleanup(handle)
        if not cleanup.clean:
            raise RuntimeError(f"Harbor cleanup was not clean: {cleanup.remaining_resource_ids}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--agent", required=True, help="module:Class Harbor agent entrypoint")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(asyncio.run(run_trial(args.task.resolve(), args.agent, args.timeout)), sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc), "official": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
