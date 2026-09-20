"""ENG-023: the fixed reference model-track coding-agent loop.

This is the model track's one, frozen agent implementation: contestants in
the model track do not bring their own agent (that is the agent track); they
only vary the underlying LLM behind `ModelProfile.requested_model`. This
module is dispatched exactly like any other Harbor installed agent, via
`ExecutionSpec.agent_import_path = "aieb_runner.model_loop:ModelTrackReferenceLoop"`
(see `backends/harbor/backend.py` - no change was needed there; this is the
same `AgentConfig(import_path=...)` mechanism `spike_agent.py` already uses).

Frozen contract (per the architecture's "pin the coding loop" requirement):
SYSTEM_PROMPT, TOOL_SCHEMAS, MAX_STEPS, MAX_RETRIES_PER_STEP,
MAX_CONTEXT_CHARS and STEP_RETRY_BACKOFF_SECONDS are module-level constants,
not per-entrant configuration - only the provider adapter (and therefore the
underlying model) varies between model-track entrants.

Configuration is per-trial-safe constructor input, NOT process-wide
`os.environ` (review finding, 2026-09-21): `HarborBackend.launch()`
(`backends/harbor/backend.py`) runs each trial as its own
`asyncio.create_task(trial.run(), ...)` inside the host worker process, so
multiple trials - potentially with different requested models - can be
genuinely CONCURRENT in that one process. Reading `os.environ` at `run()`
time would therefore be racy across concurrent entrants, not merely "not
wired up yet". The fix uses the real, per-trial-safe mechanism Harbor already
has: `harbor.models.trial.config.AgentConfig.model_name` and `.kwargs` are
passed by `harbor/agents/factory.py::create_agent_from_config` straight into
the agent class's own `__init__` as ordinary per-INSTANCE Python constructor
arguments - never a shared process global. `ExecutionSpec.model_name` /
`ExecutionSpec.agent_kwargs` (`backends/base.py`) flow into exactly those
`AgentConfig` fields in `HarborBackend.launch()`.

Constructor parameters (all optional; see fail-closed behavior below):

  provider               Direct injection of a live `ProviderAdapter`
                          instance. Used by unit tests and the Harbor Docker
                          smoke test, since a live Python object cannot
                          round-trip through Harbor's serializable
                          `AgentConfig.kwargs`. Bypasses provider_kind
                          entirely when given.
  provider_kind           "fake" or "openai_compatible". Real Harbor dispatch
                          (i.e. `provider` was NOT directly injected) MUST
                          set this explicitly - there is no implicit default
                          any more. Omitting it under real dispatch raises
                          `RuntimeError` immediately: this loop never
                          silently falls back to a fake provider identity for
                          a real campaign entrant.
  requested_model         The ModelProfile.requested_model this trial claims.
                          Falls back to `self.model_name` (which
                          `BaseAgent.__init__` sets from Harbor's own
                          `AgentConfig.model_name` - see
                          `harbor/agents/base.py`) when not given. Real
                          dispatch without either raises `RuntimeError`
                          rather than silently reporting "unspecified".
  base_url                openai_compatible only: API base URL.
  api_key_env_var         openai_compatible only: name of the env var holding
                          the API key (default AIEB_MODEL_TRACK_API_KEY). The
                          key itself is still read at call time inside the
                          adapter, never stored here - this one setting
                          legitimately names an env var rather than a value,
                          since the credential itself must never be
                          serialized into `AgentConfig.kwargs`.
  settings                The real settings payload (dict) this entrant's
                          `ModelProfile.settings_digest` was computed over.
                          Verified, not decoded: `run()` recomputes
                          `sha256(json.dumps(settings, sort_keys=True))` and
                          compares it to `expected_settings_digest`, raising
                          on any mismatch (fail-closed, same philosophy as
                          this codebase's other freeze-validation gates -
                          see `scripts/build_release_bundle.py`'s
                          `digest_mismatches` handling). Threaded for real
                          into `provider.complete()` /
                          `provider.unsupported_settings()`.
  expected_settings_digest  The `ModelProfile.settings_digest` to verify
                          `settings` against. `None` skips verification
                          (test convenience).

Still read from `os.environ` at `run()` time, deliberately: these are
test-tuning knobs (bounding a test's own wall-clock/step budget), not
per-entrant identity or credential material, so they carry no cross-entrant
race risk in the way the removed identity env vars did.

  AIEB_MODEL_TRACK_DEADLINE_SEC      Override the wall-clock budget (testing
                                      hook - lets a test force a near-zero
                                      deadline without waiting).
  AIEB_MODEL_TRACK_MAX_STEPS         Override MAX_STEPS (testing hook).
  AIEB_MODEL_TRACK_FAKE_SCRIPT_ID    provider_kind="fake" only: looks up a
                                      pre-registered scripted
                                      `FakeProviderAdapter` (see
                                      `FakeProviderAdapter.register`) - the
                                      seam a same-process caller (a test, or
                                      `scripts/run_eng023_model_loop_spike.py`)
                                      uses to hand a Harbor-constructed
                                      instance a live scripted object, since
                                      that object cannot round-trip through
                                      `AgentConfig.kwargs` either.

A test or smoke script that wants the fake, no-network provider must now say
so explicitly - either `provider_kind="fake"` in `agent_kwargs` (flows
through real `AgentConfig.kwargs`, exercising the real dispatch path) or
direct `provider=FakeProviderAdapter()` injection (bypasses Harbor's config
plumbing entirely, for pure unit tests). Nothing defaults there silently any
more.

Context truncation strategy (see `_truncate_messages`): once the *serialized*
conversation exceeds MAX_CONTEXT_CHARS, the system prompt (index 0) and the
original task instruction (index 1) are always kept, the oldest "middle"
messages are dropped in a single deterministic pass (oldest-first) and
replaced with one placeholder note, and the most recent messages are kept
verbatim. This is deterministic (same conversation always truncates the same
way), keeps the model aware truncation happened (rather than silently
vanishing history), and is cheap (single pass, no re-summarization call).

Complete candidate collection: `_finalize_submission` runs on every stopping
path - `submit` tool call, MAX_STEPS exhaustion, deadline, malformed-call
exhaustion, or an unrecoverable provider error - via a `try/finally` around
the whole loop body, so a trial that errors out still produces a scoreable
candidate under `/workspace/submission/`, never nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from aieb_core.models import BudgetRole
from aieb_runner.accounting import UsageLedger, UsageReceipt
from aieb_runner.model_providers.base import (
    ProviderAdapter,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ToolCall,
)
from aieb_runner.model_providers.fake import FakeProviderAdapter
from aieb_runner.model_providers.openai_compatible import OpenAICompatibleAdapter

# --------------------------------------------------------------------------
# Frozen contract - see module docstring. Do not make these per-entrant
# configuration; only the provider adapter varies between model-track
# entrants.
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the AIEB fixed reference coding agent for the model track.
You operate a sandboxed Linux workspace rooted at /workspace. You have exactly
these tools: list_files, read_file, search, patch, run_command,
inspect_last_output, submit. Call exactly one tool per turn, with arguments
matching its schema exactly. Investigate with list_files/read_file/search,
make changes with patch or run_command, verify your work, and call submit
when you are done or cannot make further progress. Do not claim a change
succeeded without having applied it via patch or run_command. Command output
you receive may be truncated; use inspect_last_output to see more of the
immediately preceding command's output."""

