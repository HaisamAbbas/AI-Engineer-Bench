"""Real PostgreSQL regressions for the Prompt 14 campaign-start transaction."""
from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from tests import test_api_service as fixtures

# V2-GAP-004's worst-case reservation formula (budgets.py::_bounds) requires a
# complete aieb.budget/v2 `resolved` manifest and refuses to reserve (raises
# UnknownBudgetBounds) when `resolved` is missing/empty - these
# reservation-layer tests build bare CampaignRow rows with no real freeze
# manifest since they exercise sweeper/locking behavior, not budget math, so
# they need a minimal but valid `resolved` block attached.
_MINIMAL_RESOLVED_BUDGET = {
    "trials": [{"id": "t1"}],
    "protocol": {"max_replacements": 0},
    "budget": {
        "environment_upper_bound_usd": "0.5",
        "per_role_budget_usd": [
            {"role": "engineer", "limit_usd": "1.00"}, {"role": "dev_application", "limit_usd": "1.00"},
            {"role": "verifier_application", "limit_usd": "1.00"}, {"role": "verifier_judge", "limit_usd": "1.00"},
        ],
    },
}


@unittest.skipUnless(fixtures.DATABASE_URL, "AIEB_DATABASE_URL is required")
class CampaignStartAtomicityTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ApiServiceTests.setUpClass.__func__)
    setUp = fixtures.ApiServiceTests.setUp
    _seed_task = fixtures.ApiServiceTests._seed_task
    _seed_entrant = fixtures.ApiServiceTests._seed_entrant
    _seed_evaluator = fixtures.ApiServiceTests._seed_evaluator
    _draft_body = fixtures.ApiServiceTests._draft_body
    _registry_payload = staticmethod(fixtures.ApiServiceTests._registry_payload)
    _create_and_freeze = fixtures.ApiServiceTests._create_and_freeze
    _plan_and_approve = fixtures.ApiServiceTests._plan_and_approve
    _drive_campaign_to_terminal = fixtures.ApiServiceTests._drive_campaign_to_terminal

    def test_zero_work_cancellation_settlement_rolls_back_without_response(self) -> None:
        from aieb_api import budgets, db, models
        from aieb_api.routes import campaigns
        from sqlalchemy import select

        campaign_id = uuid.UUID(self._create_and_freeze())
        with db.session_factory()() as session:
            budgets.reserve_campaign_budget(session, session.get(models.CampaignRow, campaign_id))
            session.commit()
        headers = fixtures._auth_header(("operator",)) | {"Idempotency-Key": "zero-work-cancel"}
        with patch.object(campaigns, "_state_response", side_effect=RuntimeError("response failed")):
            with self.assertRaisesRegex(RuntimeError, "response failed"):
                self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=headers)
        with db.session_factory()() as session:
            self.assertEqual(session.get(models.CampaignRow, campaign_id).state, "frozen")
            reservation = session.scalars(select(models.BudgetReservationRow)).one()
            self.assertEqual(reservation.status, "active")
            self.assertIsNone(reservation.released_at)
        response = self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["campaign"]["state"], "cancelled")
        self.assertEqual(response.json()["reservation"]["status"], "released")
        self.assertEqual(self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=headers).json(), response.json())

    def test_frozen_zero_work_cancel_without_reservation(self) -> None:
        campaign_id = self._create_and_freeze()
        headers = fixtures._auth_header(("operator",)) | {"Idempotency-Key": "empty-cancel"}
        response = self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["campaign"]["state"], "cancelled")
        self.assertIsNone(response.json()["reservation"])
        self.assertEqual(self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=headers).json(), response.json())

    def test_paused_completion_waits_for_work_and_consumes_reservation(self) -> None:
        from aieb_api import db, models
        from aieb_api.worker import repository
        from sqlalchemy import select

        campaign_id = uuid.UUID(self._create_and_freeze())
        self._plan_and_approve(str(campaign_id))
        headers = fixtures._auth_header(("operator",))
        for action in ("start", "pause"):
            response = self.client.post(f"/v1/campaigns/{campaign_id}/{action}",
                headers=headers | {"Idempotency-Key": f"paused-{action}"})
            self.assertEqual(response.status_code, 200, response.text)
        with db.session_factory()() as session:
            self.assertFalse(repository.maybe_complete_campaign(session, campaign_id))
            self.assertEqual(repository.finalize_stalled_campaigns(session), [])
            self.assertIsNone(repository.claim_work_item(session, worker_id="paused-observer"))
            self.assertEqual(session.scalars(select(models.BudgetReservationRow)).one().status, "active")
        self._drive_campaign_to_terminal(campaign_id, resolve=True)
        with db.session_factory()() as session:
            self.assertEqual(repository.finalize_stalled_campaigns(session), [campaign_id])
            self.assertEqual(session.get(models.CampaignRow, campaign_id).state, "completed")
            self.assertEqual(session.scalars(select(models.BudgetReservationRow)).one().status, "consumed")
            self.assertFalse(repository.maybe_complete_campaign(session, campaign_id))
            self.assertEqual(repository.finalize_stalled_campaigns(session), [])

    def test_concurrent_bounded_sweepers_claim_disjoint_campaigns(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from aieb_api import budgets, db, models
        from aieb_api.worker import repository
        from sqlalchemy import select, text

        with db.session_factory()() as session:
            campaigns = [models.CampaignRow(name=f"sweep-{i}", state="cancelling", draft={}, resolved=_MINIMAL_RESOLVED_BUDGET) for i in range(5)]
            session.add_all(campaigns)
            session.flush()
            for campaign in campaigns:
                budgets.reserve_campaign_budget(session, campaign)
            ids = {campaign.id for campaign in campaigns}
            session.commit()
        barrier = Barrier(2)
        original = repository._finalize_terminal_campaign

        def finalize(session, campaign):
            if not session.info.get("sweep_started"):
                session.info["sweep_started"] = True
                barrier.wait(timeout=5)
            original(session, campaign)

        def sweep():
            with db.session_factory()() as session:
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                return repository.finalize_stalled_campaigns(session, limit=2)

        with patch.object(repository, "_finalize_terminal_campaign", side_effect=finalize):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(sweep) for _ in range(2)]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual([len(result) for result in results], [2, 2])
        self.assertTrue(set(results[0]).isdisjoint(results[1]))
        with db.session_factory()() as session:
            remaining = repository.finalize_stalled_campaigns(session, limit=2)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(set(results[0] + results[1] + remaining), ids)
            self.assertEqual(repository.finalize_stalled_campaigns(session), [])
            reservations = session.scalars(select(models.BudgetReservationRow)).all()
            self.assertEqual(len(reservations), 5)
            self.assertTrue(all(row.status == "released" and row.released_at is not None for row in reservations))

    def test_sweep_skips_locked_campaign_and_refreshes_cached_state(self) -> None:
        from aieb_api import budgets, db, models
        from aieb_api.worker import repository
        from sqlalchemy import select, text

        with db.session_factory()() as session:
            campaign = models.CampaignRow(name="locked-sweep", state="running", draft={}, resolved=_MINIMAL_RESOLVED_BUDGET)
            session.add(campaign)
            session.flush()
            budgets.reserve_campaign_budget(session, campaign)
            campaign_id = campaign.id
            session.commit()
        with db.session_factory()() as sweeper:
            cached = sweeper.get(models.CampaignRow, campaign_id)
            with db.session_factory()() as writer:
                row = writer.scalars(select(models.CampaignRow).where(models.CampaignRow.id == campaign_id)
                    .with_for_update()).one()
                row.state = "cancelling"
                writer.flush()
                sweeper.execute(text("SET LOCAL statement_timeout = '2s'"))
                self.assertEqual(repository.finalize_stalled_campaigns(sweeper, limit=1), [])
                writer.commit()
            self.assertEqual(cached.state, "running")
            self.assertEqual(repository.finalize_stalled_campaigns(sweeper, limit=1), [campaign_id])
            self.assertEqual(cached.state, "cancelled")
            self.assertEqual(sweeper.scalars(select(models.BudgetReservationRow)).one().status, "released")

    def test_sweep_rejects_nonpositive_bounds(self) -> None:
        from aieb_api import db
        from aieb_api.worker import repository

        with db.session_factory()() as session:
            for limit in (0, -1):
                with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "positive"):
                    repository.finalize_stalled_campaigns(session, limit=limit)

    def test_stale_concurrent_completion_has_one_winner(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from aieb_api import budgets, db, models
        from aieb_api.worker import repository
        from sqlalchemy import select, text

        with db.session_factory()() as session:
            campaign = models.CampaignRow(name="completion-race", state="cancelling", draft={}, resolved=_MINIMAL_RESOLVED_BUDGET)
            session.add(campaign)
            session.flush()
            budgets.reserve_campaign_budget(session, campaign)
            campaign_id = campaign.id
            session.commit()
        barrier = Barrier(2)

        def complete():
            with db.session_factory()() as session:
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                cached = session.get(models.CampaignRow, campaign_id)
                self.assertEqual(cached.state, "cancelling")
                barrier.wait(timeout=5)
                return repository.maybe_complete_cancellation(session, campaign_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(complete) for _ in range(2)]
            self.assertEqual(sorted(future.result(timeout=10) for future in futures), [False, True])
        with db.session_factory()() as session:
            reservation = session.scalars(select(models.BudgetReservationRow)).one()
            released_at = reservation.released_at
            budgets.set_reservation_status(session, campaign_id, "released")
            budgets.set_reservation_status(session, campaign_id, "consumed")
            session.commit()
            session.refresh(reservation)
            self.assertEqual(reservation.status, "released")
            self.assertEqual(reservation.released_at, released_at)

    def test_consumed_reservation_cannot_be_released(self) -> None:
        from aieb_api import budgets, db, models

        campaign_id = uuid.UUID(self._create_and_freeze())
        with db.session_factory()() as session:
            reservation = budgets.reserve_campaign_budget(session, session.get(models.CampaignRow, campaign_id))
            budgets.set_reservation_status(session, campaign_id, "consumed")
            session.commit()
            budgets.set_reservation_status(session, campaign_id, "released")
            session.commit()
            session.refresh(reservation)
            self.assertEqual(reservation.status, "consumed")
            self.assertIsNone(reservation.released_at)

    def test_enqueue_does_not_commit_or_expose_frozen_work(self) -> None:
        from aieb_api import db, models
        from aieb_api.worker import repository
        from sqlalchemy import func, select

        campaign_id = uuid.UUID(self._create_and_freeze())
        with db.session_factory()() as writer:
            self.assertEqual(repository.enqueue_frozen_campaign(writer, campaign_id), 1)
            with db.session_factory()() as observer:
                self.assertEqual(observer.scalar(select(func.count()).select_from(models.TrialRow)), 0)
                self.assertIsNone(repository.claim_work_item(observer, worker_id="observer"))
            writer.rollback()
        with db.session_factory()() as observer:
            self.assertEqual(observer.scalar(select(func.count()).select_from(models.TrialRow)), 0)

    def test_committed_frozen_work_is_not_claimable(self) -> None:
        from aieb_api import db
        from aieb_api.worker import repository

        campaign_id = uuid.UUID(self._create_and_freeze())
        with db.session_factory()() as session:
            repository.enqueue_frozen_campaign(session, campaign_id)
            session.commit()
        with db.session_factory()() as observer:
            self.assertIsNone(repository.claim_work_item(observer, worker_id="observer"))

    def test_concurrent_start_replays_one_committed_transaction(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from aieb_api import db, models
        from sqlalchemy import func, select

        campaign_id = self._create_and_freeze()
        self._plan_and_approve(campaign_id)
        headers = fixtures._auth_header(("operator",)) | {"Idempotency-Key": "concurrent-start"}
        barrier = Barrier(2)

        def start():
            barrier.wait(timeout=5)
            return self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=headers)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start) for _ in range(2)]
            responses = [future.result(timeout=10) for future in futures]
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(responses[0].json(), responses[1].json())
        with db.session_factory()() as session:
            for model in (models.TrialRow, models.AttemptRow, models.WorkItemRow, models.BudgetReservationRow):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), 1, model.__name__)

    def test_cancellation_releases_reservation_only_after_drain(self) -> None:
        from aieb_api import db, models
        from aieb_api.worker import repository
        from sqlalchemy import select

        campaign_id = uuid.UUID(self._create_and_freeze())
        self._plan_and_approve(str(campaign_id))
        started = self.client.post(f"/v1/campaigns/{campaign_id}/start",
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "cancel-drain-start"})
        self.assertEqual(started.status_code, 200, started.text)
        cancelled = self.client.post(f"/v1/campaigns/{campaign_id}/cancel",
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "cancel-drain"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["reservation"]["status"], "active")
        # Drain exactly as the worker loop does: claim the still-claimable item,
        # execute it with the cancellation event set, then complete cancellation.
        from aieb_api.worker.runner_bridge import execute_leased_work
        with db.session_factory()() as session:
            leased = repository.claim_work_item(session, worker_id="drain-worker")
            self.assertIsNotNone(leased)
        with db.session_factory()() as session:
            self.assertFalse(repository.maybe_complete_cancellation(session, campaign_id))
            reservation = session.scalars(select(models.BudgetReservationRow)).one()
            self.assertEqual(reservation.status, "active")
            self.assertIsNone(reservation.released_at)
        from threading import Event
        from tempfile import TemporaryDirectory
        from pathlib import Path
        cancel_event = Event()
        cancel_event.set()
        with TemporaryDirectory() as work_root:
            result = execute_leased_work(db.session_factory(), leased, worker_id="drain-worker",
                work_root=Path(work_root), cancel_event=cancel_event)
        self.assertTrue(result.finalized)
        self.assertEqual(result.execution_validity, "cancelled")
        self.assertIsNone(result.verdict)
        with db.session_factory()() as session:
            campaign = session.get(models.CampaignRow, campaign_id)
            self.assertTrue(repository.maybe_complete_cancellation(session, campaign_id))
            session.refresh(campaign)
            self.assertEqual(campaign.state, "cancelled")
            reservation = session.scalars(select(models.BudgetReservationRow)).one()
            self.assertEqual(reservation.status, "released")
            self.assertIsNotNone(reservation.released_at)
        detail = self.client.get(f"/v1/campaigns/{campaign_id}", headers=fixtures._auth_header(("operator",)))
        self.assertEqual(detail.json()["reservation"]["status"], "released")

    def test_reserve_estimate_covers_trials_and_replacements(self) -> None:
        from aieb_api import budgets

        trial = {"id": "t", "campaign_digest": "a" * 64, "task_digest": "b" * 64,
                 "entrant_digest": "c" * 64, "cohort_digest": "d" * 64, "repetition_index": 0, "order_index": 0}
        roles = [{"role": r, "limit_usd": "1.5"} for r in ("engineer", "dev_application", "verifier_application", "verifier_judge")]
        resolved = {"budget": {"per_role_budget_usd": roles, "environment_upper_bound_usd": "0"},
                    "protocol": {"max_replacements": 2}, "trials": [trial, trial]}
        self.assertEqual(budgets.reserved_amount_from_resolved(resolved), "36.0")  # 6 x 2 trials x 3 attempts
        del resolved["trials"]
        self.assertIsNone(budgets.reserved_amount_from_resolved(resolved))
        del resolved["protocol"]
        self.assertIsNone(budgets.reserved_amount_from_resolved(resolved))

    def test_indeterminate_evaluation_blocks_completion(self) -> None:
        self._assert_unresolved_completion("indeterminate", "indeterminate")

    def test_mismatched_evaluation_blocks_completion(self) -> None:
        self._assert_unresolved_completion("fail", "pass")

    def _assert_unresolved_completion(self, terminal_status: str, verdict: str) -> None:
        from aieb_api import db, models
        from aieb_api.worker import repository
        from sqlalchemy import select

        campaign_id = uuid.UUID(self._create_and_freeze())
        self._plan_and_approve(str(campaign_id))
        response = self.client.post(f"/v1/campaigns/{campaign_id}/start",
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "unresolved-start"})
        self.assertEqual(response.status_code, 200, response.text)
        self._drive_campaign_to_terminal(campaign_id, resolve=True)
        with db.session_factory()() as session:
            attempt = session.scalars(select(models.AttemptRow)).one()
            original = session.scalars(select(models.EvaluationRow)).one()
            attempt.terminal_status = terminal_status
            session.add(models.EvaluationRow(candidate_id=original.candidate_id,
                evaluator_id=original.evaluator_id, fixture_id=original.fixture_id,
                schedule_digest="8" * 64, verdict=verdict, result={"review_regression": True}))
            session.commit()
        with db.session_factory()() as session:
            self.assertTrue(repository.maybe_complete_campaign(session, campaign_id))
            self.assertEqual(session.get(models.CampaignRow, campaign_id).state, "incomplete")

    def test_preview_exposes_the_exact_ordered_frozen_matrix(self) -> None:
        self._seed_task()
        self._seed_entrant()
        create = self.client.post("/v1/campaigns", json=self._draft_body(repetitions=3),
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "preview-exact"})
        campaign_id = create.json()["id"]
        first = self.client.post(f"/v1/campaigns/{campaign_id}/preview", json=self._registry_payload(),
            headers=fixtures._auth_header(("operator",)))
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body["trial_count"], 3)
        self.assertEqual(len(body["trials"]), 3)
        self.assertEqual([trial["order_index"] for trial in body["trials"]], [0, 1, 2])
        self.assertEqual(sorted(trial["repetition_index"] for trial in body["trials"]), [0, 1, 2])
        self.assertTrue(all(trial["trial_id"] for trial in body["trials"]))
        second = self.client.post(f"/v1/campaigns/{campaign_id}/preview", json=self._registry_payload(),
            headers=fixtures._auth_header(("operator",)))
        self.assertEqual(second.json()["trials"], body["trials"])

    def test_freeze_and_preview_fail_closed_on_ambiguous_slug_revisions(self) -> None:
        from aieb_api import db, models
        from sqlalchemy import select

        from aieb_api import admission as admission_module
        from aieb_core.models import TaskRevision as _TaskRevisionContract

        self._seed_task()
        self._seed_entrant()
        with db.session_factory()() as session:
            original = session.scalars(select(models.TaskRevisionRow)).one()
            manifest = dict(original.manifest)
            manifest["version"] = "0.2.0"
            second = models.TaskRevisionRow(
                slug=original.slug, version="0.2.0", family_id=original.family_id, category=original.category,
                source_digest="9" * 64,
                manifest_digest=_TaskRevisionContract.model_validate(manifest).digest(),
                evaluator_id=original.evaluator_id,
                manifest=manifest, ticket_text=original.ticket_text,
            )
            session.add(second)
            session.flush()
            # This second pinned revision also needs its own release-eligible
            # admission record (the first revision's admission does not cover
            # it - eligibility is per revision digest), or the later pinned
            # freeze/start below fails closed with "not release-eligible"
            # exactly like the unpinned freeze above but for the wrong reason.
            admission_module.seed_fixture_admission(session, second)
            session.commit()
        create = self.client.post("/v1/campaigns", json=self._draft_body(),
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "ambiguous-draft"})
        campaign_id = create.json()["id"]
        operator = fixtures._auth_header(("operator",))
        freeze = self.client.post(f"/v1/campaigns/{campaign_id}/freeze", json=self._registry_payload(),
            headers=operator | {"Idempotency-Key": "ambiguous-freeze"})
        self.assertEqual(freeze.status_code, 409, freeze.text)
        self.assertEqual(freeze.json()["error"]["code"], "conflict")
        preview = self.client.post(f"/v1/campaigns/{campaign_id}/preview", json=self._registry_payload(), headers=operator)
        self.assertEqual(preview.status_code, 409, preview.text)
        with db.session_factory()() as session:
            self.assertEqual(session.get(models.CampaignRow, uuid.UUID(campaign_id)).state, "draft")
            self.assertEqual(session.scalars(select(models.TrialRow.id)).all(), [])
        registry = self._registry_payload()
        registry["task_versions"] = {"rag.document-freshness": "0.2.0"}
        pinned_preview = self.client.post(f"/v1/campaigns/{campaign_id}/preview", json=registry, headers=operator)
        self.assertEqual(pinned_preview.status_code, 200, pinned_preview.text)
        pinned_freeze = self.client.post(f"/v1/campaigns/{campaign_id}/freeze", json=registry,
            headers=operator | {"Idempotency-Key": "pinned-freeze"})
        self.assertEqual(pinned_freeze.status_code, 200, pinned_freeze.text)
        with db.session_factory()() as session:
            frozen = session.get(models.CampaignRow, uuid.UUID(campaign_id)).resolved
            self.assertEqual(frozen["tasks"][0]["version"], "0.2.0")
            self.assertEqual([t["trial_id"] for t in pinned_preview.json()["trials"]],
                [t["id"] for t in frozen["trials"]])
        self._plan_and_approve(campaign_id)
        started = self.client.post(f"/v1/campaigns/{campaign_id}/start",
            headers=operator | {"Idempotency-Key": "pinned-start"})
        self.assertEqual(started.status_code, 200, started.text)
        with db.session_factory()() as session:
            trial = session.scalars(select(models.TrialRow)).one()
            self.assertEqual(session.get(models.TaskRevisionRow, trial.task_revision_id).version, "0.2.0")
            self.assertEqual(len(session.scalars(select(models.TaskRevisionRow)).all()), 2)

    def test_failure_after_enqueue_rolls_back_entire_start(self) -> None:
        from aieb_api import db, models
        from aieb_api.routes import campaigns
        from sqlalchemy import func, select

        campaign_id = uuid.UUID(self._create_and_freeze())
        self._plan_and_approve(str(campaign_id))
        headers = fixtures._auth_header(("operator",)) | {"Idempotency-Key": "atomic-start"}
        with patch.object(campaigns, "_state_response", side_effect=RuntimeError("injected before response persistence")):
            with self.assertRaisesRegex(RuntimeError, "injected before response persistence"):
                self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=headers)
        with db.session_factory()() as session:
            self.assertEqual(session.get(models.CampaignRow, campaign_id).state, "approved")
            # V2-GAP-004 moved exact-matrix materialization to `plan`, not
            # `start` - TrialRow rows are durable from `_plan_and_approve`
            # above and correctly survive a rollback of a LATER `start`
            # failure; only start's own writes (attempts/work items/budget
            # reservation) must roll back to zero.
            self.assertEqual(session.scalar(select(func.count()).select_from(models.TrialRow)), 1)
            for model in (models.AttemptRow, models.WorkItemRow, models.BudgetReservationRow):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), 0, model.__name__)
        response = self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        replay = self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=headers)
        self.assertEqual(replay.json(), response.json())
