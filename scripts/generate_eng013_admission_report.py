"""Regenerate docs/implementation/evidence/ENG-013/admission-report.json from
live admission runs, rather than leaving it as a stale hand-assembled snapshot.

Review finding: the checked-in evidence file still showed TOOL-02's baseline
resolving ambiguity and its never-retry counterexample duplicating the
effect - the opposite of what the corrected fixture (AUDIT-003, finding #9)
now actually produces - because the file was never regenerated after the
fixture fix. Run this after any change that could affect the nine tasks
below, and commit the result alongside the change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rag02_admission import matrix as rag02
from scripts.run_rag03_admission import matrix as rag03
from scripts.run_rag04_admission import matrix as rag04
from scripts.run_ext01_admission import run_matrix as ext01
from scripts.run_ext03_admission import run_matrix as ext03
from scripts.run_ext04_admission import run_matrix as ext04
from scripts.run_tool02_admission import run_matrix as tool02
from scripts.run_tool03_admission import run_matrix as tool03
from scripts.run_tool04_admission import run_matrix as tool04

RUNS = {
    "rag.metadata-filter-topk": rag02,
    "rag.citation-current-span": rag03,
    "rag.embedding-version": rag04,
    "ext.missingness": ext01,
    "ext.unit-normalization": ext03,
    "ext.partial-batch": ext04,
    "tool.idempotent-write": tool02,
    "tool.session-isolation": tool03,
    "tool.corrected-arguments": tool04,
}


def main() -> None:
    report = {task_id: run() for task_id, run in RUNS.items()}
    path = ROOT / "docs" / "implementation" / "evidence" / "ENG-013" / "admission-report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
