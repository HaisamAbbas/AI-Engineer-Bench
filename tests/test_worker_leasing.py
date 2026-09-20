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
import signal
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
    from aieb_api.worker.artifact_store import PostgresArtifactStore
    from aieb_api.worker.loop import install_drain_handlers, run_worker
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
            # The kill_switch and system_fence singletons are seeded once by their migrations,
            # not re-created by this per-test TRUNCATE - reseed them so ENG-020 tests find their rows.
            connection.execute(text("INSERT INTO kill_switch (id, active) VALUES (1, false)"))
            connection.execute(text("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)"))
        self.session_factory = db.session_factory()
        self.work_root = ROOT / ".cache" / "eng015-tests" / uuid.uuid4().hex
        self.work_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.work_root, ignore_errors=True)

    def _force_candidate_corruption(self, session, candidate_id: uuid.UUID, stored: dict) -> None:
        """Simulate privileged storage corruption past the normal DB guard."""
        session.execute(text("ALTER TABLE candidate DISABLE TRIGGER candidate_evidence_immutable"))
        session.execute(update(api_models.CandidateRow).where(api_models.CandidateRow.id == candidate_id).values(stored_candidate=stored))
        session.execute(text("ALTER TABLE candidate ENABLE TRIGGER candidate_evidence_immutable"))
        session.commit()

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
            campaign.state = "running"
            session.commit()
        return campaign_id

    def _backdate_lease(self, work_item_id: uuid.UUID) -> None:
        with self.session_factory() as session:
            session.execute(
                update(api_models.WorkItemRow).where(api_models.WorkItemRow.id == work_item_id).values(
                    lease_expiry=datetime.now(timezone.utc) - timedelta(seconds=1)
                )
            )
            session.commit()

    def _run_to_completion(self, worker_id: str = "w1", *, max_phases: int = 2, **kwargs) -> list:
        """Claim and execute whatever is ready, repeatedly, until nothing is
        ready or max_phases calls have run. A normal successful attempt now
        takes two independently-leased phases (engineering, then
        verification) instead of one - this mirrors exactly what
        worker/loop.py's run_worker already does (claim -> execute -> claim
        -> execute), just without the idle-sleep/poll machinery a single
        synchronous test doesn't need."""
        from aieb_api.worker.runner_bridge import execute_leased_work

        results = []
        for _ in range(max_phases):
            with self.session_factory() as session:
                leased = repository.claim_work_item(session, worker_id=worker_id)
            if leased is None:
                break
            results.append(execute_leased_work(self.session_factory, leased, worker_id=worker_id, work_root=self.work_root, **kwargs))
        return results

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

    # ---- review finding #5: shared storage is the actual default, not merely documented --

    def test_regrade_executes_retained_candidate_without_engineering(self) -> None:
        from aieb_api.regrading import enqueue_regrade, installed_scoring_bundle, complete_correction_runs
        from aieb_api.evidence_integrity import evidence_digest
        from aieb_api.aggregation import aggregate_campaign_snapshot

        campaign_id = self._frozen_enqueued_campaign()
        self._run_to_completion()
        with self.session_factory() as session:
            campaign = session.get(api_models.CampaignRow, campaign_id)
            campaign.state = "completed"
            user = api_models.User(oidc_subject="correction-reviewer", oidc_issuer="test")
            session.add(user); session.flush()
            original = session.execute(select(api_models.EvaluationRow)).scalar_one()
            original_id, original_result = original.id, original.result
            run = enqueue_regrade(session, campaign,
                scoring_digest=evidence_digest(installed_scoring_bundle(session, campaign_id)),
                reason="staging verification", user_id=user.id)
            run_id = run.id
            session.commit()
            leased = repository.claim_work_item(session, worker_id="regrader", work_type="regrade")
        result = execute_leased_work(self.session_factory, leased, worker_id="regrader", work_root=self.work_root / "regrade")
        self.assertTrue(result.finalized)
        self.assertEqual(result.verdict, "pass")
        with self.session_factory() as session:
            complete_correction_runs(session)
            self.assertEqual(session.get(api_models.CorrectionRunRow, run_id).status, "completed")
            corrected = session.execute(select(api_models.EvaluationRow).where(api_models.EvaluationRow.correction_run_id == run_id)).scalar_one()
            self.assertNotEqual(corrected.id, original_id)
            self.assertEqual(session.get(api_models.EvaluationRow, original_id).result, original_result)
            self.assertEqual(session.query(api_models.WorkItemRow).filter_by(type="engineering").count(), 1)
            self.assertEqual(aggregate_campaign_snapshot(session, campaign_id, correction_run_id=run_id)["suite_rate"], 1.0)


    def test_verification_recovers_the_candidate_with_no_shared_filesystem_at_all(self) -> None:
        """Review finding #5: a prior version's independence test still
        pointed both phases at the same local directory, and candidate bytes
        lived only in a worker-local FilesystemArtifactStore - "the database
        alone" was only true if operators separately provisioned a shared/
        network mount across every worker host, which was not actually the
        default. The hosted worker now stores candidate bytes in Postgres
        (PostgresArtifactStore, the same AIEB_DATABASE_URL every worker
        already needs to lease work at all) - proven directly here by giving
        engineering and verification COMPLETELY SEPARATE, never-shared
        work_root directories (simulating two different hosts with no
        filesystem in common whatsoever), and confirming verification still
        succeeds and scores correctly from the database alone."""
        self._frozen_enqueued_campaign()
        engineering_root = ROOT / ".cache" / "eng015-tests" / f"host-a-{uuid.uuid4().hex}"
        verification_root = ROOT / ".cache" / "eng015-tests" / f"host-b-{uuid.uuid4().hex}"
        engineering_root.mkdir(parents=True, exist_ok=True)
        verification_root.mkdir(parents=True, exist_ok=True)
        try:
            with self.session_factory() as session:
                engineering = repository.claim_work_item(session, worker_id="host-a-worker")
            engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="host-a-worker", work_root=engineering_root)
            self.assertTrue(engineering_result.finalized)

            with self.session_factory() as session:
                verification = repository.claim_work_item(session, worker_id="host-b-worker", work_type="verification")
            verification_result = execute_leased_work(self.session_factory, verification, worker_id="host-b-worker", work_root=verification_root)
            self.assertEqual(verification_result.execution_validity, "valid")
            self.assertEqual(verification_result.verdict, "pass")
            self.assertTrue(verification_result.finalized)
        finally:
            import shutil

            shutil.rmtree(engineering_root, ignore_errors=True)
            shutil.rmtree(verification_root, ignore_errors=True)

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
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.assertIsNone(process.poll(), "worker exited before engineering began")
                with self.session_factory() as session:
                    item = session.scalars(select(api_models.WorkItemRow)).one()
                    started = session.scalars(select(api_models.AttemptEventRow).where(
                        api_models.AttemptEventRow.attempt_id == item.attempt_id,
                        api_models.AttemptEventRow.event_type == "phase.started",
                    )).first()
                    allocations = list((self.work_root / str(item.attempt_id) / "runs").glob("attempt-*/engineer"))
                    if item.state == "leased" and started is not None and allocations:
                        break
                time.sleep(0.05)
            else:
                self.fail("worker did not enter engineering with a live lease and allocation")
        finally:
            if process.poll() is None:
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

    # ---- 4. death after candidate persistence, before verification lease --

    def test_engineering_death_after_candidate_persistence_advances_to_verification(self) -> None:
        """ENG015-007's own required scenario: a worker dies after
        record_candidate commits (the candidate is durably persisted) but
        before advance_to_verification commits - the engineering work item is
        still 'leased', not 'done'. Reconciliation must recognize the
        candidate already exists and advance straight to an independently
        leased verification work item, never repeat engineering."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="doomed-engineer")
            candidate_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="doomed-engineer", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
        self.assertIsNotNone(candidate_id)
        # The worker dies here: advance_to_verification() is never called.
        self._backdate_lease(engineering.work_item_id)

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.advanced, 1)
        self.assertEqual(summary.replaced, 0)
        self.assertEqual(summary.resumed, 0)
        self.assertEqual(summary.requeued, 0)

        with self.session_factory() as session:
            engineering_item = session.get(api_models.WorkItemRow, engineering.work_item_id)
            self.assertEqual(engineering_item.state, "done")
            attempt = session.get(api_models.AttemptRow, engineering.attempt_id)
            self.assertEqual(attempt.phase, "verifying")
            self.assertIsNone(attempt.terminal_status)  # not terminal yet - only advanced, not finalized
            ready = session.execute(select(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready")).scalars().all()
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0].type, "verification")
            self.assertEqual(ready[0].attempt_id, engineering.attempt_id)  # same attempt - no re-engineering
            candidates = session.execute(select(api_models.CandidateRow).where(api_models.CandidateRow.attempt_id == engineering.attempt_id)).scalars().all()
            self.assertEqual(len(candidates), 1)  # the persisted candidate was reused, not recreated

    def test_engineering_death_after_candidate_persistence_still_attaches_its_references(self) -> None:
        """Review finding #4 (second half): a worker that dies after
        record_candidate() commits but BEFORE attach_candidate_references()
        runs leaves its collected blobs' references permanently
        candidate_id=NULL / retention_class='staging' unless the reconciler's
        own advance-to-verification recovery path also claims them - a prior
        version of that recovery path only advanced the work item, never
        called attach_candidate_references, so those references stayed
        (incorrectly) classified as staging forever, vulnerable to the
        24-hour orphan purge running before anyone ever claims them even
        though a real candidate now exists and verification is about to
        consume it."""
        self._frozen_enqueued_campaign()
        store = PostgresArtifactStore(self.session_factory)
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="doomed-engineer")
            # Mirrors what collect_candidate really does: put_bytes + create_reference
            # under the attempt's own access_scope, BEFORE record_candidate.
            blob = store.put_bytes(b"collected-but-not-yet-attached")
            reference = store.create_reference(blob, access_scope=str(engineering.attempt_id))
            candidate_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="doomed-engineer", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
        self.assertIsNotNone(candidate_id)
        # The worker dies here: attach_candidate_references() is never called,
        # exactly like advance_to_verification() in the sibling test above.
        self._backdate_lease(engineering.work_item_id)

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.advanced, 1)

        with self.session_factory() as session:
            row = session.get(api_models.WorkerArtifactReferenceRow, reference.id)
            self.assertEqual(row.candidate_id, candidate_id)
            blob_row = session.get(api_models.WorkerArtifactBlobRow, blob.sha256)
            self.assertEqual(blob_row.retention_class, "evidence")
            self.assertIsNone(blob_row.staged_until)

    def test_verification_death_after_evaluation_recorded_is_resumed_not_requeued(self) -> None:
        """The verification-phase analogue of artifact-first resume: a
        verifier dies after record_evaluation commits but before finalize() -
        reconciliation must finalize from that already-persisted evaluation,
        not requeue a duplicate verification attempt."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="engineer-1")
            candidate_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="engineer-1", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
            self.assertTrue(repository.advance_to_verification(
                session, work_item_id=engineering.work_item_id, worker_id="engineer-1", generation=engineering.generation, attempt_id=engineering.attempt_id,
            ))
        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="crashy-verifier", work_type="verification")
            recorded = repository.record_evaluation(
                session, work_item_id=verification.work_item_id, worker_id="crashy-verifier", generation=verification.generation,
                candidate_id=candidate_id,
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=self._any_evaluator_id(session), fixture_id=self._any_fixture_id(session),
                    schedule_digest="s" * 64, verdict="pass", result={"pass": True},
                ),
            )
        self.assertTrue(recorded)
        # The verifier dies here: finalize() is never called.
        self._backdate_lease(verification.work_item_id)

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.resumed, 1)
        self.assertEqual(summary.replaced, 0)
        self.assertEqual(summary.advanced, 0)
        self.assertEqual(summary.requeued, 0)

        with self.session_factory() as session:
            item = session.get(api_models.WorkItemRow, verification.work_item_id)
            self.assertEqual(item.state, "done")
            attempt = session.get(api_models.AttemptRow, engineering.attempt_id)
            self.assertEqual(attempt.terminal_status, "pass")
            ready = session.execute(select(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready")).scalars().all()
            self.assertEqual(ready, [])  # no wasted replacement/duplicate scoring

    def test_verifier_death_with_no_evaluation_retries_verification_without_re_engineering(self) -> None:
        """A dead verifier that never recorded an evaluation at all must not
        cause engineering to repeat - only a fresh verification work item for
        the SAME attempt and the SAME already-persisted candidate."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="engineer-1")
            repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="engineer-1", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
            self.assertTrue(repository.advance_to_verification(
                session, work_item_id=engineering.work_item_id, worker_id="engineer-1", generation=engineering.generation, attempt_id=engineering.attempt_id,
            ))
        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="dead-verifier", work_type="verification")
        # The verifier dies immediately - no record_evaluation call at all.
        self._backdate_lease(verification.work_item_id)

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.requeued, 1)
        self.assertEqual(summary.resumed, 0)
        self.assertEqual(summary.replaced, 0)
        self.assertEqual(summary.advanced, 0)

        with self.session_factory() as session:
            old_item = session.get(api_models.WorkItemRow, verification.work_item_id)
            self.assertEqual(old_item.state, "failed")
            attempt = session.get(api_models.AttemptRow, engineering.attempt_id)
            self.assertIsNone(attempt.terminal_status)  # not terminal - retrying, not exhausted
            ready = session.execute(select(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready")).scalars().all()
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0].type, "verification")
            self.assertEqual(ready[0].attempt_id, engineering.attempt_id)  # same attempt, not replaced
            engineering_items = session.execute(
                select(api_models.WorkItemRow).where(api_models.WorkItemRow.attempt_id == engineering.attempt_id, api_models.WorkItemRow.type == "engineering")
            ).scalars().all()
            self.assertEqual(len(engineering_items), 1)  # no second engineering item was ever created

    def test_stale_verifier_cannot_record_or_finalize_after_lease_reassignment(self) -> None:
        """The verification-phase analogue of the existing stale-engineering-
        worker fencing test: once reconciliation has requeued a dead
        verifier's work item, that verifier's own generation is fenced out of
        both record_evaluation and finalize - a legitimate second verifier's
        own calls on the replacement item must still succeed."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="engineer-1")
            candidate_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="engineer-1", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
            self.assertTrue(repository.advance_to_verification(
                session, work_item_id=engineering.work_item_id, worker_id="engineer-1", generation=engineering.generation, attempt_id=engineering.attempt_id,
            ))
        with self.session_factory() as session:
            first = repository.claim_work_item(session, worker_id="stale-verifier", work_type="verification")
        self._backdate_lease(first.work_item_id)
        reconcile_once(self.session_factory)  # marks the original 'failed'; requeues a replacement 'ready' item

        with self.session_factory() as session:
            second = repository.claim_work_item(session, worker_id="live-verifier", work_type="verification")
        self.assertIsNotNone(second)
        self.assertNotEqual(second.work_item_id, first.work_item_id)
        self.assertEqual(second.attempt_id, first.attempt_id)  # same attempt - not a new one

        with self.session_factory() as session:
            stale_record = repository.record_evaluation(
                session, work_item_id=first.work_item_id, worker_id="stale-verifier", generation=first.generation,
                candidate_id=candidate_id,
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=self._any_evaluator_id(session), fixture_id=self._any_fixture_id(session),
                    schedule_digest="s" * 64, verdict="pass", result={"pass": True},
                ),
            )
        self.assertFalse(stale_record)
        with self.session_factory() as session:
            stale_finalize = repository.finalize(
                session, work_item_id=first.work_item_id, worker_id="stale-verifier", generation=first.generation,
                attempt_id=first.attempt_id, terminal_status="pass", done=True,
            )
        self.assertFalse(stale_finalize)
        # live-verifier's own legitimate work on the replacement item still succeeds.
        with self.session_factory() as session:
            legitimate_record = repository.record_evaluation(
                session, work_item_id=second.work_item_id, worker_id="live-verifier", generation=second.generation,
                candidate_id=candidate_id,
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=self._any_evaluator_id(session), fixture_id=self._any_fixture_id(session),
                    schedule_digest="s" * 64, verdict="pass", result={"pass": True},
                ),
            )
        self.assertTrue(legitimate_record)
        with self.session_factory() as session:
            legitimate_finalize = repository.finalize(
                session, work_item_id=second.work_item_id, worker_id="live-verifier", generation=second.generation,
                attempt_id=second.attempt_id, terminal_status="pass", done=True,
            )
        self.assertTrue(legitimate_finalize)

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

    # ---- second-pass review: record_candidate's fence must be a real lock ---

    def test_concurrent_reconciler_cannot_race_a_record_candidate_still_in_flight(self) -> None:
        """Review finding #1: record_outcome's (now record_candidate's)
        fencing check was a plain SELECT, which takes no row lock under READ
        COMMITTED - a concurrent reconciler sweep could expire-and-replace the
        same attempt between that check and the commit, letting an
        already-abandoned worker's results land anyway. The fix makes the
        fencing check a real UPDATE, so it takes the same row lock the
        reconciler's SELECT ... FOR UPDATE SKIP LOCKED contends for. Still
        true after ENG015-007's split - the mechanism is shared
        (_fenced_lease_touch) between record_candidate and record_evaluation.

        This calls the real repository.record_candidate (not a
        reimplementation), using a SQLAlchemy before_commit hook to pause it -
        with its fencing UPDATE already executed and its row lock already
        held - while a genuine concurrent reconciler sweep runs in a second
        real thread, and confirms the reconciler skips the row entirely
        rather than reconciling out from under the in-flight transaction."""
        from sqlalchemy import event

        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            leased = repository.claim_work_item(session, worker_id="racer")
        self._backdate_lease(leased.work_item_id)  # looks expired to any outside, unlocked read

        barrier = threading.Barrier(2)

        def record_via_real_function() -> uuid.UUID | None:
            with self.session_factory() as session:
                def _pause_before_commit(sess: object) -> None:
                    barrier.wait(timeout=5)
                    time.sleep(0.5)  # hold the row lock open while the reconciler races in below

                event.listen(session, "before_commit", _pause_before_commit)
                try:
                    return repository.record_candidate(
                        session, work_item_id=leased.work_item_id, worker_id="racer", generation=leased.generation,
                        attempt_id=leased.attempt_id,
                        candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
                    )
                finally:
                    event.remove(session, "before_commit", _pause_before_commit)

        results: list[uuid.UUID | None] = []
        record_thread = threading.Thread(target=lambda: results.append(record_via_real_function()))

        def reconcile_concurrently() -> None:
            barrier.wait(timeout=5)
            reconcile_once(self.session_factory)

        reconcile_thread = threading.Thread(target=reconcile_concurrently)
        record_thread.start()
        reconcile_thread.start()
        record_thread.join(timeout=10)
        reconcile_thread.join(timeout=10)

        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0])
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

    # ---- gap 4: restore-drill pre-reconciliation fencing (system fence epoch) --

    def test_fence_epoch_advance_fences_pre_restore_lease_at_first_touch(self) -> None:
        """ENG-020 gap 4: a database restore resurrects every pre-restore lease and credential
        exactly as it was - the lease_expiry is still in the future, the worker/generation
        still match. Nothing fenced such a lease until the reconciler's expiry poll, and a
        stale worker heartbeating it kept that poll from ever firing. The system fence epoch
        closes the hole: the operator advances it AFTER restore, and every fenced operation on
        a pre-restore lease/credential fails at FIRST touch - before any reconciliation."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            first = repository.claim_work_item(session, worker_id="worker-a")
        self.assertIsNotNone(first)
        with self.session_factory() as session:
            self.assertEqual(repository.current_fence_epoch(session), 0)
        self.assertEqual(first.lease_epoch, 0)

        # A pre-restore credential exists and is valid BEFORE the advance.
        with self.session_factory() as session:
            issued = repository.issue_attempt_credential(
                session, attempt_id=first.attempt_id, actor_role="candidate",
                work_item_id=first.work_item_id, worker_id="worker-a", lease_generation=first.generation,
            )
            self.assertTrue(repository.verify_attempt_credential(
                session, attempt_id=first.attempt_id, actor_role="candidate", token=issued.token,
            ))
        pre_restore_token = issued.token

        # The operator's post-restore step: advance the fence epoch.
        with self.session_factory() as session:
            advanced_to = repository.advance_fence_epoch(session, reason="test: restore-drill fence advance")
        self.assertEqual(advanced_to, 1)

        # WITHOUT any reconciliation, every fenced touch by the pre-restore worker fails.
        with self.session_factory() as session:
            self.assertFalse(repository.heartbeat(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
            ))
            self.assertFalse(repository.finalize(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
                attempt_id=first.attempt_id, terminal_status="pass", done=True,
            ))
            self.assertIsNone(repository.record_candidate(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
                attempt_id=first.attempt_id,
                candidate=repository.CandidateOutcome(
                    tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid", stored_candidate={},
                ),
            ))
            self.assertFalse(repository.advance_to_verification(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
                attempt_id=first.attempt_id,
            ))
            with self.assertRaises(repository.LeaseFenceError):
                repository.issue_attempt_credential(
                    session, attempt_id=first.attempt_id, actor_role="candidate",
                    work_item_id=first.work_item_id, worker_id="worker-a", lease_generation=first.generation,
                )
            # The pre-restore token itself is dead at first use, not merely unmintable.
            self.assertFalse(repository.verify_attempt_credential(
                session, attempt_id=first.attempt_id, actor_role="candidate", token=pre_restore_token,
            ))

        # The advance must not break the system: after the reconciler's next poll replaces the
        # orphan (as it does in real life), a fresh worker claims and heartbeats normally.
        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.replaced, 1)
        with self.session_factory() as session:
            second = repository.claim_work_item(session, worker_id="worker-b")
        self.assertIsNotNone(second)
        self.assertEqual(second.lease_epoch, advanced_to)
        with self.session_factory() as session:
            self.assertTrue(repository.heartbeat(
                session, work_item_id=second.work_item_id, worker_id="worker-b", generation=second.generation,
            ))
            fresh = repository.issue_attempt_credential(
                session, attempt_id=second.attempt_id, actor_role="candidate",
                work_item_id=second.work_item_id, worker_id="worker-b", lease_generation=second.generation,
            )
            self.assertTrue(repository.verify_attempt_credential(
                session, attempt_id=second.attempt_id, actor_role="candidate", token=fresh.token,
            ))

    def test_reconciler_sweeps_a_stale_epoch_lease_while_still_renewable(self) -> None:
        """ENG-020 gap 4: reconciliation must recover a stale-epoch lease EVEN WHILE its
        lease_expiry is still in the future - the pre-restore snapshot looked exactly like a
        live, renewable lease, and only the epoch condition can claim it on the next poll.
        (An EXPIRED lease was already swept before this change; this is the case that wasn't.)"""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            first = repository.claim_work_item(session, worker_id="worker-a")
        self.assertIsNotNone(first)
        # Deliberately NOT backdated: the lease is still renewable when the epoch advances.
        with self.session_factory() as session:
            self.assertEqual(repository.advance_fence_epoch(session, reason="test: simulated restore"), 1)
            row = session.get(api_models.WorkItemRow, first.work_item_id)
            self.assertGreater(row.lease_expiry, datetime.now(timezone.utc))
        # The stale worker cannot even heartbeat it any more...
        with self.session_factory() as session:
            self.assertFalse(repository.heartbeat(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
            ))
        # ...and reconciliation STILL quarantines it via the epoch sweep.
        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.replaced, 1)
        with self.session_factory() as session:
            row = session.get(api_models.WorkItemRow, first.work_item_id)
            self.assertEqual(row.state, "failed")
            second = repository.claim_work_item(session, worker_id="worker-b")
        self.assertIsNotNone(second)
        self.assertEqual(second.lease_epoch, 1)

    def test_fence_advance_is_atomic_against_in_flight_fenced_operations(self) -> None:
        """ENG-020 gap 4, deciding-review finding 1: the epoch read must be atomic against a
        concurrent advance. Each fenced operation's transaction holds the fence row FOR SHARE
        for its WHOLE lifetime; advance_fence_epoch() takes the same row's EXCLUSIVE lock, so
        PostgreSQL serializes the two. Without this a stale worker could read epoch 0, have the
        operator advance to (and commit) epoch 1, and then land its own epoch-0 fenced mutation
        AFTER the advance committed - the exact interleaving the deciding review reproduced
        (heartbeat read 0, advance committed 1, heartbeat UPDATE then returned True). The two
        sequential epoch tests cannot see it. This regression opens a fenced transaction on one
        session, then proves a CONCURRENT advance on a second session BLOCKS (lock timeout)
        until the first transaction closes.
        Regression-red on f1000a6: with an unlocked epoch read the advance commits immediately
        and the stale heartbeat lands after it."""
        from sqlalchemy.exc import OperationalError

        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            first = repository.claim_work_item(session, worker_id="worker-a")
        self.assertIsNotNone(first)

        # A fenced operation on session_a (a pre-restore worker, epoch still 0) opens its
        # transaction: the epoch read takes the fence row's FOR SHARE lock, held until the
        # operation commits.
        session_a = self.session_factory()
        self.assertEqual(repository.current_fence_epoch(session_a, share_lock=True), 0)

        # A CONCURRENT advance on session_b cannot commit while session_a's fenced transaction
        # is open: give it a lock_timeout and prove it is blocked.
        session_b = self.session_factory()
        session_b.execute(text("SET LOCAL lock_timeout = 800"))
        with self.assertRaises(OperationalError):
            repository.advance_fence_epoch(
                session_b, reason="test: concurrent advance while a fenced transaction is open",
            )
        session_b.close()

        # session_a's fenced mutation completes under the epoch it read (0) - legitimately,
        # because the advance could NOT have committed in the meantime.
        self.assertTrue(repository.heartbeat(
            session_a, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
        ))
        session_a.close()

        # Once no fenced transaction is open the advance commits...
        with self.session_factory() as session:
            advanced_to = repository.advance_fence_epoch(session, reason="test: advance after the fenced transaction closed")
        self.assertEqual(advanced_to, 1)

        # ...and the same worker is now fenced at first touch, as the sequential tests assert.
        with self.session_factory() as session:
            self.assertFalse(repository.heartbeat(
                session, work_item_id=first.work_item_id, worker_id="worker-a", generation=first.generation,
            ))

    # ---- 6. verifier outage (trusted scorer crash, not a candidate defect) --

    def test_verifier_outage_is_infrastructure_invalid_not_a_scored_fail(self) -> None:
        from aieb_api.worker import runner_bridge

        raising_module = "tests.fixtures.worker.raising_evaluator"
        original = runner_bridge.TASK_RUNTIMES[self.TASK_SLUG]
        runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = (original[0], raising_module, original[2])
        try:
            self._frozen_enqueued_campaign()
            # Two phases now: engineering succeeds and hands off; verification
            # is where the raising evaluator actually runs and fails.
            results = self._run_to_completion(worker_id="w1")
        finally:
            runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = original

        self.assertEqual(len(results), 2)
        engineering_result, verification_result = results
        self.assertTrue(engineering_result.finalized)  # engineering itself succeeded
        self.assertEqual(verification_result.execution_validity, "infrastructure_invalid")
        self.assertIsNone(verification_result.verdict)
        with self.session_factory() as session:
            attempt = session.execute(select(api_models.AttemptRow)).scalars().one()
            self.assertEqual(attempt.terminal_status, "scorer_error")
            verification_item = session.execute(
                select(api_models.WorkItemRow).where(api_models.WorkItemRow.type == "verification")
            ).scalars().one()
            self.assertEqual(verification_item.state, "failed")
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(evaluations, [])  # never fabricate a verdict from a crashed scorer

    # ---- reviewer blocker round: the EVALUATOR must never be imported in the worker parent --

    def test_hosted_verification_never_imports_the_evaluator_in_the_worker_parent(self) -> None:
        """Codex reviewer blocker round (runner_bridge parent-import leak): the hosted worker
        previously did `importlib.import_module(evaluator_module)` in ITS OWN parent process
        before spawning the isolated VERIFY child, so evaluator TOP-LEVEL code ran against the
        worker's unsanitized environment (AIEB_DATABASE_URL, CI_BUILD_TOKEN) - the regression
        missed it because it imported the probe BEFORE planting secrets. This end-to-end leased
        test plants the worker secrets FIRST, points the installed evaluator for this task at
        fixtures/worker/import_time_probe.py (which snapshots os.environ AT IMPORT TIME), and
        drives the real execute_leased_work engineering -> verification path. If anything in
        the production parent path (TASK_RUNTIMES resolution or run_verification) imported the
        probe, the probe would land in THIS process's sys.modules, and - imported with secrets
        already planted - its import-time snapshot would contain them. Assert both: the probe
        never enters the worker/test parent's sys.modules, and the recorded evaluation shows the
        child (THE first importer, post-scrub) saw neither planted secret at import time."""
        import sys

        from aieb_api.worker import runner_bridge

        probe_module = "tests.fixtures.worker.import_time_probe"
        sys.modules.pop(probe_module, None)

        original = runner_bridge.TASK_RUNTIMES[self.TASK_SLUG]
        runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = (original[0], probe_module, "evaluate")

        planted = {
            "AIEB_DATABASE_URL": "postgresql://sentinel:PLANTED@db.example/compromised",
            "CI_BUILD_TOKEN": "PLANTED_TOK",
        }
        kept = {key: os.environ.get(key) for key in planted}
        os.environ.update(planted)
        try:
            self.assertNotIn(probe_module, sys.modules, "probe must be fresh before the leased run")
            self._frozen_enqueued_campaign()
            results = self._run_to_completion(worker_id="w1")
        finally:
            runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = original
            for key, value in kept.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].finalized)  # engineering
        self.assertTrue(results[1].finalized)  # verification
        self.assertEqual(results[1].verdict, "pass")
        self.assertNotIn(probe_module, sys.modules,
            "production verification imported the evaluator module into the worker parent - the runner_bridge leak is not closed")
        with self.session_factory() as session:
            evaluation = session.execute(select(api_models.EvaluationRow)).scalars().one()
            self.assertEqual(evaluation.verdict, "pass")
            self.assertEqual(
                evaluation.result["import_time_db_url"], "<absent>",
                "evaluator import-time code (child, first importer) saw the worker's DB secret",
            )
            self.assertEqual(
                evaluation.result["import_time_build_token"], "<absent>",
                "evaluator import-time code (child, first importer) saw the worker's build token",
            )
            self.assertEqual(
                evaluation.result["import_time_secret_substr_count"], 0,
                "evaluator import-time code (child, first importer) saw a secret-named environment member",
            )

    # ---- review finding #3: stored candidate is checked against its own digests --

    def test_stored_candidate_digest_mismatch_is_infrastructure_invalid_not_evaluated(self) -> None:
        """Review finding #3: load_stored_candidate() previously handed
        deserialization only the JSON blob, trusting it outright rather than
        checking it against CandidateRow's own authoritative tree_digest/
        manifest_digest. A stored_candidate that has diverged from the row
        that recorded it (corruption, a hand-edit, a bug elsewhere) must be
        caught before anything is built or scored under its identity."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)

        with self.session_factory() as session:
            candidate = session.execute(select(api_models.CandidateRow)).scalars().one()
            stored = dict(candidate.stored_candidate)
            stored["manifest"] = dict(stored["manifest"])
            stored["manifest"]["full_tree_hash"] = "f" * 64  # diverges from candidate.tree_digest
            self._force_candidate_corruption(session, candidate.id, stored)

        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
        result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)

        self.assertEqual(result.execution_validity, "infrastructure_invalid")
        self.assertIsNone(result.verdict)
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "infrastructure_invalid")
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(evaluations, [])  # never scored a candidate that failed identity verification

    def test_corrupted_reference_id_is_infrastructure_invalid_not_a_candidate_contract_violation(self) -> None:
        """Review finding #3 (second pass): the manifest-digest check covers
        `stored.manifest`, but NOT `file_references` - corrupting a
        reference's id (or its blob digest/length/scope) previously still
        passed that check, and reconstruction's resulting ArtifactError was
        classified as a candidate CONTRACT_VIOLATION, not infrastructure
        corruption. Reproduced directly: corrupt one file's reference id to
        a UUID that was never stored, leaving the manifest (and its digest)
        completely untouched. This must be infrastructure_invalid, since the
        candidate itself was already accepted as contract-compliant when it
        was collected - what is broken here is the STORED REFERENCE, not the
        candidate's content."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)

        with self.session_factory() as session:
            candidate = session.execute(select(api_models.CandidateRow)).scalars().one()
            stored = dict(candidate.stored_candidate)
            self.assertTrue(stored["file_references"], "expected at least one changed file reference to corrupt")
            stored["file_references"] = [dict(entry) for entry in stored["file_references"]]
            stored["file_references"][0] = dict(stored["file_references"][0])
            stored["file_references"][0]["reference"] = dict(stored["file_references"][0]["reference"])
            stored["file_references"][0]["reference"]["id"] = str(uuid.uuid4())  # never actually stored
            self._force_candidate_corruption(session, candidate.id, stored)

        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
        result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)

        self.assertEqual(result.execution_validity, "infrastructure_invalid")
        self.assertIsNone(result.verdict)
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "infrastructure_invalid")
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(evaluations, [])

    # ---- review finding #4: legacy/malformed stored_candidate rows -------

    def test_malformed_nested_reference_is_infrastructure_invalid_not_an_attribute_error(self) -> None:
        """Review finding #4: the deserializer previously caught only
        (KeyError, TypeError, ValueError) around plain dict access and
        uuid.UUID(...) - a malformed nested reference such as `"id": []`
        raised an uncaught AttributeError ('list' object has no attribute
        'replace') instead of the typed StoredCandidateUnavailableError every
        other malformed-input path already used. Reproduced directly with
        exactly that payload shape."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)

        with self.session_factory() as session:
            candidate = session.execute(select(api_models.CandidateRow)).scalars().one()
            stored = dict(candidate.stored_candidate)
            stored["file_references"] = [dict(entry) for entry in stored["file_references"]]
            if stored["file_references"]:
                stored["file_references"][0] = dict(stored["file_references"][0])
                stored["file_references"][0]["reference"] = dict(stored["file_references"][0]["reference"])
                stored["file_references"][0]["reference"]["id"] = []  # malformed: not a UUID string at all
            self._force_candidate_corruption(session, candidate.id, stored)

        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
        # Must not raise AttributeError (or anything else uncaught) out of this call.
        result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)

        self.assertEqual(result.execution_validity, "infrastructure_invalid")
        self.assertIsNone(result.verdict)
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "infrastructure_invalid")

    def test_legacy_empty_stored_candidate_is_infrastructure_invalid_not_a_crash(self) -> None:
        """Review finding #4: a pre-ENG015-007 candidate row gets '{}' from
        the a3f0c9d17b2e migration's server_default for stored_candidate.
        Verification reading such a row back must fail safe to
        infrastructure_invalid, never crash the worker process with a bare
        KeyError out of _deserialize_stored_candidate."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)

        with self.session_factory() as session:
            candidate = session.execute(select(api_models.CandidateRow)).scalars().one()
            self._force_candidate_corruption(session, candidate.id, {})

        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
        result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)

        self.assertEqual(result.execution_validity, "infrastructure_invalid")
        self.assertIsNone(result.verdict)
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "infrastructure_invalid")
            item = session.get(api_models.WorkItemRow, verification.work_item_id)
            self.assertEqual(item.state, "failed")

    # ---- review finding #5: idempotent artifact-first writes --------------

    def test_duplicate_record_candidate_call_returns_the_same_row_not_an_integrity_error(self) -> None:
        """Review finding #5: record_candidate performed an unconditional
        insert - a retry after a commit whose acknowledgement was lost (a
        dropped connection, a restarted worker replaying the same call) hit
        uq_candidate_attempt_tree and raised instead of returning the
        already-recorded identity."""
        self._frozen_enqueued_campaign()
        candidate = repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid")
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
            first_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation,
                attempt_id=engineering.attempt_id, candidate=candidate,
            )
        self.assertIsNotNone(first_id)

        with self.session_factory() as session:
            second_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation,
                attempt_id=engineering.attempt_id, candidate=candidate,
            )
        self.assertEqual(second_id, first_id)
        with self.session_factory() as session:
            rows = session.execute(select(api_models.CandidateRow).where(api_models.CandidateRow.attempt_id == engineering.attempt_id)).scalars().all()
            self.assertEqual(len(rows), 1)  # never a duplicate row

    def test_duplicate_record_evaluation_call_is_treated_as_already_recorded(self) -> None:
        """The same idempotency guarantee for record_evaluation, keyed by
        uq_evaluation_plan_digest instead of uq_candidate_attempt_tree."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
            candidate_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
            self.assertTrue(repository.advance_to_verification(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation, attempt_id=engineering.attempt_id,
            ))
        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
            evaluation = repository.EvaluationOutcome(
                evaluator_id=self._any_evaluator_id(session), fixture_id=self._any_fixture_id(session),
                schedule_digest="s" * 64, verdict="pass", result={"pass": True},
            )
            first = repository.record_evaluation(
                session, work_item_id=verification.work_item_id, worker_id="w1", generation=verification.generation,
                candidate_id=candidate_id, evaluation=evaluation,
            )
        self.assertTrue(first)

        with self.session_factory() as session:
            second = repository.record_evaluation(
                session, work_item_id=verification.work_item_id, worker_id="w1", generation=verification.generation,
                candidate_id=candidate_id, evaluation=evaluation,
            )
        self.assertTrue(second)
        with self.session_factory() as session:
            rows = session.execute(select(api_models.EvaluationRow).where(api_models.EvaluationRow.candidate_id == candidate_id)).scalars().all()
            self.assertEqual(len(rows), 1)  # never a duplicate row
        # The authoritative verdict/result always come from the FIRST persisted
        # row, whether this call wrote it or a matching retry just replayed it.
        self.assertEqual(second.verdict, "pass")
        self.assertEqual(second.newly_recorded, False)

    def test_conflicting_evaluation_retry_raises_and_keeps_the_persisted_verdict(self) -> None:
        """Review finding #3 (fifth review): on IntegrityError,
        record_evaluation previously returned the stored row WITHOUT comparing
        verdict/result against the retry payload - a second "fail" result for
        an identity already recorded as "pass" was silently treated as a
        normal idempotent replay, hiding evaluator nondeterminism or an
        integrity anomaly. record_candidate raises CandidateConflictError for
        its equivalent case; record_evaluation now raises
        EvaluationConflictError the same way. The FIRST persisted evaluation
        stays authoritative (the first valid scored attempt is final, spec
        section 16) - the row is never overwritten."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
            candidate_id = repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
            self.assertTrue(repository.advance_to_verification(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation, attempt_id=engineering.attempt_id,
            ))
        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
            evaluator_id = self._any_evaluator_id(session)
            fixture_id = self._any_fixture_id(session)
            first = repository.record_evaluation(
                session, work_item_id=verification.work_item_id, worker_id="w1", generation=verification.generation,
                candidate_id=candidate_id,
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=evaluator_id, fixture_id=fixture_id, schedule_digest="s" * 64, verdict="pass", result={"pass": True},
                ),
            )
        self.assertEqual(first.verdict, "pass")
        self.assertTrue(first.newly_recorded)

        with self.session_factory() as session:
            with self.assertRaises(repository.EvaluationConflictError) as conflict_context:
                repository.record_evaluation(
                    session, work_item_id=verification.work_item_id, worker_id="w1", generation=verification.generation,
                    candidate_id=candidate_id,
                    evaluation=repository.EvaluationOutcome(
                        evaluator_id=evaluator_id, fixture_id=fixture_id, schedule_digest="s" * 64, verdict="fail", result={"pass": False},
                    ),
                )
        conflict = conflict_context.exception
        self.assertEqual(conflict.persisted_verdict, "pass")
        self.assertEqual(conflict.reported_verdict, "fail")
        with self.session_factory() as session:
            rows = session.execute(select(api_models.EvaluationRow).where(api_models.EvaluationRow.candidate_id == candidate_id)).scalars().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].verdict, "pass")  # never overwritten by the conflicting retry
            self.assertEqual(rows[0].result, {"pass": True})

        # An IDENTICAL retry remains a genuine idempotent replay: same verdict,
        # same result, no exception.
        with self.session_factory() as session:
            replay = repository.record_evaluation(
                session, work_item_id=verification.work_item_id, worker_id="w1", generation=verification.generation,
                candidate_id=candidate_id,
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=evaluator_id, fixture_id=fixture_id, schedule_digest="s" * 64, verdict="pass", result={"pass": True},
                ),
            )
        self.assertEqual(replay.verdict, "pass")
        self.assertFalse(replay.newly_recorded)

    def test_divergent_evaluation_retry_is_audited_and_reported_infrastructure_invalid(self) -> None:
        """Review finding #3, worker path: a verification execution that
        recomputes a DIFFERENT verdict for an already-recorded evaluation
        identity (the worker-process replay after an ambiguous commit - the
        exact scenario record_evaluation's IntegrityError path exists for)
        is surfaced, not hidden: the divergence lands in a durable audit_event
        row, execute_leased_work reports infrastructure_invalid, and the FIRST
        persisted evaluation stays the authoritative score with the work item
        already finalized from it."""
        from aieb_api.worker import runner_bridge

        failing_module = "tests.fixtures.worker.failing_evaluator"
        original = runner_bridge.TASK_RUNTIMES[self.TASK_SLUG]
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)
        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w2", work_type="verification")

        # Simulate the ambiguous-commit window record_evaluation's replay path
        # exists for: the worker's record_evaluation commit landed ("pass") but
        # it crashed before finalizing, so the lease is still live and a replay
        # of the same leased item is fenced IN (not stale). The identity MUST
        # be built exactly the way execute_leased_verification builds it
        # (task-row evaluator, ensure_fixture_row fixture, manifest digest) or
        # the replay would be a DIFFERENT identity and never conflict.
        from aieb_api.worker.runner_bridge import ensure_fixture_row, task_row_evaluator_id

        with self.session_factory() as session:
            loaded = repository.load_stored_candidate(session, verification.attempt_id)
            trial = session.get(api_models.TrialRow, verification.trial_id)
            task_row = session.get(api_models.TaskRevisionRow, trial.task_revision_id)
            repository.record_evaluation(
                session, work_item_id=verification.work_item_id, worker_id="w2", generation=verification.generation,
                candidate_id=loaded.candidate_id,
                evaluation=repository.EvaluationOutcome(
                    evaluator_id=task_row_evaluator_id(session, task_row.slug, task_row.version),
                    fixture_id=ensure_fixture_row(session, task_row.slug),
                    schedule_digest=loaded.manifest_digest, verdict="pass", result={"pass": True},
                ),
            )

        # The same worker replays its last action - but this time the evaluator
        # behaves differently and recomputes "fail" under the IDENTICAL
        # evaluation identity.
        runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = (original[0], failing_module, original[2])
        try:
            second = execute_leased_work(self.session_factory, verification, worker_id="w2", work_root=self.work_root)
        finally:
            runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = original

        self.assertEqual(second.execution_validity, "infrastructure_invalid")
        self.assertIsNone(second.verdict)
        with self.session_factory() as session:
            audits = session.execute(
                select(api_models.AuditEventRow).where(api_models.AuditEventRow.action == "evaluation_conflict")
            ).scalars().all()
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0].evidence["persisted_verdict"], "pass")
            self.assertEqual(audits[0].evidence["reported_verdict"], "fail")
            # The first persisted evaluation remains the authoritative score;
            # the conflicting retry never overwrote it and never scored.
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(len(evaluations), 1)
            self.assertEqual(evaluations[0].verdict, "pass")
            item = session.get(api_models.WorkItemRow, verification.work_item_id)
            self.assertEqual(item.state, "failed")
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "infrastructure_invalid")

    def test_worker_artifact_size_cap_is_enforced_at_store_and_database(self) -> None:
        """Review finding #2: candidate bytes previously accumulated in the
        control-plane database with no size bound. The same 52 MiB cap the
        submission policy enforces is now checked at the store boundary AND
        by a database CHECK constraint, so an oversized artifact cannot be
        written through either path."""
        store = PostgresArtifactStore(self.session_factory)
        oversized = b"x" * (52_428_800 + 1)
        with self.assertRaises(Exception):
            store.put_bytes(oversized)
        with self.session_factory() as session:
            session.add(
                api_models.WorkerArtifactBlobRow(
                    sha256="f" * 64, byte_length=len(oversized), data=oversized,
                    retention_class="staging", staged_until=None,
                )
            )
            with self.assertRaises(Exception):
                session.commit()  # ck_worker_artifact_blob_max_bytes

    def test_staging_blobs_expire_after_24h_but_committed_evidence_never_purged(self) -> None:
        """Review finding #2 (then #4, a further review): unreferenced staging
        objects expire after 24 hours by default and committed evidence is
        never deleted by that cleanup job. Proven end to end: a real
        engineering phase collects a candidate (its blobs are committed via
        attach_candidate_references inside the fenced lease), and a REAL
        orphan is created the same way collect_candidate actually creates one
        - a blob AND its reference together, via the real
        PostgresArtifactStore.put_bytes/create_reference calls, never
        claimed by any candidate - aged past its own staged_until.

        Review finding #4: a prior version of this test synthesized an
        orphan as a bare blob row with NO reference at all, which does not
        reproduce the real collection path (put_bytes + create_reference
        always happen together) and masked the actual bug - the purge job
        treated ANY reference (even an unclaimed, candidate_id=NULL one) as
        protection, so a real orphan (which always has such a reference) was
        never purged in practice. The fix must delete the orphaned reference
        along with its blob, not merely skip blobs that happen to have none."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)

        with self.session_factory() as session:
            candidate = session.execute(select(api_models.CandidateRow)).scalars().one()
            references = session.execute(select(api_models.WorkerArtifactReferenceRow)).scalars().all()
            self.assertTrue(references)
            self.assertTrue(all(ref.candidate_id == candidate.id for ref in references))
            evidence_blobs = {
                ref.blob_sha256: session.get(api_models.WorkerArtifactBlobRow, ref.blob_sha256)
                for ref in references
            }
            self.assertTrue(evidence_blobs)
            self.assertTrue(all(blob.retention_class == "evidence" and blob.staged_until is None for blob in evidence_blobs.values()))

        # A REAL orphan: collected via the store's own put_bytes/create_reference
        # (exactly what aieb_runner.artifacts.collect_candidate calls), never
        # claimed by attach_candidate_references, aged past its own expiry.
        store = PostgresArtifactStore(self.session_factory)
        orphan_blob = store.put_bytes(b"never claimed")
        orphan_reference = store.create_reference(orphan_blob, access_scope="dead-attempt-scope")
        with self.session_factory() as session:
            row = session.get(api_models.WorkerArtifactBlobRow, orphan_blob.sha256)
            row.staged_until = datetime.now(timezone.utc) - timedelta(hours=1)
            session.commit()

        summary = reconcile_once(self.session_factory)
        self.assertEqual(summary.worker_artifacts_purged, 1)
        with self.session_factory() as session:
            self.assertIsNone(session.get(api_models.WorkerArtifactBlobRow, orphan_blob.sha256))  # orphan blob gone
            self.assertIsNone(session.get(api_models.WorkerArtifactReferenceRow, orphan_reference.id))  # its orphaned reference gone too
            for sha256 in evidence_blobs:
                blob = session.get(api_models.WorkerArtifactBlobRow, sha256)
                self.assertIsNotNone(blob)  # committed evidence never purged
                self.assertEqual(blob.retention_class, "evidence")

    def test_record_candidate_raises_on_a_genuine_content_conflict(self) -> None:
        """Review finding #2: record_candidate previously returned an
        existing row's id based only on (attempt_id, tree_digest), without
        comparing manifest_digest, validation_status, or stored_candidate -
        a retry with genuinely DIFFERENT content under the same identity was
        silently accepted as if it had succeeded. Fixed to raise
        CandidateConflictError instead."""
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
            repository.record_candidate(
                session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation,
                attempt_id=engineering.attempt_id,
                candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="m" * 64, validation_status="valid"),
            )
        with self.session_factory() as session:
            with self.assertRaises(repository.CandidateConflictError):
                repository.record_candidate(
                    session, work_item_id=engineering.work_item_id, worker_id="w1", generation=engineering.generation,
                    attempt_id=engineering.attempt_id,
                    candidate=repository.CandidateOutcome(tree_digest="t" * 64, manifest_digest="DIFFERENT" + "m" * 55, validation_status="valid"),
                )
        with self.session_factory() as session:
            rows = session.execute(select(api_models.CandidateRow).where(api_models.CandidateRow.attempt_id == engineering.attempt_id)).scalars().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].manifest_digest, "m" * 64)  # the original content, never overwritten

    # ---- 7. duplicate completion ----------------------------------------

    def test_duplicate_finalize_call_is_a_no_op_not_a_double_score(self) -> None:
        self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)

        with self.session_factory() as session:
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
        verification_result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)
        self.assertTrue(verification_result.finalized)

        # Duplicate completion (ENG015-007's own required scenario): a second
        # finalize call on the SAME verification work item/generation - the
        # actual call that produces a verdict - must be a no-op.
        with self.session_factory() as session:
            duplicate = repository.finalize(
                session, work_item_id=verification.work_item_id, worker_id="w1", generation=verification.generation,
                attempt_id=verification.attempt_id, terminal_status="pass", done=True,
            )
        self.assertFalse(duplicate)  # already 'done'; WHERE state='leased' excludes it

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

    def test_verification_cancellation_after_handoff_is_not_scored(self) -> None:
        """Review finding #1: cancel_event was dropped entirely on the
        verification dispatch path (execute_leased_work only ever passed it
        to execute_leased_engineering), and run_verification() had no
        cancellation mechanism at all - a verification item claimed after its
        campaign was already cancelled could still run the evaluator and
        finalize a real score. This mirrors the existing already-cancelling
        engineering test, but for the verification phase specifically: the
        engineering phase completes normally and hands off, the campaign is
        THEN cancelled, and only the subsequent verification dispatch must
        observe it and refuse to score."""
        campaign_id = self._frozen_enqueued_campaign()
        with self.session_factory() as session:
            engineering = repository.claim_work_item(session, worker_id="w1")
        engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
        self.assertTrue(engineering_result.finalized)

        with self.session_factory() as session:
            self.assertTrue(repository.cancel_campaign(session, campaign_id))
            verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
        self.assertIsNotNone(verification)

        # Mirrors run_worker's own pre-dispatch check (loop.py): cancellation
        # is observed before execute_leased_work is even called.
        cancel_event = threading.Event()
        cancel_event.set()
        result = execute_leased_work(
            self.session_factory, verification, worker_id="w1", work_root=self.work_root, cancel_event=cancel_event,
        )

        self.assertEqual(result.execution_validity, "cancelled")
        self.assertIsNone(result.verdict)
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "cancelled")
            item = session.get(api_models.WorkItemRow, verification.work_item_id)
            self.assertEqual(item.state, "failed")
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(evaluations, [])  # never scored once cancelled

    def test_verification_cancellation_arriving_mid_verify_interrupts_the_attempt(self) -> None:
        """Review finding #1 (second pass): the prior cancellation test only
        ever passed an ALREADY-SET cancel_event before dispatch - it never
        proved cancellation arriving genuinely DURING a running VERIFY call
        is interrupted, nor exercised the new background cancellation-poll
        thread execute_leased_verification starts on its own. This uses a
        deliberately slow (20-second) evaluator and cancels the campaign
        1.5 seconds after verification starts - no cancel_event is passed in
        at all, so the only thing that can detect this is the background
        poll thread execute_leased_verification starts internally."""
        from aieb_api.worker import runner_bridge

        slow_module = "tests.fixtures.worker.slow_evaluator"
        original = runner_bridge.TASK_RUNTIMES[self.TASK_SLUG]
        runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = (original[0], slow_module, original[2])
        try:
            campaign_id = self._frozen_enqueued_campaign()
            with self.session_factory() as session:
                engineering = repository.claim_work_item(session, worker_id="w1")
            engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
            self.assertTrue(engineering_result.finalized)

            with self.session_factory() as session:
                verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")

            def cancel_soon() -> None:
                time.sleep(1.5)
                with self.session_factory() as session:
                    repository.cancel_campaign(session, campaign_id)

            threading.Thread(target=cancel_soon, daemon=True).start()
            start = time.monotonic()
            result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root, lease_seconds=3)
            elapsed = time.monotonic() - start
        finally:
            runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = original

        self.assertEqual(result.execution_validity, "cancelled")
        self.assertLess(elapsed, 10)  # interrupted well before the 20-second evaluator sleep would finish
        with self.session_factory() as session:
            attempt = session.get(api_models.AttemptRow, verification.attempt_id)
            self.assertEqual(attempt.terminal_status, "cancelled")
            evaluations = session.execute(select(api_models.EvaluationRow)).scalars().all()
            self.assertEqual(evaluations, [])  # never scored once cancelled mid-VERIFY

    # ---- 9. ENG-020 worker draining (distinct from campaign cancellation) --

    def test_drain_signal_sets_the_stop_event(self) -> None:
        """install_drain_handlers wires SIGTERM/SIGINT to stop_event.set(). Uses
        signal.raise_signal (in-process) rather than os.kill on a child, since real
        cross-process SIGTERM delivery is not portable to Windows - this tests the actual
        Python-level wiring, which is the part that was genuinely missing."""
        stop_event = threading.Event()
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            install_drain_handlers(stop_event)
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(stop_event.is_set())
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def test_drain_finishes_the_in_flight_item_and_never_claims_the_next_one(self) -> None:
        """The assertion is lease integrity, not just 'the loop exited': the in-flight work
        item that was already claimed and executed before drain was requested must be fully
        finalized (no orphaned lease for the reconciler to have to recover), and once
        stop_event is set, a second still-ready item must never be claimed at all - drain
        stops claiming, it does not abandon in-flight work. Deterministic (no thread-timing
        race): stop_event is set only AFTER the first item has already finished processing,
        exactly mirroring what `run_worker`'s own pre-claim check (loop.py) guarantees."""
        self._frozen_enqueued_campaign(repetitions=2)
        stop_event = threading.Event()

        processed_before_drain = run_worker(
            self.session_factory, worker_id="drain-worker", work_root=self.work_root,
            max_iterations=1, stop_event=stop_event,
        )
        self.assertEqual(processed_before_drain, 1)
        with self.session_factory() as session:
            states_before = sorted(item.state for item in session.execute(select(api_models.WorkItemRow)).scalars().all())
        # One engineering item finalized ('done') and, since it produced a candidate, its
        # follow-on verification work item was enqueued ('ready') - alongside the second
        # trial's still-untouched engineering item ('ready'). No lease is outstanding.
        self.assertEqual(states_before, ["done", "ready", "ready"])

        stop_event.set()
        processed_after_drain = run_worker(
            self.session_factory, worker_id="drain-worker", work_root=self.work_root,
            max_iterations=None, stop_event=stop_event,
        )
        self.assertEqual(processed_after_drain, 0)  # stop_event checked before claiming - nothing new claimed

        with self.session_factory() as session:
            states_after = sorted(item.state for item in session.execute(select(api_models.WorkItemRow)).scalars().all())
        self.assertNotIn("leased", states_after)  # no lease left dangling by the drain itself
        self.assertEqual(states_after, states_before)  # unchanged: nothing further was ever claimed

        # A reconciler pass finds nothing to recover - proving there is no orphaned lease,
        # not merely that the test didn't look for one.
        summary = reconcile_once(self.session_factory, self.work_root)
        self.assertEqual(summary.replaced, 0)
        self.assertEqual(summary.resumed, 0)
        self.assertEqual(summary.orphaned_attempt_ids, ())

    # ---- 10. ENG-020 auto-pause and the global kill switch: two DISTINCT mechanisms ---

    def test_auto_pause_fires_after_three_consecutive_infrastructure_failures(self) -> None:
        """Per-campaign, scoped to this campaign's own outcomes - not the global kill
        switch. Uses the same deterministic raising-verifier monkeypatch as
        test_verifier_outage_is_infrastructure_invalid_not_a_scored_fail: engineering
        succeeds normally for each of three trials, but verification genuinely raises and
        is finalized as 'infrastructure_invalid' every time - a real, reproducible
        infrastructure failure, not a guess about which fixture path happens to fail.
        Claims by explicit work_type (not run_worker's own queue-wide claim order, which is
        driven by a random UUID sort and cannot be relied on to pair one trial's engineering
        claim with its own verification claim deterministically) so this test drives exactly
        engineering-then-verification per trial, matching what auto-pause is scoped to
        (verification/regrade outcomes only - see loop.py)."""
        from aieb_api.worker import runner_bridge

        raising_module = "tests.fixtures.worker.raising_evaluator"
        original = runner_bridge.TASK_RUNTIMES[self.TASK_SLUG]
        runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = (original[0], raising_module, original[2])
        try:
            campaign_id = self._frozen_enqueued_campaign(repetitions=3)
            for expected_streak in (1, 2):
                with self.session_factory() as session:
                    engineering = repository.claim_work_item(session, worker_id="w1", work_type="engineering")
                engineering_result = execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
                self.assertTrue(engineering_result.finalized)  # engineering itself succeeds

                with self.session_factory() as session:
                    verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
                verification_result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)
                self.assertEqual(verification_result.execution_validity, "infrastructure_invalid")

                with self.session_factory() as session:
                    paused = repository.record_infrastructure_outcome(session, campaign_id, verification_result.execution_validity)
                self.assertFalse(paused)
                with self.session_factory() as session:
                    campaign = session.get(api_models.CampaignRow, campaign_id)
                    self.assertEqual(campaign.consecutive_infrastructure_failures, expected_streak)
                    self.assertEqual(campaign.state, "running")

            # Third consecutive infrastructure failure: crosses the threshold.
            with self.session_factory() as session:
                engineering = repository.claim_work_item(session, worker_id="w1", work_type="engineering")
            execute_leased_work(self.session_factory, engineering, worker_id="w1", work_root=self.work_root)
            with self.session_factory() as session:
                verification = repository.claim_work_item(session, worker_id="w1", work_type="verification")
            verification_result = execute_leased_work(self.session_factory, verification, worker_id="w1", work_root=self.work_root)
            with self.session_factory() as session:
                paused = repository.record_infrastructure_outcome(session, campaign_id, verification_result.execution_validity)
            self.assertTrue(paused)
            with self.session_factory() as session:
                campaign = session.get(api_models.CampaignRow, campaign_id)
                self.assertEqual(campaign.state, "paused")
                self.assertTrue(campaign.auto_paused)
                self.assertIn("consecutive infrastructure failures", campaign.auto_pause_reason)
        finally:
            runner_bridge.TASK_RUNTIMES[self.TASK_SLUG] = original

    def test_run_worker_only_scores_auto_pause_on_verification_or_regrade_outcomes(self) -> None:
        """A completely healthy engineering phase (advances to verification) must never
        count toward the infrastructure-failure streak, even though its own ExecutionResult
        carries the same 'infrastructure_invalid' execution_validity value the underlying
        runner reports for any phase that does not itself produce a scored verdict - loop.py
        must gate on `leased.work_type`, not on `result.execution_validity` alone."""
        campaign_id = self._frozen_enqueued_campaign(repetitions=1)
        processed = run_worker(self.session_factory, worker_id="w1", work_root=self.work_root, max_iterations=1)
        self.assertEqual(processed, 1)
        with self.session_factory() as session:
            campaign = session.get(api_models.CampaignRow, campaign_id)
            # A lone successful engineering phase must leave the streak at zero.
            self.assertEqual(campaign.consecutive_infrastructure_failures, 0)
            self.assertEqual(campaign.state, "running")

    def test_task_failures_alone_never_auto_pause_and_reset_the_counter(self) -> None:
        """Negative test (explicitly required): a scored candidate outcome is NOT an
        infrastructure failure, however many occur in a row, and must never trigger
        auto-pause - exercised directly at the exact boundary `record_infrastructure_outcome`
        enforces, independent of which real fixture happens to produce which outcome."""
        campaign_id = self._frozen_enqueued_campaign(repetitions=1)
        with self.session_factory() as session:
            for verdict_validity in ("valid", "valid", "valid", "valid", "valid"):
                paused = repository.record_infrastructure_outcome(session, campaign_id, verdict_validity)
                self.assertFalse(paused)
            campaign = session.get(api_models.CampaignRow, campaign_id)
            self.assertEqual(campaign.consecutive_infrastructure_failures, 0)
            self.assertEqual(campaign.state, "running")
            self.assertFalse(campaign.auto_paused)

    def test_an_infrastructure_streak_is_reset_by_one_non_infrastructure_outcome(self) -> None:
        campaign_id = self._frozen_enqueued_campaign(repetitions=1)
        with self.session_factory() as session:
            repository.record_infrastructure_outcome(session, campaign_id, "infrastructure_invalid")
            repository.record_infrastructure_outcome(session, campaign_id, "infrastructure_invalid")
            campaign = session.get(api_models.CampaignRow, campaign_id)
            self.assertEqual(campaign.consecutive_infrastructure_failures, 2)
        with self.session_factory() as session:
            repository.record_infrastructure_outcome(session, campaign_id, "valid")
            campaign = session.get(api_models.CampaignRow, campaign_id)
            self.assertEqual(campaign.consecutive_infrastructure_failures, 0)
        with self.session_factory() as session:
            # Two more infrastructure failures after the reset must NOT reach the
            # threshold - proving the streak genuinely broke, not merely paused counting.
            repository.record_infrastructure_outcome(session, campaign_id, "infrastructure_invalid")
            paused = repository.record_infrastructure_outcome(session, campaign_id, "infrastructure_invalid")
            self.assertFalse(paused)

    def test_kill_switch_stops_all_new_dispatch_platform_wide(self) -> None:
        """The kill switch is global and independent of any one campaign's health -
        distinct from auto-pause above. Activating it must deny a claim outright even for a
        perfectly healthy, freshly-frozen campaign with ready work."""
        self._frozen_enqueued_campaign(repetitions=1)
        with self.session_factory() as session:
            self.assertFalse(repository.is_kill_switch_active(session))
            claimed = repository.claim_work_item(session, worker_id="w1")
            self.assertIsNotNone(claimed)  # sanity: dispatch works before the switch is thrown

        with self.session_factory() as session:
            requested = repository.activate_kill_switch(session, activated_by_user_id=None, reason="spend investigation")
            self.assertGreaterEqual(requested, 1)  # the still-running campaign's teardown was requested

        with self.session_factory() as session:
            self.assertTrue(repository.is_kill_switch_active(session))
            denied = repository.claim_work_item(session, worker_id="w2")
            self.assertIsNone(denied)  # nothing new dispatches while active, regardless of ready work

        with self.session_factory() as session:
            repository.deactivate_kill_switch(session)
            self.assertFalse(repository.is_kill_switch_active(session))

    def test_kill_switch_tears_down_a_paused_campaign_too(self) -> None:
        """Regression: `cancel_campaign`'s WHERE clause previously excluded 'paused', so a
        paused campaign with in-flight leased work was silently skipped by
        activate_kill_switch's "every non-terminal campaign" teardown request (confirmed
        directly: rowcount 0, no-op) - contradicting its own docstring. A paused campaign is
        exactly the case that matters most here: it already has outstanding leased work and no
        new dispatch is stopping it, so the kill switch's bounded-teardown promise must reach
        it too."""
        campaign_id = self._frozen_enqueued_campaign(repetitions=1)
        with self.session_factory() as session:
            claimed = repository.claim_work_item(session, worker_id="w1")
            self.assertIsNotNone(claimed)
            campaign = session.get(api_models.CampaignRow, campaign_id)
            campaign.state = "paused"
            session.commit()

        with self.session_factory() as session:
            requested = repository.activate_kill_switch(session, activated_by_user_id=None, reason="paused-campaign teardown")
        self.assertEqual(requested, 1)  # the paused campaign's teardown WAS requested, not skipped

        with self.session_factory() as session:
            campaign = session.get(api_models.CampaignRow, campaign_id)
            self.assertEqual(campaign.state, "cancelling")


if __name__ == "__main__":
    unittest.main()
