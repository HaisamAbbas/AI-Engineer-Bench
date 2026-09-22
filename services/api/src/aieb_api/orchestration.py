"""V2-GAP-004 release/campaign orchestration state machine and matrix checks.

Entity mapping (the plan's separate immutable entities map onto the hosted
schema as follows, each enforced immutably where the plan requires it):

- ``ReleaseDraft``      -> ``campaign.draft`` (aieb.campaign-draft/v1). Mutable
  only while ``campaign.state == 'draft'``; the ``campaign_frozen_manifest_immutable``
  BEFORE UPDATE trigger rejects any later change.
- ``ReleaseManifest``   -> ``campaign.resolved`` + ``manifest_digest`` +
  ``cohort_digest`` (aieb.campaign/v1). Written exactly once by freeze and
  immutable thereafter (same trigger).
- ``Campaign``          -> ``campaign`` row, whose ``state`` follows the
  transitions below - rejected at the service layer here AND in PostgreSQL by
  the ``campaign_state_transition`` trigger (migration
  ``orchestration_state_machine_and_matrix``), so a raw ``psql`` UPDATE cannot
  bypass the machine either.
- ``CampaignCell``      -> ``trial`` row (one per task_revision x
  entrant_revision x repetition_index), with the plan-required per-cell
  identity (deterministic id + cell_digest), order, budget allocation, planned
  deadline, and initial status persisted by plan-time expansion.
- ``Attempt``           -> ``attempt`` row (append-only; replacements add rows,
  never overwrite).
- ``Publication``       -> ``publication`` row (published/withdrawn/superseded)
  plus the pre-publication ``publication_preparation`` row
  (prepared/approved/rejected/published).

Campaign state machine (V2-GAP-004 plan section 1):

    draft      -> frozen
    frozen     -> planned | cancelling
    planned    -> approved | cancelling
    approved   -> running | cancelling
    running    -> paused | cancelling | completed | incomplete
    paused     -> running | cancelling | completed | incomplete
    cancelling -> cancelled
    cancelled / completed / incomplete are terminal

``paused``/``cancelling`` are operational sub-states of the plan's linear
``running -> completed/incomplete/cancelled`` step: pause is reversible, and
cancellation drains leased work before reaching ``cancelled``. Everything not
listed is illegal and rejected by both enforcement layers. A same-state
update (non-transition bookkeeping writes such as counters or notes) is not a
transition and stays allowed.
"""

from __future__ import annotations

from aieb_core.models import Trial as TrialContract
from sqlalchemy import select
from sqlalchemy.orm import Session

from .evidence_integrity import evidence_digest
from .models import CampaignRow, TrialRow


class OrchestrationError(ValueError):
    """Service-level rejection of an illegal orchestration action."""


class MatrixMismatchError(OrchestrationError):
    """The persisted cell matrix does not exactly equal the frozen manifest matrix."""


ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"frozen"}),
    "frozen": frozenset({"planned", "cancelling"}),
    "planned": frozenset({"approved", "cancelling"}),
    "approved": frozenset({"running", "cancelling"}),
    "running": frozenset({"paused", "cancelling", "completed", "incomplete"}),
    "paused": frozenset({"running", "cancelling", "completed", "incomplete"}),
    "cancelling": frozenset({"cancelled"}),
    "cancelled": frozenset(),
    "completed": frozenset(),
    "incomplete": frozenset(),
}

# States from which a manifest identity exists (frozen or later). The
# operator CLI uses this to decide when digest enforcement applies.
MANIFEST_ANCHORED_STATES = frozenset(
    {"frozen", "planned", "approved", "running", "paused", "cancelling", "cancelled", "completed", "incomplete"}
)

# States a campaign may be cancelled from (plan section 1 keeps cancellation
# reachable at every non-terminal, non-draining state).
CANCELLABLE_STATES = ("frozen", "planned", "approved", "running", "paused")


def assert_transition(frm: str, to: str) -> None:
    """Service-level illegal-transition rejection (mirrored in PostgreSQL)."""
    if frm == to:
        return
    allowed = ALLOWED_TRANSITIONS.get(frm)
    if allowed is None:
        raise OrchestrationError(f"unknown campaign state {frm!r}")
    if to not in allowed:
        raise OrchestrationError(
            f"illegal campaign state transition: {frm!r} -> {to!r} "
            f"(allowed from {frm!r}: {sorted(allowed) or 'none - terminal'})"
        )


