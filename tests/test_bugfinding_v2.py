from __future__ import annotations

import unittest
import hashlib
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError
from scripts.validate_bugfinding_track import audit
from scripts.prepare_bugfinding_release import prepare
from scripts.audit_model_track_authorization import _verify_evidence_ref, ROOT
from aieb_core.canonical import content_hash
from aieb_core.bugfinding_v2 import (
    BugFinding,
    BugEvaluationResult,
    BugHiddenLabel,
    BugHiddenLabelSet,
    BugPatch,
    BugReleaseManifest,
    BugRepositorySnapshot,
    BugSubmission,
    BugTaskRevision,
    FindingEvidence,
    score_bug_submission,
)


class BugFindingContractsTest(unittest.TestCase):
    def test_finding_requires_reproducible_evidence_and_safe_location(self):
        with self.assertRaises(ValidationError):
            BugFinding(schema_version="aieb.bug-finding/v2", finding_id="x", location="../secret", symbol_or_line="1", reproduction_steps=("run",), observed_behavior="bad", expected_behavior="good", impact="high", severity="major", evidence=(), confidence=0.8)

    def test_modes_cannot_be_mixed(self):
        payload = {"schema_version": "aieb.bug-submission/v2", "repository": {}, "mode": "finding-only", "findings": []}
        with self.assertRaises(ValidationError):
            BugSubmission(**payload, patch={"patch_content": "diff", "patch_digest": "0" * 64, "changed_paths": ("src/a.py",)})

    def test_release_cannot_be_official_or_cross_cohort(self):
        self.assertFalse(audit()["official_release_eligible"])
        with self.assertRaises(ValidationError):
            BugReleaseManifest(schema_version="aieb.bug-release/v2", release_id="mvp2-bugfinding-release-x", cohort_id="mvp2-bugfinding-a", mode="finding-only", tasks=(), status="official")

    def test_catalog_has_a_pinned_development_task_but_no_admission_claim(self):
        report = audit()
        self.assertEqual(report["tasks_admitted"], 0)
        self.assertFalse(report["hidden_label_workflow_complete"])
        self.assertFalse(report["execution_evidence_complete"])
        self.assertEqual(report["tasks"][0]["repository_digest"], "0e55378bd4ccb01a34b03f182b56cb1fdc01082982954d5e59406b5832dc837f")
        self.assertEqual(report["tasks"][0]["status"], "blocked")

    def test_release_preparation_refuses_placeholder_labels_before_writing(self):
        with self.assertRaisesRegex(RuntimeError, "controls are incomplete"):
            prepare("mvp2-bugfinding-release-development-v1", Path(".cache") / "mvp2-release.json")

    def test_patch_digest_is_bound_to_patch_content(self):
        with self.assertRaises(ValidationError):
            BugPatch(
                patch_content="diff --git a/a.py b/a.py\n",
                patch_digest="0" * 64,
                changed_paths=("a.py",),
            )
        patch_text = "diff --git a/a.py b/a.py\n"
        patch = BugPatch(
            patch_content=patch_text,
            patch_digest=hashlib.sha256(patch_text.encode()).hexdigest(),
            changed_paths=("a.py",),
        )
        self.assertEqual(patch.patch_content, patch_text)

    def test_model_track_evidence_uri_requires_existing_matching_digest(self):
        evidence = ROOT / "README.md"
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        ok, reason = _verify_evidence_ref(f"evidence://README.md#{digest}", "readme")
        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertFalse(_verify_evidence_ref("evidence://README.md#" + "0" * 64, "readme")[0])
        self.assertFalse(_verify_evidence_ref("private://unavailable", "readme")[0])

    def test_hidden_label_set_is_digest_bound_and_scoring_is_component_based(self):
        label = BugHiddenLabel(
            schema_version="aieb.bug-label/v2",
            label_id="bug-1",
            location="src/service.py",
            symbol_or_line="handle:10",
            severity="major",
            finding_digest="1" * 64,
            reproduction_digest="2" * 64,
        )
        label_payload = {
            "schema_version": "aieb.bug-label-set/v2",
            "task_id": "task-1",
            "revision": "1.0.0",
            "repository_digest": "3" * 64,
            "labels": [label.model_dump(mode="json")],
        }
        labels = BugHiddenLabelSet(**label_payload, label_set_digest=content_hash(label_payload))
        repository = BugRepositorySnapshot(
            schema_version="aieb.bug-repository/v2",
            repository_id="repo",
            revision="0123456",
            content_digest="3" * 64,
            license_id="Apache-2.0",
            provenance_digest="4" * 64,
            documentation_digest="5" * 64,
            allowed_commands=("pytest",),
        )
        finding = BugFinding(
            schema_version="aieb.bug-finding/v2",
            finding_id="candidate-1",
            location="src/service.py",
            symbol_or_line="handle:10",
            reproduction_steps=("pytest -q",),
            observed_behavior="bad",
            expected_behavior="good",
            impact="lost update",
            severity="major",
            evidence=(FindingEvidence(kind="reproduction", reference="stdout"),),
            confidence=Decimal("0.9"),
        )
        submission = BugSubmission(
            schema_version="aieb.bug-submission/v2",
            repository=repository,
            mode="finding-only",
            findings=(finding, finding),
        )
        task = BugTaskRevision(
            schema_version="aieb.bug-task/v2",
            task_id="task-1",
            revision="1.0.0",
            cohort_id="mvp2-bugfinding-development-v1",
            repository=repository,
            objective="find the defect",
            severity_taxonomy_version="aieb.bug-severity/v1",
            hidden_label_digest=labels.label_set_digest,
            evaluator_digest="6" * 64,
            hidden_label_ref="private://labels",
            evaluator_ref="private://evaluator",
            mode="finding-only",
            baseline_ref="baseline",
            reference_ref="reference",
            alternative_ref="alternative",
            negative_control_ref="negative",
            public_test_ref="tests",
        )
        evaluation_payload = {
            "schema_version": "aieb.bug-evaluation/v2",
            "task_id": "task-1",
            "revision": "1.0.0",
            "label_set_digest": labels.label_set_digest,
            "evaluator_digest": task.evaluator_digest,
            "matched_label_ids": ["bug-1"],
            "finding_evidence_digests": {"bug-1": label.finding_digest},
            "reproduction_evidence_digests": {"bug-1": label.reproduction_digest},
            "patch_content_digest": None,
            "patch_correct": None,
        }
        evaluation = BugEvaluationResult(
            **evaluation_payload,
            evaluation_digest=content_hash(evaluation_payload),
        )
        score = score_bug_submission(submission, labels, task=task, evaluation=evaluation)
        self.assertEqual(score.true_positive_count, 1)
        self.assertEqual(score.duplicate_count, 1)
        self.assertEqual(score.false_positive_count, 0)
        self.assertEqual(score.precision, Decimal("1"))

        with self.assertRaisesRegex(ValueError, "digest does not match"):
            score_bug_submission(
                submission,
                labels,
                task=task.model_copy(update={"hidden_label_digest": "4" * 64}),
                evaluation=evaluation,
            )
        with self.assertRaisesRegex(ValueError, "not bound to the frozen task revision"):
            score_bug_submission(
                submission,
                labels,
                task=task.model_copy(update={"revision": "2.0.0"}),
                evaluation=evaluation,
            )

        with self.assertRaises(ValidationError):
            BugHiddenLabelSet(**label_payload, label_set_digest="0" * 64)


if __name__ == "__main__":
    unittest.main()
