from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from aieb_core.canonical import CanonicalizationError, content_hash
from aieb_core.models import (
    Attempt, BudgetProfile, CampaignDraft, CandidateFile, CandidateManifest, Cohort,
    EntrantRevision, EvaluationPlan, EvaluationResult, ExecutionValidity, ModelProfile,
    ProtocolRevision, RequirementCheck, ResolvedCampaign, RoleBudget, TaskRevision, UsageSummary, Verdict, parse_yaml,
)
from aieb_core.planner import PlanningError, Registry, assert_comparable, freeze_campaign


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "implementation" / "evidence" / "ENG-002" / "examples"


def task(slug: str = "rag.document-freshness", digest_char: str = "a") -> TaskRevision:
    value = parse_yaml((EXAMPLES / "task-revision.yaml").read_text(encoding="utf-8"), TaskRevision)
    raw = value.model_dump(mode="json")
    raw["id"] = slug
    raw["source"]["repository_digest"] = digest_char * 64
    return TaskRevision.model_validate(raw)


def entrant(slug: str, capabilities: tuple[str, ...] = ("network-allowlist", "service-compose")) -> EntrantRevision:
    return EntrantRevision(
        schema_version="aieb.entrant/v1", id=slug, track="agents", agent_implementation="codex",
        agent_version="example-pinned", engineer_model=ModelProfile(provider_class="example", requested_model="example-1", reported_model=None, settings_digest="1" * 64),
        prompt_digest="2" * 64, tools_digest="3" * 64, capabilities=capabilities, credential_ref_type="broker",
    )


def registry() -> tuple[Registry, CampaignDraft]:
    tasks = {item.id: item for item in (task("rag.document-freshness", "a"), task("extraction.batch-alignment", "b"), task("tool.false-completion", "c"))}
    entrants = {item.id: item for item in (entrant("agent-a-pinned"), entrant("agent-b-pinned"))}
    budget = BudgetProfile(schema_version="aieb.budget/v1", id="budget-v1", engineer_wall_seconds=1200, verification_wall_seconds=300, engineer_cpu=1, engineer_memory_mb=512, per_role_budget_usd=(RoleBudget(role="engineer", limit_usd="1.50"), RoleBudget(role="dev_application", limit_usd=None), RoleBudget(role="verifier_application", limit_usd="0"), RoleBudget(role="verifier_judge", limit_usd=None)))
    cohort = Cohort(schema_version="aieb.cohort/v1", id="cpu-fixture-standard-v1", track="agents", suite_id="dev-suite-v1", protocol_id="protocol-v1", budget_profile_id="budget-v1", dependency_mode="fixture", application_model_profile=task().application, hardware_class="cpu-small-v1", required_capabilities=("network-allowlist", "service-compose"))
    protocol = ProtocolRevision(schema_version="aieb.protocol/v1", id="protocol-v1", scoring_digest="4" * 64, max_replacements=2, required_trace_coverage=False, hard_cost_ranking=False)
    draft = CampaignDraft(schema_version="aieb.campaign-draft/v1", id="repair-pilot-example", cohort_id=cohort.id, task_ids=tuple(tasks), entrant_ids=tuple(entrants), repetitions=3, order_seed=4107, max_concurrent_trials=2, optimistic_revision=0)
    return Registry(tasks, entrants, {cohort.id: cohort}, {protocol.id: protocol}, {budget.id: budget}), draft


