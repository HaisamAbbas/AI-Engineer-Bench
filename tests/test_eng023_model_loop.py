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

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


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
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)
        self.env = _FakeEnvironment(self.tmp_path / "workspace")
        self._env_overrides: list[str] = []

    def _set_env(self, key: str, value: str) -> None:
        os.environ[key] = value
        self._env_overrides.append(key)
        self.addCleanup(os.environ.pop, key, None)

    def _make_loop(self, provider=None) -> ModelTrackReferenceLoop:
        return ModelTrackReferenceLoop(logs_dir=self.tmp_path / "logs", provider=provider)

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
        self._set_env(model_loop.ENV_REQUESTED_MODEL, "requested-model-x")
        provider = FakeProviderAdapter(
            script=[
                _response(
                    tool_calls=(ToolCall(id="1", name="submit", arguments={}),),
                    reported_model="a-different-model-y",
                )
            ]
        )
        await self._run(self._make_loop(provider=provider))
        summary = _summary(self.env)
        self.assertEqual(summary["requested_model"], "requested-model-x")
        self.assertEqual(summary["reported_model"], "a-different-model-y")
        self.assertEqual(summary["coverage_label"], "estimated_time_limited")

    async def test_matching_reported_model_with_no_declined_settings_is_full_match(self) -> None:
        self._set_env(model_loop.ENV_REQUESTED_MODEL, "same-model")
        provider = FakeProviderAdapter(
            script=[
                _response(
                    tool_calls=(ToolCall(id="1", name="submit", arguments={}),),
                    reported_model="same-model",
                )
            ]
        )
        await self._run(self._make_loop(provider=provider))
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


if __name__ == "__main__":
    unittest.main()
