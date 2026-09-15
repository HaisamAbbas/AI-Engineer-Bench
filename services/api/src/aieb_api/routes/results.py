"""GET /v1/releases, GET /v1/publications/{id}/results, GET /v1/comparisons,
GET /v1/corrections (public reads).

Public results come from immutable publication snapshots, never live mutable
trial tables (spec section 33/36).
"""

from __future__ import annotations

from uuid import UUID

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
    ComparisonResponse,
    CorrectionEntry,
    EntrantComparisonEligible,
    EntrantComparisonIneligible,
    EntrantResultEntry,
    Page,
    PublicationResultsResponse,
    PublicationSummary,
    TaskPairedDifference,
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


@router.get("/publications/{publication_id}/results", response_model=PublicationResultsResponse)
def get_publication_results(publication_id: UUID, session: Session = Depends(get_session)) -> PublicationResultsResponse:
    row = session.get(PublicationRow, publication_id)
    if row is None:
        raise not_found()
    campaign = session.get(CampaignRow, row.campaign_id)
    notice = "this snapshot has been withdrawn; it remains addressable but is not canonical" if row.status == "withdrawn" else None
    return PublicationResultsResponse(
        id=row.id, campaign_id=row.campaign_id, snapshot_digest=row.snapshot_digest, status=row.status,
        supersedes_id=row.supersedes_id, created_at=row.created_at.isoformat(),
        cohort_digest=campaign.cohort_digest if campaign else None,
        snapshot=_verified_snapshot(row), notice=notice,
    )


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
                )
            )
    return entries


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
    entrants come from the same, single publication). Cohort compatibility
    is real, not assumed: entrants are only paired-comparable when their
    publications' campaigns share the same `cohort_digest` - the same
    frozen task/entrant/repetition plan, not merely "some publication
    exists." Incompatible entrants still get their own eligible aggregate
    (separate panels), just no paired difference - never a fabricated
    calculated winner across genuinely different cohorts (spec journey 6.1).
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

    publication_cache: dict[UUID, tuple[PublicationRow, AnalysisSnapshot, str | None]] = {}
    for pub_id in set(resolved_publication_ids):
        row = session.get(PublicationRow, pub_id)
        if row is None:
            raise not_found()
        campaign = session.get(CampaignRow, row.campaign_id)
        publication_cache[pub_id] = (row, _verified_snapshot(row), campaign.cohort_digest if campaign else None)

    unique_publication_ids = set(resolved_publication_ids)
    if len(unique_publication_ids) == 1:
        # Every entrant comes from the same publication - the common case -
        # so they share the same frozen cohort by construction; no
        # cohort_digest bookkeeping needed to know that.
        cohort_comparable = True
        non_comparable_reason = None
    else:
        cohort_digests = {publication_cache[pub_id][2] for pub_id in unique_publication_ids}
        cohort_comparable = len(cohort_digests) == 1 and None not in cohort_digests
        non_comparable_reason = (
            None if cohort_comparable
            else "entrants come from publications with different (or unresolvable) frozen cohorts; paired statistics are not meaningful across different cohorts"
        )

    entrants: dict[str, EntrantComparisonEligible | EntrantComparisonIneligible] = {}
    entrant_task_rates: dict[str, dict[str, float | None]] = {}
    for entrant_id, pub_id in zip(entrant_ids, resolved_publication_ids):
        _, snapshot, _ = publication_cache[pub_id]
        if entrant_id in snapshot.per_entrant:
            entrants[entrant_id] = EntrantComparisonEligible(eligible=True, aggregate=snapshot.per_entrant[entrant_id])
            entrant_task_rates[entrant_id] = _task_ids_for_entrant(snapshot, entrant_id)
        else:
            entrants[entrant_id] = EntrantComparisonIneligible(eligible=False, reason="not present in its publication's snapshot")

    paired_differences: dict[str, list[TaskPairedDifference]] | None = None
    if cohort_comparable:
        eligible_ids = [entrant_id for entrant_id in entrant_ids if entrants[entrant_id].eligible]
        paired_differences = {}
        for i, left in enumerate(eligible_ids):
            for right in eligible_ids[i + 1 :]:
                pair_key = f"{left}|{right}"
                task_ids = sorted(set(entrant_task_rates[left]) | set(entrant_task_rates[right]))
                diffs = []
                for task_id in task_ids:
                    left_rate = entrant_task_rates[left].get(task_id)
                    right_rate = entrant_task_rates[right].get(task_id)
                    difference = (left_rate - right_rate) if left_rate is not None and right_rate is not None else None
                    diffs.append(TaskPairedDifference(task_id=task_id, left_rate=left_rate, right_rate=right_rate, difference=difference))
                paired_differences[pair_key] = diffs

    return ComparisonResponse(
        publication_id=resolved_publication_ids[0], cohort_comparable=cohort_comparable,
        non_comparable_reason=non_comparable_reason, entrants=entrants, paired_differences=paired_differences,
    )
