"""Run RAG-01 baseline/reference/control admission cases on fresh HTTP services."""

from __future__ import annotations

import json
import shutil
import argparse
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.maintainer.rag01.evaluator import evaluate


TASK = ROOT / "suites" / "dev" / "rag.document-freshness"
RUNS = ROOT / ".cache" / "rag01-admission"


def evaluate_variant(name: str, replacement: Path | None = None) -> dict[str, object]:
    RUNS.mkdir(parents=True, exist_ok=True)
    workspace = RUNS / f"{name}-{uuid4()}"
    shutil.copytree(TASK / "repo", workspace)
    if replacement is not None:
        shutil.copyfile(replacement, workspace / "knowledge_service" / "backend.py")
        support = TASK / "counterexamples" / "support.py"
        if replacement.parent == support.parent and replacement.name != "hardcoded-output.py":
            shutil.copyfile(support, workspace / "knowledge_service" / "support.py")
    try:
        outcome = evaluate(workspace)
        return {"variant": name, **outcome}
    finally:
        shutil.rmtree(workspace)


def run_matrix() -> dict[str, object]:
    cases: list[tuple[str, Path | None]] = [
        ("baseline", None),
        ("reference", TASK / "reference" / "backend.py"),
        ("alternative", TASK / "alternative" / "backend.py"),
        ("hardcoded-output", TASK / "counterexamples" / "hardcoded-output.py"),
        ("visible-delete-filter", TASK / "counterexamples" / "visible-delete-filter.py"),
        ("global-rebuild", TASK / "counterexamples" / "global-rebuild.py"),
        ("stale-resurrection", TASK / "counterexamples" / "stale-resurrection.py"),
        ("incorrect-citation", TASK / "counterexamples" / "incorrect-citation.py"),
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
