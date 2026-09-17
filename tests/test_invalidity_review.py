"""Targeted PostgreSQL regression for historical invalidity review."""
import unittest
import uuid
from tests import test_api_service as fixtures


@unittest.skipUnless(fixtures.DATABASE_URL, "requires disposable PostgreSQL")
class InvalidityReviewTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ApiServiceTests.setUpClass.__func__)
    setUp = fixtures.ApiServiceTests.setUp
    _seed_task = fixtures.ApiServiceTests._seed_task
    _seed_entrant = fixtures.ApiServiceTests._seed_entrant
    _seed_evaluator = fixtures.ApiServiceTests._seed_evaluator
    _create_and_freeze = fixtures.ApiServiceTests._create_and_freeze
    _draft_body = fixtures.ApiServiceTests._draft_body
    _registry_payload = staticmethod(fixtures.ApiServiceTests._registry_payload)

    def test_review_is_historical_authorized_and_append_only(self):
        from sqlalchemy import select
        from aieb_api import db, models
        campaign = self._create_and_freeze(repetitions=1)
        operator = fixtures._auth_header(("operator",))
        reviewer = fixtures._auth_header(("reviewer",), subject="invalidity-reviewer")
        self.assertEqual(self.client.post(f"/v1/campaigns/{campaign}/start",
            headers=operator | {"Idempotency-Key": "start"}).status_code, 200)
        with db.session_factory()() as session:
            attempt = session.execute(select(models.AttemptRow)).scalars().one()
            attempt.phase, attempt.terminal_status = "terminal", "infrastructure_invalid"
            attempt_id, trial_id = attempt.id, attempt.trial_id
            session.add(models.AttemptRow(trial_id=trial_id, number=2, phase="terminal", terminal_status="pass"))
            session.commit()
        url = f"/v1/campaigns/{campaign}/invalid-attempts/{attempt_id}"
        evidence = self.client.get(url, headers=operator)
        self.assertEqual(evidence.status_code, 200, evidence.text)
        self.assertEqual(evidence.json()["attempt_number"], 1)
        self.assertFalse(evidence.json()["can_review"])
        body = {"decision": "approve", "rationale": "Confirmed infrastructure outage"}
        self.assertEqual(self.client.post(url + "/reviews", headers=operator, json=body).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 401)
        headers = reviewer | {"Idempotency-Key": "review-1"}
        created = self.client.post(url + "/reviews", headers=headers, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        replay = self.client.post(url + "/reviews", headers=headers, json=body)
        self.assertEqual(replay.json()["id"], created.json()["id"])
        self.assertEqual(self.client.post(url + "/reviews", headers=headers,
            json={"decision": "reject", "rationale": "Different"}).status_code, 409)
        second = self.client.post(url + "/reviews", headers=reviewer | {"Idempotency-Key": "review-2"},
            json={"decision": "reject", "rationale": "Follow-up review disputes classification"})
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(self.client.post(url + "/reviews", headers=headers,
            json={"decision": "approve", "rationale": "  "}).status_code, 422)
        final = self.client.get(url, headers=reviewer).json()
        self.assertEqual([r["decision"] for r in final["reviews"]], ["approve", "reject"])
        self.assertEqual(final["terminal_status"], "infrastructure_invalid")
        self.assertTrue(final["can_review"])
        self.assertEqual(self.client.get(url.replace(campaign, str(uuid.uuid4())), headers=reviewer).status_code, 404)
        with db.session_factory()() as session:
            self.assertEqual(session.get(models.AttemptRow, attempt_id).terminal_status, "infrastructure_invalid")


if __name__ == "__main__":
    unittest.main()
