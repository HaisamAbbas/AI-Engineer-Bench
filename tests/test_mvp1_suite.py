from __future__ import annotations

import unittest

from scripts.validate_mvp1_suite import audit


class Mvp1SuiteAuditTest(unittest.TestCase):
    def test_catalog_has_three_declared_categories_but_is_not_official(self) -> None:
        report = audit()
        self.assertTrue(report["category_diversity_satisfied"])
        self.assertFalse(report["official_release_eligible"])
        self.assertEqual(report["catalog_review_status"], "pending-independent-review")

    def test_audit_keeps_all_candidates_development_only(self) -> None:
        report = audit()
        self.assertTrue(all(row["status"] == "candidate" for row in report["tasks"]))
        self.assertTrue(all(row["independent_review"] == "pending" for row in report["tasks"]))

    def test_one_content_complete_anchor_per_application_family(self) -> None:
        report = audit()
        anchors = {row["id"] for row in report["tasks"] if row["status"] == "candidate"}
        self.assertTrue({"rag.document-freshness", "ext.batch-alignment", "tool.idempotent-write"}.issubset(anchors))


if __name__ == "__main__":
    unittest.main()
