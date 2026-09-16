"""ENG-015 test fixture: a trusted evaluator that takes a while before
returning a verdict and HONOURS the cooperative cancellation contract
(lifecycle.py's review finding #1 fix): it receives the runner's stop Event
and returns promptly as CancelledError once it is set. Used by
tests.test_attempt_lifecycle to prove a contract-abiding evaluator is joined
cleanly on cancellation, not abandoned. A fixture that deliberately ignores
the stop event lives in uncooperative_evaluator.py.
"""
from __future__ import annotations

import time
from pathlib import Path

from aieb_runner.lifecycle import CancelledError


def evaluate(candidate_path: Path, stop=None) -> dict[str, object]:
    (candidate_path.parent / "evaluator-started.txt").write_text("started", encoding="utf-8")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            raise CancelledError("evaluation cancelled while candidate was still being exercised")
        time.sleep(0.05)
    (candidate_path.parent / "evaluator-ran-to-completion.txt").write_text("late", encoding="utf-8")
    return {"pass": True}
