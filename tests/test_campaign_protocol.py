from __future__ import annotations

import unittest
from uuid import UUID

from aieb_core.models import ExecutionValidity, Trial, Verdict
from aieb_runner.campaign import (
    CampaignAttempt, FrozenCampaignEvidence, append_attempt,
    canonical_ranking_eligible, selected_attempts,
)


class CampaignProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.trial = Trial(id=UUID(int=1), campaign_digest="a" * 64, task_digest="b" * 64, entrant_digest="c" * 64, cohort_digest="d" * 64, repetition_index=0, order_index=0)
        self.evidence = FrozenCampaignEvidence(schema_version="aieb.campaign-evidence/v2", campaign_id=UUID(int=9), campaign_digest="e" * 64, planned_trials=(self.trial,))

    def test_invalid_replacement_is_retained_but_does_not_score(self) -> None:
        failed = CampaignAttempt(trial_id=self.trial.id, attempt_number=0, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID)
        passed = CampaignAttempt(trial_id=self.trial.id, attempt_number=1, execution_validity=ExecutionValidity.VALID, verdict=Verdict.PASS)
        evidence = append_attempt(append_attempt(self.evidence, failed), passed)
        self.assertEqual(len(evidence.attempts), 2)
        self.assertEqual(selected_attempts(evidence)[self.trial.id].attempt_number, 1)
        self.assertTrue(canonical_ranking_eligible(evidence))

    def test_later_replacement_cannot_improve_an_existing_score(self) -> None:
        passed = CampaignAttempt(trial_id=self.trial.id, attempt_number=0, execution_validity=ExecutionValidity.VALID, verdict=Verdict.FAIL)
        later = CampaignAttempt(trial_id=self.trial.id, attempt_number=1, execution_validity=ExecutionValidity.VALID, verdict=Verdict.PASS)
        evidence = append_attempt(append_attempt(self.evidence, passed), later)
        self.assertEqual(selected_attempts(evidence)[self.trial.id].verdict, Verdict.FAIL)

    def test_incomplete_cohort_is_not_rankable(self) -> None:
        self.assertFalse(canonical_ranking_eligible(self.evidence))
        with self.assertRaises(ValueError):
            CampaignAttempt(trial_id=self.trial.id, attempt_number=0, execution_validity=ExecutionValidity.VALID)

    def test_attempt_outside_frozen_matrix_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            append_attempt(self.evidence, CampaignAttempt(trial_id=UUID(int=2), attempt_number=0, execution_validity=ExecutionValidity.INFRASTRUCTURE_INVALID))


if __name__ == "__main__":
    unittest.main()
