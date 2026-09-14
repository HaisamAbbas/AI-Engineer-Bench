"""Reconciliation entrypoint (`aieb-reconciler` script): recovers expired leases
and tears down local allocations a killed worker left behind.

See docs/implementation/evidence/ENG-015/reconciliation-runbook.md for the
operational procedure this automates.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from .. import db
from . import repository
from .metrics import log_event


def teardown_orphan_allocations(session_factory: sessionmaker, work_root: Path) -> list[str]:
    """Remove local work-root directories for attempts whose lease expired -
    evidence a killed worker (SIGKILL) never reached its own cleanup phase for.

    execute_leased_work lays out each attempt as
    `<work_root>/<attempt_id>/runs/attempt-<...>/{engineer,build}`; only those
    two writable allocations are removed here, mirroring exactly what
    LocalAttemptRunner's own cleanup phase removes for an attempt that
    finished normally. Immutable evidence (attempt.json, stored artifacts
    under .../artifacts/) is left untouched.
    """
    with session_factory() as session:
        orphan_attempt_ids = repository.teardown_orphans(session)
    removed = []
    for attempt_id in orphan_attempt_ids:
        runs_dir = work_root / str(attempt_id) / "runs"
        if not runs_dir.is_dir():
            continue
        for attempt_root in runs_dir.glob("attempt-*"):
            for allocation in ("engineer", "build"):
                path = attempt_root / allocation
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                    removed.append(str(path))
    return removed


def reconcile_once(session_factory: sessionmaker, work_root: Path | None = None) -> repository.ReconciliationSummary:
    if work_root is not None:
        removed = teardown_orphan_allocations(session_factory, work_root)
        if removed:
            log_event("reconciler.orphans_removed", count=len(removed), paths=removed)
    with session_factory() as session:
        summary = repository.reconcile_expired_leases(session)
    if summary.resumed or summary.replaced or summary.exhausted:
        log_event("reconciler.summary", resumed=summary.resumed, replaced=summary.replaced, exhausted=summary.exhausted)
    return summary


def main() -> None:
    db.configure()
    session_factory = db.session_factory()
    work_root = Path(os.environ.get("AIEB_WORKER_WORK_ROOT", ".aieb-worker-runs"))
    poll_seconds = float(os.environ.get("AIEB_RECONCILER_POLL_SECONDS", "10"))
    log_event("reconciler.start", poll_seconds=poll_seconds)
    while True:
        reconcile_once(session_factory, work_root)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
