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
    from sqlalchemy import func, select, text, update
    from sqlalchemy.exc import IntegrityError

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
                    session.add(api_models.RoleBinding(user_id=user.id, role=role, scope=auth.GLOBAL_SCOPE))
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
                ticket_text="Repair stale document ingestion. Updated documents must replace old searchable text.",
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
        # The saved draft is retrievable so editors can load it instead of
        # requiring operators to keep their own manifest copy.
        stored = self.client.get(f"/v1/campaigns/{campaign_id}", headers=_auth_header(("operator",)))
        self.assertEqual(stored.status_code, 200, stored.text)
        self.assertEqual(stored.json()["draft"], draft)
        response = self.client.patch(
            f"/v1/campaigns/{campaign_id}", json={"draft": draft}, headers=_auth_header(("operator",)) | {"If-Match": "999", "Idempotency-Key": "patch-stale"}
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
                # aieb.budget/v2: declares the environment upper bound the
                # architecture's upper-bound reservation formula requires.
                "schema_version": "aieb.budget/v2", "id": "budget-a", "engineer_wall_seconds": 1200, "verification_wall_seconds": 300,
                "engineer_cpu": 2, "engineer_memory_mb": 1024,
                "environment_upper_bound_usd": "0.5",
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
        with db.session_factory()() as session:
            protocol = session.execute(
                select(api_models.ProtocolRevisionRow).where(api_models.ProtocolRevisionRow.version == "protocol-a")
            ).scalar_one()
            self.assertEqual(protocol.scoring_digest, "9" * 64)
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
            response = self.client.patch(f"/v1/campaigns/{campaign_id}", json={"draft": variant},
                headers=headers | {"Idempotency-Key": f"patch-race-{repetitions}"})
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

    # ---- ENG-017: campaign administration + budgets --------------------

    def _draft_body(self, *, repetitions: int = 1, name: str = "admin-campaign") -> dict:
        return {
            "name": name,
            "draft": {
                "schema_version": "aieb.campaign-draft/v1", "id": "draft-1", "cohort_id": "cohort-a",
                "task_ids": ["rag.document-freshness"], "entrant_ids": ["agent-a"],
                "repetitions": repetitions, "order_seed": 1, "max_concurrent_trials": 1, "optimistic_revision": 0,
            },
        }

    def _create_and_freeze(self, *, repetitions: int = 1, budget_limits: str | None = None) -> str:
        self._seed_task()
        self._seed_entrant()
        create = self.client.post(
            "/v1/campaigns", json=self._draft_body(repetitions=repetitions),
            headers=_auth_header(("operator",)) | {"Idempotency-Key": f"admin-{uuid.uuid4().hex[:8]}"},
        )
        self.assertEqual(create.status_code, 201, create.text)
        campaign_id = create.json()["id"]
        registry = self._registry_payload()
        if budget_limits is not None:
            registry["budget"]["per_role_budget_usd"] = [
                {"role": role, "limit_usd": budget_limits}
                for role in ("engineer", "dev_application", "verifier_application", "verifier_judge")
            ]
        frozen = self.client.post(
            f"/v1/campaigns/{campaign_id}/freeze", json=registry,
            headers=_auth_header(("operator",)) | {"Idempotency-Key": f"frz-{uuid.uuid4().hex[:8]}"},
        )
        self.assertEqual(frozen.status_code, 200, frozen.text)
        return campaign_id

    def test_matrix_preview_returns_exact_matrix_without_persisting(self) -> None:
        self._seed_task()
        self._seed_entrant()
        create = self.client.post(
            "/v1/campaigns", json=self._draft_body(repetitions=3),
            headers=_auth_header(("operator",)) | {"Idempotency-Key": "prev-1"},
        )
        campaign_id = create.json()["id"]
        response = self.client.post(
            f"/v1/campaigns/{campaign_id}/preview", json=self._registry_payload(), headers=_auth_header(("operator",))
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["trial_count"], 3)
        self.assertEqual(body["cells"], [{"task_id": "rag.document-freshness", "entrant_id": "agent-a", "repetitions": 3}])
        self.assertEqual(body["cohort_id"], "cohort-a")
        with db.session_factory()() as session:
            self.assertEqual(session.execute(select(func.count()).select_from(api_models.TrialRow).where(api_models.TrialRow.campaign_id == uuid.UUID(campaign_id))).scalar_one(), 0)

    def test_start_reserves_budget_enqueues_trials_and_runs(self) -> None:
        campaign_id = self._create_and_freeze(repetitions=2, budget_limits="1.500000")
        response = self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth_header(("operator",)) | {"Idempotency-Key": "start-1"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["campaign"]["state"], "running")
        self.assertEqual(body["reservation"]["status"], "active")
        self.assertEqual(body["reservation"]["enforcement"], "estimated_time_limited")
        # (4 roles x 1.5 + 0.5 environment bound) x 2 trials x (1 + 2 replacements)
        self.assertEqual(body["reservation"]["reserved_usd"], "39.000000")
        with db.session_factory()() as session:
            trials = session.execute(select(func.count()).select_from(api_models.TrialRow).where(api_models.TrialRow.campaign_id == uuid.UUID(campaign_id))).scalar_one()
            self.assertEqual(trials, 2)
        # Idempotent replay.
        replay = self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth_header(("operator",)) | {"Idempotency-Key": "start-1"})
        self.assertEqual(replay.status_code, 200)

    def test_start_requires_a_frozen_campaign(self) -> None:
        self._seed_task()
        self._seed_entrant()
        create = self.client.post("/v1/campaigns", json=self._draft_body(), headers=_auth_header(("operator",)) | {"Idempotency-Key": "start-draft"})
        campaign_id = create.json()["id"]
        response = self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth_header(("operator",)) | {"Idempotency-Key": "s2"})
        self.assertEqual(response.status_code, 409)

    def test_pause_stops_new_dispatch_and_resume_restores_it(self) -> None:
        campaign_id = self._create_and_freeze(repetitions=1)
        operator = _auth_header(("operator",))
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=operator | {"Idempotency-Key": "start-p"})
        paused = self.client.post(f"/v1/campaigns/{campaign_id}/pause", headers=operator | {"Idempotency-Key": "pause-p"})
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["campaign"]["state"], "paused")
        # A retry of the same pause (same key) replays the stored response.
        replay = self.client.post(f"/v1/campaigns/{campaign_id}/pause", headers=operator | {"Idempotency-Key": "pause-p"})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), paused.json())
        from aieb_api.worker import repository
        with db.session_factory()() as session:
            self.assertIsNone(repository.claim_work_item(session, worker_id="w-paused"))
        self.client.post(f"/v1/campaigns/{campaign_id}/resume", headers=operator | {"Idempotency-Key": "resume-p"})
        with db.session_factory()() as session:
            leased = repository.claim_work_item(session, worker_id="w-resumed")
            self.assertIsNotNone(leased)

    def test_cancel_releases_reservation_and_stops_dispatch(self) -> None:
        campaign_id = self._create_and_freeze(repetitions=1)
        operator = _auth_header(("operator",))
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=operator | {"Idempotency-Key": "start-c"})
        cancelled = self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=operator | {"Idempotency-Key": "cancel-c"})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["campaign"]["state"], "cancelling")
        self.assertEqual(cancelled.json()["reservation"]["status"], "active")  # released only after drain
        # Cancellation is replay-safe: the same key returns the stored response
        # rather than re-deriving a new state or a second reservation change.
        replay = self.client.post(f"/v1/campaigns/{campaign_id}/cancel", headers=operator | {"Idempotency-Key": "cancel-c"})
        self.assertEqual(replay.json(), cancelled.json())
        from aieb_api.worker import repository
        with db.session_factory()() as session:
            # A cancelling campaign's ready item is still claimable so the worker
            # can finalize it as `cancelled` (it is NOT engineered) - that is how
            # cancellation drains, not by leaving work stuck.
            self.assertTrue(repository.is_campaign_cancelling(session, uuid.UUID(campaign_id)))

    def test_progress_and_invalid_attempts(self) -> None:
        campaign_id = self._create_and_freeze(repetitions=1)
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth_header(("operator",)) | {"Idempotency-Key": "start-pr"})
        progress = self.client.get(f"/v1/campaigns/{campaign_id}/progress", headers=_auth_header(("operator",)))
        self.assertEqual(progress.status_code, 200, progress.text)
        pbody = progress.json()
        self.assertEqual(pbody["planned_trials"], 1)
        self.assertEqual(pbody["observed_trials"], 1)
        # Force the single attempt to an infrastructure-invalid terminal state.
        with db.session_factory()() as session:
            attempt = session.execute(
                select(api_models.AttemptRow).join(api_models.TrialRow, api_models.TrialRow.id == api_models.AttemptRow.trial_id)
                .where(api_models.TrialRow.campaign_id == uuid.UUID(campaign_id))
            ).scalars().first()
            session.execute(
                update(api_models.AttemptRow).where(api_models.AttemptRow.id == attempt.id)
                .values(phase="terminal", terminal_status="infrastructure_invalid")
            )
            session.commit()
        invalid = self.client.get(f"/v1/campaigns/{campaign_id}/invalid-attempts", headers=_auth_header(("reviewer",)))
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(len(invalid.json()), 1)
        self.assertEqual(invalid.json()[0]["terminal_status"], "infrastructure_invalid")

    def test_campaign_admin_endpoints_require_a_role(self) -> None:
        campaign_id = self._create_and_freeze(repetitions=1)
        for path in (f"/v1/campaigns/{campaign_id}/start", f"/v1/campaigns/{campaign_id}/pause", f"/v1/campaigns/{campaign_id}/cancel"):
            self.assertEqual(self.client.post(path).status_code, 401)
            # A subject with ONLY the visitor role (not the operator that
            # _create_and_freeze grants the default subject) must be forbidden.
            self.assertEqual(self.client.post(path, headers=_auth_header(("visitor",), subject="visitor-only")).status_code, 403)

    def test_a_frozen_plan_cannot_be_edited(self) -> None:
        campaign_id = self._create_and_freeze(repetitions=1)
        response = self.client.patch(
            f"/v1/campaigns/{campaign_id}", json={"draft": self._draft_body()["draft"]},
            headers=_auth_header(("operator",)) | {"If-Match": "1", "Idempotency-Key": "patch-frozen-plan"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("draft", response.json()["error"]["message"])

    def _drive_campaign_to_terminal(self, campaign_id: uuid.UUID, *, resolve: bool, retain_source: bool = False) -> None:
        from aieb_core.models import CandidateManifest
        from aieb_runner.artifacts import StoredCandidate
        from aieb_api.worker.runner_bridge import _serialize_stored_candidate

        with db.session_factory()() as session:
            session.execute(update(api_models.WorkItemRow).where(api_models.WorkItemRow.state == "ready").values(state="done"))
            attempt = session.execute(
                select(api_models.AttemptRow).join(api_models.TrialRow, api_models.TrialRow.id == api_models.AttemptRow.trial_id)
                .where(api_models.TrialRow.campaign_id == campaign_id)
            ).scalars().first()
            if resolve:
                session.execute(update(api_models.AttemptRow).where(api_models.AttemptRow.id == attempt.id).values(phase="terminal", terminal_status="pass"))
                manifest = CandidateManifest(schema_version="aieb.candidate/v1", id=uuid.uuid4(), base_revision_digest="1" * 64, full_tree_hash="2" * 64, files=())
                stored = StoredCandidate(manifest, (), ())
                if retain_source:
                    from aieb_core.models import SubmissionPolicy
                    from aieb_runner.artifacts import collect_candidate
                    from aieb_api.worker.artifact_store import PostgresArtifactStore
                    source = ROOT / "suites/dev/rag.document-freshness/repo"
                    stored = collect_candidate(frozen_source=source, workspace=source,
                        submission=SubmissionPolicy(include=("knowledge_service/**",), protected=("dev_tests/**",), max_artifact_bytes=52428800),
                        base_revision_digest="1" * 64, store=PostgresArtifactStore(db.session_factory()), access_scope=str(attempt.id))
                    manifest = stored.manifest
                candidate = api_models.CandidateRow(attempt_id=attempt.id, tree_digest=manifest.full_tree_hash, manifest_digest=manifest.digest(), validation_status="valid", stored_candidate=_serialize_stored_candidate(stored))
                fixture = api_models.FixtureRevisionRow(digest="4" * 64, visibility="public", family_id="knowledge-service-a")
                task = session.execute(select(api_models.TaskRevisionRow).where(api_models.TaskRevisionRow.slug == "rag.document-freshness")).scalar_one()
                session.add_all([candidate, fixture]); session.flush()
                session.add(api_models.EvaluationRow(candidate_id=candidate.id, evaluator_id=task.evaluator_id, fixture_id=fixture.id, schedule_digest=manifest.digest(), verdict="pass", result={"checks": {"api-ready": True}}))
            else:
                session.execute(update(api_models.AttemptRow).where(api_models.AttemptRow.id == attempt.id).values(phase="terminal", terminal_status="infrastructure_invalid"))
            session.commit()

    def test_campaign_completion_marks_completed_when_all_trials_resolve(self) -> None:
        from aieb_api.worker import repository

        campaign_id = uuid.UUID(self._create_and_freeze(repetitions=1))
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth_header(("operator",)) | {"Idempotency-Key": "cmp-ok"})
        self._drive_campaign_to_terminal(campaign_id, resolve=True)
        with db.session_factory()() as session:
            self.assertTrue(repository.maybe_complete_campaign(session, campaign_id))
        detail = self.client.get(f"/v1/campaigns/{campaign_id}", headers=_auth_header(("operator",)))
        self.assertEqual(detail.json()["campaign"]["state"], "completed")
        self.assertEqual(detail.json()["reservation"]["status"], "consumed")

    def test_campaign_completion_marks_incomplete_when_a_trial_is_invalid(self) -> None:
        from aieb_api.worker import repository

        campaign_id = uuid.UUID(self._create_and_freeze(repetitions=1))
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=_auth_header(("operator",)) | {"Idempotency-Key": "cmp-bad"})
        self._drive_campaign_to_terminal(campaign_id, resolve=False)
        with db.session_factory()() as session:
            self.assertTrue(repository.maybe_complete_campaign(session, campaign_id))
        detail = self.client.get(f"/v1/campaigns/{campaign_id}", headers=_auth_header(("operator",)))
        self.assertEqual(detail.json()["campaign"]["state"], "incomplete")

    def test_publication_prepare_review_publish_withdraw_and_redaction(self) -> None:
        from aieb_api import signing
        from aieb_api.worker import repository

        campaign_id = uuid.UUID(self._create_and_freeze())
        operator = _auth_header(("operator",))
        reviewer = _auth_header(("reviewer",), subject="reviewer-only")
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=operator | {"Idempotency-Key": "pub-start-2"})
        self._drive_campaign_to_terminal(campaign_id, resolve=True)
        with db.session_factory()() as session:
            self.assertTrue(repository.maybe_complete_campaign(session, campaign_id))
        prepared = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=operator | {"Idempotency-Key": "pub-prep-1"}, json={})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        review_url = f"/v1/publications/preparations/{prepared.json()['id']}/review"

        # Read the exact prepared materials before deciding, with identity-aware eligibility.
        detail_url = f"/v1/publications/preparations/{prepared.json()['id']}"
        self.assertEqual(self.client.get(detail_url).status_code, 401)
        detail = self.client.get(detail_url, headers=reviewer)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["preparation"], prepared.json())
        self.assertTrue(detail.json()["can_approve"])
        self.assertIn("selections", detail.json()["evidence_manifest"])
        self.assertTrue(detail.json()["snapshot"])
        own = self.client.get(detail_url, headers=operator)
        self.assertFalse(own.json()["can_approve"])
        self.assertIn("cannot approve", own.json()["approval_blocked_reason"])

        # Rejection keeps the preparation reviewable state honest.
        rejected = self.client.post(review_url, headers=reviewer | {"Idempotency-Key": "pub-rev-reject"},
            json={"decision": "reject", "notes": "not yet"})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["status"], "rejected")
        # review_kind is server-derived, never client-selected: without an
        # independence attestation a distinct-identity review is honestly
        # labelled single_maintainer even for a rejection.
        self.assertEqual(rejected.json()["review_kind"], "single_maintainer")

        prepared = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=operator | {"Idempotency-Key": "pub-prep-2"}, json={})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        approved = self.client.post(
            f"/v1/publications/preparations/{prepared.json()['id']}/review",
            headers=reviewer | {"Idempotency-Key": "pub-rev-approve"},
            json={"decision": "approve", "independence_attestation": True},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        # A distinct identity WITH the attestation is labelled independent, and
        # the label travels into the signed manifest.
        self.assertEqual(approved.json()["review_kind"], "independent")
        publication_id = approved.json()["published_publication_id"]

        signature = self.client.get(f"/v1/publications/{publication_id}/signature")
        self.assertEqual(signature.status_code, 200, signature.text)
        self.assertTrue(signing.verify_manifest(
            signature.json()["signed_manifest"], signature.json()["manifest_signature"], signature.json()["signing_public_key"],
        ))
        tampered = dict(signature.json()["signed_manifest"]); tampered["reviewer_id"] = str(uuid.uuid4())
        self.assertFalse(signing.verify_manifest(
            tampered, signature.json()["manifest_signature"], signature.json()["signing_public_key"],
        ))

        export = self.client.get(f"/v1/publications/{publication_id}/export")
        self.assertEqual(export.status_code, 200, export.text)
        self.assertEqual(len(export.json()["runs"]), 1)
        private_keys = {"source", "diff", "diffs", "engineering_stdout", "engineering_stderr", "diagnostics", "artifact_id", "artifact_ref_id", "file_references"}

        def assert_public(value):
            if isinstance(value, dict):
                self.assertFalse(private_keys.intersection(value))
                if "cost_usd" in value:
                    self.assertIsNone(value["cost_usd"])
                for child in value.values():
                    assert_public(child)
            elif isinstance(value, list):
                for child in value:
                    assert_public(child)

        assert_public(export.json())
        from aieb_api.snapshots import snapshot_digest
        self.assertEqual(snapshot_digest(export.json()["snapshot"]), export.json()["snapshot_digest"])

        withdrawn = self.client.post(
            f"/v1/publications/{publication_id}/withdraw", headers=reviewer | {"Idempotency-Key": "pub-withdraw"},
            json={"reason": "fixture only"},
        )
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertEqual(withdrawn.json()["status"], "withdrawn")
        # The withdrawal reason is recorded SEPARATELY from any correction
        # reason; a withdrawal never rewrites the frozen correction rationale.
        self.assertEqual(withdrawn.json()["withdrawal_reason"], "fixture only")
        # Replaying the withdrawal with the same key returns the stored bundle.
        replay = self.client.post(
            f"/v1/publications/{publication_id}/withdraw", headers=reviewer | {"Idempotency-Key": "pub-withdraw"},
            json={"reason": "fixture only"},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), withdrawn.json())
        results = self.client.get(f"/v1/publications/{publication_id}/results")
        self.assertEqual(results.status_code, 200)
        self.assertEqual(results.json()["status"], "withdrawn")
        self.assertIn("withdrawn", results.json()["notice"])

    def test_publication_prepare_discloses_cost_and_trace_evidence(self) -> None:
        from aieb_api.snapshots import snapshot_digest

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        headers = _auth_header(("operator",))
        with db.session_factory()() as session:
            attempts = session.execute(select(api_models.AttemptRow).join(api_models.TrialRow).where(
                api_models.TrialRow.campaign_id == campaign_id,
            ).order_by(api_models.AttemptRow.id)).scalars().all()
            recorded_id, missing_id = (str(attempt.id) for attempt in attempts)
            request = api_models.UsageRequestRow(
                attempt_id=attempts[0].id, actor_role="engineer", request_id="private-request-identity",
            )
            session.add(request)
            session.flush()
            session.add_all([
                api_models.UsageReceiptRow(usage_request_id=request.id, physical_retry=0, reported_cost_usd="0"),
                api_models.UsageReceiptRow(usage_request_id=request.id, physical_retry=1, estimated_cost_usd="1"),
                api_models.AttemptEventRow(attempt_id=attempts[0].id, sequence=1,
                    event_type="phase.started", payload={"private": "must-not-leak"}),
            ])
            session.commit()
        prepared = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=headers | {"Idempotency-Key": "disclosure-prep-1"}, json={})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        detail = self.client.get(f"/v1/publications/preparations/{prepared.json()['id']}", headers=headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        snapshot = detail.json()["snapshot"]
        self.assertIn("coverage_disclosure", snapshot)
        coverage = snapshot["coverage_disclosure"]
        by_attempt = {item["attempt_id"]: item for item in coverage["attempts"]}
        self.assertEqual(by_attempt[recorded_id]["trace"], "present")
        self.assertEqual(by_attempt[recorded_id]["cost_by_role"]["engineer"], "estimated")
        self.assertEqual(by_attempt[recorded_id]["cost_by_role"]["verifier_judge"], "unknown")
        self.assertEqual(by_attempt[missing_id]["trace"], "missing")
        self.assertEqual(set(by_attempt[missing_id]["cost_by_role"].values()), {"unknown"})
        self.assertFalse(coverage["hard_cost_eligible"])
        self.assertNotIn("private-request-identity", str(coverage))
        self.assertNotIn("must-not-leak", str(coverage))
        self.assertEqual(snapshot_digest(snapshot), prepared.json()["snapshot_digest"])
        # A repeat prepare with a NEW key is deduplicated on the prepared
        # content itself (existing behaviour), while a replay with the SAME key
        # returns the stored response verbatim.
        replay = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=headers | {"Idempotency-Key": "disclosure-prep-2"}, json={})
        self.assertEqual(replay.json()["id"], prepared.json()["id"])
        same_key = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=headers | {"Idempotency-Key": "disclosure-prep-1"}, json={})
        self.assertEqual(same_key.json(), prepared.json())

    def test_publication_prepare_is_idempotent_and_rejects_self_approval(self) -> None:


        from aieb_api.worker import repository

        campaign_id = uuid.UUID(self._create_and_freeze())
        headers = _auth_header(("operator",))
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=headers | {"Idempotency-Key": "pub-start"})
        self._drive_campaign_to_terminal(campaign_id, resolve=True)
        with db.session_factory()() as session:
            self.assertTrue(repository.maybe_complete_campaign(session, campaign_id))
        url = f"/v1/campaigns/{campaign_id}/publications/prepare"
        prepared = self.client.post(url, headers=headers | {"Idempotency-Key": "pub-prep-idem"}, json={})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        replay = self.client.post(url, headers=headers | {"Idempotency-Key": "pub-prep-idem"}, json={})
        self.assertEqual(replay.json(), prepared.json())
        # A mutation without a key is rejected outright, never silently applied.
        self.assertEqual(self.client.post(url, headers=headers, json={}).status_code, 400)
        review_url = f"/v1/publications/preparations/{prepared.json()['id']}/review"
        rejected = self.client.post(review_url, headers=_auth_header(("reviewer",)) | {"Idempotency-Key": "pub-self-approve"},
            json={"decision": "approve"})
        self.assertEqual(rejected.status_code, 403, rejected.text)
        # The client cannot select its own review provenance: review_kind is not
        # an accepted request field at all (strict schema, 422).
        self.assertEqual(self.client.post(review_url, headers=_auth_header(("reviewer",)) | {"Idempotency-Key": "pub-self-kind"},
            json={"decision": "approve", "review_kind": "independent"}).status_code, 422)


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

    def test_public_run_evidence_is_published_only_and_redacts_candidate_material(self) -> None:
        import hashlib
        from aieb_core.models import CandidateFile, CandidateManifest
        from aieb_runner.artifacts import ArtifactReference, BlobRef, CandidateDiff, StoredCandidate
        from aieb_api.worker.runner_bridge import _serialize_stored_candidate

        self._seed_task()
        self._seed_entrant()
        with db.session_factory()() as session:
            task = session.execute(select(api_models.TaskRevisionRow).where(api_models.TaskRevisionRow.slug == "rag.document-freshness")).scalar_one()
            entrant = session.execute(select(api_models.EntrantRevisionRow).where(api_models.EntrantRevisionRow.slug == "agent-a")).scalar_one()
            campaign = api_models.CampaignRow(name="published run", state="completed", draft={"x": 1})
            session.add(campaign)
            session.flush()
            trial = api_models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=0, cell_digest="c" * 64,
            )
            session.add(trial)
            session.flush()
            attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="terminal", terminal_status="pass")
            session.add(attempt)
            session.flush()
            artifact_bytes = b"authorized candidate artifact"
            artifact_blob = BlobRef(hashlib.sha256(artifact_bytes).hexdigest(), len(artifact_bytes))
            artifact_reference = ArtifactReference(
                id=uuid.uuid4(), blob=artifact_blob, access_scope=str(attempt.id), visibility="restricted",
            )
            manifest = CandidateManifest(
                schema_version="aieb.candidate/v1", id=uuid.uuid4(), base_revision_digest="1" * 64,
                full_tree_hash="2" * 64,
                files=(CandidateFile(path="src/app.py", operation="modify", sha256=artifact_blob.sha256, byte_length=len(artifact_bytes)),),
            )
            stored = StoredCandidate(
                manifest, (("src/app.py", artifact_reference),), (CandidateDiff("src/app.py", "modify", "--- a/src/app.py\n+++ b/src/app.py\n"),),
                engineering_stdout="private candidate log", engineering_stderr="private stderr",
            )
            candidate = api_models.CandidateRow(
                attempt_id=attempt.id, tree_digest=manifest.full_tree_hash, manifest_digest=manifest.digest(),
                validation_status="valid", stored_candidate=_serialize_stored_candidate(stored),
            )
            session.add(candidate)
            session.flush()
            session.add_all([
                api_models.WorkerArtifactBlobRow(
                    sha256=artifact_blob.sha256, byte_length=len(artifact_bytes), data=artifact_bytes,
                    retention_class="evidence", staged_until=None,
                ),
                api_models.WorkerArtifactReferenceRow(
                    id=artifact_reference.id, blob_sha256=artifact_blob.sha256, candidate_id=candidate.id,
                    access_scope=str(attempt.id), visibility="restricted",
                ),
            ])
            with self.assertRaises(IntegrityError):
                with session.begin_nested():
                    session.execute(
                        update(api_models.CandidateRow).where(api_models.CandidateRow.id == candidate.id)
                        .values(stored_candidate={"engineering_stdout": "forged"})
                    )
            fixture = api_models.FixtureRevisionRow(digest="f" * 64, visibility="public", family_id=task.family_id)
            session.add(fixture)
            session.flush()
            session.add(api_models.EvaluationRow(
                # This evaluation is the one the immutable publication will
                # select, even after a replacement attempt is recorded. Usage
                # lives IN the evaluation result (the only immutable,
                # digest-bound source public usage is served from) rather than
                # in a mutable usage_receipt row.
                candidate_id=candidate.id, evaluator_id=task.evaluator_id, fixture_id=fixture.id,
                schedule_digest=manifest.digest(), verdict="pass",
                result={
                    "checks": {"api-ready": True, "secret-fixture-check": True},
                    "diagnostics": {"private": "do not publish"},
                    "usage": {"input_tokens": 10, "output_tokens": 3, "cost_usd": "0.02"},
                },
            ))
            session.flush()
            evaluation_row = session.execute(
                select(api_models.EvaluationRow).where(api_models.EvaluationRow.candidate_id == candidate.id)
            ).scalar_one()
            with self.assertRaises(IntegrityError):
                with session.begin_nested():
                    session.execute(
                        update(api_models.EvaluationRow).where(api_models.EvaluationRow.id == evaluation_row.id)
                        .values(result={"checks": {"api-ready": False}})
                    )
            session.add(api_models.UsageRequestRow(actor_role="engineer", request_id="run-evidence-usage", attempt_id=attempt.id))
            session.flush()
            usage_request = session.execute(
                select(api_models.UsageRequestRow).where(api_models.UsageRequestRow.request_id == "run-evidence-usage")
            ).scalar_one()
            session.add(api_models.UsageReceiptRow(
                usage_request_id=usage_request.id, physical_retry=0, reported_cost_usd="0.02",
                input_tokens=10, output_tokens=3,
            ))
            from aieb_api.worker.repository import append_attempt_event
            append_attempt_event(session, attempt_id=attempt.id, event_type="phase.started", payload={"phase": "engineering"})
            session.flush()
            selected_evaluation = session.execute(
                select(api_models.EvaluationRow).where(api_models.EvaluationRow.candidate_id == candidate.id)
            ).scalar_one()
            selected_evaluation_id = selected_evaluation.id
            replacement = api_models.AttemptRow(trial_id=trial.id, number=2, phase="terminal", terminal_status="infrastructure_invalid")
            session.add(replacement)
            session.flush()
            append_attempt_event(session, attempt_id=replacement.id, event_type="phase.started", payload={"phase": "verification"})
            replacement_artifact_reference = ArtifactReference(
                id=uuid.uuid4(), blob=artifact_blob, access_scope=str(replacement.id), visibility="restricted",
            )
            replacement_stored = StoredCandidate(
                manifest, (("src/app.py", replacement_artifact_reference),),
                (CandidateDiff("src/app.py", "modify", "--- a/src/app.py\n+++ b/src/app.py\n"),),
                engineering_stdout="private candidate log", engineering_stderr="private stderr",
            )
            replacement_candidate = api_models.CandidateRow(
                attempt_id=replacement.id, tree_digest=manifest.full_tree_hash, manifest_digest=manifest.digest(),
                validation_status="valid", stored_candidate=_serialize_stored_candidate(replacement_stored),
            )
            session.add(replacement_candidate)
            session.flush()
            session.add(api_models.WorkerArtifactReferenceRow(
                id=replacement_artifact_reference.id, blob_sha256=artifact_blob.sha256, candidate_id=replacement_candidate.id,
                access_scope=str(replacement.id), visibility="restricted",
            ))
            session.add(api_models.EvaluationRow(
                candidate_id=replacement_candidate.id, evaluator_id=task.evaluator_id, fixture_id=fixture.id,
                schedule_digest="replacement-schedule", verdict="fail", result={"checks": {"api-ready": False}},
            ))
            replacement_usage = api_models.UsageRequestRow(
                actor_role="engineer", request_id="replacement-usage", attempt_id=replacement.id,
            )
            session.add(replacement_usage)
            session.flush()
            session.add(api_models.UsageReceiptRow(
                usage_request_id=replacement_usage.id, physical_retry=0, reported_cost_usd="0.05",
                input_tokens=12, output_tokens=4,
            ))
            excluded_trial = api_models.TrialRow(
                id=uuid.uuid4(), campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=1, cell_digest="e" * 64,
            )
            session.add(excluded_trial)
            campaign_id, trial_id, excluded_trial_id = campaign.id, trial.id, excluded_trial.id
            session.commit()
        publication_id = self._seed_publication(
            self._analysis_snapshot({}), campaign_id=campaign_id,
            selected_evaluations={trial_id: selected_evaluation_id},
        )
        with db.session_factory()() as session:
            with self.assertRaises(IntegrityError):
                with session.begin_nested():
                    session.execute(
                        update(api_models.PublicationRow).where(api_models.PublicationRow.id == publication_id)
                        .values(evidence_manifest={"schema_version": "forged"})
                    )

        public = self.client.get(f"/v1/public/trials/{trial_id}")
        self.assertEqual(public.status_code, 200)
        public_body = public.json()
        self.assertEqual(public_body["publication_id"], str(publication_id))
        self.assertEqual(public_body["attempt"]["number"], 1)
        self.assertEqual(public_body["evaluation_id"], str(selected_evaluation_id))
        self.assertEqual(public_body["verdict"], "pass")
        self.assertEqual(public_body["usage"], {"input_tokens": 10, "output_tokens": 3, "cost_usd": None})
        self.assertEqual(public_body["trace_state"], "partial")
        self.assertEqual(public_body["trace"][0]["event_type"], "phase.started")
        self.assertEqual(public_body["checks"], [{"requirement_id": "api-ready", "passed": True}])
        for private_key in ("diffs", "diagnostics", "engineering_stdout", "engineering_stderr", "artifact_ref_id"):
            self.assertNotIn(private_key, public_body)
        self.assertEqual(self.client.get(f"/v1/public/trials/{excluded_trial_id}").status_code, 404)

        private = self.client.get(f"/v1/trials/{trial_id}", headers=_auth_header(("reviewer",)))
        self.assertEqual(private.status_code, 200)
        private_body = private.json()
        self.assertEqual(private_body["engineering_stdout"], "private candidate log")
        self.assertEqual(private_body["usage"]["input_tokens"], 12)
        self.assertEqual(private_body["usage"]["cost_usd"], "0.050000")
        self.assertEqual(private_body["trace_state"], "partial")
        self.assertIsNone(private_body["diagnostics"])
        self.assertEqual(private_body["evaluation_state"], "invalid")
        self.assertIsNone(private_body["verdict"])
        self.assertIsNone(private_body["checks"])
        self.assertEqual(private_body["diffs"][0]["path"], "src/app.py")
        self.assertEqual(private_body["artifacts"][0]["path"], "src/app.py")
        downloaded = self.client.get(
            f"/v1/trials/{trial_id}/artifacts/{private_body['artifacts'][0]['artifact_ref_id']}/download",
            headers=_auth_header(("reviewer",)),
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, artifact_bytes)
        self.assertEqual(downloaded.headers["x-content-sha256"], artifact_blob.sha256)

        # A real trial without any publication is not a public existence oracle.
        with db.session_factory()() as session:
            private_campaign = api_models.CampaignRow(name="unpublished", state="completed", draft={"x": 2})
            session.add(private_campaign)
            session.flush()
            private_trial = api_models.TrialRow(
                campaign_id=private_campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=0, cell_digest="d" * 64,
            )
            session.add(private_trial)
            session.commit()
            private_trial_id = private_trial.id
        hidden = self.client.get(f"/v1/public/trials/{private_trial_id}")
        self.assertEqual(hidden.status_code, 404)

        legacy_campaign_id = uuid.uuid4()
        legacy_trial_id = uuid.uuid4()
        with db.session_factory()() as session:
            legacy_campaign = api_models.CampaignRow(id=legacy_campaign_id, name="legacy published", state="completed", draft={"x": 3})
            session.add(legacy_campaign)
            session.flush()
            session.add(api_models.TrialRow(
                id=legacy_trial_id, campaign_id=legacy_campaign.id, task_revision_id=task.id,
                entrant_revision_id=entrant.id, repetition=0, cell_digest="f" * 64,
            ))
            session.commit()
        self._seed_publication(self._analysis_snapshot({}), campaign_id=legacy_campaign_id)
        self.assertEqual(self.client.get(f"/v1/public/trials/{legacy_trial_id}").status_code, 404)

    # ---- auth fails closed / role enforcement --------------------------

    def test_unauthenticated_request_to_operator_route_is_401(self) -> None:
        response = self.client.post("/v1/campaigns", json={"name": "a", "draft": {}})
        self.assertEqual(response.status_code, 401)

    def test_scoped_role_binding_does_not_grant_a_global_check(self) -> None:
        """Independent review finding: role_bindings.scope (spec section 30's
        "scoped role") was persisted but never consulted - resolve_roles
        returned every role a user held regardless of scope, so a role bound
        to one campaign/suite would silently satisfy any global require_role
        check too. A binding scoped to something other than auth.GLOBAL_SCOPE
        must not satisfy a check for GLOBAL_SCOPE (every route currently
        implemented requires a global grant, since none are themselves scoped
        to a single resource yet) - but it must still satisfy a check for its
        own, matching scope."""
        subject = "scoped-only-operator"
        with db.session_factory()() as session:
            user = api_models.User(oidc_subject=subject, oidc_issuer="test")
            session.add(user)
            session.flush()
            session.add(api_models.RoleBinding(user_id=user.id, role="operator", scope="campaign-not-this-one"))
            session.commit()
        identity = auth.Identity(subject=subject, issuer="test")
        with db.session_factory()() as session:
            self.assertEqual(auth.resolve_roles(session, identity), ())
            self.assertEqual(auth.resolve_roles(session, identity, scope="campaign-not-this-one"), ("operator",))

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
        self.assertIn("Repair stale document ingestion", response.json()["ticket_text"])
        self.assertEqual(len(response.json()["ticket_digest"]), 64)
        self.assertEqual(len(response.json()["revision_digest"]), 64)

    def test_methodology_revisions_are_public_and_versioned(self) -> None:
        manifest = {
            "schema_version": "aieb.protocol/v1", "id": "protocol-test-v1",
            "scoring_digest": "a" * 64, "max_replacements": 1,
            "required_trace_coverage": True, "hard_cost_ranking": False,
        }
        with db.session_factory()() as session:
            session.add(api_models.ProtocolRevisionRow(
                version=manifest["id"], scoring_digest=manifest["scoring_digest"], manifest=manifest,
            ))
            session.commit()
        listing = self.client.get("/v1/methodology")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["version"], "protocol-test-v1")
        detail = self.client.get("/v1/methodology/protocol-test-v1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["manifest"], manifest)
        self.assertEqual(self.client.get("/v1/methodology/unknown").status_code, 404)

    def test_get_unknown_task_revision_is_404(self) -> None:
        response = self.client.get("/v1/tasks/does.not.exist/revisions/0.1.0")
        self.assertEqual(response.status_code, 404)

    def test_task_ticket_change_without_revision_digest_is_rejected(self) -> None:
        self._seed_task()
        with db.session_factory()() as session:
            session.execute(text("ALTER TABLE task_revision DISABLE TRIGGER task_revision_immutable"))
            session.execute(text("UPDATE task_revision SET ticket_text='changed ticket' WHERE slug='rag.document-freshness'"))
            session.execute(text("ALTER TABLE task_revision ENABLE TRIGGER task_revision_immutable"))
            session.commit()
        response = self.client.get("/v1/tasks/rag.document-freshness/revisions/0.1.0")
        self.assertEqual(response.status_code, 503)

    def test_task_catalog_lists_public_projection_and_filters_by_category(self) -> None:
        """ENG-016: the public task catalog page needs a real listing endpoint,
        not the full manifest - a thin, public-safe projection."""
        self._seed_task()
        response = self.client.get("/v1/tasks")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["slug"], "rag.document-freshness")
        self.assertEqual(items[0]["category"], "rag")
        self.assertEqual(items[0]["activity"], "repair")
        response = self.client.get("/v1/tasks", params={"category": "tool_app"})
        self.assertEqual(response.json()["items"], [])

    # ---- corrections (review finding: no read endpoint existed) --------

    def test_corrections_lists_superseding_and_withdrawn_publications_only(self) -> None:
        from aieb_api.snapshots import snapshot_digest as content_hash

        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="corrections-test", state="frozen", draft={"a": 1})
            session.add(campaign)
            session.flush()
            user = api_models.User(oidc_subject="reviewer-2", oidc_issuer="test")
            session.add(user)
            session.flush()
            snapshot_a = {"per_entrant": {}}
            original = api_models.PublicationRow(
                campaign_id=campaign.id, snapshot_digest=content_hash(snapshot_a), snapshot=snapshot_a,
                reviewer_id=user.id, status="withdrawn", withdrawal_reason="withdrawn after a scoring dispute",
            )
            session.add(original)
            session.flush()
            snapshot_b = {"per_entrant": {"x": 1}}
            superseding = api_models.PublicationRow(
                campaign_id=campaign.id, snapshot_digest=content_hash(snapshot_b), snapshot=snapshot_b,
                reviewer_id=user.id, supersedes_id=original.id, reason="scoring corrected",
                publication_class="non_ranked",
            )
            session.add(superseding)
            # A normal published (never-corrected) publication must NOT show up here.
            snapshot_c = {"per_entrant": {}}
            unrelated = api_models.PublicationRow(
                campaign_id=campaign.id, snapshot_digest=content_hash(snapshot_c), snapshot=snapshot_c,
                reviewer_id=user.id,
            )
            session.add(unrelated)
            session.commit()
            original_id, superseding_id = original.id, superseding.id

        response = self.client.get("/v1/corrections")
        self.assertEqual(response.status_code, 200)
        items = {item["id"]: item for item in response.json()["items"]}
        self.assertEqual(set(items), {str(original_id), str(superseding_id)})
        # Both recorded reasons are public, and they are DISTINCT fields: the
        # withdrawal rationale never overwrote the correction rationale, and
        # the non-ranking class is labelled on the public entry.
        self.assertEqual(items[str(original_id)]["withdrawal_reason"], "withdrawn after a scoring dispute")
        self.assertIsNone(items[str(original_id)]["reason"])
        self.assertEqual(items[str(superseding_id)]["reason"], "scoring corrected")
        self.assertIsNone(items[str(superseding_id)]["withdrawal_reason"])
        self.assertEqual(items[str(superseding_id)]["publication_class"], "non_ranked")
        self.assertEqual(items[str(superseding_id)]["supersedes_id"], str(original_id))

    def test_publication_results_include_supersedes_id(self) -> None:
        from aieb_api.snapshots import snapshot_digest as content_hash

        snapshot = self._analysis_snapshot({})
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="supersedes-test", state="frozen", draft={"a": 1})
            session.add(campaign)
            session.flush()
            user = api_models.User(oidc_subject="reviewer-3", oidc_issuer="test")
            session.add(user)
            session.flush()
            original = api_models.PublicationRow(
                campaign_id=campaign.id, snapshot_digest=content_hash(snapshot), snapshot=snapshot,
                reviewer_id=user.id, status="superseded",
            )
            session.add(original)
            session.flush()
            superseding = api_models.PublicationRow(
                campaign_id=campaign.id, snapshot_digest=content_hash(snapshot), snapshot=snapshot,
                reviewer_id=user.id, supersedes_id=original.id,
            )
            session.add(superseding)
            session.commit()
            original_id, superseding_id = original.id, superseding.id

        response = self.client.get(f"/v1/publications/{superseding_id}/results")
        self.assertEqual(response.json()["supersedes_id"], str(original_id))
        response = self.client.get(f"/v1/publications/{original_id}/results")
        self.assertIsNone(response.json()["supersedes_id"])

    def test_frozen_task_list_comes_from_the_manifest_and_preserves_zero_observation_tasks(self) -> None:
        """Review finding #3: a planned task with zero observations must not
        disappear from the release's frozen task list - the exact case
        incomplete-coverage reporting must preserve. A prior version
        inferred the task list from `snapshot.per_task` keys; this asserts
        it comes from `campaign.resolved["tasks"]` instead, so a task with no
        scored cell at all still appears."""
        resolved = {
            "cohort": {
                "schema_version": "aieb.cohort/v1", "id": "cohort-a", "track": "agents", "suite_id": "suite-a",
                "protocol_id": "protocol-a", "budget_profile_id": "budget-a", "dependency_mode": "fixture",
                "application_model_profile": {"dependency_mode": "fixture", "entrypoint": ["python"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
                "hardware_class": "cpu-fixture-standard-v1", "required_capabilities": ["cpu-fixture-standard-v1"],
            },
            "tasks": [
                {"id": "rag.document-freshness", "version": "0.1.0", "family_id": "knowledge-service-a", "category": "rag"},
                {"id": "rag.zero-observations", "version": "0.1.0", "family_id": "knowledge-service-b", "category": "rag"},
            ],
            "entrants": [{"id": "agent-a", "agent_version": "2.0.0"}],
        }
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(
                name="frozen-manifest-test", state="frozen", draft={"a": 1}, cohort_digest="f" * 64, resolved=resolved,
            )
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
        snapshot = self._analysis_snapshot(
            {"agent-a": 1.0},
            per_task={"rag.document-freshness:agent-a": {"s": 1, "n": 1, "rate": 1.0, "wilson_95": None, "all_k": True, "pass_power_k": 1.0}},
        )
        publication_id = self._seed_publication(snapshot, campaign_id=campaign_id)

        response = self.client.get(f"/v1/publications/{publication_id}/results")
        body = response.json()
        self.assertEqual(body["cohort"], {
            "track": "agents", "suite_id": "suite-a", "protocol_id": "protocol-a",
            "dependency_mode": "fixture", "hardware_class": "cpu-fixture-standard-v1",
            "budget_profile_id": "budget-a",
            "application_model_profile": {
                "dependency_mode": "fixture", "entrypoint": ["python"], "contract_digest": "5" * 64,
                "model_profile_id": "deterministic-rag-fixture-v1",
            },
            "required_capabilities": ["cpu-fixture-standard-v1"],
        })
        task_slugs = {task["slug"] for task in body["frozen_tasks"]}
        self.assertEqual(task_slugs, {"rag.document-freshness", "rag.zero-observations"})

    def test_entrant_results_pin_the_exact_revision_the_publication_actually_used(self) -> None:
        """Review finding #3: an entrant's "results by release" must name the
        EXACT entrant revision that publication's frozen campaign used, not
        whichever revision of the slug happens to be newest right now."""
        resolved = {"entrants": [{"id": "agent-a", "agent_version": "1.0.0"}]}
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="entrant-pin-test", state="frozen", draft={"a": 1}, resolved=resolved)
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
        self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), campaign_id=campaign_id)

        response = self.client.get("/v1/entrants/by-slug/agent-a/results")
        entries = response.json()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entrant_version"], "1.0.0")

    def test_publication_entrant_configuration_is_the_exact_frozen_revision_not_the_newest_one(self) -> None:
        """Review finding #2 (second pass): Compare previously resolved every
        panel's configuration via GET /entrants/by-slug/{slug}, which always
        returns the newest EntrantRevisionRow regardless of which publication
        is actually being shown - a historical or cross-release comparison
        could silently combine one publication's metrics with a DIFFERENT,
        newer entrant revision's model/capabilities. This new endpoint reads
        the frozen configuration directly from campaign.resolved["entrants"],
        which is pinned to this publication forever - even after a newer
        revision of the same slug is later registered."""
        frozen_manifest = {
            "schema_version": "aieb.entrant/v1", "id": "agent-a", "track": "agents", "agent_implementation": "demo",
            "agent_version": "1.0.0", "engineer_model": {"provider_class": "demo", "requested_model": "old-model", "settings_digest": "a" * 64},
            "prompt_digest": "b" * 64, "tools_digest": "c" * 64, "capabilities": ["cpu-fixture-standard-v1"],
            "credential_ref_type": "broker",
        }
        resolved = {"entrants": [frozen_manifest]}
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="entrant-config-pin-test", state="frozen", draft={"a": 1}, resolved=resolved)
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
        publication_id = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), campaign_id=campaign_id)

        # A NEWER revision of the same slug is registered afterward - the
        # publication's own pinned configuration must be unaffected by it.
        with db.session_factory()() as session:
            session.add(api_models.EntrantRevisionRow(
                slug="agent-a", version="2.0.0", track="agents", config_digest="agent-a-v2-digest",
                capabilities=["cpu-fixture-standard-v1"],
                manifest={**frozen_manifest, "agent_version": "2.0.0", "engineer_model": {**frozen_manifest["engineer_model"], "requested_model": "new-model"}},
            ))
            session.commit()

        response = self.client.get(f"/v1/publications/{publication_id}/entrants/agent-a")
        self.assertEqual(response.status_code, 200)
        manifest = response.json()["manifest"]
        self.assertEqual(manifest["agent_version"], "1.0.0")
        self.assertEqual(manifest["engineer_model"]["requested_model"], "old-model")

        response = self.client.get(f"/v1/publications/{publication_id}/entrants/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_frozen_task_count_is_served_separately_and_the_snapshot_is_not_mutated(self) -> None:
        """Review findings #1/#3, third pass: the correct total-task denominator
        (the FROZEN plan size, not the observed-cell count summarize() computes)
        is served as the separate `frozen_tasks` list, NOT by rewriting the
        snapshot at read time. The snapshot is a digest-verified immutable
        artifact; mutating any field of it during a read produces a response
        (and download bundle) whose `snapshot_digest` no longer hashes the
        `snapshot` beside it. This asserts BOTH: the frozen task list has the
        real plan size (2, including a zero-observation task), AND the served
        snapshot's own `per_entrant_total_tasks` is returned exactly as stored
        (1), untouched."""
        resolved = {
            "tasks": [
                {"id": "rag.document-freshness", "version": "0.1.0", "family_id": "knowledge-service-a", "category": "rag"},
                {"id": "rag.zero-observations", "version": "0.1.0", "family_id": "knowledge-service-b", "category": "rag"},
            ],
        }
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="frozen-total-tasks-test", state="frozen", draft={"a": 1}, resolved=resolved)
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
        # summarize() would only ever see "rag.document-freshness" - the one
        # task with an observation - so its own per_entrant_total_tasks is 1.
        snapshot = self._analysis_snapshot(
            {"agent-a": 1.0},
            per_task={"rag.document-freshness:agent-a": {"s": 1, "n": 1, "rate": 1.0, "wilson_95": None, "all_k": True, "pass_power_k": 1.0}},
            per_entrant_total_tasks={"agent-a": 1},
        )
        publication_id = self._seed_publication(snapshot, campaign_id=campaign_id)

        body = self.client.get(f"/v1/publications/{publication_id}/results").json()
        self.assertEqual(len(body["frozen_tasks"]), 2)  # the real frozen plan size, the denominator
        self.assertEqual(body["snapshot"]["per_entrant_total_tasks"], {"agent-a": 1})  # snapshot NOT mutated

    def test_served_snapshot_still_hashes_to_its_recorded_digest(self) -> None:
        """Review finding #1, third pass: the served `snapshot` must hash to
        the `snapshot_digest` served beside it - if any read-time processing
        rewrites the snapshot, the downloaded bundle's digest would no longer
        match its own snapshot, breaking immutable publication provenance.
        Guards against re-introducing snapshot mutation on reads."""
        from aieb_api.snapshots import snapshot_digest

        resolved = {
            "tasks": [{"id": "rag.document-freshness", "version": "0.1.0", "family_id": "knowledge-service-a", "category": "rag"}],
            "entrants": [{"id": "agent-a", "agent_version": "1.0.0"}],
        }
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="digest-integrity-test", state="frozen", draft={"a": 1}, resolved=resolved)
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
        snapshot = self._analysis_snapshot(
            {"agent-a": 1.0},
            per_task={"rag.document-freshness:agent-a": {"s": 1, "n": 1, "rate": 1.0, "wilson_95": None, "all_k": True, "pass_power_k": 1.0}},
            per_entrant_total_tasks={"agent-a": 1},
        )
        publication_id = self._seed_publication(snapshot, campaign_id=campaign_id)

        body = self.client.get(f"/v1/publications/{publication_id}/results").json()
        self.assertEqual(snapshot_digest(body["snapshot"]), body["snapshot_digest"])

    def test_frozen_entrant_with_zero_observations_still_appears_in_the_roster(self) -> None:
        """Review finding #3, third pass: a frozen entrant with ZERO
        observations was previously invisible - absent from `per_entrant`, so
        it had no results-table row at all instead of showing incomplete
        coverage. The frozen entrant roster (`frozen_entrants`, from
        `campaign.resolved["entrants"]`) must include it regardless of whether
        anything was scored for it."""
        resolved = {
            "entrants": [
                {"id": "agent-observed", "agent_version": "1.0.0"},
                {"id": "agent-unobserved", "agent_version": "1.0.0"},
            ],
        }
        with db.session_factory()() as session:
            campaign = api_models.CampaignRow(name="frozen-entrant-roster-test", state="frozen", draft={"a": 1}, resolved=resolved)
            session.add(campaign)
            session.commit()
            campaign_id = campaign.id
        # Only agent-observed is in the snapshot; agent-unobserved has no cell.
        publication_id = self._seed_publication(self._analysis_snapshot({"agent-observed": 1.0}), campaign_id=campaign_id)

        body = self.client.get(f"/v1/publications/{publication_id}/results").json()
        roster = {entrant["slug"] for entrant in body["frozen_entrants"]}
        self.assertEqual(roster, {"agent-observed", "agent-unobserved"})
        self.assertNotIn("agent-unobserved", body["snapshot"]["per_entrant"])  # genuinely unobserved

    def test_per_entrant_total_tasks_is_null_not_zero_for_a_legacy_snapshot(self) -> None:
        """Review finding #1 (second pass): a snapshot published before
        per_entrant_total_tasks existed has no such key in its stored JSON
        at all - this must be reported as unavailable, never silently
        coerced to an empty dict/zero, which a frontend fallback like
        `?? 0` would render as a fabricated "0 total tasks". As of review
        finding #1 (sixth pass), "unavailable" means the key is genuinely
        absent from the response (not present-and-null): serializing a
        filled-in `None` default back out would add a key the legacy
        snapshot's digest never covered - see
        test_served_snapshot_still_hashes_to_its_recorded_digest_for_a_legacy_snapshot
        below."""
        legacy_snapshot = self._analysis_snapshot({"agent-a": 1.0})
        del legacy_snapshot["per_entrant_total_tasks"]
        publication_id = self._seed_publication(legacy_snapshot)

        response = self.client.get(f"/v1/publications/{publication_id}/results")
        self.assertNotIn("per_entrant_total_tasks", response.json()["snapshot"])
        self.assertIn("per_entrant_valid_trials", response.json()["snapshot"])

    def test_served_snapshot_still_hashes_to_its_recorded_digest_for_a_legacy_snapshot(self) -> None:
        """Review finding #1, sixth pass: a snapshot published before the
        `per_entrant_*` fields existed lacks those keys in its stored JSON.
        `AnalysisSnapshot.model_validate()` fills them in with their `None`
        default so the shape validates, but a response that serialized those
        filled-in defaults back out added keys the stored/digested JSON never
        had, so the returned `snapshot` no longer hashed to the
        `snapshot_digest` served beside it - reproduced directly here by
        recomputing the digest from the HTTP response body, the same check
        test_served_snapshot_still_hashes_to_its_recorded_digest makes for a
        current-format snapshot."""
        from aieb_api.snapshots import snapshot_digest

        legacy_snapshot = self._analysis_snapshot({"agent-a": 1.0})
        del legacy_snapshot["per_entrant_total_tasks"]
        del legacy_snapshot["per_entrant_valid_trials"]
        del legacy_snapshot["per_entrant_resolved_tasks"]
        publication_id = self._seed_publication(legacy_snapshot)

        response = self.client.get(f"/v1/publications/{publication_id}/results")
        body = response.json()
        self.assertEqual(snapshot_digest(body["snapshot"]), body["snapshot_digest"])

    def test_served_snapshot_preserves_integer_values_that_pydantic_would_coerce_to_float(self) -> None:
        """Review finding #1, seventh pass: even with every key PRESENT (no
        legacy absence), round-tripping the stored JSONB through
        `AnalysisSnapshot.model_validate()` and Pydantic's own schema-driven
        JSON dump silently coerces a stored JSON integer (e.g. `suite_rate: 1`)
        into a served float (`1.0`) for any field typed `float | None` -
        recomputing the digest from that reserialized body then no longer
        matches `snapshot_digest`, the same failure mode as ENG016-013's
        read-time mutation, recurring through Pydantic's own (de)serializer
        rather than application code. The response must serve `row.snapshot`
        verbatim, bypassing response-model reserialization for this field, so
        an integer stored value is still an integer in the response."""
        from aieb_api.snapshots import snapshot_digest

        snapshot = self._analysis_snapshot({"agent-a": 1}, suite_rate=1, deadline_rate=0)
        publication_id = self._seed_publication(snapshot)

        response = self.client.get(f"/v1/publications/{publication_id}/results")
        body = response.json()
        self.assertEqual(body["snapshot"]["suite_rate"], 1)
        self.assertNotIsInstance(body["snapshot"]["suite_rate"], float)
        self.assertEqual(body["snapshot"]["per_entrant"]["agent-a"], 1)
        self.assertNotIsInstance(body["snapshot"]["per_entrant"]["agent-a"], float)
        self.assertEqual(snapshot_digest(body["snapshot"]), body["snapshot_digest"])

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

    # ---- publication snapshot integrity (review finding #14) ---------

    @staticmethod
    def _analysis_snapshot(per_entrant: dict, *, per_task: dict | None = None, **overrides: object) -> dict:
        """A complete, schema-valid aieb_analysis.metrics.summarize() output
        (services/api/src/aieb_api/schemas.py::AnalysisSnapshot), not the
        loose ad hoc shape earlier tests used - AnalysisSnapshot's `extra:
        forbid` and required fields mean a publication whose stored snapshot
        doesn't actually look like real analysis output now fails to
        validate (finding: "generated types are bypassed for the important
        result contracts") - so tests must seed the real shape too."""
        base = {
            "schema_version": "aieb.analysis/v1",
            "required_repetitions": None,
            "per_task": per_task or {},
            "per_entrant": per_entrant,
            "per_category": None,
            "complete_for_rank": True,
            "suite_rate": None,
            "cost_per_resolution": None,
            "total_campaign_cost_usd": None,
            "verifier_cost_total_usd": None,
            "successful_engineering_median_seconds": None,
            "deadline_rate": None,
            "infrastructure_attrition": None,
            "per_entrant_valid_trials": {},
            "per_entrant_resolved_tasks": {},
            "per_entrant_total_tasks": {},
            "per_entrant_cost_per_resolution": {},
            "per_entrant_verifier_cost_usd": {},
            "per_entrant_median_engineering_seconds": {},
            "per_entrant_deadline_rate": {},
            "per_entrant_infrastructure_attrition": {},
            "limitations": [],
        }
        base.update(overrides)
        return base

    def _seed_publication(
        self, snapshot: dict, *, snapshot_digest: str | None = None, campaign_id: uuid.UUID | None = None,
        selected_evaluations: dict[uuid.UUID, uuid.UUID] | None = None,
    ) -> uuid.UUID:
        from aieb_api.snapshots import snapshot_digest as content_hash
        from aieb_api.publication_evidence import build_evidence_manifest

        with db.session_factory()() as session:
            if campaign_id is None:
                campaign = api_models.CampaignRow(name="pub-test", state="frozen", draft={"a": 1})
                session.add(campaign)
                session.flush()
                campaign_id = campaign.id
            user = api_models.User(oidc_subject=f"reviewer-{uuid.uuid4().hex[:8]}", oidc_issuer="test")
            session.add(user)
            session.flush()
            evidence_manifest = build_evidence_manifest(session, campaign_id, selected_evaluations or {}, snapshot=snapshot) if selected_evaluations is not None else None
            publication = api_models.PublicationRow(
                campaign_id=campaign_id,
                snapshot_digest=snapshot_digest if snapshot_digest is not None else content_hash(snapshot),
                snapshot=snapshot,
                reviewer_id=user.id,
                evidence_manifest=evidence_manifest,
            )
            session.add(publication)
            session.commit()
            return publication.id

    def test_releases_are_ordered_newest_first_across_pages(self) -> None:
        """Review finding: with pagination, selecting the last item of only
        the first page is not the newest release - spec section 4 requires
        the newest non-withdrawn publication by default. Seed enough
        publications to force pagination (limit=1) and confirm the first
        page's only item is the newest, followed by strictly older ones."""
        import time

        created_ids = []
        for _ in range(3):
            created_ids.append(self._seed_publication(self._analysis_snapshot({})))
            time.sleep(0.01)  # ensure distinct created_at ordering, not just insertion order

        response = self.client.get("/v1/releases", params={"limit": 1})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["items"][0]["id"], str(created_ids[-1]))  # newest seeded, first returned
        self.assertIsNotNone(body["next_cursor"])

        second = self.client.get("/v1/releases", params={"limit": 1, "cursor": body["next_cursor"]})
        self.assertEqual(second.json()["items"][0]["id"], str(created_ids[-2]))

    def test_publication_results_are_served_when_snapshot_matches_its_digest(self) -> None:
        snapshot = self._analysis_snapshot({"agent-a": 1.0})
        publication_id = self._seed_publication(snapshot)
        response = self.client.get(f"/v1/publications/{publication_id}/results")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["snapshot"], snapshot)

    def _seed_scored_trial(self, *, terminal_status: str, verdict: str, campaign_state: str = "completed") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """Seed one terminal, valid-candidate trial whose attempt carries the
        given terminal_status and whose single evaluation carries the given
        verdict. Returns (campaign_id, trial_id, evaluation_id) for driving
        build_evidence_manifest directly."""
        from aieb_core.models import CandidateManifest
        from aieb_runner.artifacts import StoredCandidate
        from aieb_api.worker.runner_bridge import _serialize_stored_candidate

        self._seed_task()
        self._seed_entrant()
        with db.session_factory()() as session:
            task = session.execute(select(api_models.TaskRevisionRow).where(api_models.TaskRevisionRow.slug == "rag.document-freshness")).scalar_one()
            entrant = session.execute(select(api_models.EntrantRevisionRow).where(api_models.EntrantRevisionRow.slug == "agent-a")).scalar_one()
            campaign = api_models.CampaignRow(name=f"scored-{uuid.uuid4().hex[:8]}", state=campaign_state, draft={"x": 1})
            session.add(campaign)
            session.flush()
            trial = api_models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id, repetition=0, cell_digest="c" * 64,
            )
            session.add(trial)
            session.flush()
            attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="terminal", terminal_status=terminal_status)
            session.add(attempt)
            session.flush()
            manifest = CandidateManifest(
                schema_version="aieb.candidate/v1", id=uuid.uuid4(), base_revision_digest="1" * 64, full_tree_hash="2" * 64, files=(),
            )
            candidate = api_models.CandidateRow(
                attempt_id=attempt.id, tree_digest=manifest.full_tree_hash, manifest_digest=manifest.digest(),
                validation_status="valid", stored_candidate=_serialize_stored_candidate(StoredCandidate(manifest, (), ())),
            )
            fixture = api_models.FixtureRevisionRow(digest="4" * 64, visibility="public", family_id=task.family_id)
            session.add_all([candidate, fixture])
            session.flush()
            evaluation = api_models.EvaluationRow(
                candidate_id=candidate.id, evaluator_id=task.evaluator_id, fixture_id=fixture.id,
                schedule_digest=manifest.digest(), verdict=verdict, result={"checks": {"api-ready": True}},
            )
            session.add(evaluation)
            session.commit()
            return campaign.id, trial.id, evaluation.id

    def test_build_evidence_manifest_rejects_a_terminal_status_that_disagrees_with_the_selected_verdict(self) -> None:
        """A `fail` attempt paired with a `pass` evaluation is an integrity
        inconsistency, not a valid selection: terminal_status must equal the
        pinned evaluation's own verdict (review finding: terminal status was
        not restricted to the selected verdict)."""
        from aieb_api.publication_evidence import build_evidence_manifest

        campaign_id, trial_id, evaluation_id = self._seed_scored_trial(terminal_status="fail", verdict="pass")
        with db.session_factory()() as session:
            with self.assertRaises(ValueError):
                build_evidence_manifest(session, campaign_id, {trial_id: evaluation_id}, snapshot=self._analysis_snapshot({"agent-a": 1.0}))

    def test_build_evidence_manifest_rejects_a_non_verdict_terminal_status(self) -> None:
        """A non-verdict terminal status (here scorer_error) is never
        publishable even when a scored evaluation exists under it."""
        from aieb_api.publication_evidence import build_evidence_manifest

        campaign_id, trial_id, evaluation_id = self._seed_scored_trial(terminal_status="scorer_error", verdict="pass")
        with db.session_factory()() as session:
            with self.assertRaises(ValueError):
                build_evidence_manifest(session, campaign_id, {trial_id: evaluation_id}, snapshot=self._analysis_snapshot({"agent-a": 1.0}))

    def _seed_frozen_campaign_for_aggregation(self, *, include_entrant_b_trial: bool, campaign_state: str = "completed") -> uuid.UUID:
        """Seed a frozen campaign whose resolved manifest PLANS one task x two
        entrants (agent-a, agent-b), then persist a terminal passing trial for
        agent-a and, only when asked, for agent-b. When agent-b's trial is
        omitted, the (task, agent-b) planned cell is wholly missing from the
        real data - the exact case `aggregate_campaign_snapshot` must detect as
        incomplete coverage by wiring the frozen plan through summarize()."""
        from aieb_core.models import CandidateManifest, EntrantRevision, TaskRevision, Trial
        from aieb_runner.artifacts import StoredCandidate
        from aieb_api.worker.runner_bridge import _serialize_stored_candidate

        task_manifest = {
            "schema_version": "aieb.task/v1", "id": "rag.document-freshness", "version": "0.1.0",
            "family_id": "knowledge-service-a", "category": "rag", "activity": "repair",
            "source": {"repository_digest": "1" * 64, "commit": "synthetic", "license": "Apache-2.0", "provenance_digest": "2" * 64},
            "environment": {"official_image": "registry.example/aieb@sha256:" + "3" * 64, "engineer_cpu": 1, "engineer_memory_mb": 512, "service_topology_digest": "4" * 64, "egress_policy": "none"},
            "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
            "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 1000},
            "requirements": [{"id": "api-ready", "severity": "mandatory", "description": "ready"}],
            "evaluator": {"evaluator_digest": "6" * 64, "development_fixture": "rag01-dev-v1", "official_fixture_ref": "maintainer-only:rag01-v1"},
            "profile_compatibility": ["cohort-a"],
        }

        def _entrant_manifest(slug: str) -> dict:
            return {
                "schema_version": "aieb.entrant/v1", "id": slug, "track": "agents", "agent_implementation": "demo",
                "agent_version": "1.0.0",
                "engineer_model": {"provider_class": "demo", "requested_model": "demo-model", "reported_model": "demo-model", "settings_digest": "a" * 64},
                "prompt_digest": "b" * 64, "tools_digest": "c" * 64, "capabilities": ["cpu-fixture-standard-v1"],
                "credential_ref_type": "broker",
            }

        entrant_a_manifest = _entrant_manifest("agent-a")
        entrant_b_manifest = _entrant_manifest("agent-b")
        task_digest = TaskRevision.model_validate(task_manifest).digest()
        entrant_a_digest = EntrantRevision.model_validate(entrant_a_manifest).digest()
        entrant_b_digest = EntrantRevision.model_validate(entrant_b_manifest).digest()
        frozen_trials = {
            slug: Trial(id=uuid.uuid4(), campaign_digest="a" * 64, cohort_digest="b" * 64,
                        task_digest=task_digest, entrant_digest=digest, repetition_index=0, order_index=index)
            for index, (slug, digest) in enumerate((("agent-a", entrant_a_digest), ("agent-b", entrant_b_digest)))
        }
        resolved = {
            "protocol": self._registry_payload()["protocol"],
            "tasks": [task_manifest], "entrants": [entrant_a_manifest, entrant_b_manifest],
            "trials": [trial.model_dump(mode="json") for trial in frozen_trials.values()],
        }

        with db.session_factory()() as session:
            evaluator_id = self._seed_evaluator(session)
            task = api_models.TaskRevisionRow(
                slug="rag.document-freshness", version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator_id, manifest=task_manifest,
            )
            entrant_a = api_models.EntrantRevisionRow(slug="agent-a", version="1.0.0", track="agents", config_digest="agent-a-digest", capabilities=["cpu-fixture-standard-v1"], manifest=entrant_a_manifest)
            entrant_b = api_models.EntrantRevisionRow(slug="agent-b", version="1.0.0", track="agents", config_digest="agent-b-digest", capabilities=["cpu-fixture-standard-v1"], manifest=entrant_b_manifest)
            fixture = api_models.FixtureRevisionRow(digest="4" * 64, visibility="public", family_id="knowledge-service-a")
            campaign = api_models.CampaignRow(name=f"agg-{uuid.uuid4().hex[:8]}", state=campaign_state, draft={"repetitions": 1}, resolved=resolved)
            session.add_all([task, entrant_a, entrant_b, fixture, campaign])
            session.flush()

            def _persist_pass(entrant: api_models.EntrantRevisionRow, *, scored: bool = True) -> None:
                frozen = frozen_trials[entrant.slug]
                trial = api_models.TrialRow(id=frozen.id, campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id, repetition=frozen.repetition_index, cell_digest=frozen.digest())
                session.add(trial)
                session.flush()
                if not scored:
                    return
                attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="terminal", terminal_status="pass")
                session.add(attempt)
                session.flush()
                manifest = CandidateManifest(schema_version="aieb.candidate/v1", id=uuid.uuid4(), base_revision_digest="1" * 64, full_tree_hash="2" * 64, files=())
                candidate = api_models.CandidateRow(
                    attempt_id=attempt.id, tree_digest=manifest.full_tree_hash, manifest_digest=manifest.digest(),
                    validation_status="valid", stored_candidate=_serialize_stored_candidate(StoredCandidate(manifest, (), ())),
                )
                session.add(candidate)
                session.flush()
                session.add(api_models.EvaluationRow(
                    candidate_id=candidate.id, evaluator_id=evaluator_id, fixture_id=fixture.id,
                    schedule_digest=manifest.digest(), verdict="pass", result={"checks": {"api-ready": True}},
                ))

            _persist_pass(entrant_a)
            _persist_pass(entrant_b, scored=include_entrant_b_trial)
            campaign_id = campaign.id
            session.commit()
        return campaign_id

    def test_aggregate_campaign_snapshot_flags_a_wholly_missing_planned_cell_as_incomplete(self) -> None:
        """ENG-011 integration gate: a frozen (task, agent-b) planned cell with
        zero recorded trials must make the real aggregation report incomplete
        coverage, via planned_cells wired from the campaign's own manifest."""
        from aieb_api.aggregation import aggregate_campaign_snapshot

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=False)
        with db.session_factory()() as session:
            snapshot = aggregate_campaign_snapshot(session, campaign_id)
        self.assertFalse(snapshot["complete_for_rank"])
        self.assertIsNone(snapshot["suite_rate"])  # not ranked when coverage is incomplete
        self.assertEqual(snapshot["per_entrant"].get("agent-a"), 1.0)  # agent-a's real cell is present
        # agent-b's planned cell has no observation at all, so it has no per_task entry.
        self.assertNotIn("rag.document-freshness:agent-b", snapshot["per_task"])

    def test_aggregate_campaign_snapshot_is_complete_and_categorized_when_every_planned_cell_is_present(self) -> None:
        """With both planned cells recorded, real aggregation reports complete
        coverage, a suite rate, and a per-category breakdown sourced from the
        frozen task category - all wired end to end, not supplied by the test."""
        from aieb_api.aggregation import aggregate_campaign_snapshot

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        with db.session_factory()() as session:
            snapshot = aggregate_campaign_snapshot(session, campaign_id)
        self.assertTrue(snapshot["complete_for_rank"])
        self.assertEqual(snapshot["suite_rate"], 1.0)
        self.assertEqual(snapshot["per_entrant"]["agent-a"], 1.0)
        self.assertEqual(snapshot["per_entrant"]["agent-b"], 1.0)
        self.assertIn("rag", snapshot["per_category"])
        self.assertEqual(snapshot["per_category"]["rag"]["agent-a"], 1.0)

    def test_aggregate_campaign_snapshot_rejects_unplanned_passing_trial(self) -> None:
        from aieb_api.aggregation import CampaignNotAggregatable, aggregate_campaign_snapshot, campaign_observations
        from aieb_core.models import TaskRevision, Trial

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        with db.session_factory()() as session:
            self.assertTrue(aggregate_campaign_snapshot(session, campaign_id)["complete_for_rank"])
            original = session.execute(select(api_models.TrialRow).where(api_models.TrialRow.campaign_id == campaign_id)).scalars().first()
            task = session.get(api_models.TaskRevisionRow, original.task_revision_id)
            rogue_manifest = {**task.manifest, "id": "rag.unplanned-task"}
            rogue_task = api_models.TaskRevisionRow(
                slug=rogue_manifest["id"], version=task.version, family_id=task.family_id,
                category=task.category, source_digest=task.source_digest,
                manifest_digest=TaskRevision.model_validate(rogue_manifest).digest(),
                evaluator_id=task.evaluator_id, manifest=rogue_manifest,
            )
            session.add(rogue_task)
            session.flush()
            frozen = session.get(api_models.CampaignRow, campaign_id).resolved["trials"][0]
            rogue_contract = Trial.model_validate({**frozen, "id": str(uuid.uuid4()), "task_digest": rogue_task.manifest_digest})
            rogue = api_models.TrialRow(
                id=rogue_contract.id, campaign_id=campaign_id, task_revision_id=rogue_task.id,
                entrant_revision_id=original.entrant_revision_id, repetition=0, cell_digest=rogue_contract.digest(),
            )
            session.add(rogue)
            session.flush()
            attempt = api_models.AttemptRow(trial_id=rogue.id, number=1, phase="terminal", terminal_status="pass")
            session.add(attempt)
            session.flush()
            candidate = api_models.CandidateRow(attempt_id=attempt.id, tree_digest="9" * 64, manifest_digest="8" * 64, validation_status="valid", stored_candidate={})
            session.add(candidate)
            session.flush()
            evaluation = session.execute(select(api_models.EvaluationRow)).scalars().first()
            session.add(api_models.EvaluationRow(candidate_id=candidate.id, evaluator_id=evaluation.evaluator_id,
                fixture_id=evaluation.fixture_id, schedule_digest=evaluation.schedule_digest, verdict="pass", result={"checks": {"api-ready": True}}))
            session.commit()
            for aggregate in (campaign_observations, aggregate_campaign_snapshot):
                with self.assertRaisesRegex(CampaignNotAggregatable, "missing or extra trial IDs"):
                    aggregate(session, campaign_id)

    def test_aggregate_campaign_snapshot_rejects_missing_or_mismatched_trial_identity(self) -> None:
        from aieb_api.aggregation import CampaignNotAggregatable, aggregate_campaign_snapshot, campaign_observations

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=False)
        with db.session_factory()() as session:
            # agent-b has a planned row but no attempts, so identity changes do
            # not trip attempt foreign keys before reaching the aggregation guard.
            trial = session.execute(select(api_models.TrialRow).join(api_models.EntrantRevisionRow)
                .where(api_models.TrialRow.campaign_id == campaign_id, api_models.EntrantRevisionRow.slug == "agent-b")).scalar_one()
            cases = (("id", uuid.uuid4()), ("repetition", 1), ("cell_digest", "0" * 64), ("missing", None))
            for field, value in cases:
                with self.subTest(field=field), session.begin_nested() as savepoint:
                    try:
                        if field == "missing":
                            session.delete(trial)
                        else:
                            setattr(trial, field, value)
                        session.flush()
                        for aggregate in (campaign_observations, aggregate_campaign_snapshot):
                            with self.assertRaises(CampaignNotAggregatable):
                                aggregate(session, campaign_id)
                    finally:
                        savepoint.rollback()
    def test_aggregate_campaign_snapshot_rejects_duplicate_or_incomplete_frozen_matrix(self) -> None:
        from copy import deepcopy
        from aieb_api.aggregation import CampaignNotAggregatable, aggregate_campaign_snapshot

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True, campaign_state="draft")
        with db.session_factory()() as session:
            campaign = session.get(api_models.CampaignRow, campaign_id)
            original = deepcopy(campaign.resolved)
            for case in ("missing", "empty", "partial", "duplicate_id", "duplicate_cell", "unknown_task", "unknown_entrant"):
                with self.subTest(case=case), session.begin_nested() as savepoint:
                    try:
                        resolved = deepcopy(original)
                        if case == "missing":
                            del resolved["trials"]
                        elif case == "empty":
                            resolved["trials"] = []
                        elif case == "partial":
                            del resolved["trials"][0]["id"]
                        elif case.startswith("duplicate"):
                            duplicate = deepcopy(resolved["trials"][0])
                            if case == "duplicate_cell":
                                duplicate["id"] = str(uuid.uuid4())
                            resolved["trials"].append(duplicate)
                        else:
                            resolved["trials"][0]["task_digest" if case == "unknown_task" else "entrant_digest"] = "0" * 64
                        campaign.resolved = resolved
                        campaign.state = "completed"
                        session.flush()
                        with self.assertRaises(CampaignNotAggregatable):
                            aggregate_campaign_snapshot(session, campaign_id)
                    finally:
                        savepoint.rollback()


    def test_aggregate_campaign_snapshot_unknown_invalid_attempt_cost_is_not_discarded(self) -> None:
        from aieb_api.aggregation import aggregate_campaign_snapshot

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=False)
        with db.session_factory()() as session:
            success = self._aggregation_attempts(session, campaign_id)[0]
            success.number = 2
            session.flush()
            session.add(api_models.AttemptRow(
                trial_id=success.trial_id, number=1, phase="terminal", terminal_status="infrastructure_invalid",
            ))
            for role in ("engineer", "dev_application", "verifier_application"):
                self._aggregation_usage(session, success, role, [("1", None)])
            session.commit()
            snapshot = aggregate_campaign_snapshot(session, campaign_id)
        self.assertIsNone(snapshot["total_campaign_cost_usd"])
        self.assertIsNone(snapshot["verifier_cost_total_usd"])
        self.assertEqual(snapshot["cost_per_resolution"], 2.0)
        self.assertEqual(snapshot["infrastructure_attrition"], 0.5)

    def test_regrade_queue_retains_original_candidate_and_attempt(self) -> None:
        from aieb_api.evidence_integrity import evidence_digest
        from aieb_api.regrading import enqueue_regrade, installed_scoring_bundle, correction_for_attempt

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        with db.session_factory()() as session:
            user = api_models.User(oidc_subject="regrade-reviewer", oidc_issuer="test")
            session.add(user); session.flush()
            campaign = session.get(api_models.CampaignRow, campaign_id)
            original_ids = {a.id for a in self._aggregation_attempts(session, campaign_id)}
            digest = evidence_digest(installed_scoring_bundle(session, campaign_id))
            with self.assertRaisesRegex(ValueError, "installed trusted"):
                enqueue_regrade(session, campaign, scoring_digest="0" * 64, reason="test", user_id=user.id)
            run = enqueue_regrade(session, campaign, scoring_digest=digest, reason="test", user_id=user.id)
            session.commit()
            attempts = self._aggregation_attempts(session, campaign_id)
            self.assertEqual(len(attempts), 4)
            for attempt in attempts:
                if attempt.id in original_ids:
                    self.assertEqual((attempt.phase, attempt.terminal_status), ("terminal", "pass"))
                else:
                    self.assertEqual((attempt.phase, attempt.number), ("queued", 2))
                    self.assertEqual(correction_for_attempt(session, attempt.id).id, run.id)
            items = session.execute(select(api_models.WorkItemRow).where(api_models.WorkItemRow.type == "regrade")).scalars().all()
            self.assertEqual(len(items), 2)
            self.assertTrue(all(item.state == "ready" for item in items))
            self.assertEqual(session.query(api_models.CandidateRow).count(), 4)


    def test_regrade_then_superseding_publication_end_to_end(self) -> None:
        from aieb_api.worker import repository
        from aieb_api.worker.runner_bridge import execute_leased_work

        campaign_id = uuid.UUID(self._create_and_freeze())
        operator = _auth_header(("operator",))
        reviewer = _auth_header(("reviewer",), subject="reviewer-only")
        self.client.post(f"/v1/campaigns/{campaign_id}/start", headers=operator | {"Idempotency-Key": "rg-start"})
        self._drive_campaign_to_terminal(campaign_id, resolve=True, retain_source=True)
        with db.session_factory()() as session:
            from aieb_api.worker.repository import maybe_complete_campaign
            self.assertTrue(maybe_complete_campaign(session, campaign_id))
        first = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=operator | {"Idempotency-Key": "rg-prep-1"}, json={})
        approved = self.client.post(f"/v1/publications/preparations/{first.json()['id']}/review",
            headers=reviewer | {"Idempotency-Key": "rg-rev-1"},
            json={"decision": "approve", "independence_attestation": True})
        original_publication = approved.json()["published_publication_id"]
        self.assertEqual(self.client.get(f"/v1/publications/{original_publication}/results").json()["snapshot"]["suite_rate"], 1.0)

        bundle = self.client.get(f"/v1/campaigns/{campaign_id}/scoring-bundle", headers=reviewer)
        self.assertEqual(bundle.status_code, 200, bundle.text)
        with db.session_factory()() as session:
            frozen = session.get(api_models.CampaignRow, campaign_id).resolved
        registry = {"cohort": frozen["cohort"], "protocol": {**frozen["protocol"], "scoring_digest": bundle.json()["scoring_digest"]}, "budget": frozen["budget"]}
        regrade = self.client.post(f"/v1/campaigns/{campaign_id}/regrade",
            headers=reviewer | {"Idempotency-Key": "rg-regrade"},
            json={"registry": registry, "reason": "staging scoring correction"})
        self.assertEqual(regrade.status_code, 200, regrade.text)
        # Regrade is replay-safe: the same key returns the same correction run
        # and does not enqueue a second round of regrade work.
        regrade_replay = self.client.post(f"/v1/campaigns/{campaign_id}/regrade",
            headers=reviewer | {"Idempotency-Key": "rg-regrade"},
            json={"registry": registry, "reason": "staging scoring correction"})
        self.assertEqual(regrade_replay.json(), regrade.json())
        while True:
            with db.session_factory()() as session:
                leased = repository.claim_work_item(session, worker_id="regrader", work_type="regrade")
            if leased is None:
                break
            result = execute_leased_work(db.session_factory(), leased, worker_id="regrader", work_root=Path(".cache") / "eng011-tests" / uuid.uuid4().hex)
            self.assertEqual(result.verdict, "fail")
        from aieb_api.regrading import complete_correction_runs
        with db.session_factory()() as session:
            complete_correction_runs(session)
        run_id = regrade.json()["id"]
        self.assertEqual(self.client.get(f"/v1/correction-runs/{run_id}", headers=reviewer).json()["status"], "completed")

        superseding = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
            headers=operator | {"Idempotency-Key": "rg-prep-2"},
            json={"supersedes_publication_id": original_publication, "correction_run_id": run_id, "correction_reason": "scoring corrected"})
        self.assertEqual(superseding.status_code, 200, superseding.text)
        # A completed, valid correction run does not excuse a missing rationale.
        for reason in (None, "", " \n\t"):
            reasonless = self.client.post(f"/v1/campaigns/{campaign_id}/publications/prepare",
                headers=operator | {"Idempotency-Key": str(uuid.uuid4())},
                json={"supersedes_publication_id": original_publication, "correction_run_id": run_id,
                      "correction_reason": reason})
            self.assertEqual(reasonless.status_code, 400, reasonless.text)
            self.assertIn("correction reason", reasonless.text)
        approved = self.client.post(f"/v1/publications/preparations/{superseding.json()['id']}/review",
            headers=reviewer | {"Idempotency-Key": "rg-rev-2"},
            json={"decision": "approve", "independence_attestation": True})
        self.assertEqual(approved.status_code, 200, approved.text)
        corrected_publication = approved.json()["published_publication_id"]
        self.assertEqual(self.client.get(f"/v1/publications/{corrected_publication}/results").json()["snapshot"]["suite_rate"], 0.0)
        self.assertEqual(self.client.get(f"/v1/publications/{original_publication}/results").json()["status"], "superseded")
        corrections = self.client.get("/v1/corrections")
        self.assertIn(str(corrected_publication), repr(corrections.json()))

    def test_correction_snapshot_selects_run_without_changing_original(self) -> None:
        from aieb_api.aggregation import aggregate_campaign_snapshot, CampaignNotAggregatable

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        with db.session_factory()() as session:

            original = self._aggregation_attempts(session, campaign_id)[0]
            candidate = session.execute(select(api_models.CandidateRow).where(
                api_models.CandidateRow.attempt_id == original.id,
            )).scalar_one()
            evaluation = session.execute(select(api_models.EvaluationRow).where(
                api_models.EvaluationRow.candidate_id == candidate.id,
            )).scalar_one()
            original_identity = (original.phase, original.terminal_status, candidate.stored_candidate, evaluation.result)
            run = api_models.CorrectionRunRow(
                campaign_id=campaign_id, corrected_evaluator_id=evaluation.evaluator_id,
                corrected_fixture_id=evaluation.fixture_id, scoring_correction_digest="7" * 64,
                status="completed", reason="fixture scoring correction",
            )
            session.add(run)
            corrected = api_models.AttemptRow(trial_id=original.trial_id, number=2, phase="terminal", terminal_status="fail")
            session.add(corrected)
            session.flush()
            corrected_candidate = api_models.CandidateRow(
                attempt_id=corrected.id, tree_digest=candidate.tree_digest, manifest_digest=candidate.manifest_digest,
                stored_candidate=candidate.stored_candidate, validation_status="valid",
            )
            session.add(corrected_candidate)
            session.flush()
            session.add(api_models.EvaluationRow(
                candidate_id=corrected_candidate.id, evaluator_id=evaluation.evaluator_id,
                fixture_id=evaluation.fixture_id, schedule_digest="8" * 64, correction_run_id=run.id,
                verdict="fail", result={"checks": {"api-ready": False}},
            ))
            session.commit()
            baseline = aggregate_campaign_snapshot(session, campaign_id)
            revised = aggregate_campaign_snapshot(session, campaign_id, correction_run_id=run.id)
            self.assertEqual(baseline["suite_rate"], 1.0)
            self.assertEqual(revised["suite_rate"], 0.5)
            self.assertTrue(revised["complete_for_rank"])
            self.assertEqual(aggregate_campaign_snapshot(session, campaign_id), baseline)
            session.refresh(original); session.refresh(candidate); session.refresh(evaluation)
            self.assertEqual((original.phase, original.terminal_status, candidate.stored_candidate, evaluation.result), original_identity)
            for invalid_run in (uuid.uuid4(),):
                with self.assertRaises(CampaignNotAggregatable):
                    aggregate_campaign_snapshot(session, campaign_id, correction_run_id=invalid_run)
            run.status = "running"
            session.commit()
            with self.assertRaises(CampaignNotAggregatable):
                aggregate_campaign_snapshot(session, campaign_id, correction_run_id=run.id)


    def test_aggregate_campaign_snapshot_first_scored_attempt_is_final(self) -> None:
        from aieb_api.aggregation import aggregate_campaign_snapshot, campaign_observations

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=False)
        with db.session_factory()() as session:
            later = self._aggregation_attempts(session, campaign_id)[0]
            later.number = 2
            session.flush()
            original = api_models.AttemptRow(
                trial_id=later.trial_id, number=1, phase="terminal", terminal_status="fail",
            )
            session.add(original)
            session.flush()
            candidate = api_models.CandidateRow(
                attempt_id=original.id, tree_digest="9" * 64, manifest_digest="8" * 64,
                validation_status="valid", stored_candidate={},
            )
            session.add(candidate)
            session.flush()
            evaluation = session.execute(select(api_models.EvaluationRow)).scalar_one()
            session.add(api_models.EvaluationRow(
                candidate_id=candidate.id, evaluator_id=evaluation.evaluator_id,
                fixture_id=evaluation.fixture_id, schedule_digest=evaluation.schedule_digest,
                verdict="fail", result={"checks": {"api-ready": False}},
            ))
            session.commit()
            observations = campaign_observations(session, campaign_id)
            snapshot = aggregate_campaign_snapshot(session, campaign_id)
        self.assertEqual([v.passed for v in observations], [False, None])
        self.assertEqual(snapshot["per_task"]["rag.document-freshness:agent-a"]["n"], 1)
        self.assertEqual(snapshot["per_task"]["rag.document-freshness:agent-a"]["s"], 0)


    def test_aggregate_campaign_snapshot_cost_receipts_preserve_unknown_and_zero(self) -> None:
        from decimal import Decimal
        from aieb_api.aggregation import _role_cost, aggregate_campaign_snapshot

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=False)
        cases = (
            ([(None, None)], None),
            ([("2", None), (None, None)], None),
            ([("0", "9")], Decimal("0")),
            ([(None, "1.25"), ("2", "99")], Decimal("3.25")),
            ([], None),  # request exists but its receipt has not arrived
        )
        with db.session_factory()() as session:
            attempt = self._aggregation_attempts(session, campaign_id)[0]
            for role in ("dev_application", "verifier_application"):
                self._aggregation_usage(session, attempt, role, [("0", None)])
            session.commit()
            for receipts, expected in cases:
                with self.subTest(receipts=receipts), session.begin_nested() as savepoint:
                    try:
                        self._aggregation_usage(session, attempt, "engineer", receipts)
                        cost = _role_cost(session, attempt.id, {"engineer"})
                        self.assertEqual(None if cost is None else Decimal(cost), expected)
                        snapshot = aggregate_campaign_snapshot(session, campaign_id)
                        if expected is None:
                            self.assertIsNone(snapshot["total_campaign_cost_usd"])
                            self.assertIsNone(snapshot["cost_per_resolution"])
                        else:
                            self.assertEqual(snapshot["total_campaign_cost_usd"], float(expected))
                            self.assertEqual(snapshot["cost_per_resolution"], float(expected))
                    finally:
                        savepoint.rollback()
            # A known receipt on one request must not hide a pending second request.
            self._aggregation_usage(session, attempt, "engineer", [("2", None)])
            self._aggregation_usage(session, attempt, "engineer", [])
            self.assertIsNone(_role_cost(session, attempt.id, {"engineer"}))


    def _aggregation_attempts(self, session, campaign_id):
        return session.execute(
            select(api_models.AttemptRow)
            .join(api_models.TrialRow)
            .where(api_models.TrialRow.campaign_id == campaign_id)
            .order_by(api_models.AttemptRow.number)
        ).scalars().all()

    def _aggregation_usage(self, session, attempt, role, receipts):
        request = api_models.UsageRequestRow(
            attempt_id=attempt.id, actor_role=role, request_id=uuid.uuid4().hex,
        )
        session.add(request)
        session.flush()
        for retry, (reported, estimated) in enumerate(receipts):
            session.add(api_models.UsageReceiptRow(
                usage_request_id=request.id, physical_retry=retry,
                reported_cost_usd=reported, estimated_cost_usd=estimated,
            ))
        session.flush()

    def test_aggregate_campaign_snapshot_retains_replacement_attempt_costs_and_attrition(self) -> None:
        from aieb_api.aggregation import aggregate_campaign_snapshot, campaign_observations

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        with db.session_factory()() as session:
            successes = self._aggregation_attempts(session, campaign_id)
            success = successes[0]
            success.number = 2
            session.flush()
            invalid = api_models.AttemptRow(
                trial_id=success.trial_id, number=1, phase="terminal", terminal_status="infrastructure_invalid",
            )
            session.add(invalid)
            session.flush()
            for attempt in successes:
                for role, cost in (("engineer", "1"), ("dev_application", "2"), ("verifier_application", "3")):
                    self._aggregation_usage(session, attempt, role, [(cost, None)])
            for role, cost in (("engineer", "10"), ("dev_application", "20"), ("verifier_judge", "30")):
                self._aggregation_usage(session, invalid, role, [(cost, None)])
            session.commit()
            observations = campaign_observations(session, campaign_id)
            snapshot = aggregate_campaign_snapshot(session, campaign_id)
            entrant = session.get(api_models.EntrantRevisionRow, session.get(api_models.TrialRow, success.trial_id).entrant_revision_id).slug
        self.assertEqual(len(observations), 3)
        self.assertEqual(snapshot["total_campaign_cost_usd"], 72.0)
        self.assertEqual(snapshot["verifier_cost_total_usd"], 36.0)
        self.assertEqual(snapshot["per_entrant_verifier_cost_usd"][entrant], 33.0)
        self.assertAlmostEqual(snapshot["infrastructure_attrition"], 1 / 3)
        self.assertEqual(snapshot["per_entrant_infrastructure_attrition"][entrant], 0.5)
        self.assertEqual(snapshot["cost_per_resolution"], 3.0)
        self.assertTrue(snapshot["complete_for_rank"])
        self.assertEqual(snapshot["per_task"][f"rag.document-freshness:{entrant}"]["n"], 1)

    def test_aggregate_campaign_snapshot_unknown_deadlines_are_unavailable(self) -> None:
        from aieb_api.aggregation import aggregate_campaign_snapshot, campaign_observations

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        with db.session_factory()() as session:
            self.assertTrue(all(v.deadline is None for v in campaign_observations(session, campaign_id)))
            snapshot = aggregate_campaign_snapshot(session, campaign_id)
        self.assertIsNone(snapshot["deadline_rate"])
        self.assertEqual(snapshot["per_entrant_deadline_rate"], {"agent-a": None, "agent-b": None})

    def test_aggregate_campaign_snapshot_rejects_nonvalid_terminal_statuses(self) -> None:
        from aieb_api.aggregation import aggregate_campaign_snapshot, campaign_observations

        campaign_id = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=False)
        with db.session_factory()() as session:
            attempt = self._aggregation_attempts(session, campaign_id)[0]
            for status in ("scorer_error", "infrastructure_invalid", "cancelled", "unrecognized", None):
                with self.subTest(status=status):
                    attempt.terminal_status = status
                    session.flush()
                    observation, = campaign_observations(session, campaign_id)
                    self.assertFalse(observation.execution_valid)
                    self.assertIsNone(observation.passed)  # even with a persisted PASS evaluation
                    snapshot = aggregate_campaign_snapshot(session, campaign_id)
                    self.assertEqual(snapshot["infrastructure_attrition"], 1.0)
                    self.assertFalse(snapshot["complete_for_rank"])
                    self.assertEqual(snapshot["per_entrant_valid_trials"]["agent-a"], 0)


    def test_publication_results_reject_a_snapshot_that_does_not_match_its_recorded_digest(self) -> None:
        # Review finding #14: nothing recomputed snapshot_digest against the
        # stored snapshot JSONB before serving it as canonical public results.
        # Seed a row whose digest was never derived from the snapshot it holds
        # (simulating corruption or a bug elsewhere that wrote a mismatched pair).
        publication_id = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), snapshot_digest="0" * 64)
        response = self.client.get(f"/v1/publications/{publication_id}/results")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")

    def test_publication_results_reject_a_snapshot_that_does_not_match_the_analysis_shape(self) -> None:
        """A digest-matching but structurally-wrong snapshot (e.g. written by
        code that predates a schema change, or a hand-edited row) must also
        be caught - not just a digest mismatch. AnalysisSnapshot's own
        validation is the second, independent check finding #6 asked for."""
        publication_id = self._seed_publication({"per_entrant": {"agent-a": {"rate": "1.0"}}})
        response = self.client.get(f"/v1/publications/{publication_id}/results")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")

    def test_comparison_endpoint_also_rejects_a_mismatched_snapshot(self) -> None:
        publication_id = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), snapshot_digest="0" * 64)
        response = self.client.get(f"/v1/comparisons?publication_id={publication_id}&entrant_ids=agent-a&entrant_ids=agent-b")
        self.assertEqual(response.status_code, 503)

    def test_comparison_within_one_publication_is_always_cohort_comparable_and_returns_paired_task_differences(self) -> None:
        snapshot = self._analysis_snapshot(
            {"agent-a": 1.0, "agent-b": 0.5},
            per_task={
                "task-1:agent-a": {"s": 2, "n": 2, "rate": 1.0, "wilson_95": None, "all_k": True, "pass_power_k": 1.0},
                "task-1:agent-b": {"s": 1, "n": 2, "rate": 0.5, "wilson_95": None, "all_k": False, "pass_power_k": 0.5},
            },
        )
        publication_id = self._seed_publication(snapshot)
        response = self.client.get(f"/v1/comparisons?publication_id={publication_id}&entrant_ids=agent-a&entrant_ids=agent-b")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["cohort_comparable"])
        self.assertIsNone(body["non_comparable_reason"])
        panels = {panel["entrant_id"]: panel for panel in body["entrants"]}
        self.assertTrue(panels["agent-a"]["eligible"])
        self.assertEqual(panels["agent-a"]["aggregate"], 1.0)
        self.assertEqual(panels["agent-a"]["publication_id"], str(publication_id))
        diffs = body["task_rate_deltas"]["agent-a|agent-b"]
        self.assertEqual(diffs, [{"task_id": "task-1", "left_rate": 1.0, "right_rate": 0.5, "difference": 0.5}])

    def test_comparison_across_different_cohorts_is_not_comparable_and_has_no_paired_differences(self) -> None:
        with db.session_factory()() as session:
            campaign_a = api_models.CampaignRow(name="cohort-a", state="frozen", draft={"a": 1}, cohort_digest="c" * 64)
            campaign_b = api_models.CampaignRow(name="cohort-b", state="frozen", draft={"a": 1}, cohort_digest="d" * 64)
            session.add_all([campaign_a, campaign_b])
            session.commit()
            campaign_a_id, campaign_b_id = campaign_a.id, campaign_b.id
        publication_a = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), campaign_id=campaign_a_id)
        publication_b = self._seed_publication(self._analysis_snapshot({"agent-b": 0.5}), campaign_id=campaign_b_id)

        response = self.client.get(
            "/v1/comparisons",
            params={"entrant_ids": ["agent-a", "agent-b"], "entrant_publication_ids": [str(publication_a), str(publication_b)]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["cohort_comparable"])
        self.assertIsNotNone(body["non_comparable_reason"])
        self.assertIsNone(body["task_rate_deltas"])
        # Each entrant still gets its own eligible aggregate - separate panels, never a fabricated winner.
        panels = {panel["entrant_id"]: panel for panel in body["entrants"]}
        self.assertTrue(panels["agent-a"]["eligible"])
        self.assertEqual(panels["agent-a"]["aggregate"], 1.0)
        self.assertTrue(panels["agent-b"]["eligible"])
        self.assertEqual(panels["agent-b"]["aggregate"], 0.5)

    def test_cross_release_comparison_is_never_comparable_even_with_a_matching_cohort_digest(self) -> None:
        """Review finding #1: a prior version of this endpoint treated a
        matching campaign.cohort_digest across two different publications as
        proof the entrants were comparable - but Cohort records the frozen
        track/suite/protocol/budget/hardware identity, not the exact resolved
        task list, entrant revisions, or repetition plan, so a matching
        digest does not prove matching observations. Spec journey 6.1 is
        unconditional anyway: a cross-release comparison always shows
        separate non-comparable panels, never a calculated winner - not
        "unless the cohorts happen to match." """
        with db.session_factory()() as session:
            campaign_a = api_models.CampaignRow(name="same-cohort-a", state="frozen", draft={"a": 1}, cohort_digest="e" * 64)
            campaign_b = api_models.CampaignRow(name="same-cohort-b", state="frozen", draft={"a": 1}, cohort_digest="e" * 64)
            session.add_all([campaign_a, campaign_b])
            session.commit()
            campaign_a_id, campaign_b_id = campaign_a.id, campaign_b.id
        publication_a = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), campaign_id=campaign_a_id)
        publication_b = self._seed_publication(self._analysis_snapshot({"agent-b": 0.5}), campaign_id=campaign_b_id)

        response = self.client.get(
            "/v1/comparisons",
            params={"entrant_ids": ["agent-a", "agent-b"], "entrant_publication_ids": [str(publication_a), str(publication_b)]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["cohort_comparable"])  # matching cohort_digest is NOT sufficient across publications
        self.assertIsNotNone(body["non_comparable_reason"])
        self.assertIsNone(body["task_rate_deltas"])
        panels = {panel["entrant_id"]: panel for panel in body["entrants"]}
        self.assertEqual(panels["agent-a"]["aggregate"], 1.0)
        self.assertEqual(panels["agent-b"]["aggregate"], 0.5)

    def test_comparing_the_same_slug_across_two_releases_keeps_both_panels_distinct(self) -> None:
        """Review finding #4, third pass: the response was a dict keyed by
        slug alone, so selecting the same slug from two releases (the natural
        "did agent-a improve from release 1 to 2?" comparison) silently
        overwrote one entry and both panels rendered the same wrong aggregate.
        The response is now an ordered LIST, one panel per selection, each
        carrying its own publication_id - so agent-a@pub-a (rate 1.0) and
        agent-a@pub-b (rate 0.3) stay two distinct panels with their own real
        results."""
        with db.session_factory()() as session:
            campaign_a = api_models.CampaignRow(name="rel-1", state="frozen", draft={"a": 1}, cohort_digest="a" * 64)
            campaign_b = api_models.CampaignRow(name="rel-2", state="frozen", draft={"a": 1}, cohort_digest="b" * 64)
            session.add_all([campaign_a, campaign_b])
            session.commit()
            campaign_a_id, campaign_b_id = campaign_a.id, campaign_b.id
        publication_a = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}), campaign_id=campaign_a_id)
        publication_b = self._seed_publication(self._analysis_snapshot({"agent-a": 0.3}), campaign_id=campaign_b_id)

        response = self.client.get(
            "/v1/comparisons",
            params={"entrant_ids": ["agent-a", "agent-a"], "entrant_publication_ids": [str(publication_a), str(publication_b)]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["entrants"]), 2)  # two distinct panels, not one overwritten entry
        by_pub = {panel["publication_id"]: panel["aggregate"] for panel in body["entrants"]}
        self.assertEqual(by_pub[str(publication_a)], 1.0)
        self.assertEqual(by_pub[str(publication_b)], 0.3)  # its own real result, not the other panel's

    def test_comparing_the_exact_same_slug_and_publication_twice_is_rejected(self) -> None:
        """Selecting the identical (slug, publication) pair twice is comparing
        something to itself - meaningless, and rejected (review finding #4)."""
        publication_id = self._seed_publication(self._analysis_snapshot({"agent-a": 1.0}))
        response = self.client.get(
            f"/v1/comparisons?publication_id={publication_id}&entrant_ids=agent-a&entrant_ids=agent-a"
        )
        self.assertEqual(response.status_code, 400)

    def test_comparison_entrant_panel_rejects_inconsistent_eligible_reason_combinations(self) -> None:
        """Review finding #2, sixth pass: a flat model with both `aggregate`
        and `reason` optional let an eligible=True panel carry a `reason`,
        or an eligible=False panel carry a fabricated `aggregate` - nothing
        in the contract ruled those combinations out. The discriminated
        union (Literal[True]/Literal[False] tags on `EligibleEntrantPanel`/
        `IneligibleEntrantPanel`) must reject them at validation time."""
        import uuid as uuid_module

        from pydantic import TypeAdapter

        from aieb_api.schemas import ComparisonEntrantPanel

        adapter = TypeAdapter(ComparisonEntrantPanel)
        pub_id = str(uuid_module.uuid4())
        base = {"entrant_id": "a", "publication_id": pub_id, "publication_class": "ranked"}

        # eligible=True is only valid without a `reason` field.
        adapter.validate_python({**base, "eligible": True, "aggregate": 0.5})
        with self.assertRaises(Exception):
            adapter.validate_python({**base, "eligible": True, "aggregate": 0.5, "reason": "not eligible"})
        # eligible=False requires a `reason` and rejects a fabricated aggregate.
        adapter.validate_python({**base, "eligible": False, "reason": "missing"})
        with self.assertRaises(Exception):
            adapter.validate_python({**base, "eligible": False, "aggregate": 0.5})
        # Omitting the publication class must not silently imply ranked.
        with self.assertRaises(Exception):
            adapter.validate_python({"entrant_id": "a", "publication_id": pub_id, "eligible": True, "aggregate": 0.5})

    def test_publication_snapshot_row_rejects_direct_update_at_the_database_level(self) -> None:
        """Mirrors test_task_revision_row_rejects_direct_update_at_the_database_level
        (finding #13) for the publication table (finding #14): the snapshot
        and its digest are fixed at insert time by a trigger, independent of
        whatever the API layer happens to check."""
        from sqlalchemy.exc import IntegrityError

        publication_id = self._seed_publication(self._analysis_snapshot({}))

        with db.session_factory()() as session:
            with self.assertRaises(IntegrityError):
                session.execute(
                    update(api_models.PublicationRow).where(api_models.PublicationRow.id == publication_id).values(snapshot={"tampered": True})
                )
                session.commit()

        with db.session_factory()() as session:
            result = session.execute(update(api_models.PublicationRow).where(api_models.PublicationRow.id == publication_id).values(status="withdrawn"))
            session.commit()
            self.assertEqual(result.rowcount, 1)


if __name__ == "__main__":
    unittest.main()
