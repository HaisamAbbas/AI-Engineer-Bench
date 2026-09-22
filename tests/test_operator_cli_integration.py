"""Real PostgreSQL + uvicorn integration tests for the v2 operator CLI.

Requires AIEB_DATABASE_URL pointing at a disposable test PostgreSQL (same as
ENG-014/ENG-018 tests). When unset the whole class is skipped - a substitute
sqlite must never stand in for the real engine.

These tests exercise the ACTUAL CLI entry point (aieb_cli.main.main) over real
HTTP against a local uvicorn serving the real API, proving the core release
workflow: create/freeze (test-side seeding), then CLI plan -> approve -> run ->
inspect -> release prepare -> release inspect -> publication publish, with the
server-side gates (independent approval, no self-approval, terminal-only
publication preparation) enforced by the API rather than claimed by the CLI.
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import time
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src", ROOT / "packages/aieb-cli/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")

if DATABASE_URL:
    os.environ["AIEB_ENV"] = "test"
    os.environ["AIEB_TEST_SHARED_SECRET"] = "test-only-shared-secret-not-a-real-credential"
    os.environ.pop("AIEB_OIDC_ISSUER", None)
    os.environ.pop("AIEB_OIDC_JWKS_URL", None)
    os.environ.pop("AIEB_OIDC_AUDIENCE", None)

    import jwt
    from fastapi.testclient import TestClient
    from sqlalchemy import select, text, update

    from aieb_api import auth, db
    from aieb_api.app import create_app
    from aieb_api import models as api_models
    from aieb_cli.main import main

    OPERATOR_SUBJECT = "operator-subject"
    REVIEWER_SUBJECT = "reviewer-1"

    def _token(roles: tuple[str, ...], subject: str) -> str:
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

    def _auth_header(subject: str, roles: tuple[str, ...]) -> dict[str, str]:
        _grant_roles(subject, roles)
        return {"Authorization": f"Bearer {_token(roles, subject)}"}


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; operator CLI integration requires PostgreSQL")
class OperatorCliIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db.configure(DATABASE_URL)
        auth._configured = False  # noqa: SLF001
        cls._tmp = ROOT / ".cache" / "test-tmp" / "operator-cli-integration"
        cls._tmp.mkdir(parents=True, exist_ok=True)

    def setUp(self) -> None:
        engine = db.engine()
        with engine.begin() as connection:
            for table in reversed(api_models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
            connection.execute(text("INSERT INTO kill_switch (id, active) VALUES (1, false)"))
            connection.execute(text("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)"))
        self.app = create_app()

        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            self.port = available.getsockname()[1]
        import uvicorn

        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="error", access_log=False,
        ))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                connection = socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
                connection.close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("uvicorn did not accept connections")

        self.operator_header = _auth_header(OPERATOR_SUBJECT, ("operator",))
        self.reviewer_header = _auth_header(REVIEWER_SUBJECT, ("reviewer",))

    def tearDown(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive(), "uvicorn failed to shut down")

    # ---- fixtures ------------------------------------------------------

    def _seed_task(self) -> None:
        manifest = {
            "schema_version": "aieb.task/v1", "id": "rag.document-freshness", "version": "0.1.0", "family_id": "knowledge-service-a",
            "category": "rag", "activity": "repair",
            "source": {"repository_digest": "1" * 64, "commit": "synthetic", "license": "Apache-2.0", "provenance_digest": "2" * 64},
            "environment": {"official_image": "registry.example/aieb@sha256:" + "3" * 64, "engineer_cpu": 1, "engineer_memory_mb": 512, "service_topology_digest": "4" * 64, "egress_policy": "none"},
            "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
            "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 1000},
            "requirements": [{"id": "api-ready", "severity": "mandatory", "description": "ready"}],
            "evaluator": {"evaluator_digest": "6" * 64, "development_fixture": "rag01-dev-v1", "official_fixture_ref": "maintainer-only:rag01-v1"},
            "profile_compatibility": ["cohort-a"],
        }
        from aieb_api import admission as admission_module
        from aieb_core.models import TaskRevision as _TaskRevisionContract

        manifest_digest = _TaskRevisionContract.model_validate(manifest).digest()
        with db.session_factory()() as session:
            # Idempotent for the same reason as `_seed_evaluator`: this
            # test's core flow calls `_create_frozen_campaign()` twice in one
            # test, and two campaigns legitimately sharing one admitted task
            # revision is normal - re-inserting it a second time is not.
            existing = session.execute(
                select(api_models.TaskRevisionRow).where(
                    api_models.TaskRevisionRow.slug == "rag.document-freshness",
                    api_models.TaskRevisionRow.version == "0.1.0",
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            revision = api_models.TaskRevisionRow(
                slug="rag.document-freshness", version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest="1" * 64, manifest_digest=manifest_digest,
                evaluator_id=self._seed_evaluator(session), manifest=manifest,
                ticket_text="Repair stale document ingestion.",
            )
            session.add(revision)
            session.flush()
            # V2-GAP-004's freeze requires release-eligible (admitted) task
            # revisions - see test_api_service.py::_seed_task for the same fix.
            admission_module.seed_fixture_admission(session, revision)
            session.commit()

    def _seed_evaluator(self, session) -> uuid.UUID:
        # Idempotent: this test's core flow calls `_create_frozen_campaign()`
        # twice in one test (a second, separately-approved campaign), and
        # `_seed_task` -> `_seed_evaluator` re-seeding the same fixed
        # (code_digest, contract_version) a second time used to violate
        # `uq_evaluator_revision_digest_contract` - first surfaced once the
        # earlier admission-gate blocker (below) stopped masking it.
        existing = session.execute(
            select(api_models.EvaluatorRevisionRow).where(
                api_models.EvaluatorRevisionRow.code_digest == "e" * 64,
                api_models.EvaluatorRevisionRow.contract_version == "v1",
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id
        row = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
        session.add(row)
        session.flush()
        return row.id

    def _seed_entrant(self) -> None:
        manifest = {
            "schema_version": "aieb.entrant/v1", "id": "agent-a", "track": "agents", "agent_implementation": "demo",
            "agent_version": "1.0.0", "engineer_model": {"provider_class": "demo", "requested_model": "demo-model", "settings_digest": "a" * 64},
            "prompt_digest": "b" * 64, "tools_digest": "c" * 64, "capabilities": ["cpu-fixture-standard-v1"],
            "credential_ref_type": "broker",
        }
        with db.session_factory()() as session:
            # Idempotent for the same reason as `_seed_task`/`_seed_evaluator`.
            existing = session.execute(
                select(api_models.EntrantRevisionRow).where(
                    api_models.EntrantRevisionRow.slug == "agent-a",
                    api_models.EntrantRevisionRow.version == "1.0.0",
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            session.add(api_models.EntrantRevisionRow(
                slug="agent-a", version="1.0.0", track="agents", config_digest="agent-a-digest",
                capabilities=["cpu-fixture-standard-v1"], manifest=manifest,
            ))
            session.commit()

    def _registry(self) -> dict:
        return {
            "cohort": {
                "schema_version": "aieb.cohort/v1", "id": "cohort-a", "track": "agents", "suite_id": "suite-a",
                "protocol_id": "protocol-a", "budget_profile_id": "budget-a", "dependency_mode": "fixture",
                "application_model_profile": {"dependency_mode": "fixture", "entrypoint": ["python"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
                "hardware_class": "cpu-fixture-standard-v1", "required_capabilities": ["cpu-fixture-standard-v1"],
            },
            "protocol": {"schema_version": "aieb.protocol/v1", "id": "protocol-a", "scoring_digest": "9" * 64, "max_replacements": 2, "required_trace_coverage": False, "hard_cost_ranking": False},
            "budget": {
                "schema_version": "aieb.budget/v2", "id": "budget-a", "engineer_wall_seconds": 1200, "verification_wall_seconds": 300,
                "engineer_cpu": 2, "engineer_memory_mb": 1024, "environment_upper_bound_usd": "0.5",
                # V2-GAP-004's worst-case reservation formula treats a `None`
                # limit_usd as an undeclared (unbounded) role cap and refuses
                # to start - see test_api_service.py::_registry_payload for
                # the same fix.
                "per_role_budget_usd": [
                    {"role": "engineer", "limit_usd": "1.00"}, {"role": "dev_application", "limit_usd": "1.00"},
                    {"role": "verifier_application", "limit_usd": "1.00"}, {"role": "verifier_judge", "limit_usd": "1.00"},
                ],
            },
        }

    def _create_frozen_campaign(self) -> str:
        self._seed_task()
        self._seed_entrant()
        draft = {
            "name": "operator-campaign",
            "draft": {
                "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
                "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
                "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
            },
        }
        create = self.client.post(
            "/v1/campaigns", json=draft,
            headers=self.operator_header | {"Idempotency-Key": f"op-create-{uuid.uuid4().hex[:8]}"},
        )
        self.assertEqual(create.status_code, 201, create.text)
        campaign_id = create.json()["id"]
        frozen = self.client.post(
            f"/v1/campaigns/{campaign_id}/freeze", json=self._registry(),
            headers=self.operator_header | {"Idempotency-Key": f"op-freeze-{uuid.uuid4().hex[:8]}"},
        )
        self.assertEqual(frozen.status_code, 200, frozen.text)
        self.assertEqual(frozen.json()["state"], "frozen")
        return campaign_id

    def _manifest_path(self, campaign_id: str) -> str:
        with db.session_factory()() as session:
            row = session.get(api_models.CampaignRow, uuid.UUID(campaign_id))
            self.assertIsNotNone(row)
            self.assertEqual(row.state, "frozen")
            resolved = row.resolved
        path = self._tmp / f"manifest-{campaign_id}.json"
        path.write_text(json.dumps(resolved), encoding="utf-8")
        return str(path)

    def _cli(self, token: str, argv: list[str]) -> tuple[int, dict | None, str, str]:
        full = ["--json", "--api-url", self.base_url, "--access-token", token, *argv]
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = main(full)
        envelope = json.loads(out.getvalue().strip().splitlines()[-1]) if out.getvalue().strip() else None
        return code, envelope, out.getvalue(), err.getvalue()

    def _force_incomplete(self, campaign_id: str) -> None:
        with db.session_factory()() as session:
            session.execute(
                update(api_models.CampaignRow).where(api_models.CampaignRow.id == uuid.UUID(campaign_id))
                .values(state="incomplete")
            )
            session.commit()

    # ---- the release flow ----------------------------------------------

    def test_core_release_flow_with_cli_over_http(self) -> None:
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

        campaign_id = self._create_frozen_campaign()
        manifest = self._manifest_path(campaign_id)

        # 0) materialize the exact cell matrix (frozen -> planned): V2-GAP-004
        # requires a campaign to be `planned` before it can be `approved` -
        # `approve` alone on a merely `frozen` campaign now fails closed.
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "campaign", "plan", "--campaign", campaign_id, "--manifest", manifest,
             "--materialize", "--idempotency-key", "plan-1"],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(envelope["data"]["state"], "planned", envelope)

        # 1) reviewer approves the planned campaign through the CLI.
        code, envelope, out, _ = self._cli(
            _token(("reviewer",), REVIEWER_SUBJECT),
            ["operator", "campaign", "approve", "--campaign", campaign_id, "--reason", "independently reviewed",
             "--idempotency-key", "approve-1"],
        )
        self.assertEqual(code, 0, out)
        self.assertTrue(envelope["data"]["approval"]["approved"], envelope)

        # 2) plan reports the verified digest and the recorded approval, read-only.
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "campaign", "plan", "--campaign", campaign_id, "--manifest", manifest],
        )
        self.assertEqual(code, 0, out)
        self.assertTrue(envelope["data"]["digest_verified"], envelope)
        # `frozen` reports `state == "frozen"` at read time - the campaign has
        # already advanced past that to `approved` by this point (steps 0-1),
        # which is what a genuinely materialized-and-approved campaign looks
        # like; `state`/the approval gate below are the meaningful checks here.
        self.assertFalse(envelope["data"]["frozen"], envelope)
        self.assertEqual(envelope["data"]["state"], "approved", envelope)
        self.assertFalse(envelope["data"]["mutation_issued"], envelope)
        approval_gate = next(gate for gate in envelope["data"]["gates"] if gate["gate"] == "campaign_approved")
        self.assertTrue(approval_gate["satisfied"], envelope)
        self.assertEqual(envelope["data"]["planned_trials"], 1)
        self.assertEqual(envelope["data"]["cells"]["trials"], 1)

        # 3) an unplanned, unapproved campaign start is refused by the CLI gate
        # before it ever reaches the server - `not_planned` fires first since
        # V2-GAP-004 interposed a mandatory materialize step before approval.
        other = self._create_frozen_campaign()
        other_manifest = self._manifest_path(other)
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "campaign", "run", "--campaign", other, "--manifest", other_manifest,
             "--confirm-run", "--idempotency-key", "run-other"],
        )
        self.assertEqual(code, 2, out)
        self.assertEqual(envelope["error"]["code"], "not_planned", envelope)

        # 4) the approved campaign runs and reports durable server state.
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "campaign", "run", "--campaign", campaign_id, "--manifest", manifest,
             "--confirm-run", "--idempotency-key", "run-1"],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(envelope["data"]["state"]["campaign"]["state"], "running", envelope)

        # 5) inspect reflects the running campaign from the server.
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "campaign", "inspect", "--campaign", campaign_id],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(envelope["data"]["campaign"]["state"], "running", envelope)

        # 6) release prepare/inspect/publish for a terminal incomplete campaign.
        self._force_incomplete(campaign_id)
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "release", "prepare", "--campaign", campaign_id,
             "--manifest", manifest, "--publication-class", "non_ranked",
             "--idempotency-key", "prep-1"],
        )
        self.assertEqual(code, 0, out)
        preparation = envelope["data"]["preparation"]
        self.assertEqual(preparation["status"], "prepared", envelope)
        self.assertTrue(envelope["data"]["digest_verified"], envelope)
        preparation_id = preparation["id"]

        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "release", "inspect", "--preparation", preparation_id],
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(envelope["data"]["preparation"]["status"], "prepared", envelope)

        code, envelope, out, _ = self._cli(
            _token(("reviewer",), REVIEWER_SUBJECT),
            ["operator", "publication", "publish", "--preparation", preparation_id,
             "--independence-attestation", "--idempotency-key", "publish-1"],
        )
        self.assertEqual(code, 0, out)
        self.assertTrue(envelope["data"]["review"]["published_publication_id"], envelope)

    def test_creator_cannot_approve_own_campaign_server_side(self) -> None:
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

        campaign_id = self._create_frozen_campaign()
        code, envelope, out, _ = self._cli(
            _token(("operator",), OPERATOR_SUBJECT),
            ["operator", "campaign", "approve", "--campaign", campaign_id, "--idempotency-key", "self-approve"],
        )
        self.assertEqual(code, 2, out)
        self.assertEqual(envelope["error"]["code"], "authorization_forbidden", envelope)

    def test_missing_idempotency_key_blocks_approval_before_network(self) -> None:
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

        campaign_id = self._create_frozen_campaign()
        code, envelope, out, _ = self._cli(
            _token(("reviewer",), REVIEWER_SUBJECT),
            ["operator", "campaign", "approve", "--campaign", campaign_id],
        )
        self.assertEqual(code, 2, out)
        self.assertEqual(envelope["error"]["code"], "idempotency_key_required", envelope)


if __name__ == "__main__":
    unittest.main()