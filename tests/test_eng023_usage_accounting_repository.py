"""ENG-023 usage-accounting closure: real DB-writing repository functions
against a real test PostgreSQL instance.

Closes the review's "does not create authoritative API UsageRequestRow/
UsageReceiptRow records" finding at the `aieb_api.worker.repository` layer:
`record_usage_receipts` and `record_model_identity` are the ONLY production
code path (as of this ticket) that ever inserts a real usage row - see
docs/implementation/evidence/ENG-023/README.md for the full gap analysis.

Requires AIEB_DATABASE_URL; skipped (not faked) otherwise, matching this
project's rule against substituting sqlite/mocks for the real evaluation a
spec requirement asks for. Follows tests/test_worker_leasing.py's
session_factory/TRUNCATE fixture convention.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")

if DATABASE_URL:
    import unittest

    from sqlalchemy import select, text
    from sqlalchemy.exc import IntegrityError

    from aieb_api import db
    from aieb_api import models as api_models
    from aieb_api.worker import repository


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-023 usage-accounting repository tests are blocked")
class UsageReceiptRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.configure(DATABASE_URL)

    def setUp(self) -> None:
        engine = db.engine()
        with engine.begin() as connection:
            for table in reversed(api_models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
            connection.execute(text("INSERT INTO kill_switch (id, active) VALUES (1, false)"))
            connection.execute(text("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)"))
        self.session_factory = db.session_factory()

    def _seed_minimal_attempt(self) -> uuid.UUID:
        """The shortest real FK path to an Attempt row - mirrors
        tests/test_metrics.py::_seed_minimal_engineering_lease's minimal
        campaign/trial/attempt chain (no full cohort-freezing pipeline
        needed just to exercise usage-accounting writes)."""
        with self.session_factory() as session:
            evaluator = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
            session.add(evaluator)
            session.flush()
            task = api_models.TaskRevisionRow(
                slug="usage-accounting.task", version="0.1.0", family_id="usage-accounting-family", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator.id,
                manifest={}, revision_digest="f" * 64,
            )
            entrant = api_models.EntrantRevisionRow(
                slug="usage-accounting-agent", version="1.0.0", track="agents", config_digest="usage-accounting-agent-digest",
                capabilities=[], manifest={},
            )
            session.add_all([task, entrant])
            session.flush()
            campaign = api_models.CampaignRow(name="usage-accounting-test", state="running", draft={})
            session.add(campaign)
            session.flush()
            trial = api_models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=1, cell_digest="c" * 64,
            )
            session.add(trial)
            session.flush()
            attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="engineering", lease_generation=1)
            session.add(attempt)
            session.commit()
            return attempt.id

    # ---- record_usage_receipts: normal insert -----------------------------

    def test_normal_insert_creates_request_and_receipt_rows(self) -> None:
        attempt_id = self._seed_minimal_attempt()
        with self.session_factory() as session:
            request_ids = repository.record_usage_receipts(
                session,
                attempt_id=attempt_id,
                actor_role="engineer",
                receipts=[
                    repository.UsageReceiptInput(
                        request_id="req-1", physical_retry=0, input_tokens=42, output_tokens=8,
                    ),
                ],
            )
            self.assertEqual(len(request_ids), 1)

        with self.session_factory() as session:
            usage_request = session.execute(
                select(api_models.UsageRequestRow).where(api_models.UsageRequestRow.id == request_ids[0])
            ).scalar_one()
            self.assertEqual(usage_request.actor_role, "engineer")
            self.assertEqual(usage_request.request_id, "req-1")
            self.assertEqual(usage_request.attempt_id, attempt_id)
            receipt = session.execute(
                select(api_models.UsageReceiptRow).where(api_models.UsageReceiptRow.usage_request_id == usage_request.id)
            ).scalar_one()
            self.assertEqual(receipt.physical_retry, 0)
            self.assertEqual(receipt.input_tokens, 42)
            self.assertEqual(receipt.output_tokens, 8)

    def test_multiple_physical_retries_under_one_request_id(self) -> None:
        with self.session_factory() as session:
            request_ids = repository.record_usage_receipts(
                session,
                attempt_id=None,
                actor_role="engineer",
                receipts=[
                    repository.UsageReceiptInput(request_id="req-multi", physical_retry=0, input_tokens=1, output_tokens=1),
                    repository.UsageReceiptInput(request_id="req-multi", physical_retry=1, input_tokens=2, output_tokens=2),
                ],
            )
            self.assertEqual(len(request_ids), 1)

        with self.session_factory() as session:
            receipts = session.execute(
                select(api_models.UsageReceiptRow).where(api_models.UsageReceiptRow.usage_request_id == request_ids[0])
            ).scalars().all()
            self.assertEqual(len(receipts), 2)

    # ---- record_usage_receipts: duplicate/replay behavior -----------------

    def test_replay_with_identical_content_is_idempotent(self) -> None:
        with self.session_factory() as session:
            first = repository.record_usage_receipts(
                session,
                attempt_id=None,
                actor_role="dev_application",
                receipts=[repository.UsageReceiptInput(request_id="req-replay", physical_retry=0, input_tokens=10, output_tokens=5)],
            )
        with self.session_factory() as session:
            second = repository.record_usage_receipts(
                session,
                attempt_id=None,
                actor_role="dev_application",
                receipts=[repository.UsageReceiptInput(request_id="req-replay", physical_retry=0, input_tokens=10, output_tokens=5)],
            )
        self.assertEqual(first, second)
        with self.session_factory() as session:
            count = session.execute(
                select(api_models.UsageRequestRow).where(api_models.UsageRequestRow.request_id == "req-replay")
            ).scalars().all()
            self.assertEqual(len(count), 1)
            receipts = session.execute(
                select(api_models.UsageReceiptRow).where(api_models.UsageReceiptRow.usage_request_id == first[0])
            ).scalars().all()
            self.assertEqual(len(receipts), 1)

    def test_replay_with_different_content_conflicts(self) -> None:
        attempt_id = self._seed_minimal_attempt()
        other_attempt_id = self._seed_second_attempt()
        with self.session_factory() as session:
            repository.record_usage_receipts(
                session, attempt_id=attempt_id, actor_role="engineer",
                receipts=[repository.UsageReceiptInput(request_id="req-conflict", physical_retry=0)],
            )
        with self.session_factory() as session:
            with self.assertRaises(repository.UsageRequestConflictError):
                repository.record_usage_receipts(
                    session, attempt_id=other_attempt_id, actor_role="engineer",
                    receipts=[repository.UsageReceiptInput(request_id="req-conflict", physical_retry=0)],
                )

    def test_replay_with_different_receipt_content_conflicts(self) -> None:
        with self.session_factory() as session:
            repository.record_usage_receipts(
                session, attempt_id=None, actor_role="engineer",
                receipts=[repository.UsageReceiptInput(request_id="req-receipt-conflict", physical_retry=0, input_tokens=10)],
            )
        with self.session_factory() as session:
            with self.assertRaises(repository.UsageReceiptConflictError):
                repository.record_usage_receipts(
                    session, attempt_id=None, actor_role="engineer",
                    receipts=[repository.UsageReceiptInput(request_id="req-receipt-conflict", physical_retry=0, input_tokens=999)],
                )

    def _seed_second_attempt(self) -> uuid.UUID:
        with self.session_factory() as session:
            evaluator = api_models.EvaluatorRevisionRow(code_digest="f" * 64, contract_version="v1")
            session.add(evaluator)
            session.flush()
            task = api_models.TaskRevisionRow(
                slug="usage-accounting.task-2", version="0.1.0", family_id="usage-accounting-family-2", category="rag",
                source_digest="2" * 64, manifest_digest="e" * 64, evaluator_id=evaluator.id,
                manifest={}, revision_digest="g" * 64,
            )
            entrant = api_models.EntrantRevisionRow(
                slug="usage-accounting-agent-2", version="1.0.0", track="agents", config_digest="usage-accounting-agent-digest-2",
                capabilities=[], manifest={},
            )
            session.add_all([task, entrant])
            session.flush()
            campaign = api_models.CampaignRow(name="usage-accounting-test-2", state="running", draft={})
            session.add(campaign)
            session.flush()
            trial = api_models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=1, cell_digest="h" * 64,
            )
            session.add(trial)
            session.flush()
            attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="engineering", lease_generation=1)
            session.add(attempt)
            session.commit()
            return attempt.id

    # ---- CHECK constraint on actor_role -------------------------------------

    def test_invalid_actor_role_rejected_by_check_constraint(self) -> None:
        with self.session_factory() as session:
            with self.assertRaises(IntegrityError):
                repository.record_usage_receipts(
                    session, attempt_id=None, actor_role="not-a-real-role",
                    receipts=[repository.UsageReceiptInput(request_id="req-bad-role", physical_retry=0)],
                )


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-023 usage-accounting repository tests are blocked")
class ModelIdentityRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.configure(DATABASE_URL)

    def setUp(self) -> None:
        engine = db.engine()
        with engine.begin() as connection:
            for table in reversed(api_models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
            connection.execute(text("INSERT INTO kill_switch (id, active) VALUES (1, false)"))
            connection.execute(text("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)"))
        self.session_factory = db.session_factory()

    def test_normal_insert(self) -> None:
        with self.session_factory() as session:
            row_id = repository.record_model_identity(
                session, attempt_id=None, actor_role="engineer",
                requested_model="requested-x", reported_model="reported-y",
                settings_digest="a" * 64, coverage_label="estimated_time_limited",
            )
        with self.session_factory() as session:
            row = session.execute(
                select(api_models.AttemptModelIdentityRow).where(api_models.AttemptModelIdentityRow.id == row_id)
            ).scalar_one()
            self.assertEqual(row.requested_model, "requested-x")
            self.assertEqual(row.reported_model, "reported-y")
            self.assertEqual(row.coverage_label, "estimated_time_limited")

    def test_replay_with_identical_content_is_idempotent(self) -> None:
        attempt_id = self._seed_attempt()
        kwargs = dict(
            attempt_id=attempt_id, actor_role="engineer", requested_model="m", reported_model="m",
            settings_digest="b" * 64, coverage_label="full_match",
        )
        with self.session_factory() as session:
            first_id = repository.record_model_identity(session, **kwargs)
        with self.session_factory() as session:
            second_id = repository.record_model_identity(session, **kwargs)
        self.assertEqual(first_id, second_id)
        with self.session_factory() as session:
            rows = session.execute(
                select(api_models.AttemptModelIdentityRow).where(api_models.AttemptModelIdentityRow.attempt_id == attempt_id)
            ).scalars().all()
            self.assertEqual(len(rows), 1)

    def test_replay_with_different_content_conflicts(self) -> None:
        attempt_id = self._seed_attempt()
        with self.session_factory() as session:
            repository.record_model_identity(
                session, attempt_id=attempt_id, actor_role="engineer", requested_model="m",
                reported_model="m", settings_digest="c" * 64, coverage_label="full_match",
            )
        with self.session_factory() as session:
            with self.assertRaises(repository.ModelIdentityConflictError):
                repository.record_model_identity(
                    session, attempt_id=attempt_id, actor_role="engineer", requested_model="m",
                    reported_model="a-different-model", settings_digest="c" * 64, coverage_label="full_match",
                )

    def test_invalid_actor_role_rejected_by_check_constraint(self) -> None:
        with self.session_factory() as session:
            with self.assertRaises(IntegrityError):
                repository.record_model_identity(
                    session, attempt_id=None, actor_role="not-a-real-role",
                    requested_model="m", reported_model="m", settings_digest=None,
                    coverage_label="full_match",
                )

    def _seed_attempt(self) -> uuid.UUID:
        with self.session_factory() as session:
            evaluator = api_models.EvaluatorRevisionRow(code_digest="1" * 64, contract_version="v1")
            session.add(evaluator)
            session.flush()
            task = api_models.TaskRevisionRow(
                slug="model-identity.task", version="0.1.0", family_id="model-identity-family", category="rag",
                source_digest="3" * 64, manifest_digest="4" * 64, evaluator_id=evaluator.id,
                manifest={}, revision_digest="5" * 64,
            )
            entrant = api_models.EntrantRevisionRow(
                slug="model-identity-agent", version="1.0.0", track="agents", config_digest="model-identity-agent-digest",
                capabilities=[], manifest={},
            )
            session.add_all([task, entrant])
            session.flush()
            campaign = api_models.CampaignRow(name="model-identity-test", state="running", draft={})
            session.add(campaign)
            session.flush()
            trial = api_models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=1, cell_digest="6" * 64,
            )
            session.add(trial)
            session.flush()
            attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="engineering", lease_generation=1)
            session.add(attempt)
            session.commit()
            return attempt.id


if __name__ == "__main__":
    unittest.main()
