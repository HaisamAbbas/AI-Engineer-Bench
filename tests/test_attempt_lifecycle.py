"""ENG-006/007 behavioral tests for stop, freeze, replay, and attribution."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from ctypes import wintypes
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


def _raise_scorer_crashed(candidate_path: Path, stop=None) -> dict[str, object]:
    """Module-level (not a lambda/closure) so it is picklable for
    run_verification's subprocess-isolated VERIFY (review finding #2)."""
    raise ValueError("scorer crashed")


def _raise_bare_runtime_error(candidate_path: Path, stop=None) -> dict[str, object]:
    raise RuntimeError("evaluator's own bug, nothing to do with the candidate")


def _raise_outage(candidate_path: Path, stop=None) -> dict[str, object]:
    raise ValueError("outage")


def _large_result_evaluator(candidate_path: Path, stop=None) -> dict[str, object]:
    return {"pass": True, "payload": "x" * (2 * 1024 * 1024)}


def _oversized_result_evaluator(candidate_path: Path, stop=None) -> dict[str, object]:
    return {"pass": True, "payload": "x" * (9 * 1024 * 1024)}


def _credential_echo_evaluator(candidate_path: Path, stop=None) -> dict[str, object]:
    """Module-level (picklable for the spawn-based VERIFY subprocess): proves the
    ENG-020 verifier-role attempt variables reached the isolated evaluator process."""
    return {"pass": True, "credential_seen": os.environ.get("AIEB_ATTEMPT_CREDENTIAL")}


def _secret_probe_evaluator(candidate_path: Path, stop=None) -> dict[str, object]:
    """Module-level (picklable for the spawn-based VERIFY subprocess): proves the scrub
    (codex-audit finding 2) - the child's environment holds the allowlisted members and the
    delivered AIEB_* attempt vars, but NEVER the worker's control-plane secrets."""
    return {
        "pass": True,
        "credential_seen": os.environ.get("AIEB_ATTEMPT_CREDENTIAL"),
        "database_url_seen": os.environ.get("AIEB_DATABASE_URL", "<absent>"),
        "build_token_seen": os.environ.get("CI_BUILD_TOKEN", "<absent>"),
        "path_present": bool(os.environ.get("PATH")),
    }


