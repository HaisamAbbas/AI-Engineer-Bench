"""Stable core contracts for AI Engineer Bench."""

from .canonical import canonical_bytes, content_hash
from .models import (
    Attempt,
    BudgetProfile,
    CampaignDraft,
    CandidateManifest,
    Cohort,
    EntrantRevision,
    EvaluationPlan,
    EvaluationResult,
    EventEnvelope,
    ProtocolRevision,
    PublicationManifest,
    ResolvedCampaign,
    TaskRevision,
    Trial,
)
from .planner import freeze_campaign, resolve_campaign
from .contracts_v2 import (
    AgentConfiguration, ModelConfiguration, PublicationSnapshot, ReleaseManifest,
    ModelExecutionAuthorization, TrackAProtocol, TrackBProtocol, V2Campaign,
    V2TaskRevision, VerdictRecord,
)
from .bugfinding_v2 import (
    BugRepositorySnapshot, FindingEvidence, BugFinding, BugPatch,
    BugSubmission, BugTaskRevision, BugReleaseManifest, BugHiddenLabel,
    BugHiddenLabelSet, BugEvaluationResult, BugScore, score_bug_submission,
)

__all__ = [
    "Attempt", "BudgetProfile", "CampaignDraft", "CandidateManifest", "Cohort",
    "EntrantRevision", "EvaluationPlan", "EvaluationResult", "EventEnvelope",
    "ProtocolRevision", "PublicationManifest", "ResolvedCampaign", "TaskRevision",
    "Trial", "canonical_bytes", "content_hash", "freeze_campaign", "resolve_campaign",
    "AgentConfiguration", "ModelConfiguration", "PublicationSnapshot", "ReleaseManifest",
    "ModelExecutionAuthorization",
    "TrackAProtocol", "TrackBProtocol", "V2Campaign", "V2TaskRevision", "VerdictRecord",
    "BugRepositorySnapshot", "FindingEvidence", "BugFinding", "BugPatch",
    "BugSubmission", "BugTaskRevision", "BugReleaseManifest", "BugHiddenLabel",
    "BugHiddenLabelSet", "BugEvaluationResult", "BugScore", "score_bug_submission",
]
