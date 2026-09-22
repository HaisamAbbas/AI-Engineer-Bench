"""Versioned MVP-2 public-repository bug-finding contracts.

These contracts are intentionally separate from Track A engineering tasks and
carry an explicit cohort identity so findings can never be ranked together by
accident.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .canonical import content_hash
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
    finding_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    location: SafePath
    symbol_or_line: str = Field(min_length=1)
    reproduction_steps: tuple[str, ...] = Field(min_length=1)
    observed_behavior: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    severity: Severity
    evidence: tuple[FindingEvidence, ...] = Field(min_length=1)
    # Decimal avoids the ambiguous float representation forbidden by the core
    # canonicalizer while retaining a bounded confidence value.
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @field_validator("location")
    @classmethod
    def location_is_safe(cls, value: str) -> str:
        return _safe_path(value)


class BugPatch(ContractModel):
    """Patch bytes and their identity, rather than a caller-asserted digest."""

    patch_content: str = Field(min_length=1, max_length=5_000_000)
    patch_digest: Digest
    changed_paths: tuple[SafePath, ...] = Field(min_length=1)

    @field_validator("changed_paths")
    @classmethod
    def paths_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        paths = tuple(_safe_path(p) for p in value)
        if len(set(paths)) != len(paths):
            raise ValueError("patch paths must be unique")
        return paths

    @model_validator(mode="after")
    def digest_matches_content(self) -> "BugPatch":
        expected = hashlib.sha256(self.patch_content.encode("utf-8")).hexdigest()
        if self.patch_digest != expected:
            raise ValueError("patch_digest must match the UTF-8 patch_content bytes")
        return self


class BugHiddenLabel(ContractModel):
    """Evaluator-only label; never include a label set in a public bundle."""

    schema_version: Literal["aieb.bug-label/v2"]
    label_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    location: SafePath
    symbol_or_line: str = Field(min_length=1, max_length=256)
    severity: Severity
    finding_digest: Digest
    reproduction_digest: Digest
    expected_patch_digest: Digest | None = None

    @field_validator("location")
    @classmethod
    def label_location_is_safe(cls, value: str) -> str:
        return _safe_path(value)


class BugHiddenLabelSet(ContractModel):
    """Private evaluator input with a stable, independently auditable identity."""

    schema_version: Literal["aieb.bug-label-set/v2"]
    task_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    repository_digest: Digest
    labels: tuple[BugHiddenLabel, ...] = Field(min_length=1)
    label_set_digest: Digest

    @model_validator(mode="after")
    def labels_are_unique_and_bound(self) -> "BugHiddenLabelSet":
        ids = [label.label_id for label in self.labels]
        keys = [(label.location, label.symbol_or_line) for label in self.labels]
        if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
            raise ValueError("hidden labels must have unique ids and locations")
        expected = self.__class__.model_construct(
            schema_version=self.schema_version,
            task_id=self.task_id,
            revision=self.revision,
            repository_digest=self.repository_digest,
            labels=self.labels,
            label_set_digest="0" * 64,
        ).model_dump(mode="json", exclude={"label_set_digest"})
        if self.label_set_digest != self.__class__._digest_payload(expected):
            raise ValueError("hidden label set digest does not match its labels")
        return self

    @staticmethod
    def _digest_payload(value: object) -> str:
        from .canonical import content_hash

        return content_hash(value)


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
    evaluator_digest: Digest
    hidden_label_ref: str = Field(pattern=r"^private://.+")
    evaluator_ref: str = Field(pattern=r"^private://.+")
    mode: TrackMode
    public_examples: tuple[BugFinding, ...] = ()
    baseline_ref: str = Field(min_length=1)
    reference_ref: str = Field(min_length=1)
    alternative_ref: str = Field(min_length=1)
    negative_control_ref: str = Field(min_length=1)
    public_test_ref: str = Field(min_length=1)
    execution_evidence_ref: str | None = None


class BugEvaluationResult(ContractModel):
    """Signed-shaped output of the trusted, isolated bug evaluator.

    Submission fields never decide reproduction quality or patch correctness.
    The evaluator records the label identities it matched and the digests of
    the evidence it actually produced.  The scoring boundary verifies all of
    those bindings before using the result.
    """

    schema_version: Literal["aieb.bug-evaluation/v2"]
    task_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    label_set_digest: Digest
    evaluator_digest: Digest
    matched_label_ids: tuple[str, ...] = ()
    finding_evidence_digests: dict[str, Digest] = Field(default_factory=dict)
    reproduction_evidence_digests: dict[str, Digest] = Field(default_factory=dict)
    patch_content_digest: Digest | None = None
    patch_correct: bool | None = None
    evaluation_digest: Digest

    @model_validator(mode="after")
    def evidence_is_well_formed(self) -> "BugEvaluationResult":
        if len(set(self.matched_label_ids)) != len(self.matched_label_ids):
            raise ValueError("evaluator matched label IDs must be unique")
        matched = set(self.matched_label_ids)
        if not set(self.finding_evidence_digests) <= matched:
            raise ValueError("finding evidence may only be reported for matched labels")
        if not set(self.reproduction_evidence_digests) <= matched:
            raise ValueError("reproduction evidence may only be reported for matched labels")
        payload = self.model_dump(mode="json", exclude={"evaluation_digest"})
        if self.evaluation_digest != content_hash(payload):
            raise ValueError("evaluation_digest does not match evaluator output")
        return self


class BugReleaseManifest(ContractModel):
    schema_version: Literal["aieb.bug-release/v2"]
    release_id: str = Field(pattern=r"^mvp2-bugfinding-release-[a-z0-9-]+$")
    cohort_id: str = Field(pattern=r"^mvp2-bugfinding-[a-z0-9-]+$")
    mode: TrackMode
    tasks: tuple[BugTaskRevision, ...] = Field(min_length=1)
    status: Literal["development", "ready-for-review", "official"] = "development"

    @model_validator(mode="after")
    def cohort_isolated(self) -> "BugReleaseManifest":
        if any(task.cohort_id != self.cohort_id for task in self.tasks):
            raise ValueError("all MVP-2 tasks must share the release cohort")
        if any(task.mode != self.mode for task in self.tasks):
            raise ValueError("finding-only and patch tasks require separate releases")
        identities = [(task.task_id, task.revision) for task in self.tasks]
        if len(set(identities)) != len(identities):
            raise ValueError("an MVP-2 release cannot contain duplicate task revisions")
        if self.status == "official":
            raise ValueError("official publication requires explicit release authorization")
        return self


class BugScore(ContractModel):
    """Component metrics for one hidden-label evaluation; no opaque index."""

    schema_version: Literal["aieb.bug-score/v2"]
    task_id: str = Field(min_length=1)
    mode: TrackMode
    true_positive_count: int = Field(ge=0)
    false_positive_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    severity_correct_count: int = Field(ge=0)
    reproduction_quality_count: int = Field(ge=0)
    hidden_label_count: int = Field(ge=1)
    precision: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    recall: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    severity_accuracy: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    reproduction_quality: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    patch_correct: bool | None = None
    scoreable: bool
    limitations: tuple[str, ...] = ()


def score_bug_submission(
    submission: BugSubmission,
    labels: BugHiddenLabelSet,
    *,
    task: BugTaskRevision,
    evaluation: BugEvaluationResult,
) -> BugScore:
    """Score a submission using only a bound isolated-evaluator result.

    Matching uses the location/symbol identity, not the candidate-controlled
    finding id. Duplicate reports are counted separately and unmatched reports
    receive no credit, so speculative lists cannot inflate recall.  The
    evaluator result is deliberately required: a caller cannot assert a
    reproduction or patch outcome by passing a boolean to this function.
    """

    if labels.task_id != task.task_id or labels.revision != task.revision:
        raise ValueError("hidden label set is not bound to the frozen task revision")
    if task.hidden_label_digest != labels.label_set_digest:
        raise ValueError("hidden label set digest does not match the task revision")
    if task.repository.content_digest != labels.repository_digest:
        raise ValueError("hidden label set repository does not match the task revision")
    if submission.repository.content_digest != task.repository.content_digest:
        raise ValueError("submission repository does not match the frozen task revision")
    if submission.mode != task.mode:
        raise ValueError("submission mode does not match the frozen task revision")
    if (
        evaluation.task_id != task.task_id
        or evaluation.revision != task.revision
        or evaluation.label_set_digest != labels.label_set_digest
        or evaluation.evaluator_digest != task.evaluator_digest
    ):
        raise ValueError("evaluator result is not bound to the task, labels, and evaluator revision")

    label_by_key = {(label.location, label.symbol_or_line): label for label in labels.labels}
    label_by_id = {label.label_id: label for label in labels.labels}
    matched_ids = set(evaluation.matched_label_ids)
    if not matched_ids <= set(label_by_id):
        raise ValueError("evaluator result names an unknown hidden label")
    if set(evaluation.finding_evidence_digests) != matched_ids:
        raise ValueError("evaluator must provide finding evidence for every matched label")
    if any(
        evaluation.finding_evidence_digests[label_id] != label_by_id[label_id].finding_digest
        for label_id in matched_ids
    ):
        raise ValueError("evaluator finding evidence is not bound to the hidden finding digest")
    reproduction_ids = set(evaluation.reproduction_evidence_digests)
    if any(
        evaluation.reproduction_evidence_digests[label_id] != label_by_id[label_id].reproduction_digest
        for label_id in reproduction_ids
    ):
        raise ValueError("evaluator reproduction evidence is not bound to the hidden reproduction digest")

    seen: set[tuple[str, str]] = set()
    true_positives = duplicates = false_positives = severity_correct = reproduction = 0
    for finding in submission.findings:
        key = (finding.location, finding.symbol_or_line)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        label = label_by_key.get(key)
        if label is None or label.label_id not in matched_ids:
            false_positives += 1
            continue
        true_positives += 1
        severity_correct += finding.severity == label.severity
        reproduction += label.label_id in reproduction_ids
    label_count = len(labels.labels)
    precision = Decimal(true_positives) / Decimal(max(1, true_positives + false_positives))
    recall = Decimal(true_positives) / Decimal(label_count)
    severity_accuracy = Decimal(severity_correct) / Decimal(max(1, true_positives))
    reproduction_quality = Decimal(reproduction) / Decimal(max(1, true_positives))
    patch_correct: bool | None = None
    if submission.mode == "patch":
        if evaluation.patch_content_digest != submission.patch.patch_digest:  # type: ignore[union-attr]
            raise ValueError("evaluator patch evidence is not bound to the submitted patch bytes")
        if evaluation.patch_correct is None:
            raise ValueError("patch evaluation must report an evaluator-owned correctness result")
        expected_patch_digests = {
            label.expected_patch_digest
            for label in labels.labels
            if label.expected_patch_digest is not None
        }
        if evaluation.patch_correct and expected_patch_digests and submission.patch.patch_digest not in expected_patch_digests:  # type: ignore[union-attr]
            raise ValueError("evaluator marked a patch correct but it does not match a hidden expected patch digest")
        patch_correct = evaluation.patch_correct
    elif evaluation.patch_correct is not None or evaluation.patch_content_digest is not None:
        raise ValueError("finding-only evaluation cannot contain patch outcomes")

    return BugScore(
        schema_version="aieb.bug-score/v2",
        task_id=labels.task_id,
        mode=submission.mode,
        true_positive_count=true_positives,
        false_positive_count=false_positives,
        duplicate_count=duplicates,
        severity_correct_count=severity_correct,
        reproduction_quality_count=reproduction,
        hidden_label_count=label_count,
        precision=precision,
        recall=recall,
        severity_accuracy=severity_accuracy,
        reproduction_quality=reproduction_quality,
        patch_correct=patch_correct,
        scoreable=true_positives > 0,
        limitations=(
            "duplicate and unmatched findings receive no additional credit",
            "reproduction and patch outcomes are accepted only from the bound evaluator result",
        ),
    )