MAX_READ_BYTES = 8_000
MAX_TOOL_RESULT_CHARS = 4_000
MAX_COMMAND_OUTPUT_CHARS = 4_000
MAX_COMMAND_TIMEOUT_SEC = 30

TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "list_files",
        "description": "List files under a directory (bounded depth).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": f"Read up to {MAX_READ_BYTES} bytes of a file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": "Grep-like recursive text search under a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["pattern", "path"],
        },
    },
    {
        "name": "patch",
        "description": "Replace a file's full contents (create it if absent).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "contents": {"type": "string"},
            },
            "required": ["path", "contents"],
        },
    },
    {
        "name": "run_command",
        "description": f"Run a bounded shell command (max {MAX_COMMAND_TIMEOUT_SEC}s).",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "inspect_last_output",
        "description": "Return more of the immediately preceding command's output.",
        "parameters": {
            "type": "object",
            "properties": {"tail_chars": {"type": "integer"}},
            "required": [],
        },
    },
    {
        "name": "submit",
        "description": "Declare the task finished; stops the loop.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": [],
        },
    },
)
_TOOL_SCHEMAS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOL_SCHEMAS}

MAX_STEPS = 40
MAX_RETRIES_PER_STEP = 3
MAX_CONTEXT_CHARS = 24_000
STEP_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_WALL_CLOCK_BUDGET_SEC = 240.0

