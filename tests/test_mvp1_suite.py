from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_release_bundle import build_bundle
from scripts.validate_mvp1_suite import _logic_lines, audit


class Mvp1SuiteAuditTest(unittest.TestCase):
    def test_catalog_coverage_is_not_misreported_as_curated_diversity(self) -> None:
        report = audit()
        self.assertTrue(report["catalog_category_coverage_satisfied"])
        self.assertFalse(report["category_diversity_satisfied"])
        self.assertFalse(report["official_release_eligible"])
        self.assertEqual(report["catalog_review_status"], "curated-development-only")

    def test_audit_explicitly_rejects_all_legacy_thin_fixtures(self) -> None:
        report = audit()
        self.assertEqual(report["catalog_task_count"], 12)
        self.assertEqual(report["curated_candidate_count"], 0)
        self.assertEqual(report["rejected_task_count"], 12)
        self.assertTrue(all(row["status"] == "rejected" for row in report["tasks"]))
        self.assertTrue(all(row["curation_reason"] for row in report["tasks"]))
        self.assertTrue(all(row["independent_review"] == "pending" for row in report["tasks"]))

    def test_complete_metadata_does_not_override_a_shallow_rejection(self) -> None:
        report = audit()
        rows = {row["id"]: row for row in report["tasks"]}
        for task_id in ("rag.document-freshness", "ext.batch-alignment", "tool.idempotent-write"):
            self.assertEqual(rows[task_id]["status"], "rejected")
            self.assertEqual(rows[task_id]["depth_status"], "shallow")

    def test_structural_depth_gate_does_not_treat_metadata_as_application_depth(self) -> None:
        report = audit()
        self.assertFalse(report["depth_diversity_satisfied"])
        self.assertFalse(report["suite_admission_eligible"])
        self.assertEqual(len(report["projects"]), 3)
        self.assertTrue(all(row["depth_status"] == "shallow" for row in report["tasks"]))
        self.assertTrue(all(project["status"] == "shallow-or-incomplete" for project in report["projects"]))
        self.assertEqual(report["near_duplicate_groups"], [])
        self.assertIn("curated-task-count-not-in-12-20", report["blockers"])

    def test_depth_report_exposes_reproducible_policy_and_metrics(self) -> None:
        report = audit()
        policy = report["depth_policy"]
        self.assertEqual(policy["min_suite_tasks"], 12)
        self.assertEqual(policy["max_suite_tasks"], 20)
        self.assertGreaterEqual(policy["min_domain_source_files"], 2)
        self.assertGreaterEqual(policy["min_domain_branch_points"], 1)
        self.assertIn("tests", policy["excluded_source_parts"])
        self.assertTrue(all("normalised_fingerprint" in row for row in report["tasks"]))

    def test_multiline_padding_does_not_increase_logic_line_count(self) -> None:
        padded = "value = call(\n    # padding one\n    # padding two\n    1,\n)\n"
        with patch.object(Path, "read_text", return_value=padded):
            self.assertEqual(_logic_lines(Path("synthetic.py")), 1)

    def test_admitted_only_bundle_refuses_before_writing_when_depth_gate_fails(self) -> None:
        with patch(
            "scripts.build_release_bundle._suite_depth_audit",
            return_value={"depth_diversity_satisfied": False, "suite_admission_eligible": False},
        ):
            with self.assertRaisesRegex(RuntimeError, "SUITE NOT ADMITTED"):
                build_bundle(Path(".cache") / "v2-gap-007-no-write", require_admitted_suite=True)

    def test_default_bundle_refuses_when_every_task_is_rejected(self) -> None:
        with patch("scripts.build_release_bundle._mkdir_windows_safe") as mkdir:
            with self.assertRaisesRegex(RuntimeError, "NO CURATED TASKS"):
                build_bundle(Path("unused-no-curated-output"))
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
