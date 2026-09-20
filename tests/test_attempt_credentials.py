"""ENG-020 (spec section 37): per-attempt, per-role scoped credentials.

Requires AIEB_DATABASE_URL; skipped (not faked against sqlite) when unset.
These tests need the new `attempt_credential` migrations applied, so setUpClass
upgrades the disposable schema to head first - like test_api_migrations but
without destroying the schema afterward (downgrade is covered there).

Codex-audit follow-up coverage (Prompt-15 continuation), finding 3: issuance is
lease-fenced, so every credential is attributed to (and only usable by) the work-item
lease that minted it - stale workers are refused, and the reconciler's lease sweep
revokes a crashed worker's credentials so they DIE on crash, never linger to TTL.
Finding 1 (credential-authorized candidate capability), finding 4 (unique seed
identities), and finding 5 (no expiry leak on invalid token; malformed UUID -> 404)
are covered here as well.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")
API_DIR = ROOT / "services/api"


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AIEB_DATABASE_URL"] = DATABASE_URL or ""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True,
    )


@dataclass(frozen=True)
class _SeedBundle:
    attempt_id: uuid.UUID
    work_item_id: uuid.UUID
    work_type: str
    worker_id: str
    generation: int


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-020 real-Postgres credential tests are blocked")
class AttemptCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        upgraded = _alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        from aieb_api import db
        from aieb_api import models as api_models
        from aieb_api.worker import repository

        cls.db = db
        cls.api_models = api_models
        cls.repository = repository
        db.configure(DATABASE_URL)

    def setUp(self) -> None:
        engine = self.db.engine()
        with engine.begin() as connection:
            for table in reversed(self.api_models.Base.metadata.sorted_tables):
                connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
            # The kill_switch and system_fence singletons are seeded once by their migrations,
            # not re-created by this per-test TRUNCATE - reseed them so ENG-020 tests find their rows.
            connection.execute(text("INSERT INTO kill_switch (id, active) VALUES (1, false)"))
            connection.execute(text("INSERT INTO system_fence (id, lease_fence_epoch) VALUES (1, 0)"))
        self.session_factory = self.db.session_factory()

    def _seed_attempt(
        self, *, work_type: str = "engineering", worker_id: str = "worker-a", generation: int = 1,
    ) -> _SeedBundle:
        """Build the full ordered graph the rest of the system assumes - plus a LEASED work
        item of the requested type whose worker/generation identify the credential issuer
        (codex finding 3). Every revision row gets a uuid-derived digest so repeated calls
        never collide on the evaluator/task/entrant unique constraints (codex finding 4 - the
        old fixed digests made a second _seed_attempt() violate uq_evaluator_revision_digest_contract)."""
        uid = uuid.uuid4()
        with self.session_factory() as session:
            evaluator = self.api_models.EvaluatorRevisionRow(code_digest=uid.hex, contract_version="v1")
            session.add(evaluator)
            session.flush()
            task = self.api_models.TaskRevisionRow(
                slug=f"rag.document-freshness-{uid.hex[:8]}", version="0.1.0", family_id="knowledge-service-a", category="rag",
                source_digest=uid.hex, manifest_digest=uid.hex[::-1], evaluator_id=evaluator.id,
                manifest={"schema_version": "aieb.task/v1", "uid": uid.hex},
            )
            entrant = self.api_models.EntrantRevisionRow(
                slug=f"agent-a-{uid.hex[:8]}", version="1.0.0", track="agents", config_digest=uid.hex + "1",
                capabilities=["cpu-fixture-standard-v1"], manifest={"schema_version": "aieb.entrant/v1", "uid": uid.hex},
            )
            session.add_all([task, entrant])
            session.flush()
            campaign = self.api_models.CampaignRow(name="credential-test", state="frozen", draft={"schema_version": "aieb.campaign-draft/v1"})
            session.add(campaign)
            session.flush()
            trial = self.api_models.TrialRow(
                campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
                repetition=1, cell_digest=uid.hex + "2",
            )
            session.add(trial)
            session.flush()
            attempt = self.api_models.AttemptRow(trial_id=trial.id, number=1, phase="queued")
            session.add(attempt)
            session.flush()
            item = self.api_models.WorkItemRow(
                attempt_id=attempt.id, type=work_type, state="leased", worker_id=worker_id,
                lease_expiry=datetime.now(timezone.utc) + timedelta(hours=1), generation=generation,
            )
            session.add(item)
            session.commit()
            return _SeedBundle(
                attempt_id=attempt.id, work_item_id=item.id, work_type=work_type,
                worker_id=worker_id, generation=generation,
            )

    def _add_work_item(self, bundle: _SeedBundle, *, work_type: str, worker_id: str = "worker-a", generation: int = 1) -> _SeedBundle:
        """Add a SECOND leased work item of another type to an already-seeded attempt - tests
        that need BOTH credential roles on ONE attempt (candidate from engineering, verifier
        from verification, per the work-type->role issuance scoping) create the extra item
        here."""
        with self.session_factory() as session:
            item = self.api_models.WorkItemRow(
                attempt_id=bundle.attempt_id, type=work_type, state="leased", worker_id=worker_id,
                lease_expiry=datetime.now(timezone.utc) + timedelta(hours=1), generation=generation,
            )
            session.add(item)
            session.commit()
            return _SeedBundle(
                attempt_id=bundle.attempt_id, work_item_id=item.id, work_type=work_type,
                worker_id=worker_id, generation=generation,
            )

    def _revoke(self, session, bundle: _SeedBundle, *, role: str) -> bool:
        return self.repository.revoke_attempt_credential(
            session, attempt_id=bundle.attempt_id, actor_role=role,
            work_item_id=bundle.work_item_id, worker_id=bundle.worker_id, lease_generation=bundle.generation,
        )

    def _issue(self, session, bundle: _SeedBundle, *, role: str, ttl_seconds: int = 3600):
        return self.repository.issue_attempt_credential(
            session, attempt_id=bundle.attempt_id, actor_role=role,
            work_item_id=bundle.work_item_id, worker_id=bundle.worker_id, lease_generation=bundle.generation,
            ttl_seconds=ttl_seconds,
        )

    def test_issue_verify_status_revoke_lifecycle(self) -> None:
        bundle = self._seed_attempt()
        with self.session_factory() as session:
            issued = self._issue(session, bundle, role="candidate")
            self.assertTrue(issued.token)
            self.assertGreater(issued.expires_at, datetime.now(timezone.utc))
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="candidate", token=issued.token))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="candidate", token=issued.token + "tampered"))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=issued.token))
            status = self.repository.attempt_credential_status(session, attempt_id=bundle.attempt_id, actor_role="candidate")
            self.assertTrue(status.valid)
            self.assertEqual(status.work_item_id, bundle.work_item_id)
            self.assertEqual(status.worker_id, bundle.worker_id)
            self.assertEqual(status.lease_generation, bundle.generation)
            self.assertTrue(self._revoke(session, bundle, role="candidate"))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="candidate", token=issued.token))
            self.assertFalse(self.repository.attempt_credential_status(session, attempt_id=bundle.attempt_id, actor_role="candidate").valid)
            self.assertFalse(self._revoke(session, bundle, role="candidate"))

    def test_reissue_rotates_and_invalidates_previous_token(self) -> None:
        bundle = self._seed_attempt(work_type="verification")
        with self.session_factory() as session:
            first = self._issue(session, bundle, role="verifier")
            second = self._issue(session, bundle, role="verifier")
            self.assertNotEqual(first.token, second.token)
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=second.token))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=first.token))

    def test_expired_credential_is_invalid(self) -> None:
        bundle = self._seed_attempt()
        with self.session_factory() as session:
            issued = self._issue(session, bundle, role="candidate", ttl_seconds=1)
            row = session.execute(
                select(self.api_models.AttemptCredentialRow).where(self.api_models.AttemptCredentialRow.attempt_id == bundle.attempt_id)
            ).scalar_one()
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            session.commit()
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="candidate", token=issued.token))
            self.assertFalse(self.repository.attempt_credential_status(session, attempt_id=bundle.attempt_id, actor_role="candidate").valid)

    def test_credentials_are_role_and_attempt_scoped(self) -> None:
        bundle_a = self._seed_attempt()
        bundle_a_verify = self._add_work_item(bundle_a, work_type="verification")
        bundle_b = self._seed_attempt(worker_id="worker-b")
        with self.session_factory() as session:
            candidate = self._issue(session, bundle_a, role="candidate")
            verifier = self._issue(session, bundle_a_verify, role="verifier")
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=bundle_a.attempt_id, actor_role="candidate", token=candidate.token))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle_a.attempt_id, actor_role="verifier", token=candidate.token))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle_b.attempt_id, actor_role="candidate", token=candidate.token))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle_b.attempt_id, actor_role="verifier", token=verifier.token))

    def test_unknown_actor_role_is_rejected(self) -> None:
        bundle = self._seed_attempt()
        with self.session_factory() as session:
            with self.assertRaises(ValueError):
                self.repository.issue_attempt_credential(
                    session, attempt_id=bundle.attempt_id, actor_role="operator",
                    work_item_id=bundle.work_item_id, worker_id=bundle.worker_id, lease_generation=bundle.generation,
                )
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="operator", token="x"))

    def test_stale_worker_is_fenced_out_of_issuance(self) -> None:
        """Codex finding 3: a worker whose lease died (expired, or taken over by another
        worker bumping the generation) must not be able to issue OR rotate a credential.
        The existing live token stays valid and untouched when a stale issue is refused."""
        bundle = self._seed_attempt(work_type="verification", worker_id="worker-a", generation=1)
        with self.session_factory() as session:
            original = self._issue(session, bundle, role="verifier")

            # Takeover: another worker claims the item, bumping worker AND generation.
            item = session.execute(
                select(self.api_models.WorkItemRow).where(self.api_models.WorkItemRow.id == bundle.work_item_id)
            ).scalar_one()
            item.worker_id = "worker-b"
            item.generation = 2
            session.commit()

            # Stale worker-a/generation-1: refused, and the credential row is untouched.
            with self.assertRaises(self.repository.LeaseFenceError):
                self.repository.issue_attempt_credential(
                    session, attempt_id=bundle.attempt_id, actor_role="verifier",
                    work_item_id=bundle.work_item_id, worker_id="worker-a", lease_generation=1,
                )
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=original.token))

            # Current worker-b/generation-2 may rotate.
            rotated = self.repository.issue_attempt_credential(
                session, attempt_id=bundle.attempt_id, actor_role="verifier",
                work_item_id=bundle.work_item_id, worker_id="worker-b", lease_generation=2,
            )
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=rotated.token))

            # But once ITS lease expires, even the current worker is refused.
            item = session.execute(
                select(self.api_models.WorkItemRow).where(self.api_models.WorkItemRow.id == bundle.work_item_id)
            ).scalar_one()
            item.lease_expiry = datetime.now(timezone.utc) - timedelta(seconds=5)
            session.commit()
            with self.assertRaises(self.repository.LeaseFenceError):
                self.repository.issue_attempt_credential(
                    session, attempt_id=bundle.attempt_id, actor_role="verifier",
                    work_item_id=bundle.work_item_id, worker_id="worker-b", lease_generation=2,
                )

    def test_reconcile_sweep_revokes_credentials_after_crash(self) -> None:
        """Codex finding 3: a crashed worker's tokens must DIE at lease recovery, never
        linger usable for the rest of their TTL. Reconciling the expired item revokes both
        roles in the same locked transaction as the recovery."""
        bundle = self._seed_attempt()
        bundle_verify = self._add_work_item(bundle, work_type="verification")
        with self.session_factory() as session:
            verifier = self._issue(session, bundle_verify, role="verifier")
            candidate = self._issue(session, bundle, role="candidate")
            item = session.execute(
                select(self.api_models.WorkItemRow).where(self.api_models.WorkItemRow.id == bundle.work_item_id)
            ).scalar_one()
            item.lease_expiry = datetime.now(timezone.utc) - timedelta(seconds=5)
            session.commit()

            summary = self.repository.reconcile_expired_leases(session)
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=verifier.token) is False)
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="candidate", token=candidate.token))
            self.assertIn(bundle.attempt_id, summary.orphaned_attempt_ids)

    def test_verify_endpoint_is_token_authenticated_not_operator_authenticated_and_leaks_nothing(self) -> None:
        """The one place a scoped credential proves identity - deliberately NOT requiring an
        operator Authorization header, because the whole point is that the verifier subprocess
        holds nothing but its short-lived attempt credential (identity separation). And a failed
        validation reveals NO metadata: expires_at is None, never the roll's real lifetime
        (codex finding 5)."""
        from fastapi.testclient import TestClient

        from aieb_api import auth
        from aieb_api.app import create_app

        auth._configured = False
        bundle = self._seed_attempt(work_type="verification")
        with self.session_factory() as session:
            issued = self._issue(session, bundle, role="verifier")
        app = create_app()
        self.addCleanup(getattr(app, "shutdown", lambda: None))
        with TestClient(app) as client:
            response = client.post(
                f"/v1/attempts/{bundle.attempt_id}/credentials/verify",
                json={"actor_role": "verifier", "token": issued.token},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["valid"])
            self.assertEqual(response.json()["actor_role"], "verifier")
            self.assertIsNotNone(response.json()["expires_at"])
            wrong = client.post(
                f"/v1/attempts/{bundle.attempt_id}/credentials/verify",
                json={"actor_role": "verifier", "token": "definitely-not-the-token"},
            )
            self.assertEqual(wrong.status_code, 200)
            self.assertFalse(wrong.json()["valid"])
            self.assertIsNone(wrong.json()["expires_at"])

    def test_malformed_attempt_uuid_is_404_not_500(self) -> None:
        """Codex finding 5: a bad UUID is a bad resource reference - 404, never a bare
        unhandled ValueError bubbling out of the endpoint."""
        from fastapi.testclient import TestClient

        from aieb_api import auth
        from aieb_api.app import create_app

        auth._configured = False
        app = create_app()
        self.addCleanup(getattr(app, "shutdown", lambda: None))
        with TestClient(app) as client:
            response = client.post(
                "/v1/attempts/not-a-uuid/credentials/verify",
                json={"actor_role": "verifier", "token": "whatever"},
            )
            self.assertEqual(response.status_code, 404)

    def test_get_candidate_capability_is_credential_authorized(self) -> None:
        """Codex finding 1 (second round): the scoped credential MUST authorize a real,
        narrowly scoped capability - here, reading back THE stored candidate of that one
        attempt, VERIFIER ROLE ONLY, full payload. A missing/invalid credential is 401; a
        valid credential bound to another attempt or another role is 401/403; a verification
        engine that skipped the gate entirely must not be able to fabricate a load (finding 3)."""
        from fastapi.testclient import TestClient

        from aieb_api import auth
        from aieb_api.app import create_app

        auth._configured = False
        bundle = self._seed_attempt(work_type="verification")
        with self.session_factory() as session:
            candidate_row = self.api_models.CandidateRow(
                attempt_id=bundle.attempt_id, tree_digest="6" * 64, manifest_digest="7" * 64,
                validation_status="pending", stored_candidate={"files": {}, "meta": {"attestation": "x"}}, stored_candidate_digest="8" * 64,
            )
            session.add(candidate_row)
            issued = self._issue(session, bundle, role="verifier")
            session.commit()
        app = create_app()
        self.addCleanup(getattr(app, "shutdown", lambda: None))
        with TestClient(app) as client:
            ok = client.get(f"/v1/attempts/{bundle.attempt_id}/candidate", headers={"Authorization": f"Bearer {issued.token}"})
            self.assertEqual(ok.status_code, 200)
            body = ok.json()
            self.assertEqual(body["tree_digest"], "6" * 64)
            self.assertEqual(body["manifest_digest"], "7" * 64)
            self.assertEqual(body["stored_candidate"], {"files": {}, "meta": {"attestation": "x"}})

            no_auth = client.get(f"/v1/attempts/{bundle.attempt_id}/candidate")
            self.assertEqual(no_auth.status_code, 401)
            bad_token = client.get(f"/v1/attempts/{bundle.attempt_id}/candidate", headers={"Authorization": "Bearer garbage"})
            self.assertEqual(bad_token.status_code, 401)

            other = self._seed_attempt(worker_id="worker-c")
            other_role = client.get(f"/v1/attempts/{other.attempt_id}/candidate", headers={"Authorization": f"Bearer {issued.token}"})
            self.assertEqual(other_role.status_code, 401, "a credential for one attempt must not reach another attempt's candidate")

            eng_side = self._add_work_item(bundle, work_type="engineering", worker_id="worker-e")
            with self.session_factory() as session:
                candidate_token = self._issue(session, eng_side, role="candidate")
            wrong_role = client.get(f"/v1/attempts/{bundle.attempt_id}/candidate", headers={"Authorization": f"Bearer {candidate_token.token}"})
            self.assertEqual(wrong_role.status_code, 403, "a valid candidate-role credential for THIS attempt must not open the verifier-only candidate capability")

        with self.session_factory() as session:
            self._revoke(session, bundle, role="verifier")
        with TestClient(app) as client:
            revoked = client.get(f"/v1/attempts/{bundle.attempt_id}/candidate", headers={"Authorization": f"Bearer {issued.token}"})
            self.assertEqual(revoked.status_code, 401)

    def test_issuance_is_fenced_to_the_attempt_of_the_lease(self) -> None:
        """Second review round, finding 1: an engineering worker holding a live lease on
        attempt A must NOT be able to mint a credential that reads attempt B's candidate.
        Issuance fences on the work item's OWN attempt AND on this lease; both must match."""
        bundle_a = self._seed_attempt(worker_id="worker-a")
        bundle_b = self._seed_attempt(worker_id="worker-a")
        eng_verify = self._add_work_item(bundle_a, work_type="verification")
        with self.session_factory() as session:
            # Engineering lease on attempt A can mint candidate for attempt A.
            self._issue(session, bundle_a, role="candidate")
            # The SAME lease identity, pointed at attempt B, is refused: the work item lives
            # on attempt A, so an A/B mismatch of the credential's target attempt is a fence break.
            with self.assertRaises(self.repository.LeaseFenceError):
                self.repository.issue_attempt_credential(
                    session, attempt_id=bundle_b.attempt_id, actor_role="candidate",
                    work_item_id=bundle_a.work_item_id, worker_id="worker-a", lease_generation=1,
                )
            # And the verification work item on attempt A cannot target attempt B either.
            with self.assertRaises(self.repository.LeaseFenceError):
                self.repository.issue_attempt_credential(
                    session, attempt_id=bundle_b.attempt_id, actor_role="verifier",
                    work_item_id=eng_verify.work_item_id, worker_id="worker-a", lease_generation=1,
                )
            # No stray credential rows were minted for attempt B by either refusal.
            for row in session.execute(
                select(self.api_models.AttemptCredentialRow).where(self.api_models.AttemptCredentialRow.attempt_id == bundle_b.attempt_id)
            ).scalars():
                self.fail(f"attempt B received a credential row it must not have: {row!r}")

    def test_issuance_role_is_derived_from_work_item_type(self) -> None:
        """Second review round, finding 1: a credential's role is not caller-supplied - it is
        DERIVED from the type of the leased work item held by the caller. Engineering items
        mint candidate credentials, verification/regrade items mint verifier credentials, and
        anything else mints nothing, so a mixed attempt cannot cross-mint roles."""
        eng = self._seed_attempt()
        ver = self._add_work_item(eng, work_type="verification")
        regrade = self._add_work_item(eng, work_type="regrade")
        unknown = self._add_work_item(eng, work_type="inspection")
        with self.session_factory() as session:
            # Allowed pairings.
            self._issue(session, eng, role="candidate")
            self._issue(session, ver, role="verifier")
            self._issue(session, regrade, role="verifier")
            # Forbidden pairings raise ValueError (unknown/unmatching role), never mint.
            for bundle, role in ((eng, "verifier"), (ver, "candidate"), (regrade, "candidate")):
                with self.assertRaises(ValueError):
                    self.repository.issue_attempt_credential(
                        session, attempt_id=bundle.attempt_id, actor_role=role,
                        work_item_id=bundle.work_item_id, worker_id=bundle.worker_id,
                        lease_generation=bundle.generation,
                    )
            # Unknown work types mint nothing at all, whatever role is asked for.
            for role in ("candidate", "verifier"):
                with self.assertRaises(ValueError):
                    self.repository.issue_attempt_credential(
                        session, attempt_id=unknown.attempt_id, actor_role=role,
                        work_item_id=unknown.work_item_id, worker_id=unknown.worker_id,
                        lease_generation=unknown.generation,
                    )

    def test_stale_finally_revoke_noops_against_rotated_token(self) -> None:
        """Second review round, finding 2 (reviewer controls new_before_stale_revoke /
        new_after_stale_revoke): a stale worker's DELAYED finally-revoke, arriving AFTER a
        takeover rotated the credential, must NOT kill the replacement. Revoke is fenced to
        the exact (work_item, worker, generation) that issued the row."""
        stale = self._seed_attempt(work_type="verification", worker_id="worker-a", generation=1)
        with self.session_factory() as session:
            old_token = self._issue(session, stale, role="verifier")

            # Takeover rotates the credential under worker-b/generation-2.
            item = session.execute(
                select(self.api_models.WorkItemRow).where(self.api_models.WorkItemRow.id == stale.work_item_id)
            ).scalar_one()
            item.worker_id = "worker-b"
            item.generation = 2
            session.commit()
            new_token = self.repository.issue_attempt_credential(
                session, attempt_id=stale.attempt_id, actor_role="verifier",
                work_item_id=stale.work_item_id, worker_id="worker-b", lease_generation=2,
            )
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=stale.attempt_id, actor_role="verifier", token=new_token.token))

            # Stale worker-a's finally-revoke arrives late: NO-OP (False), new token lives.
            self.assertFalse(self._revoke(session, stale, role="verifier"))
            self.assertTrue(self.repository.verify_attempt_credential(session, attempt_id=stale.attempt_id, actor_role="verifier", token=new_token.token))

            # The current owner's own revoke still works.
            self.assertTrue(self.repository.revoke_attempt_credential(
                session, attempt_id=stale.attempt_id, actor_role="verifier",
                work_item_id=stale.work_item_id, worker_id="worker-b", lease_generation=2,
            ))
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=stale.attempt_id, actor_role="verifier", token=new_token.token))

    def test_reconciler_revokes_credentials_after_crashed_regrade(self) -> None:
        """Second review round, finding 2b: a regrade item whose lease expires WITHOUT its
        lease step (the worker crashed mid-verify) has its verifier credential revoked by the
        reconciler sweep just like engineering/verification items."""
        bundle = self._seed_attempt()
        regrade = self._add_work_item(bundle, work_type="regrade", worker_id="worker-crash", generation=1)
        with self.session_factory() as session:
            verifier = self._issue(session, regrade, role="verifier")
            item = session.execute(
                select(self.api_models.WorkItemRow).where(self.api_models.WorkItemRow.id == regrade.work_item_id)
            ).scalar_one()
            item.lease_expiry = datetime.now(timezone.utc) - timedelta(seconds=5)
            session.commit()

            summary = self.repository.reconcile_expired_leases(session)
            self.assertFalse(self.repository.verify_attempt_credential(session, attempt_id=bundle.attempt_id, actor_role="verifier", token=verifier.token))
            self.assertIn(bundle.attempt_id, summary.orphaned_attempt_ids)