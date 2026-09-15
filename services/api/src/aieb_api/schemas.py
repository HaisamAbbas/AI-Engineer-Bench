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
    required_repetitions: int | None = None
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
    # Per-entrant breakdowns of the suite-wide metrics above (review finding
    # #2: a results table needs these AS entrant rows, not one suite-wide
    # number repeated on every row). Optional/default-empty so older
    # persisted snapshots (written before this field existed) still validate
    # - _verified_snapshot's digest re-check uses the snapshot exactly as
    # stored, so a snapshot published before this field existed genuinely
    # has no such data, not merely an omitted-but-derivable one.
    per_entrant_valid_trials: dict[str, int] = {}
    per_entrant_resolved_tasks: dict[str, int] = {}
    per_entrant_total_tasks: dict[str, int] = {}
    per_entrant_cost_per_resolution: dict[str, float | None] = {}
    per_entrant_verifier_cost_usd: dict[str, float | None] = {}
    per_entrant_median_engineering_seconds: dict[str, int | None] = {}
    per_entrant_deadline_rate: dict[str, float | None] = {}
    per_entrant_infrastructure_attrition: dict[str, float | None] = {}
    limitations: list[str]


class PublicationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    snapshot_digest: str
    status: str
    created_at: str


class FrozenTaskEntry(BaseModel):
    """One task from the campaign's own frozen manifest
    (`campaign.resolved["tasks"]`) - NOT inferred from which tasks happen to
    have an observation in the published snapshot. A planned task with zero
    observations (the exact case incomplete-coverage reporting must
    preserve, spec section 28) still appears here, since it comes from the
    manifest the campaign actually froze, not from what got scored
    (review finding #3)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    version: str
    family_id: str
    category: str


class CohortIdentity(BaseModel):
    """The frozen cohort's own identifying fields
    (`campaign.resolved["cohort"]`) - real manifest data, not inferred from
    result rows, so a release page can show suite/track/dependency-mode/
    profile identifiers (review finding #2) without recomputing anything."""

    model_config = ConfigDict(extra="forbid")

    track: str
    suite_id: str
    protocol_id: str
    dependency_mode: str
    hardware_class: str


class PublicationResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    snapshot_digest: str
    status: str
    supersedes_id: UUID | None
    created_at: str
    cohort_digest: str | None
    cohort: CohortIdentity | None = None
    frozen_tasks: list[FrozenTaskEntry] = []
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
    """A per-task rate difference between two entrants IN THE SAME
    publication, computed from each entrant's own aggregated `per_task` rate
    cell. This is NOT the project/family-resampled, repetition-matched
    statistic spec section 28 describes ("resampling projects/families then
    repetitions according to the declared hierarchical model") - that
    requires per-repetition observations grouped by underlying project,
    which the persisted publication snapshot does not retain (only
    aggregated per-task rate/n). Building that is real future work (the
    existing `aieb_analysis.paired_project_difference` implements the
    correct hierarchical procedure already, but nothing in the hosted
    persistence schema populates the `project_id` it requires yet -
    disclosed, not silently claimed here). Review finding #1 (2026-09-16)."""

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
    spec section 5 names for the entrant profile page. `entrant_version`
    names the EXACT entrant revision that publication's frozen campaign
    actually used (`campaign.resolved["entrants"]`), not "whichever revision
    happens to be newest right now" - a historical result must stay pinned
    to the configuration that produced it even after a newer revision of
    the same slug is registered (review finding #3). `None` only if the
    campaign's resolved manifest could not be read at all (a real, disclosed
    failure mode, not silently defaulted to "current")."""

    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    campaign_id: UUID
    status: str
    created_at: str
    aggregate_rate: float | None
    entrant_version: str | None = None