def compute_matrix_digest(cell_identities: list[dict]) -> str:
    """Canonical digest over the exact, ordered-by-id cell identity list.

    Covers every field the plan requires the matrix to pin per cell: the
    deterministic cell id, task/entrant digests, repetition index, and the
    per-cell contract digest. Order-insensitive over cells (sorted by cell id)
    but exact over membership: adding, removing, or altering any cell changes
    the digest.
    """
    normalized = sorted(
        (
            {
                "trial_id": str(cell["trial_id"]),
                "task_digest": cell["task_digest"],
                "entrant_digest": cell["entrant_digest"],
                "repetition": int(cell["repetition"]),
                "cell_digest": cell["cell_digest"],
            }
            for cell in cell_identities
        ),
        key=lambda cell: cell["trial_id"],
    )
    return evidence_digest({"schema_version": "aieb.campaign-matrix/v1", "cells": normalized})


def verify_matrix_or_raise(session: Session, campaign: CampaignRow) -> str:
    """Plan section 3/9: refuse to start unless the persisted matrix EXACTLY
    equals the frozen manifest matrix.

    Delegates membership/identity/digest checking to the same validator
    aggregation uses (so start and publication can never disagree about what
    the frozen matrix is), then additionally checks the recorded matrix digest
    still matches a digest recomputed over the persisted rows. Returns the
    verified matrix digest.
    """
    from .aggregation import CampaignNotAggregatable, _validated_campaign_trials

    if not isinstance(campaign.resolved, dict):
        raise MatrixMismatchError("campaign has no frozen resolved manifest")
    try:
        _validated_campaign_trials(session, campaign.id)
    except CampaignNotAggregatable as exc:
        raise MatrixMismatchError(f"persisted cell matrix does not match the frozen manifest: {exc}") from exc

    resolved = campaign.resolved
    expected = compute_matrix_digest(_resolved_cells(resolved))
    persisted = compute_matrix_digest(_persisted_cells(session, campaign.id, resolved))
    if persisted != expected:
        raise MatrixMismatchError("persisted cell matrix digest differs from the frozen manifest matrix digest")
    if campaign.matrix_digest is not None and campaign.matrix_digest != expected:
        raise MatrixMismatchError(
            "the campaign's recorded matrix digest differs from its frozen manifest; the matrix was altered after planning"
        )
    return expected


def cell_digest_for(trial: dict) -> str:
    """The per-cell contract digest, computed exactly as expansion does
    (``TrialContract.model_validate(trial).digest()``)."""
    return TrialContract.model_validate(trial).digest()


def _resolved_cells(resolved: dict) -> list[dict]:
    return [
        {
            "trial_id": trial["id"],
            "task_digest": trial["task_digest"],
            "entrant_digest": trial["entrant_digest"],
            "repetition": trial["repetition_index"],
            "cell_digest": cell_digest_for(trial),
        }
        for trial in resolved.get("trials", [])
    ]


def _persisted_cells(session: Session, campaign_id, resolved: dict) -> list[dict]:
    """Persisted rows projected through the FROZEN manifest's binding: each
    persisted row's cell digest is compared against the digest recorded for
    the same deterministic trial id in the manifest."""
    expected_by_id = {str(trial["id"]): trial for trial in resolved.get("trials", [])}
    rows = session.execute(
        select(TrialRow).where(TrialRow.campaign_id == campaign_id).order_by(TrialRow.id)
    ).scalars().all()
    cells: list[dict] = []
    for row in rows:
        trial = expected_by_id.get(str(row.id))
        if trial is None:
            # Extra row: feed it through with its own digest so the digest
            # comparison also fails (membership failure is reported first by
            # _validated_campaign_trials; this keeps the digest honest too).
            cells.append({
                "trial_id": str(row.id), "task_digest": "", "entrant_digest": "",
                "repetition": row.repetition, "cell_digest": row.cell_digest,
            })
            continue
        cells.append({
            "trial_id": str(row.id),
            "task_digest": trial["task_digest"],
            "entrant_digest": trial["entrant_digest"],
            "repetition": row.repetition,
            "cell_digest": row.cell_digest,
        })
    return cells
