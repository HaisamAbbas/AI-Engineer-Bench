"""API-specific request/response projections.

Reuses aieb_core contracts directly wherever they are already the correct
public shape; adds thin wrappers only where the API needs something the
core contracts do not model (pagination envelopes, freeze registry input).
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar
from uuid import UUID

from aieb_core.models import BudgetProfile, CampaignDraft, Cohort, EntrantRevision, ProtocolRevision, TaskRevision
from pydantic import BaseModel, ConfigDict

ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    """Generic cursor-page envelope (spec section 33). Parametrized per route
    (e.g. `Page[PublicationSummary]`) rather than `items: list[dict]`, so
    each list endpoint's generated TypeScript type actually names its item
    shape instead of every list looking like `unknown[]` to the frontend."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    next_cursor: str | None = None


class FreezeRegistry(BaseModel):
    """Cohort/protocol/budget are versioned configuration, not yet persisted hosted tables
    in this ticket's schema (section 30 lists task/entrant/campaign/etc. only); the operator
    client supplies the exact revisions to resolve against, mirroring the local CLI planner."""

    model_config = ConfigDict(extra="forbid")

    cohort: Cohort
    protocol: ProtocolRevision
    budget: BudgetProfile


class CampaignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    draft: CampaignDraft


class CampaignPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: CampaignDraft


class CampaignSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    state: str
    revision: int
    manifest_digest: str | None
    cohort_digest: str | None


class TaskRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    manifest: TaskRevision


class EntrantRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    manifest: EntrantRevision


class TaskCellStats(BaseModel):
    """One (task, entrant) cell from aieb_analysis.metrics.summarize()'s
    `per_task` output - typed here rather than left as an untyped `dict` so a
    change to that shape shows up as a generated-type/frontend build error,
    not a silent runtime mismatch (review finding: "generated types are
    bypassed for the important result contracts")."""

    model_config = ConfigDict(extra="forbid")

    s: int
    n: int
    rate: float | None
    wilson_95: tuple[float, float] | None
    all_k: bool | None
    pass_power_k: float | None


class AnalysisSnapshot(BaseModel):
    """The exact shape aieb_analysis.metrics.summarize() returns. Publication
    snapshots are this package's authoritative output, persisted verbatim
    (spec: public results come from an immutable snapshot, never
    recomputed) - typing it here means the frontend's generated types
    change the moment this shape does, instead of an `as unknown as X` cast
    silently continuing to compile against a stale shape."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    per_task: dict[str, TaskCellStats]
    per_entrant: dict[str, float | None]
    per_category: dict[str, dict[str, float | None]] | None
    complete_for_rank: bool
    suite_rate: float | None
    cost_per_resolution: float | None
    total_campaign_cost_usd: float | None
    verifier_cost_total_usd: float | None
    successful_engineering_median_seconds: int | None
    deadline_rate: float | None
    infrastructure_attrition: float | None
    limitations: list[str]


class PublicationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    snapshot_digest: str
    status: str
    created_at: str


class PublicationResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    snapshot_digest: str
    status: str
    supersedes_id: UUID | None
    created_at: str
    cohort_digest: str | None
    snapshot: AnalysisSnapshot
    notice: str | None = None


class EntrantComparisonEligible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: Literal[True]
    aggregate: float | None


class EntrantComparisonIneligible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: Literal[False]
    reason: str


class TaskPairedDifference(BaseModel):
    """One task's paired outcome difference between exactly two entrants
    within the same trial/repetition cell (spec: "paired task outcomes")."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    left_rate: float | None
    right_rate: float | None
    difference: float | None


class ComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    cohort_comparable: bool
    non_comparable_reason: str | None = None
    entrants: dict[str, EntrantComparisonEligible | EntrantComparisonIneligible]
    paired_differences: dict[str, list[TaskPairedDifference]] | None = None


class TaskCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    version: str
    family_id: str
    category: str
    activity: str | None
    created_at: str


class CorrectionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    status: str
    supersedes_id: UUID | None
    created_at: str


class EntrantResultEntry(BaseModel):
    """One publication an entrant slug appears in - the "results by release"
    spec section 5 names for the entrant profile page."""

    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    campaign_id: UUID
    status: str
    created_at: str
    aggregate_rate: float | None
