"""Versioned MVP-2 public-repository bug-finding contracts.

These contracts are intentionally separate from Track A engineering tasks and
carry an explicit cohort identity so findings can never be ranked together by
accident.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .models import ContractModel

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafePath = Annotated[str, Field(min_length=1, max_length=512)]
Severity = Literal["blocker", "critical", "major", "minor", "informational"]
TrackMode = Literal["finding-only", "patch"]


def _safe_path(value: str) -> str:
    p = value.replace("\\", "/")
    if p.startswith("/") or ".." in p.split("/") or "\x00" in p:
        raise ValueError("repository paths must be relative and traversal-free")
    return p


class BugRepositorySnapshot(ContractModel):
    schema_version: Literal["aieb.bug-repository/v2"]
    repository_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content_digest: Digest
    license_id: str = Field(min_length=1)
    provenance_digest: Digest
    documentation_digest: Digest
    allowed_commands: tuple[str, ...] = Field(min_length=1)
    network_policy: Literal["none", "allowlist"] = "none"

    @field_validator("allowed_commands")
    @classmethod
    def commands_are_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not c.strip() or len(c) > 256 for c in value):
            raise ValueError("allowed commands must be unique, non-empty, and bounded")
        return value


class FindingEvidence(ContractModel):
    kind: Literal["test-output", "trace", "source", "reproduction"]
    reference: str = Field(min_length=1, max_length=2048)
    evidence_digest: Digest | None = None


class BugFinding(ContractModel):
    schema_version: Literal["aieb.bug-finding/v2"]
    finding_id: str = Field(min_length=1)
    location: SafePath
    symbol_or_line: str = Field(min_length=1)
    reproduction_steps: tuple[str, ...] = Field(min_length=1)
    observed_behavior: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    severity: Severity
    evidence: tuple[FindingEvidence, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("location")
    @classmethod
    def location_is_safe(cls, value: str) -> str:
        return _safe_path(value)


class BugPatch(ContractModel):
    patch_digest: Digest
    changed_paths: tuple[SafePath, ...] = Field(min_length=1)

    @field_validator("changed_paths")
    @classmethod
    def paths_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_path(p) for p in value)


class BugSubmission(ContractModel):
    schema_version: Literal["aieb.bug-submission/v2"]
    repository: BugRepositorySnapshot
    mode: TrackMode
    findings: tuple[BugFinding, ...]
    patch: BugPatch | None = None

    @model_validator(mode="after")
    def mode_matches_patch(self) -> "BugSubmission":
        if self.mode == "finding-only" and self.patch is not None:
            raise ValueError("finding-only submissions cannot contain a patch")
        if self.mode == "patch" and self.patch is None:
            raise ValueError("patch submissions require a patch payload")
        return self


class BugTaskRevision(ContractModel):
    schema_version: Literal["aieb.bug-task/v2"]
    task_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    cohort_id: str = Field(pattern=r"^mvp2-bugfinding-[a-z0-9-]+$")
    repository: BugRepositorySnapshot
    objective: str = Field(min_length=1)
    severity_taxonomy_version: str = Field(min_length=1)
    hidden_label_digest: Digest
    mode: TrackMode
    public_examples: tuple[BugFinding, ...] = ()
    baseline_ref: str = Field(min_length=1)
    reference_ref: str = Field(min_length=1)
    alternative_ref: str = Field(min_length=1)
    negative_control_ref: str = Field(min_length=1)


class BugReleaseManifest(ContractModel):
    schema_version: Literal["aieb.bug-release/v2"]
    release_id: str = Field(pattern=r"^mvp2-bugfinding-release-[a-z0-9-]+$")
    cohort_id: str = Field(pattern=r"^mvp2-bugfinding-[a-z0-9-]+$")
    tasks: tuple[BugTaskRevision, ...] = Field(min_length=1)
    status: Literal["development", "ready-for-review", "official"] = "development"

    @model_validator(mode="after")
    def cohort_isolated(self) -> "BugReleaseManifest":
        if any(task.cohort_id != self.cohort_id for task in self.tasks):
            raise ValueError("all MVP-2 tasks must share the release cohort")
        if self.status == "official":
            raise ValueError("official publication requires explicit release authorization")
        return self
