"""V2-GAP-004 section 6 smoke tests for the Harbor dispatch adapter
(`aieb_api.worker.harbor_dispatch`), driven with a synthetic local launcher -
no paid provider, no real Harbor cluster, per that module's own docstring
contract. No database is needed: `freeze_campaign` is a pure planner call, so
this exercises the exact same frozen-manifest shape production code produces,
then the ExecutionSpec translation and the deadline-enforcement loop added
to close finding #2 (the deadline was pinned into the spec but never
actually enforced against a launcher that kept running past it).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src", ROOT / "packages/aieb-runner/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from aieb_core.models import BudgetProfile, CampaignDraft, Cohort, EntrantRevision, ProtocolRevision, RoleBudget, TaskRevision  # noqa: E402
from aieb_core.planner import Registry, freeze_campaign  # noqa: E402

from aieb_api.worker.harbor_dispatch import (  # noqa: E402
    DispatchIntegrityError,
    build_frozen_cell_payload,
    dispatch_cell,
)
from aieb_runner.backends.base import (  # noqa: E402
    CandidateArtifacts,
    CapabilityCheck,
    CapabilityReport,
    CleanupReport,
    ExecutionHandle,
    ExecutionState,
    ExecutionStatus,
)

TASK_SLUG = "rag.document-freshness"


def _resolved_manifest(*, engineer_wall_seconds: int = 1200, verification_wall_seconds: int = 300) -> dict:
    """A frozen aieb.campaign/v1 manifest built via the real planner (the
    same `freeze_campaign` call `routes/campaigns.py::freeze` uses), so this
    test exercises the exact shape the dispatch adapter sees in production -
    not a hand-shaped stand-in."""
    task = TaskRevision.model_validate({
        "schema_version": "aieb.task/v1", "id": TASK_SLUG, "version": "0.1.0", "family_id": "knowledge-service-a",
        "category": "rag", "activity": "repair",
        "source": {"repository_digest": "1" * 64, "commit": "synthetic", "license": "Apache-2.0", "provenance_digest": "2" * 64},
        "environment": {"official_image": "registry.example/aieb@sha256:" + "3" * 64, "engineer_cpu": 1, "engineer_memory_mb": 512, "service_topology_digest": "4" * 64, "egress_policy": "none"},
        "application": {"dependency_mode": "fixture", "entrypoint": ["python", "-m", "knowledge_service.server"], "contract_digest": "5" * 64, "model_profile_id": "deterministic-rag-fixture-v1"},
        "submission": {"include": ["knowledge_service/**"], "protected": ["dev_tests/**"], "max_artifact_bytes": 52_428_800},
        "requirements": [{"id": "api-ready", "severity": "mandatory", "description": "ready"}],
        "evaluator": {"evaluator_digest": "6" * 64, "development_fixture": "rag01-dev-v1", "official_fixture_ref": "maintainer-only:rag01-v1"},
        "profile_compatibility": ["cohort-a"],
    })
    entrant = EntrantRevision.model_validate({
        "schema_version": "aieb.entrant/v1", "id": "agent-a", "track": "agents", "agent_implementation": "demo",
        "agent_version": "1.0.0", "engineer_model": {"provider_class": "demo", "requested_model": "demo-model", "settings_digest": "a" * 64},
        "prompt_digest": "b" * 64, "tools_digest": "c" * 64, "capabilities": ["cpu-fixture-standard-v1"],
        "credential_ref_type": "broker",
    })
    cohort = Cohort(
        schema_version="aieb.cohort/v1", id="cohort-a", track="agents", suite_id="suite-a", protocol_id="protocol-a",
        budget_profile_id="budget-a", dependency_mode="fixture", application_model_profile=task.application,
        hardware_class="cpu-fixture-standard-v1", required_capabilities=["cpu-fixture-standard-v1"],
    )
    protocol = ProtocolRevision(
        schema_version="aieb.protocol/v1", id="protocol-a", scoring_digest="9" * 64, max_replacements=2,
        required_trace_coverage=False, hard_cost_ranking=False,
    )
    budget = BudgetProfile(
        schema_version="aieb.budget/v1", id="budget-a", engineer_wall_seconds=engineer_wall_seconds,
        verification_wall_seconds=verification_wall_seconds, engineer_cpu=2, engineer_memory_mb=1024,
        per_role_budget_usd=[RoleBudget(role=r, limit_usd=None) for r in ("engineer", "dev_application", "verifier_application", "verifier_judge")],
    )
    draft = CampaignDraft(
        schema_version="aieb.campaign-draft/v1", id="draft-1", cohort_id="cohort-a", task_ids=[TASK_SLUG],
        entrant_ids=["agent-a"], repetitions=1, order_seed=1, max_concurrent_trials=1, optimistic_revision=0,
    )
    registry = Registry(tasks={TASK_SLUG: task}, entrants={"agent-a": entrant}, cohorts={"cohort-a": cohort}, protocols={"protocol-a": protocol}, budgets={"budget-a": budget})
    resolved = freeze_campaign(draft, registry)
    return resolved.model_dump(mode="json")


def _payload(**kwargs):
    resolved = _resolved_manifest(**kwargs)
    trial_id = resolved["trials"][0]["id"]
    return build_frozen_cell_payload(
        resolved, trial_id=trial_id, attempt_number=1, campaign_id=resolved["id"], manifest_digest="manifest-digest",
    )


class _SyntheticLauncher:
    """Completes after `steps_to_complete` status polls, or never completes
    (to exercise the deadline-stop path). Records whether stop() was called."""

    def __init__(self, *, steps_to_complete: int | None = 1) -> None:
        self.steps_to_complete = steps_to_complete
        self.polls = 0
        self.stopped_with: str | None = None
        self.cleaned_up = False

    async def preflight(self) -> CapabilityReport:
        return CapabilityReport(backend="synthetic", version="1", checks=(CapabilityCheck("local", True, "ok"),))

    async def launch(self, spec) -> ExecutionHandle:
        return ExecutionHandle(id="handle-1")

    async def status(self, handle: ExecutionHandle) -> ExecutionStatus:
        self.polls += 1
        if self.stopped_with is not None:
            return ExecutionStatus(state=ExecutionState.CANCELLED, detail=self.stopped_with)
        if self.steps_to_complete is not None and self.polls >= self.steps_to_complete:
            return ExecutionStatus(state=ExecutionState.COMPLETED)
        return ExecutionStatus(state=ExecutionState.RUNNING)

    async def stop(self, handle: ExecutionHandle, reason: str) -> None:
        self.stopped_with = reason

    async def collect(self, handle: ExecutionHandle) -> CandidateArtifacts:
        return CandidateArtifacts(
            trial_dir=Path("/tmp/trial-1"), result_path=Path("/tmp/trial-1/result.json"),
            manifest_path=Path("/tmp/trial-1/manifest.json"),
            manifest=({"reported_model": "demo-model", "usage": {"tokens": 10}, "events": [{"type": "engineer.done"}]},),
        )

    async def cleanup(self, handle: ExecutionHandle) -> CleanupReport:
        self.cleaned_up = True
        return CleanupReport(clean=True, remaining_resource_ids=())


def test_dispatch_cell_completes_within_deadline() -> None:
    payload = _payload(engineer_wall_seconds=1200, verification_wall_seconds=300)
    launcher = _SyntheticLauncher(steps_to_complete=1)
    outcome = asyncio.run(dispatch_cell(
        payload, launcher=launcher, task_dir=Path("/tmp/task"), runs_dir=Path("/tmp/runs"),
        agent_import_path="tests.agent:Agent",
    ))
    assert outcome.deadline_exceeded is False
    assert outcome.terminal_state == "completed"
    assert outcome.reported_model == "demo-model"
    assert outcome.usage == {"tokens": 10}
    assert launcher.stopped_with is None
    assert launcher.cleaned_up is True


def test_dispatch_cell_stops_launcher_at_frozen_deadline() -> None:
    # A zero wall-clock budget so the poll loop's first `remaining <= 0`
    # check fires almost immediately, without a real multi-second sleep.
    payload = _payload(engineer_wall_seconds=1, verification_wall_seconds=1)
    launcher = _SyntheticLauncher(steps_to_complete=None)  # never completes on its own
    outcome = asyncio.run(dispatch_cell(
        payload, launcher=launcher, task_dir=Path("/tmp/task"), runs_dir=Path("/tmp/runs"),
        agent_import_path="tests.agent:Agent", poll_seconds=0.01, timeout_grace_seconds=0.2,
    ))
    assert outcome.deadline_exceeded is True
    assert outcome.terminal_state == "cancelled"
    assert launcher.stopped_with == "frozen cell deadline exceeded"
    assert launcher.cleaned_up is True


def test_dispatch_cell_raises_if_launcher_ignores_deadline_stop() -> None:
    payload = _payload(engineer_wall_seconds=1, verification_wall_seconds=1)

    class _StubbornLauncher(_SyntheticLauncher):
        async def stop(self, handle: ExecutionHandle, reason: str) -> None:
            # Deliberately does NOT record the stop, so status() keeps
            # reporting RUNNING through the grace window.
            pass

    launcher = _StubbornLauncher(steps_to_complete=None)
    with pytest.raises(DispatchIntegrityError, match="kept running after a deadline stop"):
        asyncio.run(dispatch_cell(
            payload, launcher=launcher, task_dir=Path("/tmp/task"), runs_dir=Path("/tmp/runs"),
            agent_import_path="tests.agent:Agent", poll_seconds=0.01, timeout_grace_seconds=0.05,
        ))
    assert launcher.cleaned_up is True


def test_verify_spec_pinning_rejects_model_substitution() -> None:
    from aieb_api.worker.harbor_dispatch import verify_spec_pinning
    from aieb_runner.backends.base import ExecutionSpec, IsolationPolicy

    payload = _payload()
    spec = ExecutionSpec(
        task_dir=Path("/tmp/task"), runs_dir=Path("/tmp/runs"), trial_name="t", agent_import_path="tests.agent:Agent",
        agent_timeout_sec=payload.deadline_seconds, cpu_limit=payload.engineer_cpu, memory_limit_mb=payload.engineer_memory_mb,
        isolation=IsolationPolicy(), model_name="a-different-model",
    )
    with pytest.raises(DispatchIntegrityError, match="refusing to substitute a different model"):
        verify_spec_pinning(payload, spec)
