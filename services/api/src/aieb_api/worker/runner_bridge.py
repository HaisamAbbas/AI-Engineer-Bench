"""Bridge from a leased work item to the proven local attempt pipeline.

Deliberately reuses aieb_runner.lifecycle.LocalAttemptRunner unchanged in
its scoring/candidate logic - this ticket adds leasing and recovery around
it, not a second execution or scoring implementation. A real installed
agent remains blocked on ENG-001's authorization gate, so the "engineering
command" here is the same deterministic candidate-variant editor the local
CLI already uses (aieb_cli.main._editor) for development verticals.
"""

from __future__ import annotations

import importlib
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from aieb_core.models import ExecutionValidity, SubmissionPolicy
from aieb_runner.artifacts import FilesystemArtifactStore
from aieb_runner.lifecycle import AttemptConfig, EngineeringCommand, LocalAttemptRunner
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..models import EntrantRevisionRow, TaskRevisionRow, TrialRow
from . import repository
from .repository import CandidateOutcome, EvaluationOutcome, LeasedWork

ROOT = Path(__file__).resolve().parents[5]

# Duplicates aieb_cli.main.TASK_RUNTIMES' (source_dir, evaluator_module) pairs.
# services/api must not depend on aieb-cli (the dependency would run backwards
# per the monorepo layout), so this mapping is intentionally kept in sync by
# hand rather than imported; unifying it into aieb-core is a follow-up, not
# blocking this ticket.
TASK_RUNTIMES: dict[str, tuple[str, str]] = {
    "rag.document-freshness": ("knowledge_service", "tests.maintainer.rag01.evaluator"),
    "rag.metadata-filter-topk": ("search_service", "tests.maintainer.rag02.evaluator"),
    "rag.citation-current-span": ("citation_service", "tests.maintainer.rag03.evaluator"),
    "rag.embedding-version": ("embedding_service", "tests.maintainer.rag04.evaluator"),
    "ext.missingness": ("missingness_service", "tests.maintainer.ext01.evaluator"),
    "ext.batch-alignment": ("extraction_service", "tests.maintainer.ext02.evaluator"),
    "ext.unit-normalization": ("unit_service", "tests.maintainer.ext03.evaluator"),
    "ext.partial-batch": ("batch_service", "tests.maintainer.ext04.evaluator"),
    "tool.false-completion": ("workflow_service", "tests.maintainer.tool01.evaluator"),
    "tool.idempotent-write": ("write_service", "tests.maintainer.tool02.evaluator"),
    "tool.session-isolation": ("session_service", "tests.maintainer.tool03.evaluator"),
    "tool.corrected-arguments": ("correction_service", "tests.maintainer.tool04.evaluator"),
}


class UnsupportedTaskError(ValueError):
    pass


def _editor_script(task_dir: Path, candidate_variant: str, source_dir: str, script_path: Path, delay_seconds: float = 0) -> EngineeringCommand:
    delay = f"import time\ntime.sleep({delay_seconds})\n" if delay_seconds > 0 else ""
    if candidate_variant == "baseline":
        script_path.write_text(delay + "pass\n", encoding="utf-8")
    else:
        source = repr(str(task_dir / candidate_variant / "backend.py"))
        script_path.write_text(
            delay + "from pathlib import Path\nimport shutil\n"
            f"shutil.copyfile({source}, Path.cwd() / {source_dir!r} / 'backend.py')\n",
            encoding="utf-8",
        )
    return EngineeringCommand((sys.executable, str(script_path)), 30)


@dataclass(frozen=True)
class ExecutionResult:
    finalized: bool
    execution_validity: str
    verdict: str | None


def _heartbeat_loop(session_factory: sessionmaker, leased: LeasedWork, worker_id: str, lease_seconds: int, stop: threading.Event) -> None:
    interval = max(lease_seconds / 3, 1)
    while not stop.wait(interval):
        with session_factory() as session:
            if not repository.heartbeat(session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation, lease_seconds=lease_seconds):
                return  # fenced out; nothing more this worker can legitimately do


