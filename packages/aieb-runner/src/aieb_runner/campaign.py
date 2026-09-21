"""Immutable campaign planning/evidence helpers around an execution backend.

This module deliberately does not launch Harbor itself.  The Harbor adapter
produces raw trial artifacts; these contracts freeze the planned matrix and
normalize attempt outcomes before storage/publication.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from aieb_core.models import ContractModel, ExecutionValidity, ResolvedCampaign, Trial, Verdict


class TrackAMetrics(ContractModel):
    target_behavior: str | None = None
    hidden_behavior: str | None = None
    regression_preservation: str | None = None
    fault_recovery: str | None = None
    cost_usd: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    steps: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)


class CampaignAttempt(ContractModel):
    trial_id: UUID
    attempt_number: int = Field(ge=0)
    execution_validity: ExecutionValidity
    verdict: Verdict | None = None
    deadline_reached: bool | None = None
    candidate_paths: tuple[str, ...] = ()
    metrics: TrackAMetrics = TrackAMetrics()

    @model_validator(mode="after")
    def verdict_matches_validity(self) -> "CampaignAttempt":
        if self.execution_validity == ExecutionValidity.VALID and self.verdict is None:
            raise ValueError("valid attempt must carry a verdict")
        if self.execution_validity != ExecutionValidity.VALID and self.verdict is not None:
            raise ValueError("invalid attempt cannot carry a scientific verdict")
        if len(set(self.candidate_paths)) != len(self.candidate_paths):
            raise ValueError("candidate paths must be unique")
        return self


class FrozenCampaignEvidence(ContractModel):
    schema_version: Literal["aieb.campaign-evidence/v2"]
    campaign_id: UUID
    campaign_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    planned_trials: tuple[Trial, ...] = Field(min_length=1)
    attempts: tuple[CampaignAttempt, ...] = ()

    @model_validator(mode="after")
    def planned_trials_are_unique(self) -> "FrozenCampaignEvidence":
        if len({trial.id for trial in self.planned_trials}) != len(self.planned_trials):
            raise ValueError("planned trial IDs must be unique")
        return self


def freeze_campaign_evidence(campaign: ResolvedCampaign) -> FrozenCampaignEvidence:
    """Create the immutable evidence envelope before any attempt runs."""
    return FrozenCampaignEvidence(
        schema_version="aieb.campaign-evidence/v2",
        campaign_id=campaign.id,
        campaign_digest=campaign.digest(),
        planned_trials=campaign.trials,
    )


def append_attempt(evidence: FrozenCampaignEvidence, attempt: CampaignAttempt) -> FrozenCampaignEvidence:
    """Append evidence without replacing prior attempts or scored outcomes."""
    planned = {trial.id for trial in evidence.planned_trials}
    if attempt.trial_id not in planned:
        raise ValueError("attempt references a trial outside the frozen campaign")
    return evidence.model_copy(update={"attempts": (*evidence.attempts, attempt)})


def selected_attempts(evidence: FrozenCampaignEvidence) -> dict[UUID, CampaignAttempt]:
    """Select the first valid scored attempt per trial; retain all replacements."""
    grouped: dict[UUID, list[CampaignAttempt]] = defaultdict(list)
    for attempt in evidence.attempts:
        grouped[attempt.trial_id].append(attempt)
    selected: dict[UUID, CampaignAttempt] = {}
    for trial_id, attempts in grouped.items():
        for attempt in sorted(attempts, key=lambda item: (item.attempt_number, str(item.trial_id))):
            if attempt.execution_validity == ExecutionValidity.VALID and attempt.verdict is not None:
                selected[trial_id] = attempt
                break
    return selected


def canonical_ranking_eligible(evidence: FrozenCampaignEvidence) -> bool:
    """Incomplete or unscored cohorts cannot enter canonical rankings."""
    return len(selected_attempts(evidence)) == len(evidence.planned_trials)
