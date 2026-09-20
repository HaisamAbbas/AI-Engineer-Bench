"""Real Harbor Docker integration smoke test for ENG-023's model-track loop.

Modeled closely on scripts/run_eng001_spike.py: launches HarborBackend
against real Docker, dispatching `aieb_runner.model_loop:ModelTrackReferenceLoop`
via `ExecutionSpec.agent_import_path` - the exact mechanism the independent
review found broken (the module didn't exist / didn't import). This is the
concrete, executed proof that it now does.

Config wiring (2026-09-21 review follow-up): this now configures the loop
through the real, per-trial-safe mechanism - `ExecutionSpec.model_name` and
`ExecutionSpec.agent_kwargs`, which `HarborBackend.launch()` threads straight
into Harbor's own `AgentConfig.model_name`/`AgentConfig.kwargs`, which
`harbor/agents/factory.py::create_agent_from_config` passes as ordinary
per-INSTANCE Python constructor arguments to `ModelTrackReferenceLoop.__init__`
- NOT process-wide `os.environ`, which a review found unsafe for real
concurrent campaign dispatch (`HarborBackend.launch()` runs each trial as its
own `asyncio.create_task`, so concurrent trials with different requested
models could race each other's env-var reads). `agent_kwargs={"provider_kind":
"fake", ...}` is the explicit opt-in this loop now requires under real
dispatch (no more implicit "fake" default) - forcing the provider to
FakeProviderAdapter so this costs no real money and needs no real
credentials, while still exercising the real BaseInstalledAgent contract
end-to-end inside a genuine Docker container: install(), get_version_command(),
and a full run() tool-calling loop that lists/patches/reads files and runs a
command via real environment.exec() calls dispatched into the container,
then submits.

Fixture choice: this uses a NEW minimal fixture,
tests/fixtures/eng023_model_loop/task, rather than reusing
tests/fixtures/eng001_harbor/task. That fixture's docker-compose topology
(a "main" service that depends_on a second "application" service, gated by
an environment-level HTTP healthcheck) turned out to be a poor fit here: on
this dev host, HarborBackend's deny-by-default egress guard
(EgressGuardProxy) routes ALL container egress - even a plain intra-compose
call from "main" to "application" - through a proxy process running on the
HOST. "application" is a Docker Compose-internal DNS name that only
resolves inside the compose network's own embedded DNS, never from the host
machine, so the guard process itself cannot dial it
(confirmed directly: `socket.create_connection(("application", 8080))` from
this host raises `gaierror`). That made the eng001 fixture's own
environment healthcheck fail intermittently/consistently regardless of which
agent was under test - reproduced identically against the pre-existing,
unrelated `spike_agent` used by ENG-001, so it is a real, pre-existing
environment limitation, not a regression this change introduced, and not
something ENG-023 should paper over by quietly working around it in
production code. The model-track loop itself has no need for any
multi-service topology or network access at all (its default
FakeProviderAdapter makes zero network calls), so the new fixture is a
single "main" service with `network_mode = "no-network"` throughout and no
cross-service healthcheck - avoiding the unrelated bug entirely rather than
routing around it with special-cased isolation policy overrides.
"""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from pathlib import Path
from uuid import uuid4

from aieb_runner import ExecutionSpec, ExecutionState
from aieb_runner.backends.harbor import HarborBackend
from aieb_runner.model_providers.base import ProviderResponse, ToolCall, UsageInfo
from aieb_runner.model_providers.fake import FakeProviderAdapter


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "eng023_model_loop" / "task"

_SCRIPT_ID = "eng023-harbor-smoke"


def _register_scripted_provider() -> None:
    """Register a deterministic scripted FakeProviderAdapter and point the
    loop at it via AIEB_MODEL_TRACK_FAKE_SCRIPT_ID (see
    FakeProviderAdapter.register): the agent instance Harbor constructs from
    `agent_import_path` runs in this same host process (Harbor's
    installed-agent `run()` executes as an asyncio task on the host,
    dispatching into the sandboxed container only for `environment.exec()`
    calls - see HarborBackend.launch()), so the registry is visible to it.
    This exercises the real tool-calling contract (list, patch, run_command,
    submit) end to end with zero network calls."""
    script = [
        ProviderResponse(
            content="",
            tool_calls=(ToolCall(id="1", name="list_files", arguments={"path": "."}),),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=42, completion_tokens=8),
        ),
        ProviderResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id="2",
                    name="patch",
                    arguments={
                        "path": "hello-from-model-track.txt",
                        "contents": "hello from the ENG-023 model-track reference loop\n",
                    },
                ),
            ),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=50, completion_tokens=12),
        ),
        ProviderResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id="3",
                    name="run_command",
                    arguments={"command": "cat hello-from-model-track.txt"},
                ),
            ),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=55, completion_tokens=10),
        ),
        ProviderResponse(
            content="",
            tool_calls=(ToolCall(id="4", name="submit", arguments={"summary": "done"}),),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=60, completion_tokens=5),
        ),
    ]
    FakeProviderAdapter.register(_SCRIPT_ID, FakeProviderAdapter(script=script))
    os.environ["AIEB_MODEL_TRACK_FAKE_SCRIPT_ID"] = _SCRIPT_ID


