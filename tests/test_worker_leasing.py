"""ENG-015: PostgreSQL-leased execution/verification work, against a real test
PostgreSQL instance. Requires AIEB_DATABASE_URL; skipped (not faked) otherwise,
matching this project's rule against substituting sqlite/mocks for the real
evaluation a spec requirement asks for.

Controlled-failure scenarios, not happy-path smoke tests: two workers
contending for one item, death before launch, death during engineering
(a real killed subprocess), death after upload but before finalization,
lease expiry with a stale worker returning, verifier outage, duplicate
completion, and cancellation with orphan teardown.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src", ROOT / "packages/aieb-runner/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")

if DATABASE_URL:
    from aieb_core.models import (
        BudgetProfile,
        CampaignDraft,
        Cohort,
        EntrantRevision,
        ProtocolRevision,
        RoleBudget,
        TaskRevision,
    )
    from aieb_core.planner import Registry, freeze_campaign
    from sqlalchemy import select, text, update

    from aieb_api import db
    from aieb_api import models as api_models
    from aieb_api.worker import repository
    from aieb_api.worker.loop import run_worker
    from aieb_api.worker.reconciler import reconcile_once
    from aieb_api.worker.runner_bridge import execute_leased_work


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-015 real-Postgres tests are blocked")
class WorkerLeasingTests(unittest.TestCase):
    TASK_SLUG = "rag.document-freshness"

    @classmethod
    def setUpClass(cls) -> None:
        db.configure(DATABASE_URL)

    def setUp(self) -> None:
        engine = db.engine()
        with engine.begin() as connection:
            for table in reversed(api_models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
        self.session_factory = db.session_factory()
        self.work_root = ROOT / ".cache" / "eng015-tests" / uuid.uuid4().hex
        self.work_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.work_root, ignore_errors=True)

    # ---- fixtures -------------------------------------------------------

    def _seed_task(self) -> None:
        manifest = {
            "schema_version": "aieb.task/v1", "id": self.TASK_SLUG, "version": "0.1.0", "family_id": "knowledge-service-a",
            "category": "rag", "activity": "repair",
            "source": {"repository_digest": "1" * 64, "commit": "synthetic", "license": "Apache-2.0", "provenance_digest": "2" * 64},
            "environment": {"official_image": "registry.example/aieb@sha256:" + "3" * 64, "engineer_cpu": 1, "engineer_memory_mb": 512, "service_topology_digest": "4" * 64, "egress_policy": "none"},
            "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
            "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 52_428_800},
            "requirements": [{"id": "api-ready", "severity": "mandatory", "description": "ready"}],
            "evaluator": {"evaluator_digest": "6" * 64, "development_fixture": "rag01-dev-v1", "official_fixture_ref": "maintainer-only:rag01-v1"},
            "profile_compatibility": ["cohort-a"],
        }
        with self.session_factory() as session:
            evaluator = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
            session.add(evaluator)
            session.flush()
            session.add(api_models.TaskRevisionRow(
                slug=self.TASK_SLUG, version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator.id, manifest=manifest,
            ))
            session.commit()

    def _seed_entrant(self, slug: str = "agent-a") -> None:
        manifest = {
            "schema_version": "aieb.entrant/v1", "id": slug, "track": "agents", "agent_implementation": "demo",
            "agent_version": "1.0.0", "engineer_model": {"provider_class": "demo", "requested_model": "demo-model", "settings_digest": "a" * 64},
            "prompt_digest": "b" * 64, "tools_digest": "c" * 64, "capabilities": ["cpu-fixture-standard-v1"],
            "credential_ref_type": "broker",
        }
        with self.session_factory() as session:
            session.add(api_models.EntrantRevisionRow(
                slug=slug, version="1.0.0", track="agents", config_digest=f"{slug}-digest",
                capabilities=["cpu-fixture-standard-v1"], manifest=manifest,
            ))
            session.commit()

    def _frozen_enqueued_campaign(self, repetitions: int = 1) -> uuid.UUID:
        self._seed_task()
        self._seed_entrant()
        with self.session_factory() as session:
            task_row = session.execute(select(api_models.TaskRevisionRow).where(api_models.TaskRevisionRow.slug == self.TASK_SLUG)).scalar_one()
            entrant_row = session.execute(select(api_models.EntrantRevisionRow).where(api_models.EntrantRevisionRow.slug == "agent-a")).scalar_one()
            task = TaskRevision.model_validate(task_row.manifest)
            entrant = EntrantRevision.model_validate(entrant_row.manifest)

        cohort = Cohort(
            schema_version="aieb.cohort/v1", id="cohort-a", track="agents", suite_id="suite-a", protocol_id="protocol-a",
            budget_profile_id="budget-a", dependency_mode="fixture", application_model_profile=task.application,
            hardware_class="cpu-fixture-standard-v1", required_capabilities=["cpu-fixture-standard-v1"],
        )
        protocol = ProtocolRevision(
            schema_version="aieb.protocol/v1", id="protocol-a", scoring_digest="9" * 64, max_replacements=2,
            required_trace_coverage=False, hard_cost_ranking=False,
        )
        budget = BudgetProfile(
            schema_version="aieb.budget/v1", id="budget-a", engineer_wall_seconds=1200, verification_wall_seconds=300,
            engineer_cpu=2, engineer_memory_mb=1024,
            per_role_budget_usd=[RoleBudget(role=r, limit_usd=None) for r in ("engineer", "dev_application", "verifier_application", "verifier_judge")],
        )
        draft = CampaignDraft(
            schema_version="aieb.campaign-draft/v1", id="draft-1", cohort_id="cohort-a", task_ids=[self.TASK_SLUG],
            entrant_ids=["agent-a"], repetitions=repetitions, order_seed=1, max_concurrent_trials=1, optimistic_revision=0,
        )
        registry = Registry(tasks={self.TASK_SLUG: task}, entrants={"agent-a": entrant}, cohorts={"cohort-a": cohort}, protocols={"protocol-a": protocol}, budgets={"budget-a": budget})
        resolved = freeze_campaign(draft, registry)

        with self.session_factory() as session:
            campaign = api_models.CampaignRow(
                name="worker-test", state="frozen", draft=draft.model_dump(mode="json"),
                manifest_digest=resolved.digest(), cohort_digest=resolved.cohort.digest(), resolved=resolved.model_dump(mode="json"),
            )
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
            repository.enqueue_frozen_campaign(session, campaign_id)
        return campaign_id

    def _backdate_lease(self, work_item_id: uuid.UUID) -> None:
        with self.session_factory() as session:
            session.execute(
                update(api_models.WorkItemRow).where(api_models.WorkItemRow.id == work_item_id).values(
                    lease_expiry=datetime.now(timezone.utc) - timedelta(seconds=1)
                )
            )
            session.commit()

    # ---- 1. two workers contending for work -----------------------------

    def test_two_workers_never_claim_the_same_item(self) -> None:
        self._frozen_enqueued_campaign(repetitions=2)
        results: list[repository.LeasedWork | None] = [None, None]

        def claim(index: int) -> None:
            with self.session_factory() as session:
                results[index] = repository.claim_work_item(session, worker_id=f"worker-{index}")

        barrier_threads = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
        for t in barrier_threads:
            t.start()
        for t in barrier_threads:
            t.join(timeout=10)

        self.assertIsNotNone(results[0])
        self.assertIsNotNone(results[1])
        self.assertNotEqual(results[0].work_item_id, results[1].work_item_id)
        with self.session_factory() as session:
            third = repository.claim_work_item(session, worker_id="worker-2")
        self.assertIsNone(third)  # exhausted: only two ready items existed

    # ---- 2. death before launch (claimed, never executed) --------------

    def test_death_before_launch_is_replaced_not_double_counted(self) -> None:
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="doomed-worker")
        self._backdate_lease(leased.work_item_id)

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.replaced, 1)
        self.assertEqual(summary.resumed, 0)

        with self.session_factory() as session:
            old_item = session.get(api_models.WorkItemRow, leased.work_item_id)
            self.assertEqual(old_item.state, "failed")
            old_attempt = session.get(api_models.AttemptRow, leased.attempt_id)
            self.assertEqual(old_attempt.terminal_status, "infrastructure_invalid")
            new_items = session.execute(
                select(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready")
            ).scalars().all()
            self.assertEqual(len(new_items), 1)
            new_attempt = session.get(api_models.AttemptRow, new_items[0].attempt_id)
            self.assertEqual(new_attempt.number, 2)
            self.assertEqual(new_attempt.trial_id, old_attempt.trial_id)
            candidates = session.execute(select(api_models.CandidateRow).where(api_models.CandidateRow.attempt_id == old_attempt.id)).scalars().all()
            self.assertEqual(candidates, [])  # nothing was ever collected

    # ---- 3. death during engineering: a real killed subprocess ----------

    def test_death_during_engineering_real_subprocess_kill_is_replaced_and_orphans_removed(self) -> None:
        self._frozen_enqueued_campaign()
        script = self.work_root / "child_worker.py"
        script.write_text(
            "import os, sys\n"
            f"sys.path.insert(0, {str(ROOT / 'services/api/src')!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'packages/aieb-core/src')!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'packages/aieb-runner/src')!r})\n"
            f"os.environ['AIEB_DATABASE_URL'] = {DATABASE_URL!r}\n"
            "from pathlib import Path\n"
            "from aieb_api import db\n"
            "from aieb_api.worker import repository\n"
            "from aieb_api.worker.runner_bridge import execute_leased_work\n"
            "db.configure()\n"
            "sf = db.session_factory()\n"
            "with sf() as session:\n"
            "    leased = repository.claim_work_item(session, worker_id='killed-worker')\n"
            f"execute_leased_work(sf, leased, worker_id='killed-worker', work_root=Path({str(self.work_root)!r}), candidate_variant='reference', engineering_delay_seconds=20)\n",
            encoding="utf-8",
        )
        # The child sleeps 20s before touching any candidate file, giving this test a
        # wide window to kill it while it is genuinely still "engineering".
        process = subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
        time.sleep(2)  # let it claim the item and enter the sleep
        process.kill()
        process.wait(timeout=10)

        with self.session_factory() as session:
            leased_item = session.execute(select(api_models.WorkItemRow)).scalars().one()
        self.assertEqual(leased_item.state, "leased")  # the killed process never finalized

        self._backdate_lease(leased_item.id)
        engineer_dir = next((self.work_root / str(leased_item.attempt_id) / "runs").glob("attempt-*/engineer"))
        self.assertTrue(engineer_dir.is_dir())  # the killed process's own cleanup never ran

        # Orphan cleanup is driven by reconcile_expired_leases' own locked decision
        # (returned as orphaned_attempt_ids), not a separate later read, so both
        # happen in one call.
        summary = reconcile_once(self.session_factory, self.work_root)
        self.assertEqual(summary.replaced, 1)
        self.assertEqual(summary.resumed, 0)
        self.assertEqual(summary.orphaned_attempt_ids, (leased_item.attempt_id,))
        self.assertFalse(engineer_dir.exists())  # genuinely found and removed
        with self.session_factory() as session:
            candidates = session.execute(select(api_models.CandidateRow)).scalars().all()
            self.assertEqual(candidates, [])  # killed before any candidate was collected

    # ---- 4. death after upload, before finalization ---------------------

    def test_death_after_artifact_upload_is_resumed_not_replaced(self) -> None:
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="crashy-worker")
            recorded = repository.record_outcome(
                session, work_item_id=leased.work_item_id, worker_id="crashy-worker", generation=leased.generation,
                attempt_id=leased.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=self._any_evaluator_id(session), fixture_id=self._any_fixture_id(session),
                    schedule_digest="s" * 64, verdict="pass", result={"pass": True},
                ),
            )
        self.assertTrue(recorded)
        # The process "dies" here: finalize() is never called.
        self._backdate_lease(leased.work_item_id)

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.resumed, 1)
        self.assertEqual(summary.replaced, 0)

        with self.session_factory() as session:
            item = session.get(api_models.WorkItemRow, leased.work_item_id)
            self.assertEqual(item.state, "done")
            attempt = session.get(api_models.AttemptRow, leased.attempt_id)
            self.assertEqual(attempt.terminal_status, "pass")
            ready = session.execute(select(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready")).scalars().all()
            self.assertEqual(ready, [])  # no wasted replacement/duplicate scoring

    def _any_evaluator_id(self, session) -> uuid.UUID:
        return session.execute(select(api_models.EvaluatorRevisionRow.id)).scalar_one()

    def _any_fixture_id(self, session) -> uuid.UUID:
        existing = session.execute(select(api_models.FixtureRevisionRow.id)).scalar_one_or_none()
        if existing is not None:
            return existing
        row = api_models.FixtureRevisionRow(digest="f" * 64, visibility="restricted", family_id="knowledge-service-a")
        session.add(row)
        session.flush()
        session.commit()
        return row.id

    # ---- second-pass review: record_outcome's fence must be a real lock -----

    def test_concurrent_reconciler_cannot_race_a_record_outcome_still_in_flight(self) -> None:
        """Review finding #1: record_outcome's fencing check was a plain SELECT,
        which takes no row lock under READ COMMITTED - a concurrent reconciler
        sweep could expire-and-replace the same attempt between that check and
        record_outcome's own commit, letting an already-abandoned worker's
        results land anyway. The fix makes the fencing check a real UPDATE, so
        it takes the same row lock the reconciler's SELECT ... FOR UPDATE SKIP
        LOCKED contends for.

        This calls the real repository.record_outcome (not a reimplementation),
        using a SQLAlchemy before_commit hook to pause it - with its fencing
        UPDATE already executed and its row lock already held - while a genuine
        concurrent reconciler sweep runs in a second real thread, and confirms
        the reconciler skips the row entirely rather than reconciling out from
        under the in-flight transaction."""
        from sqlalchemy import event

        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="racer")
        self._backdate_lease(leased.work_item_id)  # looks expired to any outside, unlocked read

        barrier = threading.Barrier(2)

        def record_via_real_function() -> bool:
            with self.session_factory() as session:
                def _pause_before_commit(sess: object) -> None:
                    barrier.wait(timeout=5)
                    time.sleep(0.5)  # hold the row lock open while the reconciler races in below

                event.listen(session, "before_commit", _pause_before_commit)
                try:
                    return repository.record_outcome(
                        session, work_item_id=leased.work_item_id, worker_id="racer", generation=leased.generation,
                        attempt_id=leased.attempt_id,
                        candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
                        evaluation=None,
                    )
                finally:
                    event.remove(session, "before_commit", _pause_before_commit)

        results: list[bool] = []
        record_thread = threading.Thread(target=lambda: results.append(record_via_real_function()))

        def reconcile_concurrently() -> None:
            barrier.wait(timeout=5)
            reconcile_once(self.session_factory)

        reconcile_thread = threading.Thread(target=reconcile_concurrently)
        record_thread.start()
        reconcile_thread.start()
        record_thread.join(timeout=10)
        reconcile_thread.join(timeout=10)

        self.assertEqual(results, [True])
        with self.session_factory() as session:
            item = session.get(api_models.WorkItemRow, leased.work_item_id)
            self.assertEqual(item.state, "leased")  # untouched: the reconciler skipped the locked row
            candidates = session.execute(select(api_models.CandidateRow)).scalars().all()
            self.assertEqual(len(candidates), 1)  # the in-flight worker's result legitimately landed
            ready = session.execute(select(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready")).scalars().all()
            self.assertEqual(ready, [])  # no wasted concurrent replacement was created

    # ---- second-pass review: orphan cleanup must not race a delayed heartbeat --

    def test_reconciler_never_orphans_a_lease_a_live_worker_just_re_extended(self) -> None:
        """Review finding #2: orphan-file cleanup previously came from a separate,
        disconnected, unlocked SELECT - a live worker whose heartbeat was merely
        delayed (GC pause, slow round-trip) could have its files deleted even
        though its heartbeat succeeds moments later. Cleanup is now driven only
        by reconcile_expired_leases' own locked decision (orphaned_attempt_ids);
        a lease a real heartbeat call has just extended must never appear there,
        even though an earlier point-in-time read would have called it expired."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="slow-worker")
        self._backdate_lease(leased.work_item_id)  # looked expired a moment ago

        engineer_dir = self.work_root / str(leased.attempt_id) / "runs" / "attempt-fake" / "engineer"
        engineer_dir.mkdir(parents=True)
        (engineer_dir / "in-progress.txt").write_text("still working", encoding="utf-8")

        # The worker's heartbeat lands and commits before the reconciler's own
        # locked pass runs - a delayed-but-alive worker, not a dead one.
        with self.session_factory() as session:
            self.assertTrue(repository.heartbeat(session, work_item_id=leased.work_item_id, worker_id="slow-worker", generation=leased.generation))

        summary = reconcile_once(self.session_factory, self.work_root)
        self.assertEqual(summary.replaced, 0)
        self.assertEqual(summary.orphaned_attempt_ids, ())
        self.assertTrue(engineer_dir.exists())  # never touched
        with self.session_factory() as session:
            item = session.get(api_models.WorkItemRow, leased.work_item_id)
            self.assertEqual(item.state, "leased")

    # ---- 5. lease expiry with an old (stale) worker returning ------------

    def test_stale_worker_finalize_after_lease_reassignment_is_rejected(self) -> None:
        """Reconciliation replaces an expired attempt with a new one rather than
        reclaiming the same work_item in place (spec section 16: a replacement
        increments the attempt number; it does not resurrect the original
        allocation). So the fence a stale worker hits is state='leased' no longer
        matching (the original item is 'failed'), not a generation bump on the same
        row - a new worker instead claims the freshly created replacement item."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            first = repository.claim_work_item(session, worker_id="worker-a")
        self._backdate_lease(first.work_item_id)
        reconcile_once(self.session_factory)  # marks the original 'failed'; creates a replacement 'ready' item

        with self.session_factory() as session:
            second = repository.claim_work_item(session, worker_id="worker-b")
        self.assertIsNotNone(second)
        self.assertNotEqual(second.work_item_id, first.work_item_id)
        self.assertNotEqual(second.attempt_id, first.attempt_id)

        with self.session_factory() as session:
            stale_heartbeat = repository.heartbeat(session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation)
        self.assertFalse(stale_heartbeat)
        with self.session_factory() as session:
            stale_finalize = repository.finalize(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
                attempt_id=first.attempt_id, terminal_status="pass", done=True,
            )
        self.assertFalse(stale_finalize)
        # worker-b's own legitimate finalize of the replacement must still succeed.
        with self.session_factory() as session:
            legitimate = repository.finalize(
                session, work_item_id=second.work_item_id, worker_id="worker-b", generation=second.generation,
                attempt_id=second.attempt_id, terminal_status="pass", done=True,
            )
        self.assertTrue(legitimate)

    # ---- 6. verifier outage (trusted scorer crash, not a candidate defect) --

    def test_verifier_outage_is_infrastructure_invalid_not_a_scored_fail(self) -> None:
        from aieb_api.worker import runner_bridge

        raising_module = "tests.fixtures.worker.raising_evaluator"
        original = runner_bridge.TASK_RUNTIMES[self.TASK_SLUG]
        runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = (original[0], raising_module)
        try:
            self._frozen_enqueued_campaign()
            with self.session_factory() as session:
                leased = repository.claim_work_item(session, worker_id="w1")
            result = execute_leased_work(self.session_factory, leased, worker_id="w1", work_root=self.work_root)
        finally:
            runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = original

        self.assertEqual(result.execution_validity, "infrastructure_invalid")
        self.assertIsNone(result.verdict)
        with self.session_factory() as session:
            item = session.get(api_models.WorkItemRow, leased.work_item_id)
            self.assertEqual(item.state, "failed")
            attempt = session.get(api_models.AttemptRow, leased.attempt_id)
            self.assertEqual(attempt.terminal_status, "scorer_error")
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(evaluations, [])  # never fabricate a verdict from a crashed scorer

    # ---- 7. duplicate completion ----------------------------------------

    def test_duplicate_finalize_call_is_a_no_op_not_a_double_score(self) -> None:
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="w1")
        result = execute_leased_work(self.session_factory, leased, worker_id="w1", work_root=self.work_root)
        self.assertTrue(result.finalized)

        with self.session_factory() as session:
            second_attempt = repository.finalize(
                session, work_item_id=leased.work_item_id, worker_id="w1", generation=leased.generation,
                attempt_id=leased.attempt_id, terminal_status="pass", done=True,
            )
        self.assertFalse(second_attempt)  # already 'done'; WHERE state='leased' excludes it

    # ---- 8. cancellation and orphan teardown -----------------------------

    def test_cancelled_campaign_attempt_is_not_engineered_and_cleans_up(self) -> None:
        campaign_id = self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            self.assertTrue(repository.cancel_campaign(session, campaign_id))

        processed = run_worker(
            self.session_factory, worker_id="w1", work_root=self.work_root, max_iterations=1, candidate_variant="reference",
        )
        self.assertEqual(processed, 1)
        with self.session_factory() as session:
            item = session.execute(select(api_models.WorkItemRow)).scalars().one()
            self.assertEqual(item.state, "failed")
            attempt = session.execute(select(api_models.AttemptRow)).scalars().one()
            self.assertEqual(attempt.terminal_status, "cancelled")
            campaign = session.get(api_models.CampaignRow, campaign_id)
            self.assertEqual(campaign.state, "cancelled")  # no outstanding work left
        # LocalAttemptRunner's own cleanup phase runs even for a cooperatively
        # cancelled attempt: the writable engineer/build allocations are removed,
        # while the immutable attempt.json evidence is deliberately retained.
        leftover_engineer_or_build = list(self.work_root.glob("*/runs/*/engineer")) + list(self.work_root.glob("*/runs/*/build"))
        self.assertEqual(leftover_engineer_or_build, [])
        evidence_files = list(self.work_root.glob("*/runs/*/attempt.json"))
        self.assertEqual(len(evidence_files), 1)

    def test_cancellation_arriving_mid_run_interrupts_the_attempt(self) -> None:
        """Review finding #6: the prior test only covered a campaign already
        cancelling before the worker claimed work. A cancel arriving while an
        attempt is already engineering must interrupt it too, not merely block
        new dispatch and let the in-flight attempt run to completion or deadline
        regardless."""
        campaign_id = self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="w1", lease_seconds=3)

        def cancel_soon() -> None:
            time.sleep(1.5)
            with self.session_factory() as session:
                repository.cancel_campaign(session, campaign_id)

        threading.Thread(target=cancel_soon, daemon=True).start()
        start = time.monotonic()
        result = execute_leased_work(
            self.session_factory, leased, worker_id="w1", work_root=self.work_root,
            candidate_variant="reference", lease_seconds=3, engineering_delay_seconds=20,
        )
        elapsed = time.monotonic() - start

        self.assertEqual(result.execution_validity, "cancelled")
        self.assertLess(elapsed, 10)  # interrupted well before the 20-second delay would have elapsed
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, leased.attempt_id)
            self.assertEqual(attempt.terminal_status, "cancelled")


if __name__ == "__main__":
    unittest.main()
