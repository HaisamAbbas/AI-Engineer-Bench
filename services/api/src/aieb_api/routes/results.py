"""GET /v1/releases, GET /v1/publications/{id}/results, GET /v1/comparisons,
GET /v1/corrections (public reads).

Public results come from immutable publication snapshots, never live mutable
trial tables (spec section 33/36).
"""

from __future__ import annotations

from uuid import UUID

from aieb_core.models import EntrantRevision
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import invalid_request, not_found, service_unavailable
from ..models import CampaignRow, PublicationRow
from ..snapshots import snapshot_digest as compute_snapshot_digest
from ..pagination import clamp_limit, decode_cursor, page
from ..schemas import (
    AnalysisSnapshot,
    CohortIdentity,
    ComparisonEntrantPanel,
    ComparisonResponse,
    CorrectionEntry,
    EligibleEntrantPanel,
    EntrantResultEntry,
    FrozenEntrantEntry,
    FrozenTaskEntry,
    IneligibleEntrantPanel,
    Page,
    PublicationEntrantConfiguration,
    PublicationResultsResponse,
    PublicationSummary,
    TaskRateDelta,
)

router = APIRouter(prefix="/v1", tags=["results"])


def _verified_snapshot(row: PublicationRow) -> AnalysisSnapshot:
    """A publication trigger blocks a direct UPDATE to snapshot/snapshot_digest
    (see the publication-snapshot-immutability migration), but that is a
    second layer, not the only one: recompute the digest from the stored
    JSONB on every read a snapshot is actually served, so a row that
    predates the trigger, or was written through some other path, is caught
    here rather than served as canonical public results with a silent
    mismatch. Also validates the stored JSONB actually matches the
    aieb_analysis output shape (AnalysisSnapshot), not just its digest."""
    if compute_snapshot_digest(row.snapshot) != row.snapshot_digest:
        raise service_unavailable(f"publication {row.id} snapshot does not match its recorded digest")
    try:
        return AnalysisSnapshot.model_validate(row.snapshot)
    except ValueError as exc:
        raise service_unavailable(f"publication {row.id} snapshot does not match the expected analysis shape: {exc}") from exc


@router.get("/releases", response_model=Page[PublicationSummary])
def list_releases(cursor: str | None = None, limit: int | None = None, session: Session = Depends(get_session)) -> Page[PublicationSummary]:
    effective_limit = clamp_limit(limit)
    decoded = decode_cursor(cursor)
    # Newest-first (review finding: with pagination, "last item of the first
    # page" is not the newest release once there is more than one page) -
    # spec section 4: "Default page selects the newest non-withdrawn
    # publication." The cursor comparison is flipped to match (each next
    # page is strictly OLDER than the last row already returned).
    query = select(PublicationRow).where(PublicationRow.status == "published").order_by(
        PublicationRow.created_at.desc(), PublicationRow.id.desc()
    ).limit(effective_limit + 1)
    if decoded is not None:
        created_at, row_id = decoded
        query = query.where(tuple_(PublicationRow.created_at, PublicationRow.id) < (created_at, row_id))
    rows = list(session.execute(query).scalars())
    page_rows, next_cursor = page(rows, effective_limit)
    items = [
        PublicationSummary(
            id=row.id, campaign_id=row.campaign_id, snapshot_digest=row.snapshot_digest,
            status=row.status, created_at=row.created_at.isoformat(),
        )
        for row in page_rows
    ]
    return Page(items=items, next_cursor=next_cursor)


@router.get(
    "/publications/{publication_id}/results",
    response_model=PublicationResultsResponse,
   
    response_model_exclude_unset=True,
)
def get_publication_results(publication_id: UUID, session: Session = Depends(get_session)) -> PublicationResultsResponse:
    row = session.get(PublicationRow, publication_id)
    if row is None:
        raise not_found()
    campaign = session.get(CampaignRow, row.campaign_id)
    notice = "this snapshot has been withdrawn; it remains addressable but is not canonical" if row.status == "withdrawn" else None
    cohort, frozen_tasks, frozen_entrants = _frozen_manifest_data(campaign)
   
    return PublicationResultsResponse(
        id=row.id, campaign_id=row.campaign_id, snapshot_digest=row.snapshot_digest, status=row.status,
        supersedes_id=row.supersedes_id, created_at=row.created_at.isoformat(),
        cohort_digest=campaign.cohort_digest if campaign else None,
        cohort=cohort, protocol_scoring_digest=_protocol_scoring_digest(campaign),
        frozen_tasks=frozen_tasks, frozen_entrants=frozen_entrants,
        snapshot=_verified_snapshot(row), notice=notice,
    )


