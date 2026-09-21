"""Strict v2 benchmark contracts.

These models are deliberately versioned separately from the byte-compatible
v1 contracts.  A v1 frozen manifest therefore remains readable while every new
v2 document has an explicit track, protocol, provenance, and release identity.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .models import ContractModel, DependencyMode, ExecutionValidity, Slug, Track, Verdict, _has_escaping_segment

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1)]


class V2Source(ContractModel):
    repository_digest: Digest
    revision: NonEmpty
    license_id: NonEmpty
    provenance_digest: Digest
    overlap_review_ref: NonEmpty


class V2ResourceLimits(ContractModel):
    cpu: int = Field(gt=0)
    memory_mb: int = Field(gt=0)
    wall_seconds: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)


class V2NetworkPolicy(ContractModel):
    mode: Literal["none", "allowlist"]
    allowed_hosts: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def allowlist_is_explicit(self) -> "V2NetworkPolicy":
        if self.mode == "none" and self.allowed_hosts:
            raise ValueError("network mode none cannot declare allowed hosts")
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts):
            raise ValueError("network hosts must be unique")
        return self


class EvaluatorRevision(ContractModel):
    schema_version: Literal["aieb.evaluator/v2"]
    id: Slug
    revision: NonEmpty
    evaluator_digest: Digest
    public_fixture_ref: NonEmpty
    hidden_fixture_ref: NonEmpty
    protocol_version: Slug


class V2TaskRevision(ContractModel):
    schema_version: Literal["aieb.task/v2"]
    id: Slug
    version: NonEmpty
    family_id: Slug
    difficulty: Literal["introductory", "standard", "advanced", "expert"]
    track: Track
    dependency_mode: DependencyMode
    source: V2Source
    license_id: NonEmpty
    provenance_digest: Digest
    harbor_task_digest: Digest
    official_image: Annotated[str, Field(pattern=r"^.+@sha256:[0-9a-f]{64}$")]
    network_policy: V2NetworkPolicy
    resources: V2ResourceLimits
    public_status: Literal["public", "hidden"]
    evaluator: EvaluatorRevision
    protocol_version: Slug
    allowed_paths: tuple[NonEmpty, ...] = Field(min_length=1)

    @field_validator("allowed_paths")
    @classmethod
    def repo_relative_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized = []
        for path in paths:
            value = path.replace("\\", "/")
            if _has_escaping_segment(value):
                raise ValueError("task paths must be repository-relative")
            normalized.append(value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("task paths must be unique")
        return tuple(normalized)

    @model_validator(mode="after")
    def provenance_and_evaluator_match(self) -> "V2TaskRevision":
        if self.evaluator.protocol_version != self.protocol_version:
            raise ValueError("evaluator protocol version must match task protocol version")
        if self.source.license_id != self.license_id:
            raise ValueError("task license must match its source license")
        if self.source.provenance_digest != self.provenance_digest:
            raise ValueError("task provenance must match its source provenance")
        return self


class TrackAProtocol(ContractModel):
    schema_version: Literal["aieb.track-a-protocol/v2"]
    id: Slug
    track: Literal[Track.AGENTS] = Track.AGENTS
    protocol_digest: Digest
    repetitions: int = Field(gt=0)
    max_replacements: int = Field(ge=0, le=2)
    require_hidden_checks: bool
    required_capabilities: tuple[Slug, ...] = ()


class TrackBProtocol(ContractModel):
    schema_version: Literal["aieb.track-b-protocol/v2"]
    id: Slug
    track: Literal[Track.MODELS] = Track.MODELS
    protocol_digest: Digest
    repetitions: int = Field(gt=0)
    max_retries: int = Field(ge=0, le=5)
    context_limit_tokens: int = Field(gt=0)
    stopping_rule: NonEmpty


class AgentConfiguration(ContractModel):
    schema_version: Literal["aieb.agent-config/v2"]
    id: Slug
    implementation_digest: Digest
    system_prompt_digest: Digest
    tool_schema_digest: Digest
    requested_model: NonEmpty
    reported_model: NonEmpty
    capabilities: tuple[Slug, ...] = Field(min_length=1)


class ModelConfiguration(ContractModel):
    schema_version: Literal["aieb.model-config/v2"]
    id: Slug
    provider_id: NonEmpty
    requested_model: NonEmpty
    reported_model: NonEmpty
    settings_digest: Digest
    unsupported_controls: tuple[Slug, ...] = ()


class CampaignCell(ContractModel):
    trial_id: UUID
    task_id: Slug
    entrant_id: Slug
    repetition: int = Field(ge=0)
    order_index: int = Field(ge=0)


class V2Campaign(ContractModel):
    schema_version: Literal["aieb.campaign/v2"]
    id: UUID
    release_id: Slug
    track: Track
    task_ids: tuple[Slug, ...] = Field(min_length=1)
    entrant_ids: tuple[Slug, ...] = Field(min_length=1)
    repetitions: int = Field(gt=0)
    cells: tuple[CampaignCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_matrix(self) -> "V2Campaign":
        expected = {
            (task_id, entrant_id, repetition)
            for task_id in self.task_ids
            for entrant_id in self.entrant_ids
            for repetition in range(self.repetitions)
        }
        actual = {(cell.task_id, cell.entrant_id, cell.repetition) for cell in self.cells}
        if actual != expected or len(self.cells) != len(expected):
            raise ValueError("campaign cells must be an exact task x entrant x repetition matrix")
        if len({cell.trial_id for cell in self.cells}) != len(self.cells):
            raise ValueError("campaign trial IDs must be unique")
        return self


class ReleaseManifest(ContractModel):
    schema_version: Literal["aieb.release/v2"]
    id: Slug
    track: Track
    cohort_id: Slug
    task_digests: tuple[Digest, ...] = Field(min_length=1)
    evaluator_digests: tuple[Digest, ...] = Field(min_length=1)
    protocol_digest: Digest
    harbor_version: NonEmpty
    environment_digest: Digest
    repetitions: int = Field(gt=0)
    ordering_seed: int = Field(ge=0)
    scoring_digest: Digest
    exclusions: tuple[UUID, ...] = ()


class VerdictRecord(ContractModel):
    schema_version: Literal["aieb.verdict/v2"]
    trial_id: UUID
    execution_validity: ExecutionValidity
    correctness: Verdict | None
    reliability_status: Literal["completed", "deadline", "infrastructure", "cancelled"]

    @model_validator(mode="after")
    def separate_scientific_metrics(self) -> "VerdictRecord":
        if self.execution_validity != ExecutionValidity.VALID and self.correctness is not None:
            raise ValueError("invalid execution cannot carry a correctness verdict")
        if self.reliability_status == "completed" and self.execution_validity != ExecutionValidity.VALID:
            raise ValueError("completed reliability status requires a valid execution")
        return self


class PublicationSnapshot(ContractModel):
    schema_version: Literal["aieb.publication-snapshot/v2"]
    id: UUID
    release_id: Slug
    cohort_id: Slug
    release_digest: Digest
    snapshot_digest: Digest
    correctness_rate: str | None
    reliability_rate: str | None
    valid_trials: int = Field(ge=0)
    total_trials: int = Field(ge=0)
    limitations: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def counts_and_rates_are_honest(self) -> "PublicationSnapshot":
        if self.valid_trials > self.total_trials:
            raise ValueError("valid trial count cannot exceed total trial count")
        if self.correctness_rate is None and self.reliability_rate is None and self.valid_trials:
            raise ValueError("a snapshot with valid trials must expose its separate metrics")
        return self
