"""AI Engineer Bench execution boundary."""

from aieb_runner.backends.base import (
    CandidateArtifacts,
    CapabilityCheck,
    CapabilityReport,
    CleanupReport,
    ExecutionBackend,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionState,
    ExecutionStatus,
)

__all__ = [
    "CandidateArtifacts",
    "CapabilityCheck",
    "CapabilityReport",
    "CleanupReport",
    "ExecutionBackend",
    "ExecutionHandle",
    "ExecutionSpec",
    "ExecutionState",
    "ExecutionStatus",
]
from .artifacts import FilesystemArtifactStore, collect_candidate, reconstruct_candidate
from .lifecycle import (
    AttemptConfig,
    AttemptOutcome,
    AttemptPhase,
    EngineeringCommand,
    FailureAttribution,
    LocalAttemptRunner,
    ReplacementPolicy,
)
from .campaign import (
    CampaignAttempt, FrozenCampaignEvidence, TrackAMetrics,
    append_attempt, canonical_ranking_eligible, freeze_campaign_evidence,
    selected_attempts,
)

__all__ = [
    "FilesystemArtifactStore", "collect_candidate", "reconstruct_candidate",
    "AttemptConfig", "AttemptOutcome", "AttemptPhase", "EngineeringCommand",
    "FailureAttribution", "LocalAttemptRunner",
    "ReplacementPolicy",
    "CampaignAttempt", "FrozenCampaignEvidence", "TrackAMetrics",
    "append_attempt", "canonical_ranking_eligible", "freeze_campaign_evidence",
    "selected_attempts",
]
