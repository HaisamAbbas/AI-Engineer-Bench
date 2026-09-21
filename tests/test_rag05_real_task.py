"""RAG-05 real-source task: admission matrix and task-local runtime descriptor.

These tests exercise the real-repo vertical: the task built from vendored
upstream sqlite-utils 3.38 (suites/real/rag.corpus-index-drift), its trusted
evaluator (tests/maintainer/rag05), and the additive runtime.json descriptor
the CLI resolves for tasks outside the curated 12-task development registry.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aieb_cli.main import (
    TASK_RUNTIMES,
    CliError,
    _runtime_descriptor,
    _runtime_for,
    _task_check,
)

from scripts.run_rag05_admission import evaluate_variant
from tests.maintainer.rag05.fixture import DOCUMENTS, generate

TASK = ROOT / "suites" / "real" / "rag.corpus-index-drift"

# Checks the deliberately broken baseline must fail, and the checks it may
# legitimately pass (they are not the point of the defect).
BASELINE_FAILURES = {
    "latest-version-visible",
    "stale-version-absent",
    "idempotent-event-replay",
    "lower-version-rejected",
    "equal-version-conflict",
    "deleted-content-absent",
    "citation-version-mapping",
}


class Rag05AdmissionTest(unittest.TestCase):
    def test_curated_registry_is_untouched_by_the_real_suite_task(self) -> None:
        # The 12 development tasks keep their curated mapping; the real task is
        # self-describing and never added here (tests/test_eng013_admission.py
        # pins the catalog at 12 tasks and must stay true).
        self.assertEqual(len(TASK_RUNTIMES), 12)
        self.assertNotIn("rag.corpus-index-drift", TASK_RUNTIMES)

    def test_task_local_runtime_descriptor_resolves(self) -> None:
        descriptor = _runtime_descriptor(TASK)
        self.assertEqual(
            descriptor,
            (
                "knowledge_service",
                "tests.maintainer.rag05.evaluator",
                "scripts.run_rag05_admission",
                "evaluate_variant",
            ),
        )
        self.assertEqual(_runtime_for(TASK, "rag.corpus-index-drift"), descriptor)

    def test_runtime_descriptor_is_rejected_when_malformed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "runtime.json").write_text(
                json.dumps({"schema_version": "aieb.task-runtime/v1", "source_dir": "../escape"}),
                encoding="utf-8",
            )
            with self.assertRaises(CliError):
                _runtime_descriptor(task)

    def test_task_validates_against_its_own_digests(self) -> None:
        info = _task_check(TASK)
        self.assertEqual(info["task_id"], "rag.corpus-index-drift")
        self.assertEqual(info["requirements"], 13)

    def test_reference_and_alternative_pass_every_requirement(self) -> None:
        for variant in ("reference", "alternative"):
            outcome = evaluate_variant(variant, TASK / variant / "backend.py")
            self.assertTrue(outcome["pass"], f"{variant} failed: {outcome['diagnostics']}")

    def test_baseline_fails_exactly_the_intended_checks(self) -> None:
        outcome = evaluate_variant("baseline")
        self.assertFalse(outcome["pass"])
        failed = {name for name, passed in outcome["checks"].items() if not passed}
        self.assertEqual(failed, BASELINE_FAILURES)

    def test_counterexamples_fail_their_targeted_checks(self) -> None:
        expectations = {
            "global-rebuild": {"incremental-write-scope"},
            "ignore-metadata-filter": {"metadata-filter-respected"},
            "stale-resurrection": {"deleted-content-absent"},
        }
        for variant, expected in expectations.items():
            outcome = evaluate_variant(variant, TASK / "counterexamples" / f"{variant}.py")
            failed = {name for name, passed in outcome["checks"].items() if not passed}
            self.assertFalse(outcome["pass"], f"{variant} unexpectedly passed")
            self.assertTrue(
                expected <= failed,
                f"{variant} failed {sorted(failed)} which does not cover {sorted(expected)}",
            )


class Rag05FixtureTest(unittest.TestCase):
    def test_corpus_is_real_and_labels_are_seed_scoped(self) -> None:
        # The corpus text is real upstream README content: stable across seeds.
        for seed in (5309, 41):
            fixture = generate(seed)
            documents = fixture["documents"]
            for document_id, item in DOCUMENTS.items():
                self.assertEqual(documents[document_id]["text"], item["text"])
            suffix = f"{seed % 997:03x}"
            self.assertEqual(documents["library-import"]["metadata"], {"group": f"python-api-{suffix}"})
            self.assertCountEqual(fixture["ingestion_order"], list(DOCUMENTS))

    def test_fixture_version_is_pinned(self) -> None:
        self.assertEqual(generate()["fixture_version"], "rag05-heldout/v1")


if __name__ == "__main__":
    unittest.main()