class CanonicalContractTest(unittest.TestCase):
    def test_ct01_yaml_formatting_does_not_change_digest(self) -> None:
        first = parse_yaml((EXAMPLES / "task-revision.yaml").read_text(encoding="utf-8"), TaskRevision)
        second = parse_yaml((EXAMPLES / "task-revision-reformatted.yaml").read_text(encoding="utf-8"), TaskRevision)
        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(first.digest(), "bc01d9cf06e427c223acfb696a39f9097333da49f5363358e444358a8679b355")

    def test_semantic_change_changes_digest(self) -> None:
        original = task()
        changed = TaskRevision.model_validate({**original.model_dump(mode="json"), "version": "0.1.1"})
        self.assertNotEqual(original.digest(), changed.digest())

    def test_ct02_rejects_unknown_field_and_unresolved_image(self) -> None:
        raw = task().model_dump(mode="json")
        with self.assertRaises(ValidationError):
            TaskRevision.model_validate({**raw, "unreviewed": True})
        raw["environment"]["official_image"] = "registry.example/aieb/rag:latest"
        with self.assertRaises(ValidationError):
            TaskRevision.model_validate(raw)
        raw = task().model_dump(mode="json")
        raw["schema_version"] = "aieb.task/v2"
        with self.assertRaises(ValidationError):
            TaskRevision.model_validate(raw)

    def test_rejects_duplicate_requirements_paths_and_invalid_numeric_values(self) -> None:
        raw = task().model_dump(mode="json")
        raw["requirements"].append(raw["requirements"][0])
        with self.assertRaises(ValidationError):
            TaskRevision.model_validate(raw)
        raw = task().model_dump(mode="json")
        raw["submission"]["include"] = ["../secret"]
        with self.assertRaises(ValidationError):
            TaskRevision.model_validate(raw)
        with self.assertRaises(CanonicalizationError):
            content_hash({"value": float("nan")})

    def test_rejects_windows_drive_qualified_paths(self) -> None:
        """A path like "C:/outside" starts with neither "/" nor "..", so it
        previously passed both CandidateFile.path and SubmissionPolicy's
        include/protected validators, yet Path(root) / "C:/outside" discards
        root entirely on Windows (the drive letter becomes a new anchor)."""
        raw = task().model_dump(mode="json")
        raw["submission"]["include"] = ["C:/outside/**"]
        with self.assertRaises(ValidationError):
            TaskRevision.model_validate(raw)
        with self.assertRaises(ValidationError):
            CandidateFile(path="C:/outside/evil.txt", operation="add", sha256="a" * 64, byte_length=1, executable=False)

    def test_valid_and_invalid_result_envelopes_and_unknown_usage(self) -> None:
        plan = EvaluationPlan(schema_version="aieb.evaluation-plan/v1", id=uuid4(), task_digest="a" * 64, candidate_digest="b" * 64, evaluator_digest="c" * 64, fixture_digest="d" * 64, workload_seed=7, requirement_ids=("contract",))
        valid = EvaluationResult(schema_version="aieb.evaluation-result/v1", id=uuid4(), plan_id=plan.id, execution_validity="valid", verdict="pass", checks=(RequirementCheck(requirement_id="contract", status="pass"),), usage=UsageSummary())
        self.assertIsNone(valid.usage.input_tokens)
        self.assertIsNone(valid.usage.cost_usd)
        self.assertNotEqual(valid.usage, UsageSummary(input_tokens=0, output_tokens=0, cost_usd="0"))
        with self.assertRaises(ValidationError):
            EvaluationResult(schema_version="aieb.evaluation-result/v1", id=uuid4(), plan_id=plan.id, execution_validity="infrastructure_invalid", verdict="fail", checks=())
        with self.assertRaises(ValidationError):
            EvaluationResult(schema_version="aieb.evaluation-result/v1", id=uuid4(), plan_id=plan.id, execution_validity="valid", verdict="pass", checks=(RequirementCheck(requirement_id="contract", status="not_run"),))

    def test_generated_versioned_json_schemas_exist(self) -> None:
        schemas = ROOT / "docs" / "implementation" / "evidence" / "ENG-002" / "schemas"
        expected = {"TaskRevision", "EntrantRevision", "ProtocolRevision", "BudgetProfile", "Cohort", "CampaignDraft", "ResolvedCampaign", "Trial", "Attempt", "CandidateManifest", "EvaluationPlan", "EvaluationResult", "EventEnvelope", "PublicationManifest"}
        actual = {path.name.removesuffix(".schema.json") for path in schemas.glob("*.schema.json")}
        self.assertEqual(actual, expected)
        for path in schemas.glob("*.schema.json"):
            self.assertIn("$schema", json.loads(path.read_text(encoding="utf-8")))


class PlannerTest(unittest.TestCase):
    def test_pilot_expands_to_exactly_eighteen_deterministic_trials(self) -> None:
        registry_value, draft = registry()
        frozen = freeze_campaign(draft, registry_value)
        again = freeze_campaign(draft, registry_value)
        self.assertEqual(len(frozen.trials), 18)
        self.assertEqual([trial.id for trial in frozen.trials], [trial.id for trial in again.trials])
        self.assertEqual(len({trial.id for trial in frozen.trials}), 18)
        self.assertTrue(frozen.frozen)

    def test_incomplete_profile_and_incompatible_comparison_fail(self) -> None:
        registry_value, draft = registry()
        weak = entrant("agent-a-pinned", ("service-compose",))
        registry_value.entrants[weak.id] = weak
        with self.assertRaisesRegex(PlanningError, "lacks required"):
            freeze_campaign(draft, registry_value)
        registry_value, draft = registry()
        left = freeze_campaign(draft, registry_value)
        altered = CampaignDraft.model_validate({**draft.model_dump(mode="json"), "id": "other-draft", "order_seed": 2})
        right = freeze_campaign(altered, registry_value)
        self.assertIsNone(assert_comparable(left, right))
        raw = right.model_dump(mode="json")
        raw["cohort"]["hardware_class"] = "gpu-v1"
        with self.assertRaises(PlanningError):
            assert_comparable(left, type(right).model_validate(raw))

    def test_duplicate_trial_identity_is_rejected_by_draft_validation(self) -> None:
        registry_value, draft = registry()
        raw = draft.model_dump(mode="json")
        raw["task_ids"].append(raw["task_ids"][0])
        with self.assertRaises(ValidationError):
            CampaignDraft.model_validate(raw)

    def test_duplicate_trial_identity_is_rejected_in_frozen_contract(self) -> None:
        registry_value, draft = registry()
        frozen = freeze_campaign(draft, registry_value)
        raw = frozen.model_dump(mode="json")
        raw["trials"].append(raw["trials"][0])
        with self.assertRaises(ValidationError):
            ResolvedCampaign.model_validate(raw)


if __name__ == "__main__":
    unittest.main()