def execute_leased_work(
    session_factory: sessionmaker, leased: LeasedWork, *, worker_id: str, candidate_variant: str = "reference",
    work_root: Path, lease_seconds: int = repository.DEFAULT_LEASE_SECONDS, cancel_event: threading.Event | None = None,
    engineering_delay_seconds: float = 0,
) -> ExecutionResult:
    """Run one leased engineering attempt to completion and persist the outcome.

    Execution happens entirely outside any database transaction (a single
    blocking LocalAttemptRunner.run() call); a background thread heartbeats
    the lease on its own session while it runs. `engineering_delay_seconds` is
    a test seam only, letting a controlled-failure test kill the process
    mid-engineering before its (otherwise near-instant) deterministic editor
    would have finished.
    """
    work_root = Path(work_root)
    with session_factory() as session:
        trial = session.get(TrialRow, leased.trial_id)
        task_row = session.get(TaskRevisionRow, trial.task_revision_id)
        entrant_row = session.get(EntrantRevisionRow, trial.entrant_revision_id)
        task_slug = task_row.slug
        task_version = task_row.version

    runtime = TASK_RUNTIMES.get(task_slug)
    if runtime is None:
        raise UnsupportedTaskError(f"task {task_slug} has no supported local evaluator")
    source_dir, evaluator_module = runtime
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    evaluate = importlib.import_module(evaluator_module).evaluate

    task_dir = ROOT / "suites" / "dev" / task_slug
    attempt_work_root = work_root / str(leased.attempt_id)
    attempt_work_root.mkdir(parents=True, exist_ok=True)
    script = attempt_work_root / "deterministic-editor.py"
    store = FilesystemArtifactStore(attempt_work_root / "artifacts")
    runner = LocalAttemptRunner(store)

    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(session_factory, leased, worker_id, lease_seconds, stop_heartbeat), daemon=True,
    )
    heartbeat_thread.start()
    try:
        outcome = runner.run(
            AttemptConfig(
                attempt_id=f"attempt-{leased.attempt_id.hex[:8]}-{uuid4().hex[:8]}",
                frozen_source=task_dir / "repo",
                work_root=attempt_work_root / "runs",
                base_revision_digest="1" * 64,
                submission=SubmissionPolicy(include=(f"{source_dir}/**",), protected=("dev_tests/**",), max_artifact_bytes=52_428_800),
                engineering=_editor_script(task_dir, candidate_variant, source_dir, script, delay_seconds=engineering_delay_seconds),
                access_scope=str(leased.attempt_id),
            ),
            evaluate,
            cancel_event=cancel_event,
        )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)

    if outcome.execution_validity == ExecutionValidity.CANCELLED:
        with session_factory() as session:
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="cancelled", done=False,
            )
        return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=None)

    if outcome.candidate is None:
        # No candidate was ever collected (configuration/host/teardown failure, or a
        # deadline hit before any allowed file changed) - nothing to record artifact-first;
        # go straight to a fenced finalize so the reconciler can replace this attempt.
        with session_factory() as session:
            finalized = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="infrastructure_invalid", done=False,
            )
        return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=None)

    with session_factory() as session:
        evaluator_id = task_row_evaluator_id(session, task_slug, task_version)
        fixture_id = ensure_fixture_row(session, task_slug)
        candidate = CandidateOutcome(
            tree_digest=outcome.candidate.manifest.full_tree_hash,
            manifest_digest=outcome.candidate.manifest.digest(),
            validation_status="valid" if outcome.execution_validity == ExecutionValidity.VALID else "rejected",
        )
        evaluation = None
        if outcome.evaluation is not None:
            evaluation = EvaluationOutcome(
                evaluator_id=evaluator_id, fixture_id=fixture_id,
                schedule_digest=outcome.candidate.manifest.digest(),
                verdict=outcome.verdict.value if outcome.verdict else None,
                result=outcome.evaluation,
            )
        recorded = repository.record_outcome(
            session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
            attempt_id=leased.attempt_id, candidate=candidate, evaluation=evaluation, lease_seconds=lease_seconds,
        )
        if not recorded:
            return ExecutionResult(finalized=False, execution_validity=outcome.execution_validity.value, verdict=outcome.verdict.value if outcome.verdict else None)
        terminal_status = outcome.verdict.value if outcome.verdict else outcome.attribution.value
        finalized = repository.finalize(
            session, work_item_id=leased.work_item_id, worker_id=worker_id, generation=leased.generation,
            attempt_id=leased.attempt_id, terminal_status=terminal_status, done=outcome.verdict is not None,
        )
    return ExecutionResult(finalized=finalized, execution_validity=outcome.execution_validity.value, verdict=outcome.verdict.value if outcome.verdict else None)


def task_row_evaluator_id(session, task_slug: str, task_version: str) -> uuid.UUID:
    row = session.execute(select(TaskRevisionRow).where(TaskRevisionRow.slug == task_slug, TaskRevisionRow.version == task_version)).scalar_one()
    return row.evaluator_id


def ensure_fixture_row(session, task_slug: str) -> uuid.UUID:
    import hashlib

    from ..models import FixtureRevisionRow

    digest = hashlib.sha256(f"aieb:worker-fixture:{task_slug}".encode("utf-8")).hexdigest()
    existing = session.execute(select(FixtureRevisionRow).where(FixtureRevisionRow.digest == digest)).scalar_one_or_none()
    if existing is not None:
        return existing.id
    row = FixtureRevisionRow(digest=digest, visibility="restricted", family_id=task_slug)
    session.add(row)
    session.flush()
    return row.id
