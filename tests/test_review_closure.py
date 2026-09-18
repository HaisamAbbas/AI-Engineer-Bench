"""Focused review acceptance on a disposable PostgreSQL database."""
from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

from tests import test_api_service as fixtures


@unittest.skipUnless(fixtures.DATABASE_URL, "AIEB_DATABASE_URL is required")
class ReviewClosureTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ApiServiceTests.setUpClass.__func__)
    setUp = fixtures.ApiServiceTests.setUp
    _draft_body = fixtures.ApiServiceTests._draft_body
    _seed_evaluator = fixtures.ApiServiceTests._seed_evaluator
    _registry_payload = staticmethod(fixtures.ApiServiceTests._registry_payload)
    _seed_frozen_campaign_for_aggregation = fixtures.ApiServiceTests._seed_frozen_campaign_for_aggregation
    _seed_publication = fixtures.ApiServiceTests._seed_publication
    _analysis_snapshot = staticmethod(fixtures.ApiServiceTests._analysis_snapshot)

    def test_non_ranked_release_is_not_selected_by_default(self):
        from aieb_api import db, models
        from aieb_api.snapshots import snapshot_digest
        ranked = self._seed_publication(self._analysis_snapshot({}))
        with db.session_factory()() as session:
            original = session.get(models.PublicationRow, ranked)
            snapshot = {**original.snapshot, "complete_for_rank": False}
            other = models.PublicationRow(campaign_id=original.campaign_id, snapshot=snapshot,
                snapshot_digest=snapshot_digest(snapshot), reviewer_id=original.reviewer_id, status="published", publication_class="non_ranked")
            session.add(other)
            session.flush()
            other_id = str(other.id)
            session.commit()
        response = self.client.get("/v1/releases")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["id"] for row in response.json()["items"]], [str(ranked)])
        self.assertEqual(self.client.get(f"/v1/publications/{other_id}/results").status_code, 200)

    def test_publication_provenance_and_withdrawal_are_immutable(self):
        from aieb_api import db, models
        from sqlalchemy import update
        from sqlalchemy.exc import IntegrityError
        publication_id = self._seed_publication(self._analysis_snapshot({}))
        with db.session_factory()() as session:
            row = session.get(models.PublicationRow, publication_id)
            timestamp = row.created_at
            another_campaign = models.CampaignRow(name="other", state="draft", draft={})
            session.add(another_campaign)
            session.flush()
            campaign_id = another_campaign.id
            session.commit()
        for field, value in {
            "publication_class": "non_ranked", "campaign_id": campaign_id,
            "created_at": timestamp + timedelta(days=1), "reason": "rewritten",
            "review_kind": "independent", "supersedes_id": uuid.uuid4(),
        }.items():
            with self.subTest(field=field), db.session_factory()() as session:
                with self.assertRaises(IntegrityError):
                    session.execute(update(models.PublicationRow).where(models.PublicationRow.id == publication_id).values(**{field: value}))
                    session.flush()
                session.rollback()
        with db.session_factory()() as session:
            session.execute(update(models.PublicationRow).where(models.PublicationRow.id == publication_id)
                .values(status="withdrawn", withdrawal_reason="Fixture retired"))
            session.commit()
        with db.session_factory()() as session:
            with self.assertRaises(IntegrityError):
                session.execute(update(models.PublicationRow).where(models.PublicationRow.id == publication_id).values(withdrawal_reason="rewrite"))
                session.commit()
        with db.session_factory()() as session:
            with self.assertRaises(IntegrityError):
                session.execute(update(models.PublicationRow).where(models.PublicationRow.id == publication_id).values(status="published"))
                session.commit()

    def test_ranked_prepare_rejects_false_completeness_even_with_all_trials(self):
        from aieb_api.routes import publications
        campaign = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True)
        original = publications._prepared_data
        def incomplete(*args, **kwargs):
            snapshot, manifest = original(*args, **kwargs)
            return {**snapshot, "complete_for_rank": False}, manifest
        with patch.object(publications, "_prepared_data", side_effect=incomplete):
            response = self.client.post(f"/v1/campaigns/{campaign}/publications/prepare",
                headers=fixtures._auth_header(("operator",)) | {"Idempotency-Key": "incomplete"}, json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("complete_for_rank", response.text)

    def test_trace_gate_fails_closed_at_prepare_and_approval(self):
        from aieb_api import db, models
        from sqlalchemy import select
        campaign = self._seed_frozen_campaign_for_aggregation(include_entrant_b_trial=True, campaign_state="draft")
        with db.session_factory()() as session:
            row = session.get(models.CampaignRow, campaign)
            row.resolved = {**row.resolved, "protocol": {**row.resolved["protocol"], "required_trace_coverage": True}}
            row.state = "completed"
            attempts = list(session.scalars(select(models.AttemptRow)))
            for attempt in attempts:
                session.add(models.AttemptEventRow(attempt_id=attempt.id, sequence=1, event_type="phase.started", payload={"phase": "engineering"}))
            session.commit()
        headers = fixtures._auth_header(("operator",)) | {"Idempotency-Key": "trace"}
        url = f"/v1/campaigns/{campaign}/publications/prepare"
        response = self.client.post(url, headers=headers, json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("verification", response.text)
        with db.session_factory()() as session:
            for attempt in session.scalars(select(models.AttemptRow)):
                session.add(models.AttemptEventRow(attempt_id=attempt.id, sequence=2, event_type="phase.started", payload={"phase": "verification"}))
            session.commit()
        response = self.client.post(url, headers=headers, json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("action", response.text)
        from aieb_api.routes import publications
        # Seed a legacy preparation as if the former phase-only gate accepted
        # it, then prove the real approval gate rejects it without a bypass.
        with patch.object(publications, "_publication_eligibility_error", return_value=None):
            prepared = self.client.post(url, headers=headers, json={})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        denied = self.client.post(f"/v1/publications/preparations/{prepared.json()['id']}/review",
            headers=fixtures._auth_header(("reviewer",), subject="independent") | {"Idempotency-Key": "trace-review"},
            json={"decision": "approve", "independence_attestation": True})
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn("action", denied.text)

    def test_create_replay_is_scoped_to_server_principal(self):
        body = self._draft_body()
        responses = []
        for subject in ("operator-a", "operator-b"):
            headers = fixtures._auth_header(("operator",), subject=subject) | {"Idempotency-Key": "same-key"}
            response = self.client.post("/v1/campaigns", headers=headers, json=body)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.client.post("/v1/campaigns", headers=headers, json=body).json(), response.json())
            responses.append(response.json())
        self.assertNotEqual(responses[0]["id"], responses[1]["id"])
