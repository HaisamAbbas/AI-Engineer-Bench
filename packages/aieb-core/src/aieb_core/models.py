"""Strict, versioned public contracts. No execution, API, or Harbor imports."""

from __future__ import annotations

from enum import StrEnum
from decimal import Decimal, InvalidOperation
import math
from typing import Annotated, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

from .canonical import content_hash

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Slug = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")]
SafePath = Annotated[str, Field(min_length=1, max_length=512)]


def _normalised_decimal(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative decimal string or null") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} must be a non-negative finite decimal string or null")
    return format(parsed.normalize(), "f") if parsed != 0 else "0"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    def digest(self) -> str:
        return content_hash(self)

    @model_validator(mode="before")
    @classmethod
    def reject_float_contract_values(cls, value: object) -> object:
        def visit(item: object) -> None:
            if isinstance(item, float):
                if not math.isfinite(item):
                    raise ValueError("NaN and Infinity are forbidden")
                raise ValueError("floating-point values are forbidden; use integers or schema-owned decimal strings")
            if isinstance(item, dict):
                for child in item.values():
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)
        visit(value)
        return value


ModelT = TypeVar("ModelT", bound=ContractModel)


def parse_yaml(text: str, model_type: type[ModelT]) -> ModelT:
    """Parse YAML only as input syntax; identity is always model canonical JSON."""
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("a contract document must be a mapping")
    return model_type.model_validate(value)


class Category(StrEnum):
    RAG = "rag"
    EXTRACTION = "extraction"
    TOOL_APP = "tool_app"


class Activity(StrEnum):
    REPAIR = "repair"


class Track(StrEnum):
    AGENTS = "agents"
    MODELS = "models"


class DependencyMode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    CONTRACT_VIOLATION = "contract_violation"
    INDETERMINATE = "indeterminate"


class ExecutionValidity(StrEnum):
    VALID = "valid"
    INFRASTRUCTURE_INVALID = "infrastructure_invalid"
    CANCELLED = "cancelled"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    NOT_RUN = "not_run"


class Requirement(ContractModel):
    id: Slug
    severity: Literal["mandatory", "diagnostic"]
    description: str = Field(min_length=1)


class SourceRef(ContractModel):
    repository_digest: SHA256
    commit: str = Field(min_length=7)
    license: str = Field(min_length=1)
    provenance_digest: SHA256


class EnvironmentSpec(ContractModel):
    official_image: str = Field(pattern=r"^.+@sha256:[0-9a-f]{64}$")
    engineer_cpu: int = Field(gt=0)
    engineer_memory_mb: int = Field(gt=0)
    service_topology_digest: SHA256
    egress_policy: Literal["none", "allowlist"]


class ApplicationProfile(ContractModel):
    dependency_mode: DependencyMode
    entrypoint: tuple[str, ...] = Field(min_length=1)
    contract_digest: SHA256
    model_profile_id: Slug


class SubmissionPolicy(ContractModel):
    include: tuple[SafePath, ...] = Field(min_length=1)
    protected: tuple[SafePath, ...] = ()
    max_artifact_bytes: int = Field(gt=0, le=52_428_800)

    @field_validator("include", "protected")
    @classmethod
    def validate_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for path in paths:
            normal = path.replace("\\", "/")
            if normal.startswith("/") or ".." in normal.split("/") or normal == "**" or normal.startswith("**/"):
                raise ValueError("submission paths must be repo-relative and task-specific")
        if len(set(paths)) != len(paths):
            raise ValueError("submission paths must be unique")
        return paths


class EvaluatorRef(ContractModel):
    evaluator_digest: SHA256
    development_fixture: Slug
    official_fixture_ref: str = Field(min_length=1)


class TaskRevision(ContractModel):
    schema_version: Literal["aieb.task/v1"]
    id: Slug
    version: str = Field(min_length=1)
    family_id: Slug
    category: Category
    activity: Activity
    source: SourceRef
    environment: EnvironmentSpec
    application: ApplicationProfile
    submission: SubmissionPolicy
    requirements: tuple[Requirement, ...] = Field(min_length=1)
    evaluator: EvaluatorRef
    profile_compatibility: tuple[Slug, ...] = Field(min_length=1)

    @field_validator("requirements")
    @classmethod
    def unique_requirements(cls, items: tuple[Requirement, ...]) -> tuple[Requirement, ...]:
        if len({item.id for item in items}) != len(items):
            raise ValueError("requirement IDs must be unique")
        return items


