from __future__ import annotations

import unittest
from pathlib import Path

from scripts.admit_task import inspect_task


class TaskAdmissionTest(unittest.TestCase):
    def test_development_task_has_complete_package_and_stays_unreviewed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = inspect_task(root / "suites" / "dev" / "rag.document-freshness")
        self.assertTrue(report["development_only"])
        self.assertEqual(report["independent_review"], "pending")
        checks = report["automated_checks"]
        self.assertTrue(checks["package_shape"] == "pass")
        self.assertTrue(checks["variant_controls_present"])

    def test_admission_report_does_not_claim_human_approval(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = inspect_task(root / "suites" / "dev" / "rag.document-freshness")
        self.assertNotEqual(report["independent_review"], "approved")


if __name__ == "__main__":
    unittest.main()