ENV_DEADLINE_OVERRIDE = "AIEB_MODEL_TRACK_DEADLINE_SEC"
ENV_MAX_STEPS_OVERRIDE = "AIEB_MODEL_TRACK_MAX_STEPS"
ENV_FAKE_SCRIPT_ID = "AIEB_MODEL_TRACK_FAKE_SCRIPT_ID"

_JSON_SCHEMA_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


class ToolValidationError(ValueError):
    """A tool call did not match its schema: unknown tool, missing required
    field, or a field of the wrong type. Fed back to the model as a
    structured error rather than crashing the trial."""


def validate_tool_call(call: ToolCall) -> None:
    """Validate one tool call against TOOL_SCHEMAS. Raises ToolValidationError
    on any violation; this is intentionally a small hand-rolled check (the
    tool set is fixed and small) rather than a JSON Schema library
    dependency."""
    schema = _TOOL_SCHEMAS_BY_NAME.get(call.name)
    if schema is None:
        raise ToolValidationError(f"unknown tool {call.name!r}")
    if not isinstance(call.arguments, dict):
        raise ToolValidationError(f"tool {call.name!r} arguments must be an object")
    properties: dict[str, Any] = schema["parameters"]["properties"]
    required: list[str] = schema["parameters"]["required"]
    for field_name in required:
        if field_name not in call.arguments:
            raise ToolValidationError(
                f"tool {call.name!r} is missing required field {field_name!r}"
            )
    for field_name, value in call.arguments.items():
        prop = properties.get(field_name)
        if prop is None:
            raise ToolValidationError(f"tool {call.name!r} has unknown field {field_name!r}")
        expected_types = _JSON_SCHEMA_TYPES[prop["type"]]
        if not isinstance(value, expected_types):
            raise ToolValidationError(
                f"tool {call.name!r} field {field_name!r} expected {prop['type']}, "
                f"got {type(value).__name__}"
            )


@dataclass
class _LoopOutcome:
    """Everything the structured summary needs, accumulated as the loop runs."""

    requested_model: str
    reported_model: str | None = None
    unsupported_settings: tuple[str, ...] = ()
    steps_taken: int = 0
    malformed_call_count: int = 0
    provider_retry_count: int = 0
    stop_reason: str = "unknown"
    error_class: str | None = None
    submitted: bool = False
    usage_receipts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage_label(self) -> str:
        # ENG-023 "no silent fallback": any disclosed mismatch between
        # requested and reported model identity, or any settings this
        # provider could not honor, means the profile is disclosed/degraded
        # (estimated_time_limited), never silently reported as a full match.
        if self.unsupported_settings:
            return "estimated_time_limited"
        if self.reported_model is not None and self.reported_model != self.requested_model:
            return "estimated_time_limited"
        return "full_match"

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_model": self.requested_model,
            "reported_model": self.reported_model,
            "unsupported_settings": list(self.unsupported_settings),
            "coverage_label": self.coverage_label,
            "steps_taken": self.steps_taken,
            "malformed_call_count": self.malformed_call_count,
            "provider_retry_count": self.provider_retry_count,
            "stop_reason": self.stop_reason,
            "error_class": self.error_class,
            "submitted": self.submitted,
            "usage_receipts": self.usage_receipts,
        }


