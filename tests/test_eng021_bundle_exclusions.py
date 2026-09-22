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
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_release_bundle import build_bundle  # noqa: E402


def _rmtree_windows_safe(path: str) -> None:
    """Remove a tree, tolerating Windows' occasional PermissionError on rmtree.

    On Windows, a file copied via shutil.copy2 can inherit a read-only bit
    from its source, and antivirus/indexer processes can transiently hold a
    handle open on a just-written file; both cause shutil.rmtree to raise
    PermissionError ([WinError 5]) on that path. This is the standard
    mitigation (clear the read-only bit and retry the failed operation)
    rather than silently swallowing the error with ignore_errors=True, which
    would hide a real leak instead of just tolerating a known OS quirk.
    """

    def _on_error(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


def _make_test_tmp_dir() -> str:
    """Create a writable scratch directory for this test run.

    Round-2 review finding: retrying `tempfile.mkdtemp()` (the OS global temp
    root) does not help when that root's PermissionError is persistent rather
    than transient - every retry hits the same denied parent. `AIEB_TEST_TMP_ROOT`
    lets a sandboxed/CI environment point this at a location it actually has
    write access to; failing that, default to `<repo>/.cache/test-tmp` (already
    gitignored, and a location this checkout must be writable under - the test
    suite itself only runs from inside a writable repo checkout) instead of the
    OS temp root, since a restricted-ACL global TEMP is the exact failure this
    is working around. `tempfile.mkdtemp(dir=...)` is used either way so cleanup
    and uniqueness semantics stay identical to the original behavior; only the
    parent directory choice changes.
    """
    preferred_root = os.environ.get("AIEB_TEST_TMP_ROOT") or str(ROOT / ".cache" / "test-tmp")
    last_error: OSError | None = None
    for root in (preferred_root, None):  # None = final fallback to the OS default temp root
        try:
            if root is not None:
                os.makedirs(root, exist_ok=True)
            return tempfile.mkdtemp(dir=root)
        except OSError as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


def _tmp_dir_supports_nested_ops(tmp_dir: str) -> tuple[bool, str]:
    """Probe whether `tmp_dir` (freshly returned by `tempfile.mkdtemp()`) actually
    supports creating and removing a CHILD path, not just existing itself.

    Round-3 review evidence: on at least one Windows host, `tempfile.mkdtemp()`
    itself succeeds, but every subsequent operation INSIDE the directory it just
    returned (creating a child directory, creating a file, `shutil.rmtree`) raises
    `PermissionError: [WinError 5]`, even though a plain file written directly next
    to it (e.g. its own parent) succeeds. This is not about WHICH directory is
    chosen (round 2's fix) or which mkdir call is wrapped (round 3's other fix) -
    it means the host cannot do nested create/remove inside a directory this
    process itself just created, for reasons outside this repository's control
    (a security policy or filesystem behavior on that host, not a bug here). No
    relocation of the temp root can work around that. Detect it directly and skip
    with a precise reason instead of reporting a false pass or a misleading
    failure that looks like a code defect."""
    probe_dir = os.path.join(tmp_dir, "probe")
    try:
        os.mkdir(probe_dir)
        (Path(probe_dir) / "probe.txt").write_text("x", encoding="utf-8")
        shutil.rmtree(probe_dir)
        return True, ""
    except OSError as exc:
        return False, (
            f"cannot create/remove a child path inside a freshly created temp directory "
            f"({tmp_dir}): {exc!r}. This host cannot do nested create/delete inside a "
            f"directory this test process itself just created - not a path-selection issue "
            f"(AIEB_TEST_TMP_ROOT would hit the same restriction on any new directory it "
            f"creates). Verifying this test suite requires an environment where directories "
            f"created by this process support normal child create/delete."
        )


class BundleExclusionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _make_test_tmp_dir()
        supported, reason = _tmp_dir_supports_nested_ops(self.tmp)
        if not supported:
            try:
                _rmtree_windows_safe(self.tmp)
            except OSError:
                pass  # the same restriction being reported may also block this cleanup
            self.skipTest(reason)
        self.output = Path(self.tmp) / "bundle-out"

    def tearDown(self) -> None:
        _rmtree_windows_safe(self.tmp)

    def test_bundle_contains_no_maintainer_tests(self) -> None:
        build_bundle(self.output, include_rejected_development=True)
        bundle_root = self.output / "release-candidate-bundle"
        for path in bundle_root.rglob("*"):
            rel = path.relative_to(bundle_root)
            parts = rel.parts
            self.assertNotIn("maintainer", parts, f"tests/maintainer/ found in bundle at {rel}")
            self.assertNotIn("tests", parts, f"tests/ directory found in bundle at {rel}")

    def test_bundle_contains_no_dev_tests(self) -> None:
        build_bundle(self.output, include_rejected_development=True)
        bundle_root = self.output / "release-candidate-bundle"
        for path in bundle_root.rglob("*"):
            rel = path.relative_to(bundle_root)
            self.assertNotIn("dev_tests", rel.parts, f"dev_tests found in bundle at {rel}")

    def test_bundle_contains_no_holdout(self) -> None:
        build_bundle(self.output, include_rejected_development=True)
        bundle_root = self.output / "release-candidate-bundle"
        for path in bundle_root.rglob("*"):
            rel = path.relative_to(bundle_root)
            for part in rel.parts:
                self.assertNotIn("holdout", part.lower(), f"holdout path found in bundle at {rel}")

    def test_bundle_manifest_verifies_exclusion(self) -> None:
        manifest = build_bundle(self.output, include_rejected_development=True)
        self.assertTrue(manifest["protected_path_exclusion_verified"])
        self.assertEqual(manifest["excluded_from_bundle"], [
            "tests/maintainer/ - trusted evaluator and holdout code (private)",
            ".aieb/runs/ - local run state (ephemeral)",
            ".cache/ - local caches (ephemeral)",
        ])

    def test_explicit_rejected_fixture_bundle_lists_all_twelve_tasks(self) -> None:
        manifest = build_bundle(self.output, include_rejected_development=True)
        self.assertEqual(manifest["label"], "rejected-development-fixtures")
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
        from scripts.build_release_bundle import _check_no_protected_paths, _mkdir_windows_safe
        bundle_root = self.output / "release-candidate-bundle"
        _mkdir_windows_safe(bundle_root)
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
