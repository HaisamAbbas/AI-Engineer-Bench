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

Usage-accounting closure (2026-09-21 review follow-up): this spike now also
proves the ENG-023 review's "does not create authoritative API
UsageRequestRow/UsageReceiptRow records" finding is closed for real. It
registers a real usage_sink callback (via the SAME registry-by-id seam as
AIEB_MODEL_TRACK_FAKE_SCRIPT_ID/FakeProviderAdapter.register - see
`aieb_runner.model_loop.register_usage_sink`/`ENV_USAGE_SINK_ID`) that,
when the loop's `_finalize_submission` try/finally fires it, opens a real
session against the same test PostgreSQL every other real-DB test in this
repo uses (AIEB_DATABASE_URL) and calls the new
`aieb_api.worker.repository.record_usage_receipts`/`record_model_identity`
writers - the SAME production code path a future real worker-to-Harbor
dispatcher would call, not a mock. `attempt_id=None` is used deliberately:
building a full campaign/trial/attempt fixture chain just to attach this
spike's usage rows to is unrelated to what this spike proves (the
loop -> sink -> repository -> real Postgres pipeline), and `attempt_id`
is schema-legal as NULL for exactly this "no real attempt yet" case (see
`usage_request.attempt_id`'s existing nullability, mirrored by the new
`attempt_model_identity.attempt_id`). After Harbor's real Docker teardown
completes, this script independently re-queries the real database (a
fresh session, not anything held open across the run) and asserts the
expected UsageRequestRow/UsageReceiptRow/AttemptModelIdentityRow rows
actually exist with the right values - this is the concrete, executed
proof that usage flows from a real Harbor Docker run into real Postgres
rows, not merely into `model_track_summary.json`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from uuid import uuid4

from aieb_runner import ExecutionSpec, ExecutionState
from aieb_runner.backends.harbor import HarborBackend
from aieb_runner.model_loop import ENV_USAGE_SINK_ID, register_usage_sink
from aieb_runner.model_providers.base import ProviderResponse, ToolCall, UsageInfo
from aieb_runner.model_providers.fake import FakeProviderAdapter


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "eng023_model_loop" / "task"

for _src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

_SCRIPT_ID = "eng023-harbor-smoke"
_USAGE_SINK_ID = "eng023-harbor-smoke-usage-sink"


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


def _register_usage_sink() -> None:
    """Register a real usage_sink callback via the SAME registry-by-id seam
    as _register_scripted_provider above (see
    aieb_runner.model_loop.register_usage_sink/ENV_USAGE_SINK_ID) - the
    Harbor-constructed loop instance's run() executes as an asyncio task in
    THIS host process, so this registry is visible to it. The callback opens
    a real session against the real test PostgreSQL (AIEB_DATABASE_URL) and
    calls the real aieb_api.worker.repository writers - the ONLY production
    code path (as of this ticket) that ever inserts a real UsageRequestRow/
    UsageReceiptRow/AttemptModelIdentityRow. `aieb_runner.model_loop` itself
    never imports aieb_api - only this smoke script (standing in for a
    future real worker-to-Harbor dispatcher) does."""
    from aieb_api import db  # noqa: PLC0415 - deliberately deferred: aieb_runner must not import aieb_api
    from aieb_api.worker import repository

    database_url = os.environ.get("AIEB_DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "AIEB_DATABASE_URL must be set for the ENG-023 usage-accounting closure proof "
            "(real Postgres, not a mock) - see docs/implementation/SESSION_HANDOFF.md's "
            "ENG-014 setup instructions"
        )
    db.configure(database_url)
    session_factory = db.session_factory()

    def _sink(payload: dict[str, object]) -> None:
        usage_receipts = payload.get("usage_receipts") or []
        with session_factory() as session:
            if usage_receipts:
                repository.record_usage_receipts(
                    session,
                    # No real Attempt row exists for this spike - attaching usage to one
                    # is unrelated to what this proof demonstrates (the sink -> repository
                    # -> real Postgres pipeline itself). Schema-legal: attempt_id is
                    # nullable on both usage_request and attempt_model_identity for
                    # exactly this "no real attempt yet" case.
                    attempt_id=None,
                    actor_role=str(payload["actor_role"]),
                    receipts=[
                        repository.UsageReceiptInput(
                            request_id=str(receipt["request_id"]),
                            physical_retry=int(receipt["physical_attempt"]),
                            input_tokens=receipt.get("input_tokens"),
                            output_tokens=receipt.get("output_tokens"),
                        )
                        for receipt in usage_receipts
                    ],
                )
            repository.record_model_identity(
                session,
                attempt_id=None,
                actor_role=str(payload["actor_role"]),
                requested_model=str(payload["requested_model"]),
                reported_model=payload.get("reported_model"),
                settings_digest=payload.get("settings_digest"),
                coverage_label=str(payload["coverage_label"]),
            )

    register_usage_sink(_USAGE_SINK_ID, _sink)
    os.environ[ENV_USAGE_SINK_ID] = _USAGE_SINK_ID


