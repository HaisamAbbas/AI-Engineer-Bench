"""Compute real SHA-256 digests for the RAG-05 real-source task (suites/real/)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aieb_cli.main import hash_evaluator_closure, hash_file, hash_tree

TASK = ROOT / "suites" / "real" / "rag.corpus-index-drift"


def recompute() -> dict[str, str]:
    """Return the real on-disk SHA-256 digests for the RAG-05 real task."""
    return {
        "repository_digest": hash_tree(TASK / "repo"),
        "provenance_digest": hash_file(TASK / "provenance.json"),
        "contract_digest": hash_file(TASK / "contracts" / "application-api.md"),
        "service_topology_digest": hash_file(TASK / "environment" / "README.md"),
        "evaluator_digest": hash_evaluator_closure("tests.maintainer.rag05.evaluator"),
    }


def restamp(task_yaml: Path = TASK / "task.yaml", digests: dict[str, str] | None = None) -> dict[str, str]:
    """Rewrite the digest fields of task.yaml from real on-disk content.

    Importing this module is inert; only an explicit ``python
    scripts/compute_rag05_digests.py`` run (or a direct recompute() call)
    touches task.yaml.
    """
    if digests is None:
        digests = recompute()
    text = task_yaml.read_text(encoding="utf-8")
    for field, value in digests.items():
        pattern = re.compile(rf'({field}: ")[0-9a-f]{{64}}(")')
        text, count = pattern.subn(rf"\g<1>{value}\g<2>", text)
        if count != 1:
            raise SystemExit(f"field {field} not found exactly once in task.yaml")
    task_yaml.write_text(text, encoding="utf-8")
    return digests


if __name__ == "__main__":
    digests = restamp()
    print("digests written:")
    for field, value in digests.items():
        print(f"  {field}: {value}")
