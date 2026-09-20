"""ENG-023 tests for the fixed reference model-track loop.

Everything here runs without Docker, network, or credentials. `_FakeEnvironment`
below is a real-shell test double (it shells out to bash locally, which this
dev environment provides) that maps the loop's hardcoded "/workspace" paths
onto a real temp directory, so the exact command strings
`aieb_runner.model_loop` builds (find/head/grep/base64/cp) are genuinely
exercised, not just mocked away - while still requiring no Docker and no
credentials.

Follows this repo's existing async-test convention (see
tests/test_eng019_sandbox_threat_model.py): stdlib
`unittest.IsolatedAsyncioTestCase`, not pytest-asyncio (not a dependency here).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_real_bash() -> str:
    """Resolve a real (Git-for-Windows / MSYS) bash, not Windows' own
    `System32\\bash.exe`/WindowsApps WSL launcher shim, which fails with no
    real Linux subsystem installed. `shutil.which` may return either
    depending on PATH order in a bare subprocess (unlike an interactive
    shell), so this filters those out explicitly before falling back to
    well-known Git-for-Windows install locations."""
    candidates: list[str] = []
    found = shutil.which("bash")
    if found and "System32" not in found and "WindowsApps" not in found:
        candidates.append(found)
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_var)
        if not base:
            continue
        candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
        candidates.append(os.path.join(base, "Programs", "Git", "usr", "bin", "bash.exe"))
        candidates.append(os.path.join(base, "Programs", "Git", "bin", "bash.exe"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "could not locate a real (non-WSL) bash.exe for the ENG-023 model-loop test double"
    )


_BASH = _find_real_bash()


def _rmtree_windows_safe(path: str) -> None:
    """Remove a tree, tolerating Windows' occasional PermissionError on rmtree.

    Reused verbatim from `tests/test_eng021_bundle_exclusions.py`'s established
    mitigation: a file copied/created can inherit a read-only bit, and
    antivirus/indexer processes can transiently hold a handle open on a
    just-written file; both cause shutil.rmtree to raise PermissionError
    ([WinError 5]) on that path. This clears the read-only bit and retries
    the failed operation rather than silently swallowing the error.
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

    Reused verbatim (same idiom, same reasoning) from
    `tests/test_eng021_bundle_exclusions.py::_make_test_tmp_dir`: a reviewer
    reported "6 tests passed; 13 tests failed/error during TemporaryDirectory
    workspace creation/cleanup with WinError 5" running this exact file. That
    matches the ENG-021 finding this mirrors: `AIEB_TEST_TMP_ROOT` lets a
    sandboxed/CI environment point this at a location it actually has write
    access to; failing that, default to `<repo>/.cache/test-tmp` (already
    gitignored, a location this checkout must be writable under) instead of
    the OS temp root, since a restricted-ACL global TEMP is the exact failure
    this works around.
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
    """Probe whether `tmp_dir` actually supports creating/removing a CHILD path.

    Reused verbatim (same idiom) from
    `tests/test_eng021_bundle_exclusions.py::_tmp_dir_supports_nested_ops`:
    on at least one Windows host, `tempfile.mkdtemp()` itself succeeds, but
    every subsequent operation INSIDE the directory it just returned raises
    `PermissionError: [WinError 5]`, for reasons outside this repository's
    control. No relocation of the temp root can work around that - this
    detects it directly and skips with a precise reason instead of a
    misleading failure that looks like a code defect.
    """
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
            f"directory this test process itself just created - not a path-selection "
            f"issue. Verifying this test suite requires an environment where directories "
            f"created by this process support normal child create/delete."
        )

from aieb_runner import model_loop
from aieb_runner.model_loop import (
    MAX_RETRIES_PER_STEP,
    ModelTrackReferenceLoop,
    ToolValidationError,
    validate_tool_call,
)
from aieb_runner.model_providers.base import (
    AuthenticationError,
    InvalidRequestError,
    ProviderMessage,
    ProviderResponse,
    RateLimitError,
    ToolCall,
    TransportError,
    UsageInfo,
)
from aieb_runner.model_providers.fake import FakeProviderAdapter
from aieb_runner.model_providers.openai_compatible import OpenAICompatibleAdapter


