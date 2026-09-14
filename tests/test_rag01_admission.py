"""Behavioral admission checks for the RAG-01 task package.

These start the submitted application as a separate process and communicate only
through its documented HTTP API.  The evaluator never imports a candidate
module into the scorer process.
"""

from __future__ import annotations

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rag01_admission import run_matrix


class RAG01AdmissionTests(unittest.TestCase):
    def test_rag01_admission_matrix(self) -> None:
        report = run_matrix()
        matrix = {outcome["variant"]: outcome for outcome in report["matrix"]}

        self.assertFalse(matrix["baseline"]["pass"])
        self.assertTrue(matrix["reference"]["pass"])
        self.assertTrue(matrix["alternative"]["pass"])

        expected_failures = {
            "hardcoded-output": "latest-version-visible",
            "visible-delete-filter": "deleted-content-absent",
            "global-rebuild": "incremental-unaffected-write-scope",
            "stale-resurrection": "deleted-content-absent",
            "incorrect-citation": "citation-version-mapping",
        }
        for variant, requirement in expected_failures.items():
            self.assertFalse(matrix[variant]["pass"])
            self.assertFalse(matrix[variant]["checks"][requirement])

        resets = report["reference_resets"]
        self.assertEqual(len(resets), 10)
        self.assertTrue(all(outcome["pass"] for outcome in resets))