class ModelTrackReferenceLoop(BaseInstalledAgent):
    """The frozen model-track coding loop. See module docstring."""

    @staticmethod
    def name() -> str:
        return "aieb-model-track-reference-loop"

    def __init__(
        self,
        *args: Any,
        provider: ProviderAdapter | None = None,
        provider_kind: str | None = None,
        requested_model: str | None = None,
        base_url: str | None = None,
        api_key_env_var: str = "AIEB_MODEL_TRACK_API_KEY",
        settings: dict[str, object] | None = None,
        expected_settings_digest: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._injected_provider = provider
        self._provider_kind = provider_kind
        self._requested_model_override = requested_model
        self._base_url = base_url
        self._api_key_env_var = api_key_env_var
        self._settings: dict[str, object] = dict(settings) if settings is not None else {}
        self._expected_settings_digest = expected_settings_digest

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "mkdir -p /installed-agent && "
                "printf 'aieb-model-track-reference-loop 0.1.0\\n' "
                "> /installed-agent/version"
            ),
        )

    def get_version_command(self) -> str:
        return "cat /installed-agent/version"

    # -- provider selection --------------------------------------------------

    def _select_provider(self) -> ProviderAdapter:
        if self._injected_provider is not None:
            return self._injected_provider
        # Fail closed (review finding #1): real Harbor dispatch (no directly-injected
        # provider) must always name its provider_kind explicitly. There is no implicit
        # "fake" default here any more - a misconfigured real entrant must fail loudly,
        # never silently run as a fake, credential-free provider.
        kind = self._provider_kind
        if kind is None:
            raise RuntimeError(
                "ModelTrackReferenceLoop was dispatched without a directly-injected "
                "provider and without an explicit provider_kind; refusing to silently "
                "default to a fake provider identity. Pass provider_kind explicitly via "
                "ExecutionSpec.agent_kwargs (flows through Harbor AgentConfig.kwargs into "
                "this constructor), e.g. agent_kwargs={'provider_kind': 'fake', ...} for a "
                "no-network test double, or {'provider_kind': 'openai_compatible', "
                "'base_url': ...} for a real provider."
            )
        if kind == "fake":
            # AIEB_MODEL_TRACK_FAKE_SCRIPT_ID lets a same-process caller (a test, or
            # scripts/run_eng023_model_loop_spike.py) hand this Harbor-constructed
            # instance a pre-registered scripted adapter, since a live Python object
            # cannot round-trip through `AgentConfig.kwargs` either - see
            # FakeProviderAdapter.register.
            script_id = os.environ.get(ENV_FAKE_SCRIPT_ID)
            if script_id:
                registered = FakeProviderAdapter.get_registered(script_id)
                if registered is not None:
                    return registered
            return FakeProviderAdapter()
        if kind == "openai_compatible":
            if not self._base_url:
                raise RuntimeError(
                    "provider_kind='openai_compatible' requires base_url to be set "
                    "(agent_kwargs={'provider_kind': 'openai_compatible', 'base_url': ...})"
                )
            return OpenAICompatibleAdapter(
                base_url=self._base_url,
                model=self._resolve_requested_model(),
                api_key_env_var=self._api_key_env_var,
            )
        raise RuntimeError(f"unknown provider_kind: {kind!r}")

    def _resolve_requested_model(self) -> str:
        if self._requested_model_override is not None:
            return self._requested_model_override
        if self.model_name is not None:
            return self.model_name
        if self._injected_provider is not None:
            # Direct-injection (test/smoke) convenience only: a directly-injected
            # provider has no config-wiring path to have supplied an identity at all, so
            # this is not the "silent default for real dispatch" the fail-closed check
            # above guards against.
            return "unspecified"
        raise RuntimeError(
            "ModelTrackReferenceLoop could not resolve a requested_model: no "
            "requested_model constructor kwarg, no Harbor AgentConfig.model_name "
            "(ExecutionSpec.model_name), and no directly-injected provider. Real campaign "
            "dispatch must set ExecutionSpec.model_name or pass requested_model "
            "explicitly via agent_kwargs."
        )

    @staticmethod
    def _settings_digest(settings: dict[str, object]) -> str:
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()

    # -- context management ---------------------------------------------------

    @staticmethod
    def _serialized_len(messages: list[ProviderMessage]) -> int:
        return sum(len(message.content) for message in messages)

    @staticmethod
    def _truncate_messages(messages: list[ProviderMessage]) -> list[ProviderMessage]:
        """Deterministic bounded-context truncation. See module docstring for
        the exact strategy: keep index 0 (system prompt) and index 1 (task
        instruction) always; keep as many of the most recent messages as fit;
        drop the middle in one pass, replaced by a single placeholder note."""
        if ModelTrackReferenceLoop._serialized_len(messages) <= MAX_CONTEXT_CHARS or len(messages) <= 3:
            return messages
        head = messages[:2]
        head_len = ModelTrackReferenceLoop._serialized_len(head)
        budget = max(MAX_CONTEXT_CHARS - head_len, 0)
        kept_tail: list[ProviderMessage] = []
        used = 0
        for message in reversed(messages[2:]):
            used += len(message.content)
            if used > budget and kept_tail:
                break
            kept_tail.append(message)
        kept_tail.reverse()
        dropped_count = len(messages) - len(head) - len(kept_tail)
        if dropped_count <= 0:
            return messages
        placeholder = ProviderMessage(
            role="system",
            content=f"[{dropped_count} earlier message(s) truncated to stay within context budget]",
        )
        return [*head, placeholder, *kept_tail]

    # -- sandbox-side tool dispatch --------------------------------------------

    async def _sh(self, environment: BaseEnvironment, command: str, cwd: str = "/workspace") -> str:
        """Run a command directly via environment.exec (not exec_as_agent):
        a nonzero exit code is a normal tool result to hand back to the
        model, not a fatal error for the trial."""
        result = await environment.exec(command=command, cwd=cwd, timeout_sec=MAX_COMMAND_TIMEOUT_SEC)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return json.dumps(
            {
                "return_code": result.return_code,
                "stdout": stdout[:MAX_COMMAND_OUTPUT_CHARS],
                "stderr": stderr[:MAX_COMMAND_OUTPUT_CHARS],
                "truncated": len(stdout) > MAX_COMMAND_OUTPUT_CHARS
                or len(stderr) > MAX_COMMAND_OUTPUT_CHARS,
            }
        )

    async def _dispatch_tool(
        self, environment: BaseEnvironment, call: ToolCall, last_output: dict[str, str]
    ) -> str:
        args = call.arguments
        if call.name == "list_files":
            path = str(args["path"])
            return await self._sh(environment, f"find {_quote(path)} -maxdepth 2 2>&1 | head -c {MAX_COMMAND_OUTPUT_CHARS}")
        if call.name == "read_file":
            path = str(args["path"])
            return await self._sh(environment, f"head -c {MAX_READ_BYTES} {_quote(path)} 2>&1")
        if call.name == "search":
            pattern = str(args["pattern"])
            path = str(args["path"])
            return await self._sh(
                environment,
                f"grep -rn -- {_quote(pattern)} {_quote(path)} 2>&1 | head -c {MAX_COMMAND_OUTPUT_CHARS}",
            )
        if call.name == "patch":
            path = str(args["path"])
            contents = str(args["contents"])
            encoded = base64.b64encode(contents.encode("utf-8")).decode("ascii")
            command = (
                f"mkdir -p $(dirname {_quote(path)}) && "
                f"printf '%s' {_quote(encoded)} | base64 -d > {_quote(path)} && "
                f"echo written"
            )
            return await self._sh(environment, command)
        if call.name == "run_command":
            command = str(args["command"])
            cwd = str(args.get("cwd", "/workspace"))
            output = await self._sh(environment, command, cwd=cwd)
            parsed = json.loads(output)
            last_output["stdout"] = parsed["stdout"]
            last_output["stderr"] = parsed["stderr"]
            return output
        if call.name == "inspect_last_output":
            tail_chars = int(args.get("tail_chars", MAX_COMMAND_OUTPUT_CHARS))
            return json.dumps(
                {
                    "stdout_tail": last_output.get("stdout", "")[-tail_chars:],
                    "stderr_tail": last_output.get("stderr", "")[-tail_chars:],
                }
            )
        if call.name == "submit":
            return json.dumps({"acknowledged": True})
        raise ToolValidationError(f"unknown tool {call.name!r}")

    async def _finalize_submission(self, environment: BaseEnvironment, outcome: _LoopOutcome) -> None:
        """Copy whatever exists in /workspace into /workspace/submission and
        write the structured summary. Runs on EVERY stopping path (see the
        try/finally in run()) so a trial that errors mid-loop still produces
        a scoreable candidate."""
        await environment.exec(
            command=(
                "mkdir -p /workspace/submission && "
                "find /workspace -mindepth 1 -maxdepth 1 ! -name submission "
                "-exec cp -a {} /workspace/submission/ \\; 2>&1 || true"
            ),
            cwd="/workspace",
            timeout_sec=MAX_COMMAND_TIMEOUT_SEC,
        )
        summary_json = json.dumps(outcome.to_dict(), indent=2)
        encoded = base64.b64encode(summary_json.encode("utf-8")).decode("ascii")
        await environment.exec(
            command=(
                f"printf '%s' {_quote(encoded)} | base64 -d "
                "> /workspace/submission/model_track_summary.json"
            ),
            cwd="/workspace",
            timeout_sec=MAX_COMMAND_TIMEOUT_SEC,
        )

    # -- main loop --------------------------------------------------------------

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del context
        provider = self._select_provider()

        # Settings-digest verification (review finding #3): this is a VERIFICATION of a
        # real settings payload the caller provides, not a decode of the digest itself -
        # `ModelProfile.settings_digest` has no raw-payload field by design (the same
        # freeze-by-digest pattern as `EntrantRevision.prompt_digest`/`tools_digest`: the
        # real content lives elsewhere and is checked by recomputing its digest). Fail
        # closed on mismatch, exactly like this codebase's other freeze-validation gates
        # (see scripts/build_release_bundle.py's digest_mismatches handling) - never
        # silently proceed with an unverified settings payload.
        settings: dict[str, object] = dict(self._settings)
        if self._expected_settings_digest is not None:
            actual_digest = self._settings_digest(settings)
            if actual_digest != self._expected_settings_digest:
                raise RuntimeError(
                    "ModelTrackReferenceLoop settings-digest mismatch: expected "
                    f"{self._expected_settings_digest!r}, computed {actual_digest!r} from "
                    "the real settings payload passed via the settings= constructor "
                    "kwarg - refusing to proceed with an unverified settings payload."
                )

        requested_model = self._resolve_requested_model()
        max_steps = int(os.environ.get(ENV_MAX_STEPS_OVERRIDE, MAX_STEPS))
        wall_clock_budget = float(os.environ.get(ENV_DEADLINE_OVERRIDE, DEFAULT_WALL_CLOCK_BUDGET_SEC))
        deadline = time.monotonic() + wall_clock_budget

        outcome = _LoopOutcome(requested_model=requested_model)
        ledger = UsageLedger()
        messages: list[ProviderMessage] = [
            ProviderMessage(role="system", content=SYSTEM_PROMPT),
            ProviderMessage(role="user", content=instruction),
        ]
        last_output: dict[str, str] = {"stdout": "", "stderr": ""}
        malformed_streak = 0
        outcome.unsupported_settings = provider.unsupported_settings(settings)

        try:
            for step in range(max_steps):
                outcome.steps_taken = step + 1
                if time.monotonic() >= deadline:
                    outcome.stop_reason = "deadline_reached"
                    return

                messages = self._truncate_messages(messages)
                response, retries_used, error = self._call_provider_with_retries(
                    provider, tuple(messages), settings
                )
                outcome.provider_retry_count += retries_used
                if error is not None:
                    outcome.stop_reason = "unrecoverable_provider_error"
                    outcome.error_class = type(error).__name__
                    return

                assert response is not None
                outcome.reported_model = response.reported_model
                if response.unsupported_settings:
                    outcome.unsupported_settings = tuple(
                        set(outcome.unsupported_settings) | set(response.unsupported_settings)
                    )
                ledger.record(
                    UsageReceipt(
                        attempt_id=self.name(),
                        role=BudgetRole.ENGINEER,
                        request_id=str(uuid.uuid4()),
                        physical_attempt=step,
                        source="adapter",
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                )

                if not response.tool_calls:
                    malformed_streak += 1
                    messages.append(
                        ProviderMessage(
                            role="assistant",
                            content=response.content,
                        )
                    )
                    messages.append(
                        ProviderMessage(
                            role="user",
                            content="You must call exactly one tool per turn. No tool call was made.",
                        )
                    )
                    if malformed_streak >= MAX_RETRIES_PER_STEP:
                        outcome.stop_reason = "malformed_call_exhausted"
                        return
                    continue

                call = response.tool_calls[0]
                messages.append(
                    ProviderMessage(role="assistant", content=response.content, tool_calls=(call,))
                )
                try:
                    validate_tool_call(call)
                except ToolValidationError as exc:
                    malformed_streak += 1
                    outcome.malformed_call_count += 1
                    messages.append(
                        ProviderMessage(
                            role="tool",
                            content=json.dumps({"error": str(exc)}),
                            tool_call_id=call.id,
                        )
                    )
                    if malformed_streak >= MAX_RETRIES_PER_STEP:
                        outcome.stop_reason = "malformed_call_exhausted"
                        return
                    continue

                malformed_streak = 0
                if call.name == "submit":
                    outcome.submitted = True
                    outcome.stop_reason = "submitted"
                    return

                result = await self._dispatch_tool(environment, call, last_output)
                messages.append(
                    ProviderMessage(
                        role="tool",
                        content=result[:MAX_TOOL_RESULT_CHARS],
                        tool_call_id=call.id,
                    )
                )

            outcome.stop_reason = "max_steps_reached"
        finally:
            outcome.usage_receipts = [
                {
                    "role": receipt.role.value,
                    "request_id": receipt.request_id,
                    "physical_attempt": receipt.physical_attempt,
                    "input_tokens": receipt.input_tokens,
                    "output_tokens": receipt.output_tokens,
                }
                for receipt in ledger.receipts()
            ]
            await self._finalize_submission(environment, outcome)

    def _call_provider_with_retries(
        self,
        provider: ProviderAdapter,
        messages: tuple[ProviderMessage, ...],
        settings: dict[str, object],
    ) -> tuple[ProviderResponse | None, int, ProviderError | None]:
        """Call the provider, retrying retryable errors up to
        MAX_RETRIES_PER_STEP times with a fixed backoff. Returns
        (response, retries_used, terminal_error). A non-retryable error is
        returned immediately as the terminal error with zero retries."""
        retries_used = 0
        while True:
            try:
                response = provider.complete(messages, TOOL_SCHEMAS, settings)
                return response, retries_used, None
            except ProviderError as exc:
                if not exc.retryable or retries_used >= MAX_RETRIES_PER_STEP:
                    return None, retries_used, exc
                retries_used += 1
                self._sleep(STEP_RETRY_BACKOFF_SECONDS * retries_used)

    @staticmethod
    def _sleep(seconds: float) -> None:
        # Deliberately synchronous: retries are between provider HTTP calls,
        # which are themselves synchronous (urllib) - see OpenAICompatibleAdapter.
        # Kept as a separate staticmethod so tests can monkeypatch it to avoid
        # real sleeping.
        if seconds > 0:
            time.sleep(seconds)


def _quote(value: str) -> str:
    """POSIX shell single-quote a value for safe interpolation into a
    command string run inside the sandbox."""
    return "'" + value.replace("'", "'\\''") + "'"
