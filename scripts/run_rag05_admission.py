"""Run RAG-05 baseline/reference/control admission cases on fresh HTTP services.

The task is built from real upstream code (vendored sqlite-utils 3.38); the
candidate lives in suites/real/rag.corpus-index-drift. See scripts/run_rag01_admission.py
for the development-suite equivalent."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.maintainer.rag05.evaluator import evaluate

TASK = ROOT / "suites" / "real" / "rag.corpus-index-drift"
RUNS = ROOT / ".cache" / "rag05-admission"


def evaluate_variant(name: str, replacement: Path | None = None) -> dict[str, object]:
    RUNS.mkdir(parents=True, exist_ok=True)
    workspace = RUNS / f"{name}-{uuid4()}"
    shutil.copytree(TASK / "repo", workspace)
    if replacement is not None:
        shutil.copyfile(replacement, workspace / "knowledge_service" / "backend.py")
    try:
        outcome = evaluate(workspace)
        return {"variant": name, **outcome}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_matrix() -> dict[str, object]:
    cases: list[tuple[str, Path | None]] = [
        ("baseline", None),
        ("reference", TASK / "reference" / "backend.py"),
        ("alternative", TASK / "alternative" / "backend.py"),
        ("global-rebuild", TASK / "counterexamples" / "global-rebuild.py"),
        ("ignore-metadata-filter", TASK / "counterexamples" / "ignore-metadata-filter.py"),
        ("stale-resurrection", TASK / "counterexamples" / "stale-resurrection.py"),
    ]
    outcomes = [evaluate_variant(name, candidate) for name, candidate in cases]
    reference_resets = [evaluate_variant(f"reference-reset-{index}", TASK / "reference" / "backend.py") for index in range(10)]
    return {"matrix": outcomes, "reference_resets": reference_resets}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON evidence path; its parent must already be inside this repository",
    )
    args = parser.parse_args()
    report = json.dumps(run_matrix(), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        target = args.output.resolve()
        root = ROOT.resolve()
        if target != root and root not in target.parents:
            parser.error("--output must be within the repository")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
    print(report, end="")