async def run_spike() -> dict[str, object]:
    # Explicit opt-in, via the real per-trial-safe config mechanism (not os.environ):
    # ExecutionSpec.agent_kwargs={"provider_kind": "fake", ...} flows through
    # HarborBackend.launch() into Harbor's real AgentConfig.kwargs, which
    # harbor/agents/factory.py passes straight into ModelTrackReferenceLoop.__init__ as
    # ordinary per-instance constructor keyword arguments. This is the loop's new
    # fail-closed contract: provider_kind must always be explicit under real dispatch,
    # there is no implicit "fake" default any more.
    _register_scripted_provider()

    backend = HarborBackend()
    capability = await backend.preflight()
    if not capability.ready:
        raise RuntimeError(f"Harbor preflight failed: {capability}")

    trial_name = f"eng023-{uuid4().hex[:10]}"
    handle = await backend.launch(
        ExecutionSpec(
            task_dir=FIXTURE,
            runs_dir=ROOT / ".cache" / "eng023-runs",
            trial_name=trial_name,
            agent_import_path="aieb_runner.model_loop:ModelTrackReferenceLoop",
            agent_timeout_sec=90.0,
            # AgentConfig.model_name (per-instance, per-trial-safe) - not an env var.
            model_name="spike-requested-model",
            # AgentConfig.kwargs (per-instance, per-trial-safe) - not an env var. The
            # scripted adapter itself is still handed off via the
            # AIEB_MODEL_TRACK_FAKE_SCRIPT_ID registry seam (a live Python object cannot
            # round-trip through AgentConfig.kwargs' JSON-serializable dict either), but
            # WHICH provider kind to construct is now real per-trial config, not an
            # env var read at run() time.
            agent_kwargs={"provider_kind": "fake"},
        )
    )

    status = await backend.status(handle)
    for _ in range(720):
        if status.state != ExecutionState.RUNNING:
            break
        await asyncio.sleep(0.25)
        status = await backend.status(handle)
    if status.state != ExecutionState.COMPLETED:
        await backend.stop(handle, "spike wall-clock limit")
        raise RuntimeError(f"Harbor trial did not complete: {status}")

    artifacts = await backend.collect(handle)
    result = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
    candidate = artifacts.trial_dir / "artifacts" / "candidate"
    summary_path = candidate / "model_track_summary.json"
    model_track_summary = (
        json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None
    )
    cleanup = await backend.cleanup(handle)
    summary: dict[str, object] = {
        "trial_name": trial_name,
        "trial_dir": str(artifacts.trial_dir),
        "harbor_version": capability.version,
        "state": status.state.value,
        "exception_info": result.get("exception_info"),
        "agent_version": result["agent_info"]["version"],
        "reward": (result.get("verifier_result") or {}).get("rewards", {}).get("reward"),
        "model_track_summary": model_track_summary,
        "candidate_files": sorted(
            str(path.relative_to(candidate)) for path in candidate.rglob("*") if path.is_file()
        )
        if candidate.is_dir()
        else [],
        "cleanup_clean": cleanup.clean,
        "remaining_resource_ids": list(cleanup.remaining_resource_ids),
    }

    expected = {
        "state": "completed",
        "agent_version": "aieb-model-track-reference-loop 0.1.0",
        "reward": 1.0,
        "cleanup_clean": True,
    }
    mismatches = {
        key: {"expected": value, "actual": summary.get(key)}
        for key, value in expected.items()
        if summary.get(key) != value
    }
    if model_track_summary is None:
        mismatches["model_track_summary"] = {"expected": "present", "actual": None}
    elif not model_track_summary.get("submitted"):
        mismatches["model_track_summary.submitted"] = {
            "expected": True,
            "actual": model_track_summary.get("submitted"),
        }
    if "hello-from-model-track.txt" not in summary["candidate_files"]:
        mismatches["candidate_files"] = {
            "expected": "contains hello-from-model-track.txt",
            "actual": summary["candidate_files"],
        }
    if mismatches:
        raise AssertionError(f"ENG-023 model-loop Harbor smoke mismatch: {mismatches}")
    return summary


def main() -> int:
    try:
        summary = asyncio.run(run_spike())
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
