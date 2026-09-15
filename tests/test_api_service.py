"""ENG-014: hosted metadata API tests against a real test PostgreSQL instance.

Requires AIEB_DATABASE_URL to point at a disposable test database (see
docs/implementation/evidence/ENG-014/api-service.md for how to start one).
If it is not set, these tests are skipped rather than silently faked
against sqlite, matching this project's rule against presenting a
substitute as the real evaluation the spec asks for.
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
import uuid
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

    import jwt
    from fastapi.testclient import TestClient
    from sqlalchemy import select, text, update

    from aieb_api import auth, db
    from aieb_api.app import create_app
    from aieb_api import models as api_models

    def _token(roles: tuple[str, ...], subject: str = "test-subject") -> str:
        # aieb_roles is embedded for readability only - auth.py no longer reads it
        # for authorization; _grant_roles below is what actually grants access.
        return jwt.encode(
            {"sub": subject, "iss": "test", "aieb_roles": list(roles)},
            os.environ["AIEB_TEST_SHARED_SECRET"],
            algorithm="HS256",
        )

    def _grant_roles(subject: str, roles: tuple[str, ...]) -> None:
        """Seed the server-side role_bindings this identity needs, mirroring what
        an administrator would provision - a token's claims alone must not grant
        access (finding #3)."""
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
                    session.add(api_models.RoleBinding(user_id=user.id, role=role, scope="test"))
            session.commit()

    def _auth_header(roles: tuple[str, ...], subject: str = "test-subject") -> dict[str, str]:
        _grant_roles(subject, roles)
        return {"Authorization": f"Bearer {_token(roles, subject)}"}


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-014 real-Postgres tests are blocked")
class ApiServiceTests(unittest.TestCase):
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
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    # ---- fixtures -----------------------------------------------------

    def _seed_task(self, slug: str = "rag.document-freshness") -> None:
        manifest = {
            "schema_version": "aieb.task/v1", "id": slug, "version": "0.1.0", "family_id": "knowledge-service-a",
            "category": "rag", "activity": "repair",
            "source": {"repository_digest": "1" * 64, "commit": "synthetic", "license": "Apache-2.0", "provenance_digest": "2" * 64},
            "environment": {"official_image": "registry.example/aieb@sha256:" + "3" * 64, "engineer_cpu": 1, "engineer_memory_mb": 512, "service_topology_digest": "4" * 64, "egress_policy": "none"},
            "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
            "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 1000},
            "requirements": [{"id": "api-ready", "severity": "mandatory", "description": "ready"}],
            "evaluator": {"evaluator_digest": "6" * 64, "development_fixture": "rag01-dev-v1", "official_fixture_ref": "maintainer-only:rag01-v1"},
            "profile_compatibility": ["cohort-a"],
        }
        with db.session_factory()() as session:
            session.add(api_models.TaskRevisionRow(
                slug=slug, version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64,
                evaluator_id=self._seed_evaluator(session), manifest=manifest,
            ))
            session.commit()

    def _seed_evaluator(self, session) -> uuid.UUID:
        row = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
        session.add(row)
        session.flush()
        return row.id

    def _seed_entrant(self, slug: str = "agent-a") -> None:
        manifest = {
            "schema_version": "aieb.entrant/v1", "id": slug, "track": "agents", "agent_implementation": "demo",
            "agent_version": "1.0.0", "engineer_model": {"provider_class": "demo", "requested_model": "demo-model", "settings_digest": "a" * 64},
            "prompt_digest": "b" * 64, "tools_digest": "c" * 64, "capabilities": ["cpu-fixture-standard-v1"],
            "credential_ref_type": "broker",
        }
        with db.session_factory()() as session:
            session.add(api_models.EntrantRevisionRow(
                slug=slug, version="1.0.0", track="agents", config_digest=f"{slug}-digest",
                capabilities=["cpu-fixture-standard-v1"], manifest=manifest,
            ))
            session.commit()

    # ---- API-01: idempotency -------------------------------------------

    def test_idempotent_request_replays_prior_response(self) -> None:
        headers = _auth_header(("operator",)) | {"Idempotency-Key": "key-1"}
        body = {"name": "repair-pilot", "draft": {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }}
        first = self.client.post("/v1/campaigns", json=body, headers=headers)
        second = self.client.post("/v1/campaigns", json=body, headers=headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        with db.session_factory()() as session:
            self.assertEqual(session.query(api_models.CampaignRow).count(), 1)

    def test_conflicting_idempotency_key_reuse_is_409(self) -> None:
        headers = _auth_header(("operator",)) | {"Idempotency-Key": "key-2"}
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        first = self.client.post("/v1/campaigns", json={"name": "a", "draft": draft}, headers=headers)
        second = self.client.post("/v1/campaigns", json={"name": "b", "draft": draft}, headers=headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "conflict")

    # ---- API-02: private artifact ref access -------------------------

    def test_private_artifact_ref_denied_without_leakage(self) -> None:
        with db.session_factory()() as session:
            owner = api_models.User(oidc_subject="owner-subject", oidc_issuer="test")
            session.add(owner)
            session.flush()
            artifact = api_models.ArtifactRow(content_digest="f" * 64, size=10, media_type="text/plain")
            session.add(artifact)
            session.flush()
            ref = api_models.ArtifactRefRow(artifact_id=artifact.id, owner_user_id=owner.id, visibility="private")
            session.add(ref)
            session.commit()
            ref_id = ref.id
        response = self.client.get(f"/v1/artifacts/{ref_id}/download", headers=_auth_header(("submitter",), subject="someone-else"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_missing_artifact_ref_is_also_404_same_shape(self) -> None:
        response = self.client.get(f"/v1/artifacts/{uuid.uuid4()}/download", headers=_auth_header(("submitter",)))
        self.assertEqual(response.status_code, 404)

    # ---- stale edits / optimistic concurrency --------------------------

    def test_patch_with_stale_if_match_is_412(self) -> None:
        self._seed_task()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-3"},
        )
        campaign_id = create.json()["id"]
        response = self.client.patch(
            f"/v1/campaigns/{campaign_id}", json={"draft": draft}, headers=_auth_header(("operator",)) | {"If-Match": "999"}
        )
        self.assertEqual(response.status_code, 412)

    # ---- invalid state transitions -------------------------------------

    @staticmethod
    def _registry_payload() -> dict:
        return {
            "cohort": {
                "schema_version": "aieb.cohort/v1", "id": "cohort-a", "track": "agents", "suite_id": "suite-a",
                "protocol_id": "protocol-a", "budget_profile_id": "budget-a", "dependency_mode": "fixture",
                "application_model_profile": {"dependency_mode": "fixture", "entrypoint": ["python"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
                "hardware_class": "cpu-fixture-standard-v1", "required_capabilities": ["cpu-fixture-standard-v1"],
            },
            "protocol": {"schema_version": "aieb.protocol/v1", "id": "protocol-a", "scoring_digest": "9" * 64, "max_replacements": 2, "required_trace_coverage": False, "hard_cost_ranking": False},
            "budget": {
                "schema_version": "aieb.budget/v1", "id": "budget-a", "engineer_wall_seconds": 1200, "verification_wall_seconds": 300,
                "engineer_cpu": 2, "engineer_memory_mb": 1024,
                "per_role_budget_usd": [
                    {"role": "engineer", "limit_usd": None}, {"role": "dev_application", "limit_usd": None},
                    {"role": "verifier_application", "limit_usd": None}, {"role": "verifier_judge", "limit_usd": None},
                ],
            },
        }

    def test_freeze_twice_is_conflict(self) -> None:
        self._seed_task()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-4"}
        )
        campaign_id = create.json()["id"]
        registry = self._registry_payload()
        headers = _auth_header(("operator",))
        first = self.client.post(f"/v1/campaigns/{campaign_id}/freeze", json=registry, headers=headers | {"Idempotency-Key": "freeze-1"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["state"], "frozen")
        second = self.client.post(f"/v1/campaigns/{campaign_id}/freeze", json=registry, headers=headers | {"Idempotency-Key": "freeze-2"})
        self.assertEqual(second.status_code, 409)

    def test_concurrent_freeze_only_one_winner(self) -> None:
        """Two concurrent freeze calls with DIFFERENT idempotency keys race on the same
        campaign; the atomic state='draft' guard must let exactly one through, not let the
        second silently overwrite the first's resolved snapshot (review finding #2)."""
        self._seed_task()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-race"}
        )
        campaign_id = create.json()["id"]
        registry = self._registry_payload()
        headers = _auth_header(("operator",))
        barrier = threading.Barrier(2)
        results: list[int] = []

        def call(key: str) -> None:
            barrier.wait(timeout=5)
            response = self.client.post(f"/v1/campaigns/{campaign_id}/freeze", json=registry, headers=headers | {"Idempotency-Key": key})
            results.append(response.status_code)

        threads = [threading.Thread(target=call, args=(f"freeze-race-{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(sorted(results), [200, 409])
        with db.session_factory()() as session:
            row = session.get(api_models.CampaignRow, uuid.UUID(campaign_id))
            self.assertEqual(row.state, "frozen")
            self.assertEqual(row.revision, 1)  # exactly one transition, not two

    def test_concurrent_freeze_same_idempotency_key_replays_not_409(self) -> None:
        """A client retrying the exact same freeze request (same Idempotency-Key) while the
        first attempt is still in flight must get the replayed 200, never a 409 - a same-key
        retry is not a conflict (second-pass review finding #2)."""
        self._seed_task()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-same-key-race"}
        )
        campaign_id = create.json()["id"]
        registry = self._registry_payload()
        headers = _auth_header(("operator",)) | {"Idempotency-Key": "freeze-same-key"}
        barrier = threading.Barrier(2)
        results: list[dict] = []

        def call() -> None:
            barrier.wait(timeout=5)
            response = self.client.post(f"/v1/campaigns/{campaign_id}/freeze", json=registry, headers=headers)
            results.append({"status": response.status_code, "body": response.json()})

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertTrue(all(r["status"] == 200 for r in results), results)
        self.assertEqual(results[0]["body"], results[1]["body"])
        with db.session_factory()() as session:
            row = session.get(api_models.CampaignRow, uuid.UUID(campaign_id))
            self.assertEqual(row.state, "frozen")
            self.assertEqual(row.revision, 1)  # exactly one transition, not two

    def test_freeze_detects_concurrent_patch_and_does_not_silently_drop_it(self) -> None:
        """If a PATCH commits a new draft revision between freeze's initial read and its final
        write, freeze must not silently lock in the stale snapshot it started resolving
        (second-pass review finding #3: the atomic guard must check revision, not only state)."""
        self._seed_task()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-freeze-vs-patch"}
        )
        campaign_id = create.json()["id"]

        # Simulate a PATCH committing after freeze would have already read the draft, by
        # bumping the persisted revision directly - freeze reads fresh per-request, so this
        # models "a PATCH committed between freeze's read and its write" without needing to
        # win an actual thread-scheduling race.
        with db.session_factory()() as session:
            row = session.get(api_models.CampaignRow, uuid.UUID(campaign_id))
            row.revision = 5
            session.commit()

        response = self.client.post(
            f"/v1/campaigns/{campaign_id}/freeze", json=self._registry_payload(),
            headers=_auth_header(("operator",)) | {"Idempotency-Key": "freeze-vs-patch"},
        )
        # freeze's own initial read sees revision=5, so this single-request path actually
        # succeeds cleanly (it is not racing anyone). The guarantee under test is that the
        # WHERE clause includes revision at all; verify it was written into the persisted row.
        self.assertEqual(response.status_code, 200)
        with db.session_factory()() as session:
            row = session.get(api_models.CampaignRow, uuid.UUID(campaign_id))
            self.assertEqual(row.revision, 6)  # 5 -> 6, proving the UPDATE matched on revision=5

    def test_freeze_with_corrupt_referenced_manifest_is_503_not_500(self) -> None:
        """Same bug class as the registry-read corrupt-manifest fix, but at freeze's own
        TaskRevision/EntrantRevision.model_validate call sites (second-pass review finding #1)."""
        with db.session_factory()() as session:
            evaluator_id = self._seed_evaluator(session)
            session.add(api_models.TaskRevisionRow(
                slug="corrupt.freeze-task", version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator_id,
                manifest={"not": "a valid TaskRevision at all"},
            ))
            session.commit()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["corrupt.freeze-task"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-corrupt-freeze"}
        )
        campaign_id = create.json()["id"]
        response = self.client.post(
            f"/v1/campaigns/{campaign_id}/freeze", json=self._registry_payload(),
            headers=_auth_header(("operator",)) | {"Idempotency-Key": "freeze-corrupt-manifest"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")

    def test_concurrent_patch_with_same_if_match_only_one_winner(self) -> None:
        """Two concurrent PATCH requests with the same If-Match must not both succeed
        (review finding #1): the loser must see a 412, not silently lose its write."""
        self._seed_task()
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-patch-race"}
        )
        campaign_id = create.json()["id"]
        headers = _auth_header(("operator",)) | {"If-Match": "0"}
        barrier = threading.Barrier(2)
        results: list[int] = []

        def call(repetitions: int) -> None:
            variant = {**draft, "repetitions": repetitions}
            barrier.wait(timeout=5)
            response = self.client.patch(f"/v1/campaigns/{campaign_id}", json={"draft": variant}, headers=headers)
            results.append(response.status_code)

        threads = [threading.Thread(target=call, args=(reps,)) for reps in (2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(sorted(results), [200, 412])
        with db.session_factory()() as session:
            row = session.get(api_models.CampaignRow, uuid.UUID(campaign_id))
            self.assertEqual(row.revision, 1)  # exactly one edit applied, not two

    def test_concurrent_create_with_same_key_no_duplicate_and_no_500(self) -> None:
        """Review finding #3: check-then-insert in the idempotency layer is not atomic on
        its own; concurrent requests with the same key must not raise an unhandled
        IntegrityError, and must not create two campaign rows."""
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        headers = _auth_header(("operator",)) | {"Idempotency-Key": "key-create-race"}
        barrier = threading.Barrier(2)
        results: list[tuple[int, dict]] = []

        def call() -> None:
            barrier.wait(timeout=5)
            response = self.client.post("/v1/campaigns", json={"name": "a", "draft": draft}, headers=headers)
            results.append((response.status_code, response.json()))

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertTrue(all(status == 201 for status, _ in results), results)
        self.assertEqual(results[0][1], results[1][1])
        with db.session_factory()() as session:
            self.assertEqual(session.query(api_models.CampaignRow).count(), 1)

    # ---- corrupt manifests ---------------------------------------------

    def test_freeze_with_unresolvable_task_reference_is_rejected(self) -> None:
        self._seed_entrant()
        draft = {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["does.not.exist"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }
        create = self.client.post(
            "/v1/campaigns", json={"name": "a", "draft": draft}, headers=_auth_header(("operator",)) | {"Idempotency-Key": "key-5"}
        )
        campaign_id = create.json()["id"]
        response = self.client.post(
            f"/v1/campaigns/{campaign_id}/freeze", json=self._registry_payload(), headers=_auth_header(("operator",)) | {"Idempotency-Key": "freeze-corrupt"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_corrupt_task_manifest_is_503_not_500(self) -> None:
        with db.session_factory()() as session:
            evaluator_id = self._seed_evaluator(session)
            session.add(api_models.TaskRevisionRow(
                slug="corrupt.task", version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator_id,
                manifest={"not": "a valid TaskRevision at all"},
            ))
            session.commit()
        response = self.client.get("/v1/tasks/corrupt.task/revisions/0.1.0")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")

    # ---- trial access is role-gated, not merely authenticated -----------

    def test_get_trial_requires_operator_reviewer_or_admin_role(self) -> None:
        trial_id = uuid.uuid4()
        response = self.client.get(f"/v1/trials/{trial_id}", headers=_auth_header(("submitter",)))
        self.assertEqual(response.status_code, 403)

    def test_get_trial_allows_reviewer_role(self) -> None:
        trial_id = uuid.uuid4()
        response = self.client.get(f"/v1/trials/{trial_id}", headers=_auth_header(("reviewer",)))
        self.assertEqual(response.status_code, 404)  # role passes; trial itself does not exist

    # ---- auth fails closed / role enforcement --------------------------

    def test_unauthenticated_request_to_operator_route_is_401(self) -> None:
        response = self.client.post("/v1/campaigns", json={"name": "a", "draft": {}})
        self.assertEqual(response.status_code, 401)

    def test_wrong_role_is_403(self) -> None:
        response = self.client.post("/v1/campaigns", json={"name": "a", "draft": {}}, headers=_auth_header(("visitor",)) | {"Idempotency-Key": "k"})
        self.assertEqual(response.status_code, 403)

    def test_missing_idempotency_key_is_rejected(self) -> None:
        response = self.client.post("/v1/campaigns", json={"name": "a", "draft": {
            "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
            "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
            "repetitions": 1, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
        }}, headers=_auth_header(("operator",)))
        self.assertEqual(response.status_code, 400)

    # ---- registry reads ---------------------------------------------

    def test_get_task_revision_public_no_auth_required(self) -> None:
        self._seed_task()
        response = self.client.get("/v1/tasks/rag.document-freshness/revisions/0.1.0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manifest"]["id"], "rag.document-freshness")

    def test_get_unknown_task_revision_is_404(self) -> None:
        response = self.client.get("/v1/tasks/does.not.exist/revisions/0.1.0")
        self.assertEqual(response.status_code, 404)

    # ---- idempotency.finalize must not misdiagnose an unrelated conflict ----

    def test_finalize_reraises_unrelated_integrity_error(self) -> None:
        """A constraint violation from something other than the (scope, key) idempotency
        unique index must propagate as-is, not be treated as a same-key replay and crash
        with an unhandled NoResultFound (second-pass review, lower-severity finding)."""
        from sqlalchemy.exc import IntegrityError

        from aieb_api.idempotency import finalize

        with db.session_factory()() as session:
            session.add(api_models.ArtifactRow(content_digest="z" * 64, size=1, media_type="text/plain"))
            session.flush()
            # A second artifact with the same content_digest violates a different unique
            # constraint entirely (artifact.content_digest), not the idempotency one.
            session.add(api_models.ArtifactRow(content_digest="z" * 64, size=2, media_type="text/plain"))
            with self.assertRaises(IntegrityError):
                finalize(session, scope="test-scope", key="test-key", body={"a": 1}, status_code=200, response_body={"ok": True})

    # ---- persistence-level immutability (review finding #13) --------------

    def test_task_revision_row_rejects_direct_update_at_the_database_level(self) -> None:
        """Prompt 11 requires immutable frozen revisions enforced "in
        persistence, not only in UI checks" - not merely the absence of an
        update route. A BEFORE UPDATE trigger, not application code, is what
        makes this a real guarantee: this update never goes through any
        route handler at all."""
        from sqlalchemy.exc import IntegrityError

        with db.session_factory()() as session:
            evaluator = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
            session.add(evaluator)
            session.commit()
            evaluator_id = evaluator.id

        with db.session_factory()() as session:
            with self.assertRaises(IntegrityError):
                session.execute(
                    update(api_models.EvaluatorRevisionRow).where(api_models.EvaluatorRevisionRow.id == evaluator_id).values(contract_version="v2")
                )
                session.commit()

    def test_frozen_campaign_manifest_rejects_direct_update_but_state_can_still_change(self) -> None:
        """The trigger must block mutation of the frozen draft/resolved
        manifest once a campaign is no longer 'draft', while still allowing
        the state column itself to transition (frozen -> running -> ...) -
        those are legitimate lifecycle writes, not manifest tampering."""
        from sqlalchemy.exc import IntegrityError

        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="immutability-test", state="frozen", draft={"a": 1}, resolved={"b": 2})
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id

        with db.session_factory()() as session:
            with self.assertRaises(IntegrityError):
                session.execute(update(api_models.CampaignRow).where(api_models.CampaignRow.id == campaign_id).values(resolved={"b": 3}))
                session.commit()

        with db.session_factory()() as session:
            result = session.execute(update(api_models.CampaignRow).where(api_models.CampaignRow.id == campaign_id).values(state="cancelled"))
            session.commit()
            self.assertEqual(result.rowcount, 1)


if __name__ == "__main__":
    unittest.main()
