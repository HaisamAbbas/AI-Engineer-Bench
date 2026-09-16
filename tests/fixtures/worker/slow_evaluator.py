"""ENG-015 test fixture: a trusted evaluator that takes a while before
returning a verdict, so a controlled test can interrupt VERIFY with
cancellation while it is genuinely still running, rather than only checking
cancel_event before/after the call. Used only by tests.test_worker_leasing.

This fixture HONOURS the cooperative cancellation contract (lifecycle.py's
review finding #1 fix): it receives the runner's stop Event and returns
promptly as CancelledError once it is set. A fixture that deliberately
IGNORES the stop event lives in uncooperative_evaluator.py and proves the
separate abandoned-thread behaviour (workspace left in place, no teardown
race, nothing scored).
"""
from __future__ import annotations

import time
from pathlib import Path

from aieb_runner.lifecycle import CancelledError


def evaluate(candidate_path: Path, stop=None) -> dict[str, object]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            raise CancelledError("evaluation cancelled while candidate was still being exercised")
        time.sleep(0.1)
    return {"pass": True}
