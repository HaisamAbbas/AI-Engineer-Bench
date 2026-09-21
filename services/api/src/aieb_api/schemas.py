"""API-specific request/response projections.

Reuses aieb_core contracts directly wherever they are already the correct
public shape; adds thin wrappers only where the API needs something the
core contracts do not model (pagination envelopes, freeze registry input).
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar, Union
from uuid import UUID

from aieb_core.models import ApplicationProfile, BudgetProfile, BudgetProfileV2, CampaignDraft, Cohort, EntrantRevision, ProtocolRevision, TaskRevision
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # Either declared budget contract version; aieb.budget/v2 additionally
    # declares the environment upper bound the reservation formula requires.
    budget: BudgetProfile | BudgetProfileV2
    # Exact stored versions, keyed by draft slug. Unpinned legacy references
    # are accepted only when there is a single stored revision.
    task_versions: dict[str, str] = {}
    entrant_versions: dict[str, str] = {}


class CampaignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    draft: CampaignDraft


class TaskDraftCreateRequest(BaseModel):
    """Maintainer task authoring input. Admission/publication are separate."""

    model_config = ConfigDict(extra="forbid")

    manifest: dict
    ticket_text: str = Field(min_length=1, max_length=100_000)
    evaluator_code_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluator_contract_version: str = Field(min_length=1, max_length=64)


class TaskDraftUpdateRequest(TaskDraftCreateRequest):
    pass


class AuthoredTaskDraftRequest(TaskDraftCreateRequest):
    source_strategy: Literal["authored"] = "authored"
    repository_url: str = Field(min_length=1, max_length=2048)
    source_revision: str = Field(min_length=1, max_length=128)
    source_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_license_id: str = Field(min_length=1, max_length=128)
    source_provenance_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class MinedPrTaskDraftRequest(TaskDraftCreateRequest):
    source_strategy: Literal["mined_pr"] = "mined_pr"
    repository_url: str = Field(min_length=1, max_length=2048)
    base_commit: str = Field(min_length=7, max_length=128)
    patch_commit: str = Field(min_length=7, max_length=128)
    pull_request_url: str = Field(min_length=1, max_length=2048)
    pull_request_number: int = Field(gt=0)
    source_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_license_id: str = Field(min_length=1, max_length=128)
    source_provenance_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: Literal["public", "held_out", "restricted"]
    contamination_cutoff: str = Field(min_length=1, max_length=64)


class LiveWindowTaskDraftRequest(TaskDraftCreateRequest):
    source_strategy: Literal["live_window"] = "live_window"
    repository_url: str = Field(min_length=1, max_length=2048)
    source_revision: str = Field(min_length=7, max_length=128)
    source_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_license_id: str = Field(min_length=1, max_length=128)
    source_provenance_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    collected_at: str = Field(min_length=1, max_length=64)
    released_at: str = Field(min_length=1, max_length=64)
    model_cutoff: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)
    cohort_id: str = Field(pattern=r"^mvp2-live-[a-z0-9-]+$")


class TaskDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    version: str
    revision_digest: str
    evaluator_id: UUID | None = None
    status: Literal["draft", "frozen", "pending-independent-review"]


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
    ticket_text: str | None = None
    ticket_digest: str | None = None
    revision_digest: str


class EntrantRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    manifest: EntrantRevision


class MethodologyRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    scoring_digest: str
    created_at: str


class MethodologyRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    scoring_digest: str
    manifest: ProtocolRevision
    created_at: str


class RunAttemptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    phase: str
    terminal_status: str | None


class PublicRequirementCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    passed: bool | None


class PublicRunEvidence(BaseModel):
    """Published-only run projection; excludes code, raw logs, diagnostics,
    billing details, fixture data, and private artifact references."""

    model_config = ConfigDict(extra="forbid")

    trial_id: UUID
    publication_id: UUID
    task_id: str
    task_version: str
    entrant_id: str
    entrant_version: str
    repetition: int
    attempt: RunAttemptSummary | None
    verdict: Literal["pass", "fail", "contract_violation", "indeterminate"] | None
    checks: list[PublicRequirementCheck]
    evaluation_id: UUID | None = None
    evaluation_state: Literal["current", "invalid", "unscored"] = "unscored"
    trace_state: Literal["complete", "partial", "unavailable"] = "unavailable"
    trace: list[RunTraceEvent] = []
    usage: RunUsage | None = None
    configuration: dict[str, str | list[str] | None] = {}


class RunTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    event_type: str
    payload: dict[str, str | int | bool | None]
    created_at: str | None = None


class RunUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: str | None = None


class IncludedEvidenceSelection(BaseModel):
    """A trial pinned INTO a publication. Every identifying id AND digest is
    required and non-null: "this trial is included" and "this trial carries a
    complete, verifiable pinned identity" are made the same fact (review
    finding #1). A prior single flat model let `included=True` coexist with
    attempt/candidate/evaluation and all digests `None`, so the public route
    could serve a legitimate-looking published run that pinned nothing at all;
    Pydantic accepted that shape. The `Literal[True]` tag and the required
    fields together make that shape unrepresentable."""

    model_config = ConfigDict(extra="forbid")

    trial_id: UUID
    included: Literal[True]
    attempt_id: UUID
    candidate_id: UUID
    evaluation_id: UUID
    candidate_digest: str
    evaluation_digest: str
    trace_digest: str


class ExcludedEvidenceSelection(BaseModel):
    """A trial explicitly excluded from a publication. It carries no pinned
    identity, and `extra="forbid"` with only these two fields means an
    excluded selection can never smuggle a half-populated (attempt/candidate/
    evaluation) identity past validation."""

    model_config = ConfigDict(extra="forbid")

    trial_id: UUID
    included: Literal[False]


# A plain (non-discriminated) union, for the same OpenAPI/openapi-typescript
# reason ComparisonEntrantPanel documents: a boolean-tagged Pydantic
# discriminator serializes its mapping keys as the strings "True"/"False",
# which openapi-typescript then turns into string-literal `included` types
# instead of the JSON booleans every real payload carries. Pydantic's smart
# union disambiguates the two shapes on the boolean `included` value on its
# own, and each variant still renders its correct `const: true`/`const:
# false` in the schema.
PublishedEvidenceSelection = Union[IncludedEvidenceSelection, ExcludedEvidenceSelection]


class PublishedEvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aieb.published-evidence/v1"]
    campaign_id: UUID
    # The digest of the AnalysisSnapshot this manifest was published beside
    # (review finding #2). Binding the selection manifest to the snapshot as
    # one immutable, digest-checked unit prevents the snapshot from being
    # swapped for a different one after publication without detection: the
    # public read path verifies this equals the publication's own
    # snapshot_digest, so the two objects can only ever be served as the pair
    # that was published together. It does NOT prove the snapshot's rates were
    # originally derived from the selected evaluations (that semantic
    # derivation check is deferred to ENG-018) - only that the published pair
    # cannot be altered undetected afterward.
    snapshot_digest: str
    selections: list[PublishedEvidenceSelection]

    @model_validator(mode="after")
    def _unique_trial_coverage(self) -> "PublishedEvidenceManifest":
        """Every campaign trial appears at most once. Duplicate trial ids
        (whether two included, two excluded, or a contradictory
        included+excluded pair) make coverage ambiguous, so they are rejected
        rather than resolved by insertion order (review finding #1)."""
        seen: set[UUID] = set()
        for selection in self.selections:
            if selection.trial_id in seen:
                raise ValueError("published evidence selection lists a trial more than once")
            seen.add(selection.trial_id)
        return self


class RunFileDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    operation: Literal["add", "modify", "delete"]
    unified_diff: str | None
    binary: bool = False
    truncated: bool = False
    baseline_available: bool = True


class RunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_ref_id: UUID
    path: str
    content_digest: str
    size: int


class PrivateRunEvidence(BaseModel):
    """Role-gated run evidence. Candidate text and evaluator output are
    intentionally available only to operator/reviewer/administrator roles."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    task_revision_id: UUID
    entrant_revision_id: UUID
    repetition: int
    latest_attempt: RunAttemptSummary | None
    verdict: str | None = None
    checks: dict[str, bool] | None = None
    diagnostics: dict[str, str] | None = None
    engineering_stdout: str | None = None
    engineering_stderr: str | None = None
    engineering_logs_truncated: bool = False
    diffs: list[RunFileDiff]
    candidate_id: UUID | None = None
    evaluation_id: UUID | None = None
    evaluation_state: Literal["current", "superseded", "invalid", "unscored"] = "unscored"
    superseded_evaluation_count: int = 0
    trace_state: Literal["complete", "partial", "unavailable"] = "unavailable"
    trace: list[RunTraceEvent] = []
    usage: RunUsage | None = None
    configuration: dict[str, str | list[str] | None] = {}
    artifacts: list[RunArtifact] = []


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


class AttemptCoverageDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    trace: Literal["present", "missing"]
    cost_by_role: dict[Literal["engineer", "dev_application", "verifier_application", "verifier_judge"], Literal["reported", "estimated", "unknown"]]


class CoverageDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aieb.coverage-disclosure/v1"]
    attempts: list[AttemptCoverageDisclosure]
    hard_cost_eligible: bool
    limitations: list[str]


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
    # statistics.median of successful engineer_seconds - a genuine
    # conventional median, fractional for an even sample count (e.g.
    # [10, 20] -> 15.0). A prior version picked the upper-middle raw value
    # instead ([10, 20] -> 20), and a test locked that defect in as expected
    # behavior (review finding #3, second pass).
    successful_engineering_median_seconds: float | None
    deadline_rate: float | None
    infrastructure_attrition: float | None
    # Per-entrant breakdowns of the suite-wide metrics above (review finding
    # #2: a results table needs these AS entrant rows, not one suite-wide
    # number repeated on every row). Default None, NOT an empty dict (review
    # finding #1, second pass): a snapshot published before this field
    # existed genuinely has no such data at all ("unavailable in this
    # historical snapshot"), which is a different fact from "this field is
    # present and every entrant happens to have zero of something." An
    # empty-dict default collapsed both cases into the same shape, and the
    # frontend's `?.[entrantId] ?? 0` fallback then displayed a fabricated
    # `0` for genuinely-missing historical data. `None` here means "ask
    # `snapshot.per_entrant_total_tasks is None` before rendering a number,"
    # not merely "check whether this entrant's key exists."
    per_entrant_valid_trials: dict[str, int] | None = None
    per_entrant_resolved_tasks: dict[str, int] | None = None
    per_entrant_total_tasks: dict[str, int] | None = None
    per_entrant_cost_per_resolution: dict[str, float | None] | None = None
    per_entrant_verifier_cost_usd: dict[str, float | None] | None = None
    per_entrant_median_engineering_seconds: dict[str, float | None] | None = None
    per_entrant_deadline_rate: dict[str, float | None] | None = None
    per_entrant_infrastructure_attrition: dict[str, float | None] | None = None
    # Absent in historical snapshots; never synthesize evidence on public reads.
    coverage_disclosure: CoverageDisclosure | None = None
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


class FrozenEntrantEntry(BaseModel):
    """One entrant from the campaign's own frozen manifest
    (`campaign.resolved["entrants"]`) - the authoritative list of who was
    scheduled, NOT inferred from which entrants happen to have an observation
    in the snapshot. A frozen entrant with ZERO observations still appears
    here (review finding #3, third pass: such an entrant previously vanished
    from the results table entirely instead of showing incomplete coverage,
    zero valid trials, and an unavailable aggregate)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    version: str


class CohortIdentity(BaseModel):
    """The frozen cohort's own identifying fields
    (`campaign.resolved["cohort"]`) - real manifest data, not inferred from
    result rows, so a release page can show suite/track/dependency-mode/
    profile identifiers (review finding #2) without recomputing anything.

    A prior version omitted `budget_profile_id`, `application_model_profile`,
    and `required_capabilities` - real fields on `Cohort` this route already
    had access to - and the frontend mislabeled `hardware_class` as "profile",
    which is a distinct concept from the budget/application profile the spec
    actually asks for (review finding #4, second pass)."""

    model_config = ConfigDict(extra="forbid")

    track: str
    suite_id: str
    protocol_id: str
    dependency_mode: str
    hardware_class: str
    budget_profile_id: str
    application_model_profile: ApplicationProfile
    required_capabilities: tuple[str, ...]


class PublicationResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_class: Literal["ranked", "non_ranked"]

    id: UUID
    campaign_id: UUID
    snapshot_digest: str
    status: str
    supersedes_id: UUID | None
    created_at: str
    cohort_digest: str | None
    cohort: CohortIdentity | None = None
    protocol_scoring_digest: str | None = None
    # NO evaluation-date-range field: a prior version derived one from
    # min/max `attempt.created_at`, but AttemptRow only records CREATION time
    # (row insert at enqueue), not evaluation start/finish - so
    # "evaluation_completed_at" was the latest attempt-row insert, not a real
    # completion, and a single long attempt reported a zero-duration window
    # (review finding #2, third pass). Removed rather than served with an
    # accurate-sounding but fabricated value; a genuine window must wait for
    # real durable lifecycle start/finalization timestamps to exist.
    frozen_tasks: list[FrozenTaskEntry] = []
    frozen_entrants: list[FrozenEntrantEntry] = []
    snapshot: AnalysisSnapshot
    notice: str | None = None


class EligibleEntrantPanel(BaseModel):
    """`eligible=True` variant: this entrant slug was present in its
    publication's snapshot, so it carries a real (possibly still-null)
    aggregate rate - never a `reason`, which only makes sense for the
    ineligible variant."""

    model_config = ConfigDict(extra="forbid")

    entrant_id: str
    publication_id: UUID
    eligible: Literal[True]
    publication_class: Literal["ranked", "non_ranked"]
    notice: str | None = None
    # No default (review finding #2, seventh pass): required-but-nullable, not
    # optional-and-absent. A default of `None` let the field be omitted from
    # the payload entirely, which is a different, weaker contract than "always
    # present, possibly null" - the endpoint always computes a real value
    # (even if that value is itself `None`) for every eligible panel.
    aggregate: float | None


class IneligibleEntrantPanel(BaseModel):
    """`eligible=False` variant: this entrant slug was not present in its
    publication's snapshot, so it carries a `reason` and never a fabricated
    aggregate."""

    model_config = ConfigDict(extra="forbid")

    entrant_id: str
    publication_id: UUID
    eligible: Literal[False]
    publication_class: Literal["ranked", "non_ranked"]
    notice: str | None = None
    reason: str


# Identified by BOTH slug AND the publication it was selected from (review
# finding #4, third pass): the response was previously a dict keyed by slug
# alone, so selecting the same slug from two different releases (a genuine
# cross-release use case - "did agent-a improve from release 1 to release
# 2?") silently overwrote one entry, and both panels then rendered the same,
# wrong aggregate. An ordered list keyed per selection preserves both, so
# each panel shows its own publication's real result.
#
# A tagged union on `eligible` (review finding #2, sixth pass): a single flat
# model with both `aggregate` and `reason` optional let the endpoint (and any
# future caller) construct nonsensical combinations (eligible=True with a
# `reason`, eligible=False with an `aggregate`) that the generated API
# contract did nothing to rule out - only endpoint code happened to avoid
# them. The `Literal[True]`/`Literal[False]` tags make each combination the
# only one Pydantic will accept.
#
# Deliberately NOT `Field(discriminator="eligible")`: OpenAPI's
# `discriminator.mapping` keys must be strings, so a boolean-tagged Pydantic
# discriminated union serializes as `{"True": "#/.../EligibleEntrantPanel",
# "False": "#/.../IneligibleEntrantPanel"}` - openapi-typescript then read
# THAT mapping for the generated `eligible` type instead of the schema's own
# `const: true`/`const: false`, producing `eligible: "True"` (a STRING
# literal) in the generated TypeScript even though every real response
# carries the JSON boolean `true`/`false`. A plain (non-discriminated) union
# still validates the same two shapes - Pydantic's smart-union mode
# disambiguates on the boolean `eligible` value just fine - and OpenAPI
# renders each variant's own correct `const: true`/`const: false`, which
# openapi-typescript turns into the correct boolean literal type.
ComparisonEntrantPanel = Union[EligibleEntrantPanel, IneligibleEntrantPanel]


class TaskRateDelta(BaseModel):
    """A per-task rate difference between two entrants IN THE SAME
    publication, computed from each entrant's own aggregated `per_task` rate
    cell. Deliberately NOT named "paired difference"/"paired task outcome"
    (review finding #5, second pass: the API had already disclosed this
    wasn't real pairing, but the field/UI naming still called it "paired,"
    which overclaims by name even with an accurate docstring) - this is NOT
    the project/family-resampled, repetition-matched statistic spec section
    28 describes ("resampling projects/families then repetitions according
    to the declared hierarchical model") - that requires per-repetition
    observations grouped by underlying project, which the persisted
    publication snapshot does not retain (only aggregated per-task rate/n).
    Building that is real future work (the existing
    `aieb_analysis.paired_project_difference` implements the correct
    hierarchical procedure already, but nothing in the hosted persistence
    schema populates the `project_id` it requires yet - disclosed, not
    silently claimed here). Review finding #1 (2026-09-16)."""

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
    entrants: list[ComparisonEntrantPanel]
    task_rate_deltas: dict[str, list[TaskRateDelta]] | None = None


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
    # Two DISTINCT recorded reasons, never one mutable field: `reason` is the
    # correction/supersession rationale frozen at publish time (immutability
    # trigger), `withdrawal_reason` is set only when the publication is
    # withdrawn. A withdrawal no longer overwrites the correction reason.
    reason: str | None = None
    withdrawal_reason: str | None = None
    # `ranked` publications may become the canonical ranking release;
    # `non_ranked` publications are explicitly excluded from canonical ranks.
    publication_class: Literal["ranked", "non_ranked"] | None = None


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
    publication_class: Literal["ranked", "non_ranked"]
    notice: str | None = None
    entrant_version: str | None = None


class PublicationEntrantConfiguration(BaseModel):
    """The EXACT entrant configuration (model, prompt/tools digests,
    capabilities, credential reference type) THIS publication's frozen
    campaign actually used for one entrant slug, read from
    `campaign.resolved["entrants"]` - not whichever revision of that slug
    happens to be newest right now. Compare must never combine a historical
    (or cross-release) publication's metrics with a newer entrant
    revision's configuration (review finding #2, second pass)."""

    model_config = ConfigDict(extra="forbid")

    manifest: EntrantRevision


# ---- ENG-017: campaign administration + budgets ----------------------------


class MatrixPreviewCell(BaseModel):
    """One planned (task, entrant) cell in the exact matrix a freeze WOULD
    produce for the current draft + supplied registry - computed by the pure
    planner, never persisted."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    entrant_id: str
    repetitions: int


class MatrixPreviewTrial(BaseModel):
    """The exact trial a freeze WOULD create: deterministic identity, cell
    membership, repetition index and dispatch position (spec section 28/13)."""

    model_config = ConfigDict(extra="forbid")

    trial_id: UUID
    task_id: str
    entrant_id: str
    repetition_index: int
    order_index: int


class MatrixPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    trial_count: int
    cohort_id: str
    protocol_id: str
    budget_profile_id: str
    reserved_budget_usd: str | None
    cells: list[MatrixPreviewCell]
    # The ordered frozen matrix itself, not only aggregate cell counts, so an
    # operator can review the exact dispatch input before committing to a freeze.
    trials: list[MatrixPreviewTrial]


class BudgetReservationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str
    enforcement: Literal["hard", "estimated_time_limited"]
    reserved_usd: str | None
    status: Literal["active", "released", "consumed"]


class CampaignStateResponse(BaseModel):
    """A campaign lifecycle transition's authoritative result: the server's
    current campaign state, the reservation (if any), and a human-readable
    notice about consequences (e.g. what cancellation does to in-flight work)."""

    model_config = ConfigDict(extra="forbid")

    campaign: CampaignSummary
    draft: CampaignDraft | None = None
    reservation: BudgetReservationSummary | None = None
    notice: str | None = None


class CampaignStateCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    count: int


class CampaignProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    state: str
    planned_trials: int
    observed_trials: int
    attempts_by_phase: list[CampaignStateCount]
    attempts_by_terminal_status: list[CampaignStateCount]
    work_items_by_state: list[CampaignStateCount]


class InvalidAttemptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trial_id: UUID
    attempt_id: UUID
    attempt_number: int
    terminal_status: str


class CampaignApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class CampaignApprovalSummary(BaseModel):
    """The single campaign-approval fact: which independent reviewer approved
    this frozen campaign (or that none ever did). Derived from the recorded
    ReviewRow, never recomputed from a rule."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    approved: bool
    approved_by_user_id: UUID | None = None
    approved_at: str | None = None
    reason: str | None = None
    review_id: UUID | None = None


# ---- ENG-018: publication preparation, review, corrections -----------------


class PublicationPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supersedes_publication_id: UUID | None = None
    correction_reason: str | None = None
    correction_run_id: UUID | None = None
    # Prompt 14: preparation validates a COMPLETE cohort for a ranked
    # publication. `ranked` is the only default and rejects incomplete
    # coverage; publishing an incomplete snapshot requires the operator to
    # explicitly choose `non_ranked`, and such a publication is labelled and
    # can never become the canonical ranked release.
    publication_class: Literal["ranked", "non_ranked"] = "ranked"


class PublicationPreparationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    status: Literal["prepared", "approved", "rejected", "published"]
    snapshot_digest: str
    evidence_manifest_digest: str
    review_kind: str | None
    supersedes_publication_id: UUID | None
    published_publication_id: UUID | None
    created_at: str
    publication_class: Literal["ranked", "non_ranked"]


class PublicationPreparationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preparation: PublicationPreparationSummary
    snapshot: dict
    evidence_manifest: PublishedEvidenceManifest
    correction_reason: str | None
    can_approve: bool
    approval_blocked_reason: str | None


class PublicationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    # review_kind is NEVER client-selected: the server derives whether the
    # review is independent from recorded identities (see publications.py).
    # The reviewer supplies an organizational-independence ATTESTATION - an
    # input to the server's derivation, not the label itself.
    independence_attestation: bool = False
    notes: str | None = None


class PublicationWithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class RegradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry: FreezeRegistry
    reason: str | None = None


class CorrectionRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    status: Literal["running", "completed", "failed"]
    regrade_work_items: int
    created_at: str


class PublicationSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    signed_manifest: dict
    manifest_signature: str
    signing_public_key: str
    signing_key_id: str
    review_kind: str | None


class PublicationExport(BaseModel):
    """A redacted, self-verifiable publication bundle. Carries ONLY public
    data: the exact snapshot, frozen cohort/task/entrant manifest identity, the
    signed manifest, and per-trial PUBLIC (whitelist-redacted) run evidence -
    never candidate source, logs, diagnostics, costs, or artifact references."""

    model_config = ConfigDict(extra="forbid")

    publication_id: UUID
    campaign_id: UUID
    status: str
    snapshot_digest: str
    snapshot: dict
    cohort: CohortIdentity | None
    frozen_tasks: list[FrozenTaskEntry]
    frozen_entrants: list[FrozenEntrantEntry]
    signature: PublicationSignature | None
    runs: list[PublicRunEvidence]
    notice: str | None = None
    # Public provenance transparency: whether this snapshot may be treated as
    # the canonical ranked release, and the recorded reasons (correction +
    # withdrawal) so a reader can see why a result changed or was withdrawn.
    publication_class: Literal["ranked", "non_ranked"] = "ranked"
    correction_reason: str | None = None
    withdrawal_reason: str | None = None


PublicRunEvidence.model_rebuild()
PrivateRunEvidence.model_rebuild()


class ResumeCampaignRequest(BaseModel):
    """ENG-020: resuming a campaign the auto-pause mechanism paused (spec sections 39/48)
    requires an explicit, informed acknowledgement - a same-click resume as a manual pause
    would defeat the point of pausing for operator review in the first place. Ignored (and
    unnecessary) when resuming a manually-paused campaign."""

    model_config = ConfigDict(extra="forbid")

    acknowledge_auto_pause: bool = False


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, description="non-empty audit reason for activating the kill switch")

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reason must be a non-empty string")
        return v


class KillSwitchStatus(BaseModel):
    active: bool
    reason: str | None = None
    activated_at: str | None = None
    campaigns_teardown_requested: int | None = None
