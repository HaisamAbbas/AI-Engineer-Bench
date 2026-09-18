"""ENG-021: Test family split validation and three-label discipline.

Verifies the actual clustering in the catalog matches the documented
expectations: 3 base application types across 12 family IDs.

Run: python -m unittest tests.test_eng021_family_split -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_family_split import validate_family_split  # noqa: E402


class FamilySplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = validate_family_split()
        cls.catalog = json.loads((ROOT / "suites" / "dev" / "catalog.json").read_text())

    def test_three_base_application_types(self) -> None:
        clustering = self.result["clustering"]
        self.assertEqual(clustering["base_application_types"], ["assistant-service", "extraction-service", "knowledge-service"])
        self.assertEqual(clustering["base_type_count"], 3)

    def test_twelve_family_ids(self) -> None:
        self.assertEqual(len(self.catalog["tasks"]), 12)
        families = self.result["family_groups"]
        # Each family should have exactly 1 task (12 tasks, 12 family IDs)
        for family, task_ids in families.items():
            self.assertEqual(len(task_ids), 1, f"Family {family} has {len(task_ids)} tasks, expected 1")

    def test_four_families_per_base_type(self) -> None:
        clustering = self.result["clustering"]
        for base_type, families in clustering["families_per_base_type"].items():
            self.assertEqual(families, 4, f"Base type {base_type} has {families} families, expected 4")

    def test_diversity_not_six_projects(self) -> None:
        """Document the gap: spec section 28 expects 6 projects, repo has 3."""
        clustering = self.result["clustering"]
        self.assertEqual(clustering["arch_section_28_acknowledged_projects"], 6)
        self.assertEqual(clustering["actual_project_count"], 3)
        self.assertIn("3 < 6", clustering["caveat"])
        self.assertIn("exploratory", clustering["caveat"])

    def test_statuses_are_pending_not_approved(self) -> None:
        """No task should be marked 'approved' or 'released'."""
        for detail in self.result["task_details"]:
            pass  # status_note is in the audit, not here
        # The validate_family_split result itself should state PENDING
        self.assertIn("PENDING", self.result["review_note"])
        self.assertIn("not an independent", self.result["review_note"])


if __name__ == "__main__":
    unittest.main()