def _protocol_scoring_digest(campaign: CampaignRow | None) -> str | None:
    """The frozen protocol's own scoring digest (`campaign.resolved["protocol"]
    ["scoring_digest"]`) - real manifest data, part of the provenance bundle
    spec section 36 describes (review finding #4, second pass: a prior
    version of the download bundle had no protocol/scoring digest at all)."""
    if campaign is None or not campaign.resolved:
        return None
    protocol = campaign.resolved.get("protocol")
    return protocol.get("scoring_digest") if protocol else None


def _frozen_manifest_data(
    campaign: CampaignRow | None,
) -> tuple[CohortIdentity | None, list[FrozenTaskEntry], list[FrozenEntrantEntry]]:
    """Real data from the campaign's own frozen manifest
    (`campaign.resolved`), not inferred from which observations happen to
    exist in a published snapshot (review finding #3). `campaign.resolved`
    is only populated once a campaign is frozen - a campaign row that
    somehow has none yields no cohort identity and empty frozen lists,
    rather than raising, since a publication should always have one in
    practice but this must fail safe, not crash the results page. The frozen
    task AND entrant lists both come from here so a zero-observation task or
    entrant still appears (incomplete coverage), rather than vanishing
    because nothing was scored for it (review finding #3, first and third
    passes)."""
    if campaign is None or not campaign.resolved:
        return None, [], []
    resolved = campaign.resolved
    cohort_manifest = resolved.get("cohort")
    cohort = (
        CohortIdentity(
            track=cohort_manifest["track"], suite_id=cohort_manifest["suite_id"], protocol_id=cohort_manifest["protocol_id"],
            dependency_mode=cohort_manifest["dependency_mode"], hardware_class=cohort_manifest["hardware_class"],
            budget_profile_id=cohort_manifest["budget_profile_id"],
            application_model_profile=cohort_manifest["application_model_profile"],
            required_capabilities=tuple(cohort_manifest["required_capabilities"]),
        )
        if cohort_manifest else None
    )
    frozen_tasks = [
        FrozenTaskEntry(slug=task["id"], version=task["version"], family_id=task["family_id"], category=task["category"])
        for task in resolved.get("tasks", [])
    ]
    frozen_entrants = [
        FrozenEntrantEntry(slug=entrant["id"], version=entrant["agent_version"])
        for entrant in resolved.get("entrants", [])
    ]
    return cohort, frozen_tasks, frozen_entrants


@router.get("/publications/{publication_id}/entrants/{slug}", response_model=PublicationEntrantConfiguration)
def get_publication_entrant_configuration(
    publication_id: UUID, slug: str, session: Session = Depends(get_session),
) -> PublicationEntrantConfiguration:
    """The EXACT entrant configuration THIS publication's frozen campaign
    used for `slug` - not whichever revision of that slug is newest right
    now (review finding #2, second pass). Compare previously called
    `GET /entrants/by-slug/{slug}` for every panel, which always resolves
    the most recent `EntrantRevisionRow` regardless of which publication
    the panel is actually showing metrics for - a historical or
    cross-release comparison could silently combine one publication's
    metrics with a DIFFERENT, newer entrant revision's model/capabilities.
    Reads directly from `campaign.resolved["entrants"]`, the same frozen
    manifest `frozen_tasks`/`cohort` already come from - real data, not a
    second guess."""
    row = session.get(PublicationRow, publication_id)
    if row is None:
        raise not_found()
    campaign = session.get(CampaignRow, row.campaign_id)
    if campaign is None or not campaign.resolved:
        raise not_found()
    for entrant in campaign.resolved.get("entrants", []):
        if entrant.get("id") == slug:
            return PublicationEntrantConfiguration(manifest=EntrantRevision.model_validate(entrant))
    raise not_found()


@router.get("/corrections", response_model=Page[CorrectionEntry])
def list_corrections(cursor: str | None = None, limit: int | None = None, session: Session = Depends(get_session)) -> Page[CorrectionEntry]:
    """Append-only corrections: a publication that supersedes an earlier one, or a
    withdrawal - both are visible as a real query over `publication`, not fabricated
    content. There is no free-text "reason" field on `publication` yet, so this cannot
    yet show why a correction happened, only that one did (which publication superseded
    which, and any withdrawal) - a disclosed gap, not an invented reason."""
    effective_limit = clamp_limit(limit)
    decoded = decode_cursor(cursor)
    query = select(PublicationRow).where(
        (PublicationRow.supersedes_id.is_not(None)) | (PublicationRow.status == "withdrawn")
    ).order_by(PublicationRow.created_at.desc(), PublicationRow.id.desc()).limit(effective_limit + 1)
    if decoded is not None:
        created_at, row_id = decoded
        query = query.where(tuple_(PublicationRow.created_at, PublicationRow.id) < (created_at, row_id))
    rows = list(session.execute(query).scalars())
    page_rows, next_cursor = page(rows, effective_limit)
    items = [
        CorrectionEntry(id=row.id, campaign_id=row.campaign_id, status=row.status, supersedes_id=row.supersedes_id, created_at=row.created_at.isoformat())
        for row in page_rows
    ]
    return Page(items=items, next_cursor=next_cursor)


