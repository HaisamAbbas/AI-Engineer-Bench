"""ENG-006/007 behavioral tests for stop, freeze, replay, and attribution."""

from __future__ import annotations

import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_API_SRC = ROOT / "services" / "api" / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from aieb_core.models import ExecutionValidity, SubmissionPolicy, Verdict
from aieb_runner.artifacts import FilesystemArtifactStore
from aieb_runner.lifecycle import (
    AttemptConfig,
    AttemptOutcome,
    EngineeringCommand,
    FailureAttribution,
    LocalAttemptRunner,
    ReplacementPolicy,
)
from aieb_api.worker.runner_bridge import _deserialize_stored_candidate, _serialize_stored_candidate
from tests.maintainer.rag01.evaluator import evaluate


TASK = ROOT / "suites" / "dev" / "rag.document-freshness"
BASE_DIGEST = "a" * 64


class AttemptLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / ".cache" / "eng006-007-tests" / str(uuid4())
        self.root.mkdir(parents=True)
        self.store = FilesystemArtifactStore(self.root / "store")
        self.runner = LocalAttemptRunner(self.store)
        self.policy = SubmissionPolicy(
            include=("knowledge_service/**",),
            protected=("dev_tests/**",),
            max_artifact_bytes=1024 * 1024,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def config(self, name: str, script: Path, deadline: float = 3) -> AttemptConfig:
        return AttemptConfig(
            attempt_id=name,
            frozen_source=TASK / "repo",
            work_root=self.root / "attempts",
            base_revision_digest=BASE_DIGEST,
            submission=self.policy,
            engineering=EngineeringCommand((sys.executable, str(script)), deadline),
            access_scope=f"scope-{name}",
        )

    def script(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(body, encoding="utf-8")
        return path

    def reference_editor(self, extra: str = "") -> str:
        reference = repr(str(TASK / "reference" / "backend.py"))
        return (
            "from pathlib import Path\n"
            "import shutil\n"
            f"shutil.copyfile({reference}, Path.cwd() / 'knowledge_service' / 'backend.py')\n"
            + extra
        )

    def test_engineering_and_verification_can_run_as_two_independent_calls(self) -> None:
        """ENG015-007: verification must be resumable from a fresh AttemptOutcome
        carrying only the artifact-store-backed candidate reference - not the
        same in-process object run_engineering produced - since the hosted
        worker's verification phase may run in an entirely different process
        (even a different worker) after the engineering phase's own process
        has already exited. This is the property the leasing split depends on;
        prove it directly rather than only through the composed run().

        Reusing self.runner/self.store here (as an earlier version of this
        test did) would only prove the split works when both phases share one
        in-process object - not the two-phase claim ENG-015 actually makes
        (review finding #2). This test instead round-trips through the same
        JSON (de)serialization services/api/aieb_api.worker.runner_bridge
        actually persists to and reads back from PostgreSQL, and reconstructs
        the candidate through a SECOND, independently-constructed
        FilesystemArtifactStore instance pointed at the same root directory -
        the same shape a real distributed deployment gets from pointing every
        worker's AIEB_WORKER_WORK_ROOT at one shared/network filesystem. It is
        this fixture's job to attach a fresh store to already-written bytes,
        not to fabricate a distributed object store neither this pipeline nor
        ENG-015 provides; see DECISIONS.md ENG015-008 for that disclosed,
        narrowed scope (same-host or shared-filesystem workers only)."""
        config = self.config("split", self.script("split.py", self.reference_editor()))
        engineered = self.runner.run_engineering(config)
        self.assertIsNotNone(engineered.candidate)
        self.assertEqual(engineered.phases, ["provision", "engineer", "stop", "collect"])

        serialized = _serialize_stored_candidate(engineered.candidate)
        reconstructed_candidate = _deserialize_stored_candidate(serialized)
        self.assertEqual(reconstructed_candidate, engineered.candidate)

        # A fresh outcome and a fresh ArtifactStore/runner instance, as a
        # different worker process attached to the same shared work_root
        # would construct after loading only the persisted JSON from the
        # database - not engineered itself, not sharing any other in-memory
        # state (not even the original store object) with engineering.
        independent_store = FilesystemArtifactStore(self.root / "store")
        independent_runner = LocalAttemptRunner(independent_store)
        resumed = AttemptOutcome(config.attempt_id, candidate=reconstructed_candidate)
        verified = independent_runner.run_verification(config, evaluate, resumed)
        self.assertEqual(verified.execution_validity, ExecutionValidity.VALID)
        self.assertEqual(verified.verdict, Verdict.PASS)
        self.assertTrue(verified.cleanup_clean)
        self.assertEqual([file.path for file in verified.candidate.manifest.files], ["knowledge_service/backend.py"])

        # The composed run() (used by the local CLI, unaffected by this split)
        # produces the same verdict for the same reference fix.
        composed = self.runner.run(
            self.config("composed", self.script("composed.py", self.reference_editor())), evaluate
        )
        self.assertEqual(composed.verdict, verified.verdict)

    def test_valid_fix_replays_only_submitted_files_in_fresh_build(self) -> None:
        outcome = self.runner.run(
            self.config("valid", self.script("valid.py", self.reference_editor())), evaluate
        )
        self.assertEqual(outcome.execution_validity, ExecutionValidity.VALID)
        self.assertEqual(outcome.verdict, Verdict.PASS)
        self.assertTrue(outcome.cleanup_clean)
        self.assertEqual([file.path for file in outcome.candidate.manifest.files], ["knowledge_service/backend.py"])
        self.assertIn("collect", outcome.phases)
        self.assertIn("build", outcome.phases)
        self.assertIn("verify", outcome.phases)
        self.assertTrue(outcome.evidence_path.is_file())
        self.assertFalse((self.root / "attempts" / "valid" / "engineer").exists())
        self.assertFalse((self.root / "attempts" / "valid" / "build").exists())

    def test_deadline_stops_process_tree_before_artifact_freeze(self) -> None:
        escaped_marker = repr(str(self.root / "late-child-marker.txt"))
        body = self.reference_editor(
            "from subprocess import Popen\n"
            "import sys, time\n"
            f"Popen([sys.executable, '-c', \"import time; time.sleep(1); open({escaped_marker}, 'w').write('late')\"])\n"
            "time.sleep(20)\n"
        )
        outcome = self.runner.run(self.config("deadline", self.script("deadline.py", body), 0.25), evaluate)
        self.assertEqual(outcome.termination_reason, "deadline")
        self.assertEqual(outcome.attribution, FailureAttribution.RESOURCE_LIMIT)
        self.assertTrue(outcome.cleanup_clean)
        self.assertNotIn("knowledge_service/late.py", [file.path for file in outcome.candidate.manifest.files])
        time.sleep(1.2)
        self.assertFalse((self.root / "late-child-marker.txt").exists())

    def test_cancel_event_stops_engineering_before_deadline_with_no_verdict(self) -> None:
        """ENG-015: hosted campaign cancellation must interrupt a still-running attempt
        before its deadline, not merely stop dispatching new ones."""
        from threading import Event, Thread

        cancel_event = Event()
        body = self.reference_editor("import time\ntime.sleep(20)\n")
        Thread(target=lambda: (time.sleep(0.3), cancel_event.set()), daemon=True).start()
        outcome = self.runner.run(
            self.config("cancelled", self.script("cancelled.py", body), deadline=20), evaluate, cancel_event=cancel_event,
        )
        self.assertEqual(outcome.termination_reason, "cancelled")
        self.assertEqual(outcome.execution_validity, ExecutionValidity.CANCELLED)
        self.assertIsNone(outcome.verdict)
        self.assertTrue(outcome.cleanup_clean)

    def test_partial_and_no_artifact_are_explicit_replay_outcomes(self) -> None:
        partial = self.runner.run(
            self.config(
                "partial",
                self.script("partial.py", self.reference_editor("Path('knowledge_service/note.py').write_text('allowed', encoding='utf-8')\n")),
            ),
            evaluate,
        )
        self.assertEqual(partial.verdict, Verdict.PASS)
        self.assertEqual(
            [file.path for file in partial.candidate.manifest.files],
            ["knowledge_service/backend.py", "knowledge_service/note.py"],
        )
        no_artifact = self.runner.run(
            self.config("empty", self.script("empty.py", "pass\n")), evaluate
        )
        self.assertEqual(no_artifact.execution_validity, ExecutionValidity.VALID)
        self.assertEqual(no_artifact.verdict, Verdict.FAIL)
        self.assertEqual(no_artifact.candidate.manifest.files, ())

    def test_protected_change_runtime_failure_and_scorer_crash_are_attributed(self) -> None:
        protected_source = self.root / "protected-source"
        shutil.copytree(TASK / "repo", protected_source)
        (protected_source / "dev_tests").mkdir()
        (protected_source / "dev_tests" / "trusted.py").write_text("trusted\n", encoding="utf-8")
        config = self.config("protected", self.script("protected.py", "from pathlib import Path\nPath('dev_tests/trusted.py').write_text('changed')\n"))
        config = AttemptConfig(**{**config.__dict__, "frozen_source": protected_source})
        protected = self.runner.run(config, evaluate)
        self.assertEqual(protected.verdict, Verdict.CONTRACT_VIOLATION)
        self.assertEqual(protected.attribution, FailureAttribution.SUBMISSION_CONTRACT_VIOLATION)

        runtime = self.runner.run(
            self.config("runtime", self.script("runtime.py", "from pathlib import Path\nPath('knowledge_service/backend.py').write_text('not python')\n")),
            evaluate,
        )
        self.assertEqual(runtime.verdict, Verdict.FAIL)
        self.assertEqual(runtime.attribution, FailureAttribution.CANDIDATE_RUNTIME_FAILURE)

        scorer = self.runner.run(
            self.config("scorer", self.script("scorer.py", self.reference_editor())),
            lambda _: (_ for _ in ()).throw(ValueError("scorer crashed")),
        )
        self.assertEqual(scorer.execution_validity, ExecutionValidity.INFRASTRUCTURE_INVALID)
        self.assertIsNone(scorer.verdict)
        self.assertEqual(scorer.attribution, FailureAttribution.SCORER_ERROR)
        self.assertTrue(scorer.retryable)

    def test_bare_runtime_error_from_evaluator_is_scorer_error_not_candidate_failure(self) -> None:
        """Review finding #16: only CandidateUnavailableError means "the
        candidate is broken"; an evaluator's own unrelated bug raising a bare
        RuntimeError (a generic, widely-used Python exception any code could
        raise by accident) must not be misattributed as a scored candidate
        failure - it must fall through to SCORER_ERROR like any other
        unexpected trusted-evaluator exception."""
        bare_runtime_error = self.runner.run(
            self.config("bare-runtime-error", self.script("bare.py", self.reference_editor())),
            lambda _: (_ for _ in ()).throw(RuntimeError("evaluator's own bug, nothing to do with the candidate")),
        )
        self.assertEqual(bare_runtime_error.execution_validity, ExecutionValidity.INFRASTRUCTURE_INVALID)
        self.assertIsNone(bare_runtime_error.verdict)
        self.assertEqual(bare_runtime_error.attribution, FailureAttribution.SCORER_ERROR)

    def test_configuration_and_teardown_failures_do_not_become_verdicts(self) -> None:
        invalid = AttemptConfig(
            attempt_id="bad-config",
            frozen_source=TASK / "repo",
            work_root=self.root / "attempts",
            base_revision_digest=BASE_DIGEST,
            submission=self.policy,
            engineering=EngineeringCommand((), 0),
            access_scope="scope-bad",
        )
        configuration = self.runner.run(invalid, evaluate)
        self.assertEqual(configuration.attribution, FailureAttribution.CONFIGURATION_FAILURE)
        self.assertIsNone(configuration.verdict)
        self.assertTrue(configuration.evidence_path.is_file())

        config = self.config("teardown", self.script("teardown.py", self.reference_editor()))
        original = shutil.rmtree
        def fail_allocations(path: Path, *args: object, **kwargs: object) -> None:
            if Path(path).name in {"engineer", "build"}:
                raise OSError("forced teardown failure")
            original(path, *args, **kwargs)
        with patch("aieb_runner.lifecycle.shutil.rmtree", side_effect=fail_allocations):
            teardown = self.runner.run(config, evaluate)
        self.assertEqual(teardown.attribution, FailureAttribution.TEARDOWN_FAILURE)
        self.assertEqual(teardown.execution_validity, ExecutionValidity.INFRASTRUCTURE_INVALID)
        self.assertTrue(teardown.retryable)

    def test_candidate_failures_are_not_retried_but_infrastructure_is_capped(self) -> None:
        candidate = self.config("candidate", self.script("candidate.py", "raise SystemExit(2)\n"))
        unused = self.config("unused", self.script("unused.py", self.reference_editor()))
        outcomes = self.runner.run_with_replacements((candidate, unused), evaluate, ReplacementPolicy(1))
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].attribution, FailureAttribution.CANDIDATE_BUILD_FAILURE)
        self.assertFalse(outcomes[0].retryable)

        first = self.config("first", self.script("first.py", self.reference_editor()))
        second = self.config("second", self.script("second.py", self.reference_editor()))
        outcomes = self.runner.run_with_replacements(
            (first, second), lambda _: (_ for _ in ()).throw(ValueError("outage")), ReplacementPolicy(1)
        )
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(item.evidence_path.is_file() for item in outcomes))
