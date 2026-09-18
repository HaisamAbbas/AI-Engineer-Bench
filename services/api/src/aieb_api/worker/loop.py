"""Local multi-worker launch entrypoint (spec section 39/40; `aieb-worker` script).

Run several of these against the same AIEB_DATABASE_URL to exercise real
worker contention: each claims a different ready work item (SKIP LOCKED),
never the same one twice.
"""

from __future__ import annotations

import os
import signal
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..models import TrialRow
from . import repository
from .metrics import log_event
from .repository import LeasedWork
from .runner_bridge import execute_leased_work


def _campaign_id_for_trial(session: Session, trial_id: uuid.UUID) -> uuid.UUID:
    return session.execute(select(TrialRow.campaign_id).where(TrialRow.id == trial_id)).scalar_one()


def install_drain_handlers(stop_event: threading.Event) -> None:
    """ENG-020 worker draining (spec section 40): SIGTERM/SIGINT set `stop_event`, which
    `run_worker` already checks at the top of every iteration (before claiming) - so a signal
    lets the currently in-flight work item finish and finalize normally, then stops claiming
    new ones and returns cleanly. This is deliberately NOT `cancel_event` (campaign
    cancellation, which interrupts in-flight work): draining never touches work already
    claimed, so no lease is ever abandoned mid-attempt for the reconciler to have to recover.
    A deployed worker with no handler installed has no way to reach `stop_event` at all - an
    orchestrator's SIGTERM just kills it mid-attempt and orphans the lease, which is exactly
    the failure this closes."""

    def _handle(signum: int, _frame: object) -> None:
        log_event("worker.drain_signal_received", signal=signal.Signals(signum).name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle)


def run_worker(
    session_factory: sessionmaker,
    *,
    worker_id: str,
    work_root: Path,
    poll_seconds: float = 1.0,
    lease_seconds: int = repository.DEFAULT_LEASE_SECONDS,
    candidate_variant: str = "reference",
    max_iterations: int | None = None,
    stop_event: threading.Event | None = None,
) -> int:
    """Poll for ready work, execute it, repeat. Returns the number of work items
    this call processed (claimed and either finalized or fenced out)."""
    processed = 0
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            leased: LeasedWork | None = repository.claim_work_item(session, worker_id=worker_id, lease_seconds=lease_seconds)
        if leased is None:
            log_event("worker.idle", worker_id=worker_id)
            # The idle sweep is what un-sticks campaigns whose lifecycle can
            # no longer be advanced by finishing work: a cancelling drain
            # whose last item completed (or a frozen campaign cancelled with
            # no work items at all), and a running/paused campaign whose
            # final item finished between after-processing checkpoints or
            # while paused (resume creates no new work, so only this path can
            # finalize it). Bounded and SKIP LOCKED: concurrent idle workers
            # claim different campaigns and never double-finalize.
            with session_factory() as session:
                finalized = repository.finalize_stalled_campaigns(session)
            if finalized:
                log_event("worker.stalled_campaigns_finalized", worker_id=worker_id,
                          campaign_ids=[str(cid) for cid in finalized])
            if max_iterations is not None:
                continue
            time.sleep(poll_seconds)
            continue

        with session_factory() as session:
            campaign_id = _campaign_id_for_trial(session, leased.trial_id)
            cancelling = repository.is_campaign_cancelling(session, campaign_id)
        log_event("worker.claimed", worker_id=worker_id, work_item_id=str(leased.work_item_id), attempt_id=str(leased.attempt_id), cancelling=cancelling)

        cancel_event = threading.Event()
        if cancelling:
            cancel_event.set()
        result = execute_leased_work(
            session_factory, leased, worker_id=worker_id, candidate_variant=candidate_variant,
            work_root=work_root, lease_seconds=lease_seconds, cancel_event=cancel_event,
        )
        processed += 1
        log_event(
            "worker.finished", worker_id=worker_id, work_item_id=str(leased.work_item_id),
            finalized=result.finalized, execution_validity=result.execution_validity, verdict=result.verdict,
        )
        with session_factory() as session:
            # Auto-pause is scored on VERIFICATION (and regrade) outcomes only: an
            # engineering-only ExecutionResult's `execution_validity` is not a real verdict on
            # whether the trial is healthy (a plain successful engineering phase that advanced
            # to verification reports the same "infrastructure_invalid" value the underlying
            # runner outcome carries for a phase that never itself produces a scored verdict) -
            # counting it would auto-pause on completely normal engineering activity.
            if leased.work_type in ("verification", "regrade"):
                auto_paused = repository.record_infrastructure_outcome(session, campaign_id, result.execution_validity)
                if auto_paused:
                    log_event("worker.campaign_auto_paused", worker_id=worker_id, campaign_id=str(campaign_id))
            repository.maybe_complete_cancellation(session, campaign_id)
            repository.maybe_complete_campaign(session, campaign_id)
            from ..regrading import complete_correction_runs
            complete_correction_runs(session)
    return processed


def main() -> None:
    db.configure()
    session_factory = db.session_factory()
    worker_id = os.environ.get("AIEB_WORKER_ID", f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    work_root = Path(os.environ.get("AIEB_WORKER_WORK_ROOT", ".aieb-worker-runs"))
    poll_seconds = float(os.environ.get("AIEB_WORKER_POLL_SECONDS", "1.0"))
    lease_seconds = int(os.environ.get("AIEB_WORKER_LEASE_SECONDS", str(repository.DEFAULT_LEASE_SECONDS)))
    candidate_variant = os.environ.get("AIEB_WORKER_CANDIDATE_VARIANT", "reference")
    stop_event = threading.Event()
    install_drain_handlers(stop_event)
    log_event("worker.start", worker_id=worker_id, work_root=str(work_root))
    run_worker(
        session_factory, worker_id=worker_id, work_root=work_root, poll_seconds=poll_seconds,
        lease_seconds=lease_seconds, candidate_variant=candidate_variant, stop_event=stop_event,
    )
    log_event("worker.drained", worker_id=worker_id)


if __name__ == "__main__":
    main()