def _query_recorded_usage_rows() -> dict[str, object]:
    """Independently re-query the real database AFTER Harbor's Docker
    teardown has completed, in a FRESH session - the concrete proof that
    real rows exist in real Postgres, not merely that the sink was called."""
    from sqlalchemy import select

    from aieb_api import db
    from aieb_api import models as api_models

    with db.session_factory()() as session:
        identity = session.execute(
            select(api_models.AttemptModelIdentityRow)
            .where(
                api_models.AttemptModelIdentityRow.attempt_id.is_(None),
                api_models.AttemptModelIdentityRow.actor_role == "engineer",
                api_models.AttemptModelIdentityRow.requested_model == "spike-requested-model",
            )
            .order_by(api_models.AttemptModelIdentityRow.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if identity is None:
            return {"identity_row_found": False}

        usage_requests = session.execute(
            select(api_models.UsageRequestRow)
            .where(
                api_models.UsageRequestRow.attempt_id.is_(None),
                api_models.UsageRequestRow.actor_role == "engineer",
            )
            .order_by(api_models.UsageRequestRow.created_at.desc())
            .limit(4)
        ).scalars().all()
        receipt_count = 0
        for usage_request in usage_requests:
            receipt_count += len(
                session.execute(
                    select(api_models.UsageReceiptRow).where(
                        api_models.UsageReceiptRow.usage_request_id == usage_request.id
                    )
                ).scalars().all()
            )
        return {
            "identity_row_found": True,
            "identity_requested_model": identity.requested_model,
            "identity_reported_model": identity.reported_model,
            "identity_coverage_label": identity.coverage_label,
            "identity_settings_digest": identity.settings_digest,
            "usage_request_rows_found": len(usage_requests),
            "usage_receipt_rows_found": receipt_count,
        }


async def run_spike() -> dict[str, object]:
    # Explicit opt-in, via the real per-trial-safe config mechanism (not os.environ):
    # ExecutionSpec.agent_kwargs={"provider_kind": "fake", ...} flows through
    # HarborBackend.launch() into Harbor's real AgentConfig.kwargs, which
    # harbor/agents/factory.py passes straight into ModelTrackReferenceLoop.__init__ as
    # ordinary per-instance constructor keyword arguments. This is the loop's new
    # fail-closed contract: provider_kind must always be explicit under real dispatch,
    # there is no implicit "fake" default any more.
    _register_scripted_provider()
    _register_usage_sink()

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

    # ENG-023 usage-accounting closure: independently re-query the real database AFTER
    # Harbor's Docker teardown (cleanup) has already completed above - the concrete proof
    # that the usage_sink -> repository writers actually persisted real rows into real
    # Postgres, not merely that the in-container model_track_summary.json was written.
    summary["usage_accounting"] = _query_recorded_usage_rows()

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

    usage_accounting = summary["usage_accounting"]
    expected_usage_accounting = {
        "identity_row_found": True,
        "identity_requested_model": "spike-requested-model",
        "identity_reported_model": "fake-reference-model-v1",
        "identity_coverage_label": "estimated_time_limited",
    }
    for key, value in expected_usage_accounting.items():
        if usage_accounting.get(key) != value:
            mismatches[f"usage_accounting.{key}"] = {"expected": value, "actual": usage_accounting.get(key)}
    if usage_accounting.get("identity_row_found") and not usage_accounting.get("usage_request_rows_found"):
        mismatches["usage_accounting.usage_request_rows_found"] = {
            "expected": "> 0", "actual": usage_accounting.get("usage_request_rows_found"),
        }
    if usage_accounting.get("identity_row_found") and not usage_accounting.get("usage_receipt_rows_found"):
        mismatches["usage_accounting.usage_receipt_rows_found"] = {
            "expected": "> 0", "actual": usage_accounting.get("usage_receipt_rows_found"),
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