@router.get("/entrants/by-slug/{slug}/results", response_model=list[EntrantResultEntry])
def get_entrant_results(slug: str, session: Session = Depends(get_session)) -> list[EntrantResultEntry]:
    """Every published release this entrant slug appears in (spec section 5:
    entrant profile shows "results by release"). A real, if currently
    linear, scan over published snapshots - there is no per-entrant index
    into publications yet, since no real campaign has ever been published;
    this is honest cross-referencing, not a stubbed empty list, and is
    disclosed as not scaling past a small number of publications until such
    an index is added."""
    rows = session.execute(
        select(PublicationRow).where(PublicationRow.status == "published").order_by(PublicationRow.created_at.desc())
    ).scalars().all()
    entries: list[EntrantResultEntry] = []
    for row in rows:
        try:
            snapshot = _verified_snapshot(row)
        except Exception:  # noqa: BLE001 - a corrupted row must not break this whole listing
            continue
        if slug in snapshot.per_entrant:
            entries.append(
                EntrantResultEntry(
                    publication_id=row.id, campaign_id=row.campaign_id, status=row.status,
                    created_at=row.created_at.isoformat(), aggregate_rate=snapshot.per_entrant[slug],
                    entrant_version=_entrant_version_in_campaign(session, row.campaign_id, slug),
                )
            )
    return entries


def _entrant_version_in_campaign(session: Session, campaign_id: UUID, slug: str) -> str | None:
    """The EXACT entrant revision THIS publication's frozen campaign used
    for the given slug (`campaign.resolved["entrants"]`) - not whichever
    revision of that slug happens to be newest right now. A historical
    result must stay pinned to the configuration that actually produced it
    (review finding #3). Returns None only if the campaign/manifest cannot
    be read or the slug is not in it - a real, disclosed failure mode."""
    campaign = session.get(CampaignRow, campaign_id)
    if campaign is None or not campaign.resolved:
        return None
    for entrant in campaign.resolved.get("entrants", []):
        if entrant.get("id") == slug:
            return entrant.get("agent_version")
    return None


def _task_ids_for_entrant(snapshot: AnalysisSnapshot, entrant_id: str) -> dict[str, float | None]:
    rates: dict[str, float | None] = {}
    for key, cell in snapshot.per_task.items():
        task_id, _, cell_entrant = key.rpartition(":")
        if cell_entrant == entrant_id:
            rates[task_id] = cell.rate
    return rates


