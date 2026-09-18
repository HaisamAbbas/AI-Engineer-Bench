"""ENG-021: Test the campaign proposal generation.

Verifies that the proposal document is internally consistent, uses
assumption-based (not pilot-derived) sample sizes, and properly cites
its BLOCKED status.

Run: python -m unittest tests.test_eng021_campaign_proposal -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_campaign_proposal import generate_proposal  # noqa: E402


class CampaignProposalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proposal = generate_proposal()

    def test_label_is_official_public_origin(self) -> None:
        self.assertEqual(self.proposal["label"], "official-public-origin")

    def test_not_released(self) -> None:
        self.assertIn("NOT OFFICIALLY RELEASED", self.proposal["release_status"])
        self.assertIn("BLOCKED", self.proposal["release_status"])

    def test_three_blocks_present(self) -> None:
        gates = {b["gate"] for b in self.proposal["blocks"]}
        for required in ["ENG-001", "ENG-012", "ENG-019", "ENG-020", "independent-review", "python-lint-type-ci"]:
            self.assertIn(required, gates, f"Missing block gate: {required}")

    def test_trial_matrix_180_trials(self) -> None:
        spec = self.proposal["campaign_specification"]
        self.assertEqual(spec["total_trials"], 180)
        self.assertEqual(spec["repetitions"], 5)
        matrix_desc = self.proposal["trial_matrix"]["description"]
        self.assertIn("180 trials", matrix_desc)

    def test_sample_size_not_pilot_derived(self) -> None:
        sm = self.proposal["sample_size_methodology"]
        self.assertIn("REAL PILOT VARIANCE UNAVAILABLE", sm["note_critical"])
        self.assertIn("ASSUMPTION-BASED", sm["note_critical"])

    def test_not_statistically_powered(self) -> None:
        sm = self.proposal["sample_size_methodology"]
        conclusion = sm["conclusion"]
        self.assertIn("NOT statistically powered", conclusion)
        self.assertIn("Real pilot variance is required", conclusion)

    def test_mme_is_0_10(self) -> None:
        self.assertEqual(self.proposal["minimum_meaningful_effect"]["value"], 0.10)

    def test_clustering_reports_three_base_types(self) -> None:
        cluster = self.proposal["statistical_protocol"]["clustering"]
        self.assertEqual(cluster["base_application_types"], ["knowledge-service", "extraction-service", "assistant-service"])
        self.assertEqual(cluster["actual_count"], 3)  # if present
        self.assertIn("exploratory", cluster["caveat"])

    def test_freeze_declarations_complete(self) -> None:
        freeze = self.proposal["freeze_declarations"]
        for field in ["tasks", "entrants", "repetitions", "dependency_mode", "budget_profile_id", "protocol_id", "max_replacements", "ordering_rule", "minimum_meaningful_effect"]:
            self.assertIn(field, freeze, f"Missing freeze field: {field}")

    def test_independent_human_approval_required(self) -> None:
        review = self.proposal["review_status"]
        self.assertEqual(review["independent_human_approval"], "REQUIRED before ENG-022; not substituted by this preparation")

    def test_retention_cites_eng011(self) -> None:
        self.assertIn("ENG-011", self.proposal["analysis_plan"]["retention_basis"])

    def test_cost_reservation_is_estimate(self) -> None:
        cost = self.proposal["cost_reservation"]
        self.assertEqual(cost["enforcement"], "estimated_time_limited")
        self.assertEqual(cost["hard_cap_status"], "NOT ENFORCED - no provider reservation integration exists; estimates only")


if __name__ == "__main__":
    unittest.main()
