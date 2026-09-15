"""ENG-015 test fixture: a trusted evaluator that sleeps before returning a
verdict, so a controlled test can interrupt VERIFY with cancellation while
it is genuinely still running, rather than only checking cancel_event
before/after the call. Used only by tests.test_worker_leasing."""
from __future__ import annotations

import time
from pathlib import Path


def evaluate(candidate_path: Path) -> dict[str, object]:
    time.sleep(20)
    return {"pass": True}