class ModelProfile(ContractModel):
    provider_class: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    reported_model: str | None = None
    settings_digest: SHA256


class EntrantRevision(ContractModel):
    schema_version: Literal["aieb.entrant/v1"]
    id: Slug
    track: Track
    agent_implementation: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    engineer_model: ModelProfile
    prompt_digest: SHA256
    tools_digest: SHA256
    capabilities: tuple[Slug, ...] = Field(min_length=1)
    credential_ref_type: Literal["broker", "direct", "subscription"]

    @field_validator("capabilities")
    @classmethod
    def canonical_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("capabilities must be unique")
        return tuple(sorted(values))


class ProtocolRevision(ContractModel):
    schema_version: Literal["aieb.protocol/v1"]
    id: Slug
    scoring_digest: SHA256
    max_replacements: int = Field(ge=0, le=2)
    required_trace_coverage: bool
    hard_cost_ranking: bool


class BudgetRole(StrEnum):
    ENGINEER = "engineer"
    DEV_APPLICATION = "dev_application"
    VERIFIER_APPLICATION = "verifier_application"
    VERIFIER_JUDGE = "verifier_judge"


class RoleBudget(ContractModel):
    role: BudgetRole
    limit_usd: str | None = None

    @field_validator("limit_usd")
    @classmethod
    def normalized_limit(cls, value: str | None) -> str | None:
        return _normalised_decimal(value, "role budget")


class BudgetProfile(ContractModel):
    schema_version: Literal["aieb.budget/v1"]
    id: Slug
    engineer_wall_seconds: int = Field(gt=0)
    verification_wall_seconds: int = Field(gt=0)
    engineer_cpu: int = Field(gt=0)
    engineer_memory_mb: int = Field(gt=0)
    per_role_budget_usd: tuple[RoleBudget, ...] = Field(min_length=4, max_length=4)

    @field_validator("per_role_budget_usd")
    @classmethod
    def complete_roles(cls, values: tuple[RoleBudget, ...]) -> tuple[RoleBudget, ...]:
        if {value.role for value in values} != set(BudgetRole):
            raise ValueError("budget profile must declare each role exactly once")
        if len({value.role for value in values}) != len(values):
            raise ValueError("budget roles must be unique")
        return tuple(sorted(values, key=lambda value: value.role.value))


class Cohort(ContractModel):
    schema_version: Literal["aieb.cohort/v1"]
    id: Slug
    track: Track
    suite_id: Slug
    protocol_id: Slug
    budget_profile_id: Slug
    dependency_mode: DependencyMode
    application_model_profile: ApplicationProfile
    hardware_class: Slug
    required_capabilities: tuple[Slug, ...]

    @field_validator("required_capabilities")
    @classmethod
    def canonical_required_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("required capabilities must be unique")
        return tuple(sorted(values))


class CampaignDraft(ContractModel):
    schema_version: Literal["aieb.campaign-draft/v1"]
    id: Slug
    cohort_id: Slug
    task_ids: tuple[Slug, ...] = Field(min_length=1)
    entrant_ids: tuple[Slug, ...] = Field(min_length=1)
    repetitions: int = Field(gt=0)
    order_seed: int = Field(ge=0)
    max_concurrent_trials: int = Field(gt=0)
    optimistic_revision: int = Field(ge=0)

    @field_validator("task_ids", "entrant_ids")
    @classmethod
    def unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("campaign references must be unique")
        return values


class Trial(ContractModel):
    schema_version: Literal["aieb.trial/v1"] = "aieb.trial/v1"
    id: UUID
    campaign_digest: SHA256
    task_digest: SHA256
    entrant_digest: SHA256
    cohort_digest: SHA256
    repetition_index: int = Field(ge=0)
    order_index: int = Field(ge=0)


class ResolvedCampaign(ContractModel):
    schema_version: Literal["aieb.campaign/v1"] = "aieb.campaign/v1"
    id: UUID
    draft_digest: SHA256
    cohort: Cohort
    protocol: ProtocolRevision
    budget: BudgetProfile
    tasks: tuple[TaskRevision, ...]
    entrants: tuple[EntrantRevision, ...]
    trials: tuple[Trial, ...]
    frozen: Literal[True] = True

    @field_validator("trials")
    @classmethod
    def unique_trial_ids(cls, trials: tuple[Trial, ...]) -> tuple[Trial, ...]:
        if len({trial.id for trial in trials}) != len(trials):
            raise ValueError("frozen campaign cannot contain duplicate trial identities")
        return trials


class Attempt(ContractModel):
    schema_version: Literal["aieb.attempt/v1"]
    id: UUID
    trial_id: UUID
    attempt_number: int = Field(ge=0)
    lease_generation: int = Field(ge=0)
    execution_validity: ExecutionValidity
    termination_reason: str | None = None


class CandidateFile(ContractModel):
    path: SafePath
    operation: Literal["add", "modify", "delete"]
    sha256: SHA256 | None = None
    byte_length: int | None = Field(default=None, ge=0)
    executable: bool = False

    @field_validator("path")
    @classmethod
    def safe_candidate_path(cls, path: str) -> str:
        normal = path.replace("\\", "/")
        if normal.startswith("/") or ".." in normal.split("/"):
            raise ValueError("candidate path must be repo-relative")
        return path

    @model_validator(mode="after")
    def deleted_files_have_no_content(self) -> "CandidateFile":
        if self.operation == "delete" and (self.sha256 is not None or self.byte_length is not None):
            raise ValueError("deleted files cannot include content metadata")
        if self.operation != "delete" and (self.sha256 is None or self.byte_length is None):
            raise ValueError("added and modified files require content metadata")
        return self


class CandidateManifest(ContractModel):
    schema_version: Literal["aieb.candidate/v1"]
    id: UUID
    base_revision_digest: SHA256
    full_tree_hash: SHA256
    files: tuple[CandidateFile, ...]

    @field_validator("files")
    @classmethod
    def candidate_paths_unique(cls, files: tuple[CandidateFile, ...]) -> tuple[CandidateFile, ...]:
        if len({file.path for file in files}) != len(files):
            raise ValueError("candidate paths must be unique")
        return files


class EvaluationPlan(ContractModel):
    schema_version: Literal["aieb.evaluation-plan/v1"]
    id: UUID
    task_digest: SHA256
    candidate_digest: SHA256
    evaluator_digest: SHA256
    fixture_digest: SHA256
    workload_seed: int = Field(ge=0)
    requirement_ids: tuple[Slug, ...] = Field(min_length=1)


class RequirementCheck(ContractModel):
    requirement_id: Slug
    status: CheckStatus
    evidence_refs: tuple[str, ...] = ()
    diagnostic: str | None = None


class UsageSummary(ContractModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: str | None = None

    @field_validator("cost_usd")
    @classmethod
    def normalized_cost(cls, value: str | None) -> str | None:
        return _normalised_decimal(value, "cost")


class EvaluationResult(ContractModel):
    schema_version: Literal["aieb.evaluation-result/v1"]
    id: UUID
    plan_id: UUID
    execution_validity: ExecutionValidity
    verdict: Verdict | None
    checks: tuple[RequirementCheck, ...]
    usage: UsageSummary | None = None

    @model_validator(mode="after")
    def validity_and_verdict_are_distinct(self) -> "EvaluationResult":
        if self.execution_validity != ExecutionValidity.VALID and self.verdict is not None:
            raise ValueError("invalid or cancelled execution cannot carry a scientific verdict")
        if self.execution_validity == ExecutionValidity.VALID and self.verdict is None:
            raise ValueError("valid execution requires a verdict")
        if self.verdict == Verdict.PASS and any(check.status != CheckStatus.PASS for check in self.checks):
            raise ValueError("a PASS verdict requires every reported check to pass")
        return self


class EventEnvelope(ContractModel):
    schema_version: Literal["aieb.event/v1"]
    id: UUID
    attempt_id: UUID
    sequence: int = Field(ge=0)
    event_type: Slug
    payload: dict[str, str | int | bool | None]


class PublicationManifest(ContractModel):
    schema_version: Literal["aieb.publication/v1"]
    id: UUID
    cohort_digest: SHA256
    evaluation_result_digests: tuple[SHA256, ...] = Field(min_length=1)
    excluded_trial_ids: tuple[UUID, ...] = ()
    analysis_digest: SHA256
    approval_digest: SHA256
