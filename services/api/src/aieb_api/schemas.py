"""API-specific request/response projections.

Reuses aieb_core contracts directly wherever they are already the correct
public shape; adds thin wrappers only where the API needs something the
core contracts do not model (pagination envelopes, freeze registry input).
"""

from __future__ import annotations

from uuid import UUID

from aieb_core.models import BudgetProfile, CampaignDraft, Cohort, EntrantRevision, ProtocolRevision, TaskRevision
from pydantic import BaseModel, ConfigDict


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict]
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
