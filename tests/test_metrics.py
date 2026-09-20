"""ENG-020 gap 6: Prometheus exporter tests against a real test PostgreSQL instance.

Requires AIEB_DATABASE_URL to point at a disposable test database (see
docs/implementation/evidence/ENG-014/api-service.md); skipped rather than faked
against sqlite otherwise, matching this project's rule against substituting a fake
evaluation for the real one the spec asks for.

Covers:
- `/metrics` is authenticated-safe (401 with no credentials, 403 with an insufficient
  role, 200 with an operator/reviewer/administrator role).
- The response is valid Prometheus text-exposition-format (0.0.4) with the right
  content type.
- Every metric name deploy/alerts/prometheus-rules.yml's alert expressions reference is
  actually present in a real scrape (the "make the existing alert rules executable
  against real metric names" regression check).
- The instrumentation is real, not decorative: activating/deactivating the kill switch,
  driving a campaign's consecutive_infrastructure_failures, finalizing an attempt as
  infrastructure_invalid via lease reconciliation, and purging expired staging
  artifacts via the reconciler each visibly change the corresponding scraped value.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")

if DATABASE_URL:
    os.environ["AIEB_ENV"] = "test"
    os.environ["AIEB_TEST_SHARED_SECRET"] = "test-only-shared-secret-not-a-real-credential"
    os.environ.pop("AIEB_OIDC_ISSUER", None)
    os.environ.pop("AIEB_OIDC_JWKS_URL", None)
    os.environ.pop("AIEB_OIDC_AUDIENCE", None)

    import unittest

    import jwt
    from fastapi.testclient import TestClient
    from sqlalchemy import select, text, update

    from aieb_api import auth, db
    from aieb_api import models as api_models
    from aieb_api.app import create_app
    from aieb_api.worker import metrics, reconciler, repository

    RULES_PATH = ROOT / "deploy" / "alerts" / "prometheus-rules.yml"

    def _token(roles: tuple[str, ...], subject: str = "test-subject") -> str:
        return jwt.encode(
            {"sub": subject, "iss": "test", "aieb_roles": list(roles)},
            os.environ["AIEB_TEST_SHARED_SECRET"],
            algorithm="HS256",
        )

    def _grant_roles(subject: str, roles: tuple[str, ...]) -> None:
        if not roles:
            return
        with db.session_factory()() as session:
            user = session.execute(
                select(api_models.User).where(api_models.User.oidc_issuer == "test", api_models.User.oidc_subject == subject)
            ).scalar_one_or_none()
            if user is None:
                user = api_models.User(oidc_subject=subject, oidc_issuer="test")
                session.add(user)
                session.flush()
            existing = set(
                session.execute(select(api_models.RoleBinding.role).where(api_models.RoleBinding.user_id == user.id)).scalars().all()
            )
            for role in roles:
                if role not in existing:
                    session.add(api_models.RoleBinding(user_id=user.id, role=role, scope=auth.GLOBAL_SCOPE))
            session.commit()

    def _auth_header(roles: tuple[str, ...], subject: str = "test-subject") -> dict[str, str]:
        _grant_roles(subject, roles)
        return {"Authorization": f"Bearer {_token(roles, subject)}"}


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-020 gap 6 real-Postgres tests are blocked")
class MetricsExporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.configure(DATABASE_URL)
        auth._configured = False  # noqa: SLF001 - force re-read of env for this test process
        cls.app = create_app()

    def setUp(self) -> None:
        engine = db.engine()
        with engine.begin() as connection:
            for table in reversed(api_models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
            connection.execute(text("INSERT INTO kill_switch (id, active) VALUES (1, false)"))
            connection.execute(text("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)"))
        # This module's in-process counters/gauges persist across tests within the same
        # process (they are, correctly, an in-process registry) - reset them so one
        # test's counts cannot leak into another's assertions.
        metrics.counters.clear()
        metrics._gauges.clear()
        metrics._metric_counters.clear()
        self.session_factory = db.session_factory()
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def _scrape(self, roles: tuple[str, ...] = ("operator",)) -> str:
        response = self.client.get("/metrics", headers=_auth_header(roles))
        self.assertEqual(response.status_code, 200, response.text)
        return response.text

    # ---- fixtures ---------------------------------------------------------

    def _seed_campaign(self, *, state: str = "running") -> uuid.UUID:
        with self.session_factory() as session:
            campaign = api_models.CampaignRow(name="metrics-test", state=state, draft={})
            session.add(campaign)
            session.commit()
            return campaign.id

    def _seed_minimal_engineering_lease(self, *, expired: bool = True) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """A campaign/trial/attempt/work_item chain minimal enough to drive
        `reconcile_expired_leases` without going through the full cohort-freezing
        pipeline test_worker_leasing.py uses - this test only needs the leasing
        machinery's terminal-state side effect (infrastructure_invalid), not a real
        resolved manifest."""
        with self.session_factory() as session:
            evaluator = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
            session.add(evaluator)
            session.flush()
            task = api_models.TaskRevisionRow(
                slug="metrics.task", version="0.1.0", family_id="metrics-family", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator.id,
                manifest={}, revision_digest="f" * 64,
            )
            entrant = api_models.EntrantRevisionRow(
                slug="metrics-agent", version="1.0.0", track="agents", config_digest="metrics-agent-digest",
                capabilities=[], manifest={},
            )
            session.add_all([task, entrant])
            session.flush()
            campaign = api_models.CampaignRow(name="metrics-lease-test", state="running", draft={})
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
            session.flush()
            heartbeat_at = datetime.now(timezone.utc)
            lease_expiry = (
                datetime.now(timezone.utc) - timedelta(seconds=5)
                if expired else datetime.now(timezone.utc) + timedelta(seconds=60)
            )
            work_item = api_models.WorkItemRow(
                attempt_id=attempt.id, type="engineering", state="leased", worker_id="metrics-worker",
                lease_expiry=lease_expiry, last_heartbeat_at=heartbeat_at, generation=1, lease_epoch=0,
            )
            session.add(work_item)
            session.commit()
            return campaign.id, attempt.id, work_item.id

    def _seed_active_reservation(self, campaign_id: uuid.UUID) -> None:
        with self.session_factory() as session:
            session.add(
                api_models.BudgetReservationRow(
                    campaign_id=campaign_id, reservation_id="res-1", enforcement="estimated_time_limited",
                    status="active",
                )
            )
            session.commit()

    # ---- authentication / shape --------------------------------------------

    def test_metrics_requires_authentication(self) -> None:
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthenticated")

    def test_metrics_requires_authorized_role(self) -> None:
        response = self.client.get("/metrics", headers=_auth_header(("submitter",)))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_metrics_returns_valid_prometheus_text_for_authorized_roles(self) -> None:
        for role in ("operator", "reviewer", "administrator"):
            with self.subTest(role=role):
                response = self.client.get("/metrics", headers=_auth_header((role,), subject=f"subject-{role}"))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers["content-type"].startswith("text/plain"))
                body = response.text
                self.assertIn("# HELP aieb_kill_switch_active", body)
                self.assertIn("# TYPE aieb_kill_switch_active gauge", body)
                self.assertIn("# TYPE aieb_reconciler_worker_artifacts_purged_total counter", body)

    # ---- regression: alert rule metric names are all really exported ------

    def test_every_alert_rule_metric_name_appears_in_a_real_scrape(self) -> None:
        rules_text = RULES_PATH.read_text(encoding="utf-8")
        expr_lines = re.findall(r"^\s*expr:\s*(.+)$", rules_text, flags=re.MULTILINE)
        self.assertTrue(expr_lines, "expected at least one alert rule expr in prometheus-rules.yml")
        metric_names: set[str] = set()
        for expr in expr_lines:
            metric_names.update(re.findall(r"\baieb_[a-zA-Z0-9_]*[a-zA-Z0-9]\b", expr))
        self.assertTrue(metric_names, "no metric names parsed out of the alert rules file")

        body = self._scrape()
        for metric_name in sorted(metric_names):
            with self.subTest(metric=metric_name):
                self.assertIn(metric_name, body, f"{metric_name} referenced by an alert rule but absent from /metrics")

    # ---- functional: instrumentation reflects real state, not decoration --

    def test_kill_switch_gauge_reflects_activation_and_deactivation(self) -> None:
        def _gauge_value(body: str) -> str:
            match = re.search(r"^aieb_kill_switch_active\s+(\S+)$", body, flags=re.MULTILINE)
            self.assertIsNotNone(match, body)
            return match.group(1)

        self.assertEqual(_gauge_value(self._scrape()), "0")

        with self.session_factory() as session:
            repository.activate_kill_switch(session, activated_by_user_id=None, reason="metrics test")
        self.assertEqual(_gauge_value(self._scrape()), "1")

        with self.session_factory() as session:
            repository.deactivate_kill_switch(session)
        self.assertEqual(_gauge_value(self._scrape()), "0")

    def test_kill_switch_metric_fails_closed_when_control_row_is_missing(self) -> None:
        with self.session_factory() as session:
            session.execute(text("DELETE FROM kill_switch WHERE id = 1"))
            session.commit()
        body = self._scrape()
        self.assertIsNotNone(re.search(r"^aieb_kill_switch_active\s+1$", body, flags=re.MULTILINE), body)

    def test_worker_counter_is_read_from_shared_database(self) -> None:
        with self.session_factory() as session:
            repository.record_metric_counter(session, "aieb_attempt_infrastructure_invalid_total", 7)
            session.commit()
        # Deliberately clear the in-process mirror: the API must still expose
        # the worker's durable value from PostgreSQL.
        metrics._metric_counters.clear()
        body = self._scrape()
        self.assertIsNotNone(re.search(r"^aieb_attempt_infrastructure_invalid_total\s+7$", body, flags=re.MULTILINE), body)

    def test_campaign_consecutive_infrastructure_failures_gauge_tracks_repository_state(self) -> None:
        campaign_id = self._seed_campaign(state="running")
        with self.session_factory() as session:
            repository.record_infrastructure_outcome(session, campaign_id, "infrastructure_invalid")
            repository.record_infrastructure_outcome(session, campaign_id, "infrastructure_invalid")

        body = self._scrape()
        expected = f'aieb_campaign_consecutive_infrastructure_failures{{campaign_id="{campaign_id}"}} 2'
        self.assertIn(expected, body, body)

        with self.session_factory() as session:
            repository.record_infrastructure_outcome(session, campaign_id, "valid")

        body = self._scrape()
        reset = f'aieb_campaign_consecutive_infrastructure_failures{{campaign_id="{campaign_id}"}} 0'
        self.assertIn(reset, body, body)

    def test_infrastructure_invalid_counter_increments_on_lease_reconciliation(self) -> None:
        self._seed_minimal_engineering_lease(expired=True)
        before = self._scrape()
        before_match = re.search(r"^aieb_attempt_infrastructure_invalid_total\s+(\S+)$", before, flags=re.MULTILINE)
        before_value = float(before_match.group(1)) if before_match else 0.0

        with self.session_factory() as session:
            summary = repository.reconcile_expired_leases(session)
        self.assertEqual(len(summary.orphaned_attempt_ids), 1)

        after = self._scrape()
        after_match = re.search(r"^aieb_attempt_infrastructure_invalid_total\s+(\S+)$", after, flags=re.MULTILINE)
        self.assertIsNotNone(after_match, after)
        self.assertEqual(float(after_match.group(1)), before_value + 1)

    def test_reconciler_purged_counter_increments_by_purged_count(self) -> None:
        with self.session_factory() as session:
            session.add(
                api_models.WorkerArtifactBlobRow(
                    sha256="a" * 64, byte_length=4, data=b"data", retention_class="staging",
                    staged_until=datetime.now(timezone.utc) - timedelta(hours=1),
                )
            )
            session.commit()

        before = self._scrape()
        before_match = re.search(r"^aieb_reconciler_worker_artifacts_purged_total\s+(\S+)$", before, flags=re.MULTILINE)
        before_value = float(before_match.group(1)) if before_match else 0.0

        summary = reconciler.reconcile_once(self.session_factory)
        self.assertEqual(summary.worker_artifacts_purged, 1)

        after = self._scrape()
        after_match = re.search(r"^aieb_reconciler_worker_artifacts_purged_total\s+(\S+)$", after, flags=re.MULTILINE)
        self.assertIsNotNone(after_match, after)
        self.assertEqual(float(after_match.group(1)), before_value + 1)

    def test_budget_reservation_age_gauge_reports_active_reservations(self) -> None:
        campaign_id = self._seed_campaign(state="running")
        self._seed_active_reservation(campaign_id)

        body = self._scrape()
        match = re.search(
            r'aieb_budget_reservation_age_seconds\{campaign_id="%s",status="active"\}\s+(\S+)' % re.escape(str(campaign_id)),
            body,
        )
        self.assertIsNotNone(match, body)
        self.assertGreaterEqual(float(match.group(1)), 0.0)

    def test_worker_heartbeat_age_gauge_reports_leased_work_items(self) -> None:
        _, _, work_item_id = self._seed_minimal_engineering_lease(expired=False)

        body = self._scrape()
        match = re.search(
            r'aieb_worker_heartbeat_age_seconds\{[^}]*work_item_id="%s"[^}]*\}\s+(\S+)' % re.escape(str(work_item_id)),
            body,
        )
        self.assertIsNotNone(match, body)
        self.assertGreaterEqual(float(match.group(1)), 0.0)


if __name__ == "__main__":
    unittest.main()