def _tree_spawning_evaluator(candidate_path: Path, stop=None) -> dict[str, object]:
    """Spawn a server-like descendant and then ignore VERIFY cancellation."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid_file = candidate_path.parent / "verify-process-pids.json"
    pid_file.write_text(json.dumps({"evaluator": os.getpid(), "child": child.pid}), encoding="utf-8")
    while True:
        time.sleep(1)


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    stat_path = Path("/proc") / str(pid) / "stat"
    if stat_path.exists():
        try:
            if stat_path.read_text(encoding="utf-8").split()[2] == "Z":
                return False
        except (OSError, IndexError):
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _cancel_after_marker(marker: Path, cancel_event, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.02)
    if marker.exists():
        cancel_event.set()


class _BlockingReadStore:
    """Pickleable store wrapper that stalls inside BUILD's artifact read."""

    def __init__(self, inner: FilesystemArtifactStore, marker: Path) -> None:
        self.inner = inner
        self.marker = marker

    def put_bytes(self, data: bytes):
        return self.inner.put_bytes(data)

    def create_reference(self, blob, *, access_scope: str, visibility: str = "restricted"):
        return self.inner.create_reference(blob, access_scope=access_scope, visibility=visibility)

    def read(self, reference, *, principal_scope: str) -> bytes:
        self.marker.write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(60)
        return self.inner.read(reference, principal_scope=principal_scope)

    def verify(self, blob) -> None:
        self.inner.verify(blob)


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

    def test_engineering_output_larger_than_pipe_buffer_is_drained_and_bounded(self) -> None:
        script = self.script(
            "large-output.py",
            "import sys\n"
            "sys.stdout.write('o' * (2 * 1024 * 1024))\n"
            "sys.stderr.write('e' * (2 * 1024 * 1024))\n",
        )
        outcome = self.runner.run_engineering(self.config("large-output", script, deadline=10))
        self.assertIsNotNone(outcome.candidate)
        self.assertEqual(len(outcome.engineering_stdout.encode("utf-8")), 64 * 1024)
        self.assertEqual(len(outcome.engineering_stderr.encode("utf-8")), 64 * 1024)
        self.assertTrue(outcome.engineering_logs_truncated)
        self.assertEqual(outcome.candidate.engineering_stdout, outcome.engineering_stdout)
        restored = _deserialize_stored_candidate(_serialize_stored_candidate(outcome.candidate))
        self.assertEqual(restored.engineering_stderr, outcome.engineering_stderr)
        self.assertTrue(restored.engineering_logs_truncated)

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
        self.assertEqual(outcome.execution_validity, ExecutionValidity.VALID, outcome.diagnostics)
        self.assertEqual(outcome.verdict, Verdict.PASS)
        self.assertTrue(outcome.cleanup_clean)
        self.assertEqual([file.path for file in outcome.candidate.manifest.files], ["knowledge_service/backend.py"])
        self.assertIn("collect", outcome.phases)
        self.assertIn("build", outcome.phases)
        self.assertIn("verify", outcome.phases)
        self.assertTrue(outcome.evidence_path.is_file())
        self.assertFalse((self.root / "attempts" / "valid" / "engineer").exists())
        self.assertFalse((self.root / "attempts" / "valid" / "build").exists())

    def test_engineering_extra_env_is_delivered_to_the_subprocess(self) -> None:
        """ENG-020 scoped-credential delivery, candidate role: the contestant code runs as a
        subprocess, so the attempt variables must reach its environment - a credential never
        delivered to the code that should present it is not a credential."""
        script = self.script(
            "env-echo.py",
            "import os\nprint(os.environ['AIEB_ATTEMPT_CREDENTIAL'])\n" + self.reference_editor(),
        )
        config = AttemptConfig(
            **{
                **self.config("env-echo", script, deadline=20).__dict__,
                "engineering": EngineeringCommand(
                    (sys.executable, str(script)),
                    20,
                    extra_env={
                        "AIEB_ATTEMPT_ID": "attempt-env-echo",
                        "AIEB_ATTEMPT_ROLE": "candidate",
                        "AIEB_ATTEMPT_CREDENTIAL": "candidate-token-abc",
                    },
                ),
            }
        )
        outcome = self.runner.run_engineering(config)
        self.assertIsNotNone(outcome.candidate)
        self.assertIn("candidate-token-abc", outcome.engineering_stdout)

    def test_verification_attempt_vars_are_delivered_to_the_isolated_verify_subprocess(self) -> None:
        """ENG-020 verifier role: the verify subprocess (spawn-based, own process tree) sees
        the issued attempt variables, so the verifier can prove its scoped identity."""
        outcome = self._collected_outcome("verifier-env")
        resumed = AttemptOutcome(outcome.attempt_id, candidate=outcome.candidate)
        verified = self.runner.run_verification(
            self.config("verifier-env", self.script("verifier-env.py", self.reference_editor()), 20),
            _credential_echo_evaluator,
            resumed,
            attempt_vars={
                "AIEB_ATTEMPT_ID": "attempt-verifier-env",
                "AIEB_ATTEMPT_ROLE": "verifier",
                "AIEB_ATTEMPT_CREDENTIAL": "verifier-token-xyz",
            },
        )
        self.assertEqual(verified.verdict, Verdict.PASS, verified.diagnostics)
        self.assertEqual(verified.evaluation["credential_seen"], "verifier-token-xyz")

    def test_subprocess_environ_never_inherits_worker_secrets(self) -> None:
        """Codex-audit finding 2: candidate code and the trusted evaluator run as subprocesses
        of the worker, so they must NEVER inherit the worker's full environment. Prove it by
        planting sentinel secrets (a control-plane DB URL and a build token) in the parent
        process environment and asserting neither reaches the engineering subprocess nor the
        isolated verify subprocess - while the runner's own surface (an allowlisted member like
        PATH, and the delivered AIEB_* attempt vars) still does, so legitimate tooling does not
        silently break."""
        planted = {
            "AIEB_DATABASE_URL": "postgresql://sentinel:PLANTED@db.example/compromised",
            "CI_BUILD_TOKEN": "PLANTED_TOK",
        }
        kept = {key: os.environ.get(key) for key in planted}
        os.environ.update(planted)
        try:
            script = self.script(
                "env-scrub.py",
                "import os\n"
                "print('CRED', os.environ.get('AIEB_ATTEMPT_CREDENTIAL', '<absent>'))\n"
                "print('DB', os.environ.get('AIEB_DATABASE_URL', '<absent>'))\n"
                "print('TOK', os.environ.get('CI_BUILD_TOKEN', '<absent>'))\n"
                + self.reference_editor(),
            )
            config = AttemptConfig(
                **{
                    **self.config("env-scrub", script, deadline=20).__dict__,
                    "engineering": EngineeringCommand(
                        (sys.executable, str(script)),
                        20,
                        extra_env={
                            "AIEB_ATTEMPT_ID": "attempt-env-scrub",
                            "AIEB_ATTEMPT_ROLE": "candidate",
                            "AIEB_ATTEMPT_CREDENTIAL": "candidate-token-scrubbed",
                        },
                    ),
                }
            )
            outcome = self.runner.run_engineering(config)
            self.assertIsNotNone(outcome.candidate, outcome.diagnostics)
            self.assertIn("CRED candidate-token-scrubbed", outcome.engineering_stdout)
            self.assertNotIn(planted["AIEB_DATABASE_URL"], outcome.engineering_stdout)
            self.assertNotIn("DB postgresql://", outcome.engineering_stdout)
            self.assertNotIn(planted["CI_BUILD_TOKEN"], outcome.engineering_stdout)
            self.assertNotIn("TOK PLANTED_TOK", outcome.engineering_stdout)

            verified = self.runner.run_verification(
                config,
                _secret_probe_evaluator,
                AttemptOutcome(outcome.attempt_id, candidate=outcome.candidate),
                attempt_vars={
                    "AIEB_ATTEMPT_ID": "attempt-env-scrub-verify",
                    "AIEB_ATTEMPT_ROLE": "verifier",
                    "AIEB_ATTEMPT_CREDENTIAL": "verifier-token-scrubbed",
                },
            )
            self.assertEqual(verified.verdict, Verdict.PASS, verified.diagnostics)
            self.assertEqual(verified.evaluation["credential_seen"], "verifier-token-scrubbed")
            self.assertNotEqual(verified.evaluation["database_url_seen"], planted["AIEB_DATABASE_URL"])
            self.assertNotEqual(verified.evaluation["build_token_seen"], planted["CI_BUILD_TOKEN"])
            self.assertTrue(verified.evaluation["path_present"], "allowlisted PATH must survive the scrub")
        finally:
            for key, value in kept.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_import_time_env_leak_is_closed_evaluator_module_runs_after_scrub(self) -> None:
        """Codex-audit finding 4 (second review round): spawn unpickles the Process target and
        args at child bootstrap, BEFORE _verify_subprocess_entrypoint scrubs os.environ - so a
        pickled evaluator FUNCTION forced the evaluator's module to be imported (and its
        import-time code to run) against the worker's unsanitized environment. The fix passes
        the evaluator as (module, qualname) identity STRINGS and resolves it - importing the
        module - only after the scrub, so top-level module code sees the scrubbed env.
        fixtures/worker/import_time_probe.py snapshots os.environ AT IMPORT TIME and reports
        it; run verification against it with planted secrets and assert its import-time
        snapshot never saw them, even though the module was fresh (child-side) at spawn."""
        from tests.fixtures.worker.import_time_probe import evaluate as import_time_probe_evaluate

        planted = {
            "AIEB_DATABASE_URL": "postgresql://sentinel:PLANTED@db.example/compromised",
            "CI_BUILD_TOKEN": "PLANTED_TOK",
        }
        kept = {key: os.environ.get(key) for key in planted}
        os.environ.update(planted)
        try:
            collected = self._collected_outcome("import-time-leak")
            verified = self.runner.run_verification(
                self.config("import-time-leak", self.script("import-time-leak.py", self.reference_editor()), 20),
                import_time_probe_evaluate,
                AttemptOutcome(collected.attempt_id, candidate=collected.candidate),
                attempt_vars={
                    "AIEB_ATTEMPT_ID": "attempt-import-time-leak",
                    "AIEB_ATTEMPT_ROLE": "verifier",
                    "AIEB_ATTEMPT_CREDENTIAL": "verifier-token-import-time",
                },
            )
            self.assertEqual(verified.verdict, Verdict.PASS, verified.diagnostics)
            self.assertEqual(
                verified.evaluation["import_time_db_url"], "<absent>",
                "import-time evaluator module code observed the worker's DB secret before the scrub",
            )
            self.assertEqual(
                verified.evaluation["import_time_build_token"], "<absent>",
                "import-time evaluator module code observed the worker's build token before the scrub",
            )
            self.assertEqual(
                verified.evaluation["import_time_secret_substr_count"], 0,
                "import-time evaluator module code observed a secret-named environment member",
            )
            self.assertNotEqual(verified.evaluation["runtime_db_url"], planted["AIEB_DATABASE_URL"])
            self.assertNotEqual(verified.evaluation["runtime_build_token"], planted["CI_BUILD_TOKEN"])
        finally:
            for key, value in kept.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_proxy_value_embedded_credentials_are_scrubbed_from_child_env(self) -> None:
        """Codex-audit finding 4 (second review round): a NAME allowlist is not enough when the
        allowlisted variable's VALUE smuggles a credential - an operator's
        HTTPS_PROXY=http://proxyuser:proxypass@corp.example:3128 hands 'proxypass' to candidate
        and evaluator code. _sanitized_child_env strips userinfo and URL query credentials from
        every allowlisted value it forwards."""
        from aieb_runner.lifecycle import _sanitized_child_env

        planted = {
            "HTTPS_PROXY": "http://proxyuser:proxypass@corp.example:3128",
            "http_proxy": "http://user:pass@10.0.0.1:8080?password=abc",
            "ALL_PROXY": "socks5h://admin:sekret@proxy.local:1080",
        }
        kept = {key: os.environ.get(key) for key in planted}
        os.environ.update(planted)
        try:
            scrubbed = _sanitized_child_env()
            https = scrubbed.get("HTTPS_PROXY", "")
            self.assertIn("corp.example:3128", https, "proxy host must survive the scrub")
            self.assertNotIn("proxyuser", https)
            self.assertNotIn("proxypass", https)
            http = scrubbed.get("http_proxy", "")
            self.assertNotIn("user:pass@", http)
            self.assertNotIn("password=abc", http)
            all_proxy = scrubbed.get("ALL_PROXY", "")
            self.assertIn("proxy.local:1080", all_proxy)
            self.assertNotIn("admin:sekret@", all_proxy)
        finally:
            for key, value in kept.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

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

    def _collected_outcome(self, name: str) -> AttemptOutcome:
        """run_engineering -> outcome with a collected candidate, ready for
        run_verification against any evaluator (the ENG015-007 split)."""
        return self.runner.run_engineering(
            self.config(name, self.script(f"{name}.py", self.reference_editor()), deadline=20)
        )

    def test_cooperative_cancellation_during_verify_terminates_the_subprocess_cleanly(self) -> None:
        """Review finding #1/#2, fix part A: VERIFY now runs the evaluator in
        an owned subprocess (_run_verify_isolated), not an in-process thread.
        An evaluator honouring the cooperative stop event (fixtures/worker/
        cooperative_slow_evaluator.py) exits promptly as CancelledError once
        signalled, so the subprocess is joined cleanly well within the grace
        period, the build allocation is torn down safely (cleanup_clean=True),
        and nothing is ever scored. A background thread sets cancel_event
        shortly after VERIFY starts - the evaluator is a real module-level
        function (required: it must be picklable to run in a child process),
        so cancellation can no longer be triggered from inside it via a
        shared in-process closure the way the old thread-based test did."""
        from threading import Event, Thread

        from tests.fixtures.worker.cooperative_slow_evaluator import evaluate as cooperative_evaluate

        collected = self._collected_outcome("cooperative-cancel")
        self.assertIsNotNone(collected.candidate)
        cancel_event = Event()
        started_marker = self.root / "attempts" / "cooperative-cancel" / "evaluator-started.txt"
        Thread(target=_cancel_after_marker, args=(started_marker, cancel_event), daemon=True).start()

        outcome = self.runner.run_verification(
            self.config("cooperative-cancel", self.script("cooperative-cancel.py", self.reference_editor()), deadline=20),
            cooperative_evaluate, collected, cancel_event=cancel_event,
        )
        attempt_root = self.root / "attempts" / "cooperative-cancel"
        self.assertEqual(outcome.execution_validity, ExecutionValidity.CANCELLED)
        self.assertIsNone(outcome.verdict)
        self.assertIsNone(outcome.evaluation)
        self.assertTrue(outcome.cleanup_clean)  # subprocess joined -> teardown safe
        self.assertFalse((attempt_root / "build").exists())
        # The evaluator exited via CancelledError before finishing its own
        # 30-second loop - it never reached the line writing this marker.
        self.assertFalse((self.root / "attempts" / "cooperative-cancel" / "evaluator-ran-to-completion.txt").exists())

    def test_uncooperative_evaluator_result_is_discarded_even_if_it_finishes_within_grace(self) -> None:
        """Review finding #1's exact reproduction: an evaluator that IGNORES
        the stop event but happens to finish naturally DURING the grace
        window (fixtures/worker/uncooperative_evaluator.py sleeps only 1.5s,
        well under the default 5-second grace) previously came back from
        _run_cancelable as `completed=True, abandoned=False` with nothing
        recording that cancellation had fired - its late, unrequested result
        was silently scored. With VERIFY's subprocess isolation, ANY
        cancellation observed while the subprocess is still running is
        unconditionally `cancelled=True` (VerifyRun), regardless of whether
        the subprocess goes on to exit "cleanly" on its own - proven here by
        confirming the evaluator DID actually run to completion (its file
        marker exists) while the outcome is still discarded (no verdict)."""
        from threading import Event, Thread

        from tests.fixtures.worker.uncooperative_evaluator import evaluate as uncooperative_evaluate

        collected = self._collected_outcome("uncooperative-within-grace")
        self.assertIsNotNone(collected.candidate)
        cancel_event = Event()
        started_marker = self.root / "attempts" / "uncooperative-within-grace" / "evaluator-started.txt"
        Thread(target=_cancel_after_marker, args=(started_marker, cancel_event), daemon=True).start()

        outcome = self.runner.run_verification(
            self.config("uncooperative-within-grace", self.script("uncooperative-within-grace.py", self.reference_editor()), deadline=20),
            uncooperative_evaluate, collected, cancel_event=cancel_event,
        )
        attempt_root = self.root / "attempts" / "uncooperative-within-grace"
        self.assertEqual(outcome.execution_validity, ExecutionValidity.CANCELLED)
        self.assertIsNone(outcome.verdict)  # never scored, despite the evaluator finishing "successfully"
        self.assertIsNone(outcome.evaluation)
        # The evaluator DID run to completion within the grace window -
        # proving this is not merely a case that never gets far enough to
        # matter, but the exact race finding #1 named. It writes its marker
        # one level up from the candidate path it receives (fixtures/worker/
        # uncooperative_evaluator.py), so the marker survives even though
        # `build` itself is torn down as part of normal (non-abandoned)
        # cleanup once the subprocess is confirmed dead.
        time.sleep(0.5)
        self.assertTrue((attempt_root / "abandoned-evaluator-finished.txt").exists())

    def test_uncooperative_evaluator_past_grace_is_forcibly_terminated(self) -> None:
        """Review finding #2's core fix: an evaluator that ignores the stop
        event AND runs longer than the grace period is not merely
        "abandoned" (the old thread-based behaviour, which could never
        preempt it) - the owned subprocess is genuinely killed
        (terminate()/kill()), so it can never go on to produce the marker
        file its body would otherwise write. This is real containment, not
        discarding a still-running worker's eventual result."""
        from threading import Event, Thread

        from aieb_runner import lifecycle as lifecycle_module
        from tests.fixtures.worker.uncooperative_evaluator import evaluate as uncooperative_evaluate

        collected = self._collected_outcome("uncooperative-terminated")
        self.assertIsNotNone(collected.candidate)
        cancel_event = Event()
        started_marker = self.root / "attempts" / "uncooperative-terminated" / "evaluator-started.txt"
        Thread(target=_cancel_after_marker, args=(started_marker, cancel_event), daemon=True).start()

        original_grace = lifecycle_module.EVALUATOR_CANCEL_GRACE_SECONDS
        lifecycle_module.EVALUATOR_CANCEL_GRACE_SECONDS = 0.2  # test seam: shorter than the fixture's 1.5s sleep
        try:
            start = time.monotonic()
            outcome = self.runner.run_verification(
                self.config("uncooperative-terminated", self.script("uncooperative-terminated.py", self.reference_editor()), deadline=20),
                uncooperative_evaluate, collected, cancel_event=cancel_event,
            )
            elapsed = time.monotonic() - start
        finally:
            lifecycle_module.EVALUATOR_CANCEL_GRACE_SECONDS = original_grace

        attempt_root = self.root / "attempts" / "uncooperative-terminated"
        self.assertEqual(outcome.execution_validity, ExecutionValidity.CANCELLED)
        self.assertIsNone(outcome.verdict)
        self.assertLess(elapsed, 5)  # bounded: poll + grace + forced termination, not the evaluator's own sleep
        # Real containment (review finding #2): the subprocess was killed
        # before it ever reached its own sleep's end, so it NEVER writes the
        # marker - unlike the old thread-based "abandoned" behaviour, which
        # could only let it keep running to completion unobserved.
        time.sleep(2.0)
        self.assertFalse((attempt_root / "abandoned-evaluator-finished.txt").exists())
        # Subprocess containment means the build allocation is always safe
        # to tear down once VERIFY returns - there is no "left running" state
        # for a killed process the way there was for an abandoned thread.
        self.assertTrue(outcome.cleanup_clean)
        self.assertFalse((attempt_root / "build").exists())

    def test_verify_cancellation_kills_evaluator_and_descendant_processes(self) -> None:
        """VERIFY cancellation owns the evaluator's complete process tree."""
        from threading import Event, Thread

        from aieb_runner import lifecycle as lifecycle_module

        name = "verify-process-tree"
        collected = self._collected_outcome(name)
        cancel_event = Event()
        attempt_root = self.root / "attempts" / name
        pid_file = attempt_root / "verify-process-pids.json"

        def cancel_after_child_started() -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not pid_file.exists():
                time.sleep(0.05)
            cancel_event.set()

        original_grace = lifecycle_module.EVALUATOR_CANCEL_GRACE_SECONDS
        lifecycle_module.EVALUATOR_CANCEL_GRACE_SECONDS = 0.2
        Thread(target=cancel_after_child_started, daemon=True).start()
        try:
            outcome = self.runner.run_verification(
                self.config(name, self.script(f"{name}.py", self.reference_editor())),
                _tree_spawning_evaluator, collected, cancel_event=cancel_event,
            )
        finally:
            lifecycle_module.EVALUATOR_CANCEL_GRACE_SECONDS = original_grace

        self.assertEqual(outcome.execution_validity, ExecutionValidity.CANCELLED)
        self.assertTrue(pid_file.is_file())
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(_pid_is_running(pid) for pid in pids.values()):
            time.sleep(0.05)
        self.assertFalse(_pid_is_running(pids["evaluator"]), f"evaluator PID {pids['evaluator']} survived cancellation")
        self.assertFalse(_pid_is_running(pids["child"]), f"descendant PID {pids['child']} survived cancellation")

    def test_verify_drains_results_larger_than_pipe_buffer_and_caps_oversized_results(self) -> None:
        """The parent reads while VERIFY runs, and result bytes have a hard cap."""
        large_name = "verify-large-result"
        collected = self._collected_outcome(large_name)
        large = self.runner.run_verification(
            self.config(large_name, self.script(f"{large_name}.py", self.reference_editor())),
            _large_result_evaluator, collected,
        )
        self.assertEqual(large.verdict, Verdict.PASS)
        self.assertEqual(len(large.evaluation["payload"]), 2 * 1024 * 1024)

        oversized_name = "verify-oversized-result"
        collected = self._collected_outcome(oversized_name)
        oversized = self.runner.run_verification(
            self.config(oversized_name, self.script(f"{oversized_name}.py", self.reference_editor())),
            _oversized_result_evaluator, collected,
        )
        self.assertEqual(oversized.attribution, FailureAttribution.SCORER_ERROR)
        self.assertTrue(any("exceeds" in diagnostic for diagnostic in oversized.diagnostics))
        self.assertTrue(oversized.cleanup_clean)

    def test_cancellation_during_stalled_build_kills_reconstruction_process(self) -> None:
        """Cancellation can stop BUILD while its artifact read is blocked."""
        from threading import Event, Thread

        name = "cancel-during-build"
        collected = self._collected_outcome(name)
        cancel_event = Event()
        marker = self.root / "build-read-started.txt"
        self.runner.store = _BlockingReadStore(self.store, marker)

        def cancel_after_read_starts() -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.02)
            cancel_event.set()

        Thread(target=cancel_after_read_starts, daemon=True).start()
        started = time.monotonic()
        outcome = self.runner.run_verification(
            self.config(name, self.script(f"{name}.py", self.reference_editor())),
            evaluate, collected, cancel_event=cancel_event,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(outcome.execution_validity, ExecutionValidity.CANCELLED)
        self.assertTrue(marker.is_file())
        self.assertLess(elapsed, 5)
        self.assertTrue(outcome.cleanup_clean)
        self.assertFalse((self.root / "attempts" / name / "build").exists())

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
            _raise_scorer_crashed,
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
            _raise_bare_runtime_error,
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
        outcomes = self.runner.run_with_replacements((first, second), _raise_outage, ReplacementPolicy(1))
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(item.evidence_path.is_file() for item in outcomes))
