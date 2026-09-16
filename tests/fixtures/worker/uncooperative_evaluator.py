"""ENG-015 test fixture: a trusted evaluator that deliberately IGNORES the
cooperative stop event the runner passes it - the exact contract violation
the cancellation fix tolerates with a bounded grace before abandoning the
thread. Used only by tests.test_attempt_lifecycle to prove that cancelling
against such an evaluator (a) never scores or persists its late result and
(b) does NOT delete the build allocation its abandoned thread may still be
reading (the teardown race from review finding #1)."""
from __future__ import annotations

import time
from pathlib import Path


def evaluate(candidate_path: Path, stop=None) -> dict[str, object]:
    # Ignores `stop` entirely for the grace window and beyond; records that it
    # was still running AFTER cancellation so the test can observe the
    # abandoned thread actually outliving it.
    (candidate_path.parent / "evaluator-started.txt").write_text("started", encoding="utf-8")
    time.sleep(1.5)
    (candidate_path.parent / "abandoned-evaluator-finished.txt").write_text("late", encoding="utf-8")
    return {"pass": True}
