"""ENG-015 test fixture: a trusted evaluator that returns a PASS-verdict for a
candidate, used to simulate a first verification attempt recording an
evaluation before dying (the lost-ack path), after which a second verifier
running the divergent evaluator recomputes a different verdict under the same
identity (tests.test_worker_leasing's evaluation-conflict test)."""
from __future__ import annotations

from pathlib import Path


def evaluate(candidate_path: Path, stop=None) -> dict[str, object]:
    return {"pass": False, "checks": {"api-ready": False}, "diagnostics": {"api-ready": "divergent retry: evaluator behaved differently"}}