@dataclass
class _ExecResult:
    stdout: str | None
    stderr: str | None
    return_code: int


class _FakeEnvironment:
    """Minimal BaseEnvironment double: only implements the one method
    (`exec`) the loop actually calls. Real shell semantics via bash, with
    the loop's hardcoded "/workspace" transparently mapped to a real temp
    directory so find/head/grep/base64/cp behave exactly as they would
    inside the real sandbox."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        root_posix = self.root.as_posix()
        rewritten = command.replace("/workspace", root_posix)
        real_cwd = cwd.replace("/workspace", root_posix) if cwd else None
        completed = subprocess.run(
            [_BASH, "-lc", rewritten],
            cwd=real_cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec or 30,
        )
        return _ExecResult(completed.stdout, completed.stderr, completed.returncode)


def _response(content="", tool_calls=(), reported_model="reported-model", **kwargs) -> ProviderResponse:
    return ProviderResponse(
        content=content,
        tool_calls=tool_calls,
        reported_model=reported_model,
        finish_reason="stop",
        usage=UsageInfo(prompt_tokens=10, completion_tokens=5),
        **kwargs,
    )


def _summary(env: _FakeEnvironment) -> dict:
    return json.loads((env.root / "submission" / "model_track_summary.json").read_text())


# ---------------------------------------------------------------------------
# Tool schema validation (pure, synchronous)
# ---------------------------------------------------------------------------


class ToolSchemaValidationTest(unittest.TestCase):
    def test_well_formed_tool_call_is_accepted(self) -> None:
        validate_tool_call(ToolCall(id="1", name="read_file", arguments={"path": "a.txt"}))

    def test_unknown_tool_name_rejected(self) -> None:
        with self.assertRaisesRegex(ToolValidationError, "unknown tool"):
            validate_tool_call(ToolCall(id="1", name="delete_everything", arguments={}))

    def test_missing_required_field_rejected(self) -> None:
        with self.assertRaisesRegex(ToolValidationError, "missing required field"):
            validate_tool_call(ToolCall(id="1", name="read_file", arguments={}))

    def test_wrong_type_rejected(self) -> None:
        with self.assertRaisesRegex(ToolValidationError, "expected string"):
            validate_tool_call(ToolCall(id="1", name="read_file", arguments={"path": 123}))


# ---------------------------------------------------------------------------
# Context truncation (pure, synchronous)
# ---------------------------------------------------------------------------


class ContextTruncationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = model_loop.MAX_CONTEXT_CHARS
        model_loop.MAX_CONTEXT_CHARS = 100
        self.addCleanup(setattr, model_loop, "MAX_CONTEXT_CHARS", self._orig)

    def test_truncation_is_deterministic_and_keeps_head_and_tail(self) -> None:
        messages = [
            ProviderMessage(role="system", content="SYSTEM PROMPT"),
            ProviderMessage(role="user", content="TASK INSTRUCTION"),
        ] + [ProviderMessage(role="user", content=f"turn-{i}" * 5) for i in range(20)]

        truncated_once = ModelTrackReferenceLoop._truncate_messages(messages)
        truncated_twice = ModelTrackReferenceLoop._truncate_messages(list(messages))

        self.assertEqual(truncated_once[0].content, "SYSTEM PROMPT")
        self.assertEqual(truncated_once[1].content, "TASK INSTRUCTION")
        self.assertLess(len(truncated_once), len(messages))
        self.assertEqual(truncated_once[-1].content, messages[-1].content)
        self.assertEqual(
            [m.content for m in truncated_once], [m.content for m in truncated_twice]
        )


# ---------------------------------------------------------------------------
# Credential protection (pure, synchronous)
# ---------------------------------------------------------------------------


class CredentialProtectionTest(unittest.TestCase):
    def test_adapter_never_persists_key_on_the_instance(self) -> None:
        os.environ["AIEB_TEST_KEY"] = "super-secret-value"
        self.addCleanup(os.environ.pop, "AIEB_TEST_KEY", None)
        adapter = OpenAICompatibleAdapter(
            base_url="http://localhost:1", model="m", api_key_env_var="AIEB_TEST_KEY"
        )
        self.assertFalse(hasattr(adapter, "api_key"))
        for field_name in adapter.__dataclass_fields__:
            self.assertNotEqual(getattr(adapter, field_name), "super-secret-value")


# ---------------------------------------------------------------------------
# The loop itself: async tests via IsolatedAsyncioTestCase (this repo's
# established convention - see test_eng019_sandbox_threat_model.py).
# ---------------------------------------------------------------------------


class ModelTrackReferenceLoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = _make_test_tmp_dir()
        supported, reason = _tmp_dir_supports_nested_ops(self._tmp)
        if not supported:
            try:
                _rmtree_windows_safe(self._tmp)
            except OSError:
                pass  # the same restriction being reported may also block this cleanup
            self.skipTest(reason)
        self.addCleanup(_rmtree_windows_safe, self._tmp)
        self.tmp_path = Path(self._tmp)
        self.env = _FakeEnvironment(self.tmp_path / "workspace")
        self._env_overrides: list[str] = []

    def _set_env(self, key: str, value: str) -> None:
        os.environ[key] = value
        self._env_overrides.append(key)
        self.addCleanup(os.environ.pop, key, None)

    def _make_loop(
        self,
        provider=None,
        *,
        provider_kind=None,
        requested_model=None,
        base_url=None,
        settings=None,
        expected_settings_digest=None,
        model_name=None,
    ) -> ModelTrackReferenceLoop:
        return ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            model_name=model_name,
            provider=provider,
            provider_kind=provider_kind,
            requested_model=requested_model,
            base_url=base_url,
            settings=settings,
            expected_settings_digest=expected_settings_digest,
        )

    async def _run(self, loop: ModelTrackReferenceLoop, instruction: str = "do the task") -> None:
        await loop.run(instruction, self.env, context=None)

    # -- malformed tool calls -------------------------------------------------

    async def test_malformed_call_eventual_recovery(self) -> None:
        provider = FakeProviderAdapter(
            script=[
                _response(tool_calls=(ToolCall(id="1", name="read_file", arguments={}),)),
                _response(tool_calls=(ToolCall(id="2", name="submit", arguments={}),)),
            ]
        )
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertTrue(summary["submitted"])
        self.assertEqual(summary["stop_reason"], "submitted")
        self.assertEqual(summary["malformed_call_count"], 1)

    async def test_malformed_call_exhaustion_still_collects_candidate(self) -> None:
        original = model_loop.MAX_RETRIES_PER_STEP
        model_loop.MAX_RETRIES_PER_STEP = 2
        self.addCleanup(setattr, model_loop, "MAX_RETRIES_PER_STEP", original)
        provider = FakeProviderAdapter(
            script=[_response(tool_calls=(ToolCall(id="1", name="nope", arguments={}),))]
        )
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertEqual(summary["stop_reason"], "malformed_call_exhausted")
        self.assertFalse(summary["submitted"])
        self.assertTrue((self.env.root / "submission" / "model_track_summary.json").exists())

    # -- deadline ---------------------------------------------------------------

    async def test_deadline_stops_loop_and_collects_candidate(self) -> None:
        self._set_env(model_loop.ENV_DEADLINE_OVERRIDE, "0")
        provider = FakeProviderAdapter(
            script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))]
        )
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertEqual(summary["stop_reason"], "deadline_reached")
        self.assertEqual(provider.call_count, 0)
        self.assertTrue((self.env.root / "submission" / "model_track_summary.json").exists())

    # -- provider error attribution ----------------------------------------------

    async def _assert_error_attribution(self, error_cls, retryable: bool) -> None:
        original = model_loop.STEP_RETRY_BACKOFF_SECONDS
        model_loop.STEP_RETRY_BACKOFF_SECONDS = 0.0
        self.addCleanup(setattr, model_loop, "STEP_RETRY_BACKOFF_SECONDS", original)
        provider = FakeProviderAdapter(script=[error_cls("boom")])
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertEqual(summary["stop_reason"], "unrecoverable_provider_error")
        self.assertEqual(summary["error_class"], error_cls.__name__)
        if retryable:
            self.assertEqual(summary["provider_retry_count"], MAX_RETRIES_PER_STEP)
            self.assertEqual(provider.call_count, MAX_RETRIES_PER_STEP + 1)
        else:
            self.assertEqual(summary["provider_retry_count"], 0)
            self.assertEqual(provider.call_count, 1)

    async def test_transport_error_is_retryable(self) -> None:
        await self._assert_error_attribution(TransportError, retryable=True)

    async def test_rate_limit_error_is_retryable(self) -> None:
        await self._assert_error_attribution(RateLimitError, retryable=True)

    async def test_authentication_error_is_not_retryable(self) -> None:
        await self._assert_error_attribution(AuthenticationError, retryable=False)

    async def test_invalid_request_error_is_not_retryable(self) -> None:
        await self._assert_error_attribution(InvalidRequestError, retryable=False)

    # -- credential protection (loop-level) --------------------------------------

    async def test_loop_never_interpolates_credential_into_exec_commands(self) -> None:
        self._set_env("AIEB_MODEL_TRACK_API_KEY", "top-secret-credential")
        seen_commands: list[str] = []
        original_exec = self.env.exec

        async def _spying_exec(command, cwd=None, env=None, timeout_sec=None, user=None):
            seen_commands.append(command)
            return await original_exec(command, cwd=cwd, env=env, timeout_sec=timeout_sec, user=user)

        self.env.exec = _spying_exec  # type: ignore[method-assign]

        provider = FakeProviderAdapter(
            script=[
                _response(
                    tool_calls=(
                        ToolCall(id="1", name="run_command", arguments={"command": "echo hi"}),
                    )
                ),
                _response(tool_calls=(ToolCall(id="2", name="submit", arguments={}),)),
            ]
        )
        await self._run(self._make_loop(provider=provider))
        self.assertTrue(seen_commands)
        self.assertTrue(all("top-secret-credential" not in cmd for cmd in seen_commands))

    # -- complete candidate collection -------------------------------------------

    async def test_mid_loop_error_still_produces_a_candidate(self) -> None:
        (self.env.root / "already-here.txt").write_text("existing work\n")
        provider = FakeProviderAdapter(script=[AuthenticationError("bad key")])
        await self._run(self._make_loop(provider=provider))
        candidate = self.env.root / "submission"
        self.assertTrue((candidate / "already-here.txt").exists())
        self.assertTrue((candidate / "model_track_summary.json").exists())

    # -- no silent fallback --------------------------------------------------------

    async def test_reported_model_mismatch_is_disclosed_not_silent(self) -> None:
        provider = FakeProviderAdapter(
            script=[
                _response(
                    tool_calls=(ToolCall(id="1", name="submit", arguments={}),),
                    reported_model="a-different-model-y",
                )
            ]
        )
        await self._run(self._make_loop(provider=provider, requested_model="requested-model-x"))
        summary = _summary(self.env)
        self.assertEqual(summary["requested_model"], "requested-model-x")
        self.assertEqual(summary["reported_model"], "a-different-model-y")
        self.assertEqual(summary["coverage_label"], "estimated_time_limited")

    async def test_matching_reported_model_with_no_declined_settings_is_full_match(self) -> None:
        provider = FakeProviderAdapter(
            script=[
                _response(
                    tool_calls=(ToolCall(id="1", name="submit", arguments={}),),
                    reported_model="same-model",
                )
            ]
        )
        await self._run(self._make_loop(provider=provider, requested_model="same-model"))
        summary = _summary(self.env)
        self.assertEqual(summary["coverage_label"], "full_match")

    async def test_response_declaring_unsupported_settings_is_disclosed(self) -> None:
        provider = FakeProviderAdapter(
            script=[
                _response(
                    tool_calls=(ToolCall(id="1", name="submit", arguments={}),),
                    unsupported_settings=("logprobs",),
                )
            ]
        )
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertEqual(summary["unsupported_settings"], ["logprobs"])
        self.assertEqual(summary["coverage_label"], "estimated_time_limited")

    # -- basic end-to-end tool dispatch (list/read/search/patch/run_command) -----

    async def test_full_tool_dispatch_sequence(self) -> None:
        provider = FakeProviderAdapter(
            script=[
                _response(tool_calls=(ToolCall(id="1", name="list_files", arguments={"path": "."}),)),
                _response(
                    tool_calls=(
                        ToolCall(
                            id="2",
                            name="patch",
                            arguments={"path": "out.txt", "contents": "hello world\n"},
                        ),
                    )
                ),
                _response(
                    tool_calls=(
                        ToolCall(id="3", name="read_file", arguments={"path": "out.txt"}),
                    )
                ),
                _response(
                    tool_calls=(
                        ToolCall(
                            id="4",
                            name="search",
                            arguments={"pattern": "hello", "path": "."},
                        ),
                    )
                ),
                _response(
                    tool_calls=(
                        ToolCall(
                            id="5",
                            name="run_command",
                            arguments={"command": "wc -l out.txt"},
                        ),
                    )
                ),
                _response(
                    tool_calls=(ToolCall(id="6", name="inspect_last_output", arguments={}),)
                ),
                _response(tool_calls=(ToolCall(id="7", name="submit", arguments={"summary": "done"}),)),
            ]
        )
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertTrue(summary["submitted"])
        self.assertEqual((self.env.root / "submission" / "out.txt").read_text(), "hello world\n")


# ---------------------------------------------------------------------------
# Per-trial-safe config wiring: fail-closed provider/model identity, and real
# settings-digest verification (review findings #1 and #3, 2026-09-21).
# ---------------------------------------------------------------------------


class ConfigWiringFailClosedTest(unittest.IsolatedAsyncioTestCase):
    """No os.environ identity/config reads any more: `provider_kind` and
    `requested_model` come only from constructor kwargs (which is what
    `AgentConfig.kwargs`/`AgentConfig.model_name` become - see
    `backends/harbor/backend.py`) or from direct `provider=` injection. A
    real-dispatch entrant (no `provider=` injected) that omits `provider_kind`
    - or that has no resolvable model identity at all - must fail hard, never
    silently default to a fake provider or an "unspecified" model."""

    def setUp(self) -> None:
        self._tmp = _make_test_tmp_dir()
        supported, reason = _tmp_dir_supports_nested_ops(self._tmp)
        if not supported:
            try:
                _rmtree_windows_safe(self._tmp)
            except OSError:
                pass
            self.skipTest(reason)
        self.addCleanup(_rmtree_windows_safe, self._tmp)
        self.tmp_path = Path(self._tmp)
        self.env = _FakeEnvironment(self.tmp_path / "workspace")

    async def test_real_dispatch_without_provider_kind_fails_closed(self) -> None:
        loop = ModelTrackReferenceLoop(logs_dir=self.tmp_path / "logs")
        with self.assertRaisesRegex(RuntimeError, "provider_kind"):
            await loop.run("do the task", self.env, context=None)

    async def test_unknown_provider_kind_fails_closed(self) -> None:
        loop = ModelTrackReferenceLoop(logs_dir=self.tmp_path / "logs", provider_kind="not-a-real-kind")
        with self.assertRaisesRegex(RuntimeError, "unknown provider_kind"):
            await loop.run("do the task", self.env, context=None)

    async def test_openai_compatible_without_base_url_fails_closed(self) -> None:
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs", provider_kind="openai_compatible", requested_model="m"
        )
        with self.assertRaisesRegex(RuntimeError, "base_url"):
            await loop.run("do the task", self.env, context=None)

    async def test_real_dispatch_without_resolvable_requested_model_fails_closed(self) -> None:
        # provider_kind is explicit ("fake" dispatches cleanly), but neither requested_model
        # nor Harbor's AgentConfig.model_name (model_name=) was ever supplied.
        loop = ModelTrackReferenceLoop(logs_dir=self.tmp_path / "logs", provider_kind="fake")
        with self.assertRaisesRegex(RuntimeError, "requested_model"):
            await loop.run("do the task", self.env, context=None)

    async def test_provider_kind_fake_dispatches_through_real_config_wiring(self) -> None:
        # No provider= direct injection at all: provider_kind="fake" alone (the same value
        # a real ExecutionSpec.agent_kwargs={'provider_kind': 'fake'} would carry through
        # AgentConfig.kwargs) is enough to run cleanly end to end.
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            provider_kind="fake",
            requested_model="wired-via-agent-kwargs",
        )
        await loop.run("do the task", self.env, context=None)
        summary = _summary(self.env)
        self.assertEqual(summary["requested_model"], "wired-via-agent-kwargs")

    async def test_model_name_from_harbor_agent_config_is_used_as_requested_model(self) -> None:
        # `model_name=` is exactly the keyword `AgentFactory.create_agent_from_config`
        # passes from the real `AgentConfig.model_name` (see harbor/agents/factory.py) -
        # BaseAgent.__init__ stores it as self.model_name, which _resolve_requested_model
        # falls back to when no explicit requested_model override is given.
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            model_name="harbor-agent-config-model",
            provider_kind="fake",
        )
        await loop.run("do the task", self.env, context=None)
        summary = _summary(self.env)
        self.assertEqual(summary["requested_model"], "harbor-agent-config-model")


class SettingsDigestVerificationTest(unittest.IsolatedAsyncioTestCase):
    """`settings` is a real payload the loop must verify against
    `expected_settings_digest` before proceeding (never decode a hash back
    into settings - that's not how ModelProfile.settings_digest works), then
    thread the REAL settings into the provider - not the previously
    hardcoded `settings = {}`."""

    def setUp(self) -> None:
        self._tmp = _make_test_tmp_dir()
        supported, reason = _tmp_dir_supports_nested_ops(self._tmp)
        if not supported:
            try:
                _rmtree_windows_safe(self._tmp)
            except OSError:
                pass
            self.skipTest(reason)
        self.addCleanup(_rmtree_windows_safe, self._tmp)
        self.tmp_path = Path(self._tmp)
        self.env = _FakeEnvironment(self.tmp_path / "workspace")

    @staticmethod
    def _digest(settings: dict) -> str:
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()

    async def test_mismatched_settings_digest_fails_closed(self) -> None:
        provider = FakeProviderAdapter(
            script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))]
        )
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            provider=provider,
            settings={"temperature": 0.2},
            expected_settings_digest="0" * 64,
        )
        with self.assertRaisesRegex(RuntimeError, "settings-digest mismatch"):
            await loop.run("do the task", self.env, context=None)
        self.assertEqual(provider.call_count, 0)

    async def test_matching_settings_digest_proceeds(self) -> None:
        settings = {"temperature": 0.2}
        provider = FakeProviderAdapter(
            script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))]
        )
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            provider=provider,
            settings=settings,
            expected_settings_digest=self._digest(settings),
        )
        await loop.run("do the task", self.env, context=None)
        summary = _summary(self.env)
        self.assertTrue(summary["submitted"])

    async def test_real_settings_are_threaded_into_unsupported_settings_check(self) -> None:
        # declined_settings makes the FakeProviderAdapter genuinely decline "logprobs" -
        # this only shows up if the loop passes the REAL settings dict through to
        # provider.unsupported_settings(), not the old hardcoded settings = {}.
        settings = {"logprobs": True}
        provider = FakeProviderAdapter(
            declined_settings=("logprobs",),
            script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))],
        )
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            provider=provider,
            requested_model="m",
            settings=settings,
            expected_settings_digest=self._digest(settings),
        )
        await loop.run("do the task", self.env, context=None)
        summary = _summary(self.env)
        self.assertEqual(summary["unsupported_settings"], ["logprobs"])
        self.assertEqual(summary["coverage_label"], "estimated_time_limited")


# ---------------------------------------------------------------------------
# ENG-023 usage-accounting closure: the usage_sink hook fires exactly once,
# on every stopping path, with a plain-dict payload - no DB, no Docker. This
# is the fast, dependency-free proof the hook fires correctly; the real
# end-to-end proof against real Postgres/Harbor is
# scripts/run_eng023_model_loop_spike.py.
# ---------------------------------------------------------------------------


class UsageSinkHookTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = _make_test_tmp_dir()
        supported, reason = _tmp_dir_supports_nested_ops(self._tmp)
        if not supported:
            try:
                _rmtree_windows_safe(self._tmp)
            except OSError:
                pass
            self.skipTest(reason)
        self.addCleanup(_rmtree_windows_safe, self._tmp)
        self.tmp_path = Path(self._tmp)
        self.env = _FakeEnvironment(self.tmp_path / "workspace")
        self.calls: list[dict] = []

    def _sink(self, payload: dict) -> None:
        self.calls.append(payload)

    def _make_loop(self, provider, **kwargs) -> ModelTrackReferenceLoop:
        return ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs",
            provider=provider,
            usage_sink=self._sink,
            **kwargs,
        )

    async def test_sink_called_once_on_submit(self) -> None:
        provider = FakeProviderAdapter(
            script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),), reported_model="reported-x")]
        )
        loop = self._make_loop(provider, requested_model="requested-x")
        await loop.run("do the task", self.env, context=None)
        self.assertEqual(len(self.calls), 1)
        payload = self.calls[0]
        self.assertEqual(payload["actor_role"], "engineer")
        self.assertEqual(payload["requested_model"], "requested-x")
        self.assertEqual(payload["reported_model"], "reported-x")
        self.assertEqual(payload["coverage_label"], "estimated_time_limited")
        self.assertIsInstance(payload["usage_receipts"], list)
        self.assertTrue(len(payload["usage_receipts"]) >= 1)

    async def test_sink_called_once_on_deadline(self) -> None:
        os.environ[model_loop.ENV_DEADLINE_OVERRIDE] = "0"
        self.addCleanup(os.environ.pop, model_loop.ENV_DEADLINE_OVERRIDE, None)
        provider = FakeProviderAdapter(script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))])
        loop = self._make_loop(provider, requested_model="requested-x")
        await loop.run("do the task", self.env, context=None)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["usage_receipts"], [])

    async def test_sink_called_once_on_provider_error(self) -> None:
        provider = FakeProviderAdapter(script=[AuthenticationError("bad key")])
        loop = self._make_loop(provider, requested_model="requested-x")
        await loop.run("do the task", self.env, context=None)
        self.assertEqual(len(self.calls), 1)

    async def test_sink_called_once_on_malformed_call_exhaustion(self) -> None:
        original = model_loop.MAX_RETRIES_PER_STEP
        model_loop.MAX_RETRIES_PER_STEP = 2
        self.addCleanup(setattr, model_loop, "MAX_RETRIES_PER_STEP", original)
        provider = FakeProviderAdapter(script=[_response(tool_calls=(ToolCall(id="1", name="nope", arguments={}),))])
        loop = self._make_loop(provider, requested_model="requested-x")
        await loop.run("do the task", self.env, context=None)
        self.assertEqual(len(self.calls), 1)

    async def test_no_sink_configured_is_unchanged_behavior(self) -> None:
        provider = FakeProviderAdapter(script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))])
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs", provider=provider, requested_model="requested-x"
        )
        await loop.run("do the task", self.env, context=None)
        summary = _summary(self.env)
        self.assertTrue(summary["submitted"])

    async def test_registry_by_id_resolution_mirrors_fake_provider_seam(self) -> None:
        provider = FakeProviderAdapter(script=[_response(tool_calls=(ToolCall(id="1", name="submit", arguments={}),))])
        model_loop.register_usage_sink("test-sink-id", self._sink)
        os.environ[model_loop.ENV_USAGE_SINK_ID] = "test-sink-id"
        self.addCleanup(os.environ.pop, model_loop.ENV_USAGE_SINK_ID, None)
        loop = ModelTrackReferenceLoop(
            logs_dir=self.tmp_path / "logs", provider=provider, requested_model="requested-x"
        )
        await loop.run("do the task", self.env, context=None)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
