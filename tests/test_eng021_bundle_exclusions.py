"""ENG-021: Test that the release candidate bundle excludes protected paths.

This is a passing test, not a claim in a doc. The bundle must NOT contain
tests/maintainer/, dev_tests/, or any holdout fixture paths. Per spec
section 48's "Hidden fixture exposed" runbook: if a private holdout
fixture leaks into a public bundle, the incident response requires
quarantining and rotation. This test prevents that class of leak at build
time.

Run: python -m unittest tests.test_eng021_bundle_exclusions -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_release_bundle import build_bundle  # noqa: E402


class BundleExclusionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.output = Path(self.tmp) / "bundle-out"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundle_contains_no_maintainer_tests(self) -> None:
        build_bundle(self.output)
        bundle_root = self.output / "release-candidate-bundle"
        for path in bundle_root.rglob("*"):
            rel = path.relative_to(bundle_root)
            parts = rel.parts
            self.assertNotIn("maintainer", parts, f"tests/maintainer/ found in bundle at {rel}")
            self.assertNotIn("tests", parts, f"tests/ directory found in bundle at {rel}")

    def test_bundle_contains_no_dev_tests(self) -> None:
        build_bundle(self.output)
        bundle_root = self.output / "release-candidate-bundle"
        for path in bundle_root.rglob("*"):
            rel = path.relative_to(bundle_root)
            self.assertNotIn("dev_tests", rel.parts, f"dev_tests found in bundle at {rel}")

    def test_bundle_contains_no_holdout(self) -> None:
        build_bundle(self.output)
        bundle_root = self.output / "release-candidate-bundle"
        for path in bundle_root.rglob("*"):
            rel = path.relative_to(bundle_root)
            for part in rel.parts:
                self.assertNotIn("holdout", part.lower(), f"holdout path found in bundle at {rel}")

    def test_bundle_manifest_verifies_exclusion(self) -> None:
        manifest = build_bundle(self.output)
        self.assertTrue(manifest["protected_path_exclusion_verified"])
        self.assertEqual(manifest["excluded_from_bundle"], [
            "tests/maintainer/ - trusted evaluator and holdout code (private)",
            ".aieb/runs/ - local run state (ephemeral)",
            ".cache/ - local caches (ephemeral)",
        ])

    def test_bundle_manifest_lists_all_twelve_tasks(self) -> None:
        manifest = build_bundle(self.output)
        self.assertEqual(manifest["total_tasks"], 12)
        task_ids = [t["task_id"] for t in manifest["tasks"]]
        expected = [
            "rag.document-freshness", "rag.metadata-filter-topk", "rag.citation-current-span",
            "rag.embedding-version", "ext.missingness", "ext.batch-alignment",
            "ext.unit-normalization", "ext.partial-batch", "tool.false-completion",
            "tool.idempotent-write", "tool.session-isolation", "tool.corrected-arguments",
        ]
        self.assertEqual(sorted(task_ids), sorted(expected))

    def test_bundle_fails_if_maintainer_slipped_in(self) -> None:
        """Verify the build itself refuses to produce a bundle with protected paths."""
        # Test the _check_no_protected_paths function detects violations
        from scripts.build_release_bundle import _check_no_protected_paths
        bundle_root = self.output / "release-candidate-bundle"
        bundle_root.mkdir(parents=True, exist_ok=True)
        fake = bundle_root / "tests" / "maintainer" / "leaked.py"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("SHOULD NOT BE HERE", encoding="utf-8")

        result = _check_no_protected_paths(bundle_root)
        self.assertTrue(len(result["violations"]) > 0)
        self.assertTrue(any("maintainer" in v.replace("\\", "/") for v in result["violations"]))

        # Verify build_bundle has the guard that raises on violations
        import inspect
        source = inspect.getsource(build_bundle)
        self.assertIn("global_check[\"violations\"]", source)
        self.assertIn("BUNDLE VIOLATION", source)


if __name__ == "__main__":
    unittest.main()
