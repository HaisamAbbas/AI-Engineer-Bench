"""Reusable fresh-workspace admission runner for deterministic development tasks."""
from __future__ import annotations
import shutil
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]

def matrix(task_dir: str, package: str, evaluator, controls: list[tuple[str, str | None]]) -> dict[str, object]:
    task = ROOT / "suites" / "dev" / task_dir
    runs = ROOT / ".cache" / f"{task_dir}-admission"
    def one(name: str, candidate: str | None = None) -> dict[str, object]:
        workspace = runs / f"{name}-{uuid4()}"
        runs.mkdir(parents=True, exist_ok=True)
        shutil.copytree(task / "repo", workspace)
        if candidate:
            shutil.copyfile(task / candidate, workspace / package / "backend.py")
        try:
            return {"variant": name, **evaluator(workspace)}
        finally:
            shutil.rmtree(workspace)
    return {
        "matrix": [one(name, candidate) for name, candidate in controls],
        "reference_resets": [one(f"reset-{index}", "reference/backend.py") for index in range(10)],
    }
