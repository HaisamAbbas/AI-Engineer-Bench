"""Reconciliation entrypoint (`aieb-reconciler` script): recovers expired leases
and tears down local allocations a killed worker left behind.

See docs/implementation/evidence/ENG-015/reconciliation-runbook.md for the
operational procedure this automates.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import replace
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from .. import db
from . import repository
from .metrics import inc_counter, log_event


def _remove_orphan_allocations(work_root: Path, attempt_ids: tuple[uuid.UUID, ...]) -> list[str]:
    """Remove local work-root directories for attempts reconcile_expired_leases()
    just authoritatively decided on, in this same call - replaced with a new
    attempt, advanced from engineering to verification, resumed from an
    already-persisted evaluation, or requeued for another verification
    attempt (ENG015-007: not only "replaced" any more, since a dead lease at
    either phase boundary can leave a stale local allocation behind).

    execute_leased_work lays out each attempt as
    `<work_root>/<attempt_id>/runs/attempt-<...>/{engineer,build}`; only those
    two writable allocations are removed here, mirroring exactly what
    LocalAttemptRunner's own cleanup phase removes for an attempt that
    finished normally - removing both unconditionally is safe even when only
    one exists (or neither does). Immutable evidence (attempt.json, stored
    artifacts under .../artifacts/) is left untouched.

    Deliberately takes `attempt_ids` from the caller rather than re-deriving
    "which leases look expired" with its own separate query: a second,
    disconnected read here would reintroduce the exact race this function
    exists to avoid - a live worker whose heartbeat is merely delayed could
    look "expired" to an independent point-in-time SELECT even though the
    reconciler's own locked pass (which re-checks under FOR UPDATE at lock
    time) correctly did not touch that row. Only attempt_ids the reconciler
    has already committed a decision for are ever passed in.
    """
    removed = []
    for attempt_id in attempt_ids:
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
    with session_factory() as session:
        summary = repository.reconcile_expired_leases(session)
        # Spec section 37: unreferenced staging blobs expire after 24 hours;
        # committed evidence is never touched. Candidate bytes collected by a
        # worker that died before record_candidate are reclaimed here instead
        # of accumulating in the control-plane database (review finding #2).
        purged = repository.purge_expired_worker_artifacts(session)
    summary = replace(summary, worker_artifacts_purged=purged)
    if purged:
        inc_counter("aieb_reconciler_worker_artifacts_purged_total", purged)
    if summary.resumed or summary.replaced or summary.exhausted or summary.advanced or summary.requeued or purged:
        log_event(
            "reconciler.summary", resumed=summary.resumed, replaced=summary.replaced, exhausted=summary.exhausted,
            advanced=summary.advanced, requeued=summary.requeued, worker_artifacts_purged=purged,
        )
    if work_root is not None and summary.orphaned_attempt_ids:
        removed = _remove_orphan_allocations(work_root, summary.orphaned_attempt_ids)
        if removed:
            log_event("reconciler.orphans_removed", count=len(removed), paths=removed)
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
