"""ENG-015 test fixture: a trusted evaluator that crashes (verifier outage),
distinct from a candidate defect. Used only by tests.test_worker_leasing."""
from __future__ import annotations

from pathlib import Path


def evaluate(candidate_path: Path) -> dict[str, object]:
    raise ConnectionError("verifier outage: evaluator unreachable")
