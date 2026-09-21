"""Run a real model-track coding agent (or a scripted stand-in) against the
real-repo RAG-05 task in real Docker via Harbor.

This is the end-user-style test: the fixed reference loop
(aieb_runner.model_loop:ModelTrackReferenceLoop) receives the task instruction
and the deliberately broken real-source repository (vendored sqlite-utils
3.38), edits files inside a real container with the loop's real tools, and is
scored by the trusted RAG-05 evaluator running in a separate verifier
container. Reward 1.0 means the agent's repair passed the held-out evaluation.

Provider selection (fail-closed, per the ENG-023 contract):
- default: provider_kind="openai_compatible" against an OpenAI-compatible
  chat endpoint. Requires AIEB_MODEL_TRACK_API_KEY in this process's
  environment (read at call time only; never written to any file). GLM
  example:
      $env:AIEB_MODEL_TRACK_API_KEY='<key>'
      python scripts/run_rag05_model_agent.py --model glm-4.6
- --fake: a scripted provider that performs a genuine repair through the real
  tool contract (patch backend.py with the task's reference implementation,
  run dev_tests, submit). Zero network calls, zero cost; proves the whole
  container/loop/verifier pipeline before spending real money.

No Postgres/usage-sink is configured: the loop's model_track_summary.json
carries the usage receipts (sink=None is today's supported local behavior).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tarfile
import time
import traceback
from pathlib import Path
from uuid import uuid4

from aieb_runner import ExecutionSpec, ExecutionState
from aieb_runner.backends.harbor import HarborBackend
from aieb_runner.model_loop import ENV_FAKE_SCRIPT_ID
from aieb_runner.model_providers.base import ProviderResponse, ToolCall, UsageInfo
from aieb_runner.model_providers.fake import FakeProviderAdapter

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "suites" / "real" / "rag.corpus-index-drift"
FIXTURE = ROOT / "tests" / "fixtures" / "rag05_model_agent" / "task"
_SCRIPT_ID = "rag05-model-agent-fake"


def _stage_fixture() -> None:
    """Stage the run inputs into the Harbor fixture build contexts: the
    workspace tarball (environment/) and the trusted evaluator snapshot
    (tests/rag05/). Regenerated on every run so they can never drift from
    the canonical task/evaluator sources."""
    workspace_tar = FIXTURE / "environment" / "workspace.tar.gz"
    with tarfile.open(workspace_tar, "w:gz") as tar:
        for path in sorted(TASK.joinpath("repo").rglob("*")):
            if "__pycache__" in path.parts:
                continue
            tar.add(path, arcname=str(path.relative_to(TASK / "repo")))

    snapshot = FIXTURE / "tests" / "rag05"
    if snapshot.exists():
        shutil.rmtree(snapshot, ignore_errors=True)
    snapshot.mkdir(parents=True)
    for name in ("__init__.py", "fixture.py", "evaluator.py"):
        shutil.copyfile(ROOT / "tests" / "maintainer" / "rag05" / name, snapshot / name)


def _register_fake_script() -> None:
    """Scripted provider that performs a genuine repair through the real tool
    contract: patch the broken backend with the task's reference
    implementation, run the public self-check, then submit."""
    reference = (TASK / "reference" / "backend.py").read_text(encoding="utf-8")
    script = [
        ProviderResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id="1",
                    name="patch",
                    arguments={"path": "knowledge_service/backend.py", "contents": reference},
                ),
            ),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=48, completion_tokens=16),
        ),
        ProviderResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id="2",
                    name="run_command",
                    arguments={"command": "python dev_tests/selfcheck.py"},
                ),
            ),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=64, completion_tokens=24),
        ),
        ProviderResponse(
            content="",
            tool_calls=(ToolCall(id="3", name="submit", arguments={"summary": "repair applied"}),),
            reported_model="fake-reference-model-v1",
            finish_reason="tool_calls",
            usage=UsageInfo(prompt_tokens=80, completion_tokens=8),
        ),
    ]
    FakeProviderAdapter.register(_SCRIPT_ID, FakeProviderAdapter(script=script))
    os.environ[ENV_FAKE_SCRIPT_ID] = _SCRIPT_ID


async def run_agent(args: argparse.Namespace) -> dict[str, object]:
    _stage_fixture()

    requested_model = args.model
    if args.fake:
        _register_fake_script()
        agent_kwargs: dict[str, object] = {"provider_kind": "fake"}
        max_steps, deadline_sec = 8, 240.0
    else:
        if not os.environ.get("AIEB_MODEL_TRACK_API_KEY"):
            raise SystemExit(
                "Set AIEB_MODEL_TRACK_API_KEY in the environment before running a "
                "real provider (the key is read at call time and never written "
                "to disk). For a zero-cost pipeline check use --fake instead."
            )
        agent_kwargs = {
            "provider_kind": "openai_compatible",
            "base_url": args.base_url,
            "requested_model": requested_model,
            "settings": {"temperature": 0.2},
        }
        max_steps, deadline_sec = args.max_steps, float(args.deadline_sec)

    os.environ["AIEB_MODEL_TRACK_MAX_STEPS"] = str(max_steps)
    os.environ["AIEB_MODEL_TRACK_DEADLINE_SEC"] = str(deadline_sec)

    backend = HarborBackend()
    capability = await backend.preflight()
    if not capability.ready:
        raise RuntimeError(f"Harbor preflight failed: {capability}")

    trial_name = f"rag05-agent-{'fake' if args.fake else 'real'}-{uuid4().hex[:10]}"
    handle = await backend.launch(
        ExecutionSpec(
            task_dir=FIXTURE,
            runs_dir=ROOT / ".cache" / "rag05-agent-runs",
            trial_name=trial_name,
            agent_import_path="aieb_runner.model_loop:ModelTrackReferenceLoop",
            agent_timeout_sec=deadline_sec + 240.0,
            model_name=requested_model,
            agent_kwargs=agent_kwargs,
        )
    )

    status = await backend.status(handle)
    started = time.monotonic()
    while status.state == ExecutionState.RUNNING and time.monotonic() - started < deadline_sec + 420.0:
        await asyncio.sleep(2.0)
        status = await backend.status(handle)
    if status.state != ExecutionState.COMPLETED:
        await backend.stop(handle, "runner wall-clock limit")
        raise RuntimeError(f"Harbor trial did not complete: {status.state}")

    artifacts = await backend.collect(handle)
    result = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
    candidate = artifacts.trial_dir / "artifacts" / "candidate"
    summary_path = candidate / "model_track_summary.json"
    model_track_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None
    harness_out = artifacts.trial_dir / "verifier" / "harness.out"
    verifier_outcome: object = None
    if harness_out.is_file():
        raw_harness = harness_out.read_text(encoding="utf-8")
        try:
            verifier_outcome = json.loads(raw_harness)
        except json.JSONDecodeError:
            verifier_outcome = {"unparsed_output_tail": raw_harness[-2000:]}
    cleanup = await backend.cleanup(handle)

    summary: dict[str, object] = {
        "trial_name": trial_name,
        "trial_dir": str(artifacts.trial_dir),
        "harbor_version": capability.version,
        "state": status.state.value,
        "reward": (result.get("verifier_result") or {}).get("rewards", {}).get("reward"),
        "submitted": (model_track_summary or {}).get("submitted"),
        "requested_model": (model_track_summary or {}).get("requested_model"),
        "reported_model": (model_track_summary or {}).get("reported_model"),
        "steps_taken": (model_track_summary or {}).get("steps_taken"),
        "stop_reason": (model_track_summary or {}).get("stop_reason"),
        "usage_receipts": (model_track_summary or {}).get("usage_receipts"),
        "verifier_outcome": verifier_outcome,
        "cleanup_clean": cleanup.clean,
    }
    expected = {"state": "completed", "reward": 1.0, "cleanup_clean": True}
    mismatches = {key: summary.get(key) for key, value in expected.items() if summary.get(key) != value}
    if not summary["submitted"]:
        mismatches["submitted"] = False
    if mismatches:
        raise AssertionError(f"RAG-05 model-agent run mismatch: {mismatches}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="scripted zero-cost provider instead of a real LLM")
    parser.add_argument("--base-url", default="https://open.bigmodel.cn/api/paas/v4", help="OpenAI-compatible endpoint base URL")
    parser.add_argument("--model", default="glm-4.6", help="requested model name")
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--deadline-sec", type=int, default=1400)
    args = parser.parse_args()
    try:
        summary = asyncio.run(run_agent(args))
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
