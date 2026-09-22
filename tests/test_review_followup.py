"""Second-review regressions against disposable PostgreSQL, no SQLite substitute."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
import uuid
from unittest.mock import patch

from tests import test_api_service as fixtures


@unittest.skipUnless(fixtures.DATABASE_URL, "AIEB_DATABASE_URL is required")
class ReviewFollowupTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ApiServiceTests.setUpClass.__func__)
    setUp = fixtures.ApiServiceTests.setUp
    _draft_body = fixtures.ApiServiceTests._draft_body
    _seed_evaluator = fixtures.ApiServiceTests._seed_evaluator
    _registry_payload = staticmethod(fixtures.ApiServiceTests._registry_payload)
    _seed_frozen_campaign_for_aggregation = fixtures.ApiServiceTests._seed_frozen_campaign_for_aggregation
    _seed_task = fixtures.ApiServiceTests._seed_task
    _seed_publication = fixtures.ApiServiceTests._seed_publication
    _analysis_snapshot = staticmethod(fixtures.ApiServiceTests._analysis_snapshot)

    def _prepare(self, campaign):
        response = self.client.post(f"/v1/campaigns/{campaign}/publications/prepare",
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": str(uuid.uuid4())}, json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def _publish(self, campaign):
        preparation = self._prepare(campaign)
        response = self.client.post(f"/v1/publications/preparations/{preparation}/review",
            headers=fixtures._auth_header(("reviewer",), subject="reviewer") | {"Idempotency-Key": str(uuid.uuid4())},
            json={"decision": "approve", "independence_attestation": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["published_publication_id"]

    def test_campaign_creator_cannot_submit_a_publication_rejection(self):
        """Independence applies to every decision, not only approvals."""
        from aieb_api import db, models
        campaign = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        preparation = self._prepare(campaign)
        with db.session_factory()() as session:
            row = session.get(models.CampaignRow, campaign)
            creator = session.get(models.User, row.created_by_user_id)
            creator_subject = creator.oidc_subject
        response = self.client.post(
            f"/v1/publications/preparations/{preparation}/review",
            headers=fixtures._auth_header(("reviewer",), subject=creator_subject)
            | {"Idempotency-Key": "creator-reject"},
            json={"decision": "reject", "independence_attestation": True, "notes": "rejected"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_fixture_admission_provisions_the_database_reviewer_grant(self):
        from aieb_api import db, models
        from sqlalchemy import select
        self._seed_task()
        with db.session_factory()() as session:
            reviewer = session.execute(
                select(models.User).where(
                    models.User.oidc_issuer == "fixture",
                    models.User.oidc_subject == "fixture-reviewer",
                )
            ).scalar_one()
            self.assertIsNotNone(session.execute(
                select(models.RoleBinding).where(
                    models.RoleBinding.user_id == reviewer.id,
                    models.RoleBinding.role == "reviewer",
                    models.RoleBinding.scope == "global",
                )
            ).scalar_one_or_none())

    def test_non_ranked_class_reaches_results_history_and_comparison(self):
        from aieb_api import db, models
        from aieb_api.snapshots import snapshot_digest
        snapshot = self._analysis_snapshot({"agent-a": 1.0, "agent-b": 0.5})
        ranked = self._seed_publication(snapshot)
        with db.session_factory()() as session:
            original = session.get(models.PublicationRow, ranked)
            snapshot = {**snapshot, "complete_for_rank": False}
            row = models.PublicationRow(campaign_id=original.campaign_id, reviewer_id=original.reviewer_id,
                snapshot=snapshot, snapshot_digest=snapshot_digest(snapshot), status="published", publication_class="non_ranked")
            session.add(row)
            session.flush()
            non_ranked = str(row.id)
            session.commit()
        results = self.client.get(f"/v1/publications/{non_ranked}/results").json()
        self.assertEqual(results.get("publication_class"), "non_ranked")
        self.assertIn("non-ranking", results["notice"])
        self.assertEqual(results["snapshot"], snapshot)  # never rewrite the hashed evidence
        history = self.client.get("/v1/entrants/by-slug/agent-a/results").json()
        entry = next(item for item in history if item["publication_id"] == non_ranked)
        self.assertEqual(entry["publication_class"], "non_ranked")
        self.assertIn("non-ranking", entry["notice"])
        for ids in ([non_ranked, non_ranked], [str(ranked), non_ranked]):
            response = self.client.get("/v1/comparisons", params=[("entrant_ids", "agent-a"), ("entrant_ids", "agent-b"),
                *(('entrant_publication_ids', pub) for pub in ids)])
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertFalse(body["cohort_comparable"])
            self.assertIsNone(body["task_rate_deltas"])
            self.assertIn("non-ranking", body["non_comparable_reason"])
            self.assertEqual(body["entrants"][-1]["publication_class"], "non_ranked")

    def test_lifecycle_events_do_not_satisfy_required_trace_coverage(self):
        from aieb_api import db, models
        from sqlalchemy import select
        # See test_review_closure.py::test_trace_gate_fails_closed_at_prepare_and_approval:
        # `resolved`/`state` must both be baked in at seed time now - the
        # V2-GAP-004 DB triggers make a frozen campaign's manifest immutable
        # and reject a direct draft -> completed jump.
        campaign = self._seed_frozen_campaign_for_aggregation(
            include_entrant_b_trial=True, campaign_state="completed",
            protocol_overrides={"required_trace_coverage": True},
        )
        with db.session_factory()() as session:
            for attempt in session.scalars(select(models.AttemptRow)):
                for sequence, phase in enumerate(("engineering", "verification"), 1):
                    session.add(models.AttemptEventRow(attempt_id=attempt.id, sequence=sequence,
                        event_type="phase.started", payload={"phase": phase}))
            session.commit()
        response = self.client.post(f"/v1/campaigns/{campaign}/publications/prepare",
            headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "trace"}, json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("action", response.text)

    def _concurrent_after_cache_miss(self, module, request):
        barrier = Barrier(2)
        original = module.check_or_reserve
        def synchronize(session, **kwargs):
            result = original(session, **kwargs)
            if not session.info.get("cache_checked"):
                session.info["cache_checked"] = True
                self.assertIsNone(result)
                barrier.wait(timeout=5)
            return result
        with patch.object(module, "check_or_reserve", side_effect=synchronize):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(request) for _ in range(2)]
                responses = [future.result(timeout=15) for future in futures]
        self.assertEqual([r.status_code for r in responses], [200, 200], [r.text for r in responses])
        self.assertEqual(responses[0].json(), responses[1].json())
        return responses[0].json()

    def test_concurrent_patch_replays_after_stale_revision(self):
        from aieb_api import db, models
        from aieb_api.routes import campaigns
        headers = fixtures._auth_header(("operator",))
        created = self.client.post("/v1/campaigns", headers=headers | {"Idempotency-Key": "create"}, json=self._draft_body())
        self.assertEqual(created.status_code, 201, created.text)
        campaign = created.json()["id"]
        body = {"draft": self._draft_body(repetitions=2)["draft"]}
        headers |= {"Idempotency-Key": "patch-race", "If-Match": "0"}
        self._concurrent_after_cache_miss(campaigns, lambda: self.client.patch(f"/v1/campaigns/{campaign}", headers=headers, json=body))
        with db.session_factory()() as session:
            self.assertEqual(session.get(models.CampaignRow, uuid.UUID(campaign)).revision, 1)
        changed = {"draft": self._draft_body(repetitions=3)["draft"]}
        self.assertEqual(self.client.patch(f"/v1/campaigns/{campaign}", headers=headers, json=changed).status_code, 409)
        self.assertEqual(self.client.patch(f"/v1/campaigns/{campaign}", headers=headers | {"Idempotency-Key": "new-key"}, json=body).status_code, 412)

    def test_concurrent_review_replays_after_preparation_lock(self):
        from aieb_api import db, models
        from aieb_api.routes import publications
        from sqlalchemy import select, func
        campaign = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        preparation = self._prepare(campaign)
        headers = fixtures._auth_header(("reviewer",), subject="reviewer") | {"Idempotency-Key": "review-race"}
        url = f"/v1/publications/preparations/{preparation}/review"
        self._concurrent_after_cache_miss(publications, lambda: self.client.post(url, headers=headers,
            json={"decision": "approve", "independence_attestation": True}))
        with db.session_factory()() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(models.PublicationRow)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(models.ReviewRow)), 1)
        self.assertEqual(self.client.post(url, headers=headers, json={"decision": "reject"}).status_code, 409)

    def test_superseding_requires_nonblank_correction_reason(self):
        campaign = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        publication = self._publish(campaign)
        # Missing reason must be rejected before doing any correction-run processing.
        for reason in (None, "", " \n\t"):
            response = self.client.post(f"/v1/campaigns/{campaign}/publications/prepare",
                headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": str(uuid.uuid4())},
                json={"supersedes_publication_id": publication, "correction_reason": reason})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("correction reason", response.text)

    def test_repeated_withdrawal_requires_same_recorded_reason(self):
        campaign = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        publication = self._publish(campaign)
        headers = fixtures._auth_header(("reviewer",), subject="reviewer")
        url = f"/v1/publications/{publication}/withdraw"
        for key, reason, status in (("first", "retired", 200), ("same", "retired", 200), ("changed", "different", 409)):
            response = self.client.post(url, headers=headers | {"Idempotency-Key": key}, json={"reason": reason})
            self.assertEqual(response.status_code, status, response.text)
        export = self.client.get(f"/v1/publications/{publication}/export").json()
        self.assertEqual(export["withdrawal_reason"], "retired")