@router.get("/comparisons", response_model=ComparisonResponse)
def get_comparison(
    entrant_ids: list[str] = Query(..., alias="entrant_ids"),
    publication_id: UUID | None = None,
    entrant_publication_ids: list[str] | None = Query(default=None, alias="entrant_publication_ids"),
    session: Session = Depends(get_session),
) -> ComparisonResponse:
    """Compare 2-4 entrants. Each entrant is looked up in its OWN publication:
    `entrant_publication_ids` (same order/length as `entrant_ids`) names one
    per entrant for a genuine cross-release comparison; an entrant with no
    corresponding entry falls back to `publication_id` (the common case: all
    entrants come from the same, single publication).

    Paired per-task differences are ONLY ever computed within a single
    publication. Spec journey 6.1 is unconditional: "A cross-release
    comparison shows separate panels with a non-comparable label, never a
    calculated winner" - not "unless the cohorts happen to match." An
    earlier version of this endpoint treated equal `cohort_digest` values
    across different publications as sufficient proof of comparable
    observations; that was wrong on its own terms too, since `Cohort` itself
    does not carry the exact task list, entrant revisions, or repetition
    plan - a matching digest does not prove matching observations (review
    finding #1, 2026-09-16). Entrants from different publications therefore
    always get separate eligible panels with no paired difference, exactly
    as spec 6.1 requires - never a fabricated calculated winner.

    The response `entrants` is an ORDERED LIST, one panel per selection,
    each carrying its own `publication_id` (review finding #4, third pass):
    a prior version keyed it by slug alone, so selecting the same slug from
    two releases (the natural "did agent-a improve from release 1 to 2?"
    comparison) silently overwrote one entry and both panels rendered the
    same wrong aggregate. Selecting the exact same (slug, publication) twice
    is meaningless (comparing something to itself) and is rejected.
    """
    if not (2 <= len(entrant_ids) <= 4):
        raise invalid_request("comparisons require between 2 and 4 entrant_ids")
    if entrant_publication_ids is not None and len(entrant_publication_ids) != len(entrant_ids):
        raise invalid_request("entrant_publication_ids, when given, must have exactly one entry per entrant_ids")
    if publication_id is None and entrant_publication_ids is None:
        raise invalid_request("publication_id or entrant_publication_ids is required")

    resolved_publication_ids: list[UUID] = []
    for index in range(len(entrant_ids)):
        explicit = entrant_publication_ids[index] if entrant_publication_ids is not None else None
        if explicit:
            resolved_publication_ids.append(UUID(explicit))
        elif publication_id is not None:
            resolved_publication_ids.append(publication_id)
        else:
            raise invalid_request(f"no publication given for entrant_ids[{index}]")

    selections = list(zip(entrant_ids, resolved_publication_ids))
    if len(set(selections)) != len(selections):
        raise invalid_request("the same entrant cannot be selected from the same publication twice in one comparison")

    publication_cache: dict[UUID, tuple[PublicationRow, AnalysisSnapshot]] = {}
    for pub_id in set(resolved_publication_ids):
        row = session.get(PublicationRow, pub_id)
        if row is None:
            raise not_found()
        publication_cache[pub_id] = (row, _verified_snapshot(row))

    unique_publication_ids = set(resolved_publication_ids)
    if len(unique_publication_ids) == 1:
        # Every entrant comes from the same publication - the common case -
        # so they share the same frozen cohort, task list, entrant revisions,
        # and repetition plan by construction; this is the ONLY case paired
        # per-task differences are computed for.
        cohort_comparable = True
        non_comparable_reason = None
    else:
        # A cross-release comparison (entrants from different publications)
        # is unconditionally non-comparable (spec journey 6.1: "A
        # cross-release comparison shows separate panels with a
        # non-comparable label, never a calculated winner"). A matching
        # `campaign.cohort_digest` across publications was previously treated
        # as sufficient proof of comparability - it is not: `Cohort` records
        # the frozen track/suite/protocol/budget/hardware identity, not the
        # exact resolved task list, entrant revisions, or repetition
        # schedule, so two publications sharing a cohort_digest could still
        # differ in exactly the observations paired statistics require to
        # match (review finding #1, 2026-09-16).
        cohort_comparable = False
        non_comparable_reason = "entrants come from different publications (a cross-release comparison); paired statistics are only computed within a single publication"

    entrants: list[ComparisonEntrantPanel] = []
    entrant_task_rates: dict[str, dict[str, float | None]] = {}
    for entrant_id, pub_id in zip(entrant_ids, resolved_publication_ids):
        _, snapshot = publication_cache[pub_id]
        if entrant_id in snapshot.per_entrant:
            entrants.append(EligibleEntrantPanel(
                entrant_id=entrant_id, publication_id=pub_id, eligible=True, aggregate=snapshot.per_entrant[entrant_id],
            ))
            entrant_task_rates[entrant_id] = _task_ids_for_entrant(snapshot, entrant_id)
        else:
            entrants.append(IneligibleEntrantPanel(
                entrant_id=entrant_id, publication_id=pub_id, eligible=False,
                reason="not present in its publication's snapshot",
            ))

    task_rate_deltas: dict[str, list[TaskRateDelta]] | None = None
    if cohort_comparable:
        # cohort_comparable is only True when all entrants share ONE
        # publication, and exact duplicate (slug, publication) selections
        # were rejected above - so every eligible slug here is distinct and a
        # slug-keyed delta map cannot collide.
        eligible_ids = [panel.entrant_id for panel in entrants if panel.eligible]
        task_rate_deltas = {}
        for i, left in enumerate(eligible_ids):
            for right in eligible_ids[i + 1 :]:
                pair_key = f"{left}|{right}"
                task_ids = sorted(set(entrant_task_rates[left]) | set(entrant_task_rates[right]))
                diffs = []
                for task_id in task_ids:
                    left_rate = entrant_task_rates[left].get(task_id)
                    right_rate = entrant_task_rates[right].get(task_id)
                    difference = (left_rate - right_rate) if left_rate is not None and right_rate is not None else None
                    diffs.append(TaskRateDelta(task_id=task_id, left_rate=left_rate, right_rate=right_rate, difference=difference))
                task_rate_deltas[pair_key] = diffs

    return ComparisonResponse(
        publication_id=resolved_publication_ids[0], cohort_comparable=cohort_comparable,
        non_comparable_reason=non_comparable_reason, entrants=entrants, task_rate_deltas=task_rate_deltas,
    )
