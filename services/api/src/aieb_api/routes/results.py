"""GET /v1/releases, GET /v1/publications/{id}/results, GET /v1/comparisons (public reads).

Public results come from immutable publication snapshots, never live mutable
trial tables (spec section 33/36).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import invalid_request, not_found
from ..models import PublicationRow
from ..pagination import clamp_limit, decode_cursor, page
from ..schemas import Page

router = APIRouter(prefix="/v1", tags=["results"])


@router.get("/releases", response_model=Page)
def list_releases(cursor: str | None = None, limit: int | None = None, session: Session = Depends(get_session)) -> Page:
    effective_limit = clamp_limit(limit)
    decoded = decode_cursor(cursor)
    query = select(PublicationRow).where(PublicationRow.status == "published").order_by(
        PublicationRow.created_at, PublicationRow.id
    ).limit(effective_limit + 1)
    if decoded is not None:
        created_at, row_id = decoded
        query = query.where(tuple_(PublicationRow.created_at, PublicationRow.id) > (created_at, row_id))
    rows = list(session.execute(query).scalars())
    page_rows, next_cursor = page(rows, effective_limit)
    items = [
        {
            "id": str(row.id),
            "campaign_id": str(row.campaign_id),
            "snapshot_digest": row.snapshot_digest,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in page_rows
    ]
    return Page(items=items, next_cursor=next_cursor)


@router.get("/publications/{publication_id}/results")
def get_publication_results(publication_id: UUID, session: Session = Depends(get_session)) -> dict:
    row = session.get(PublicationRow, publication_id)
    if row is None:
        raise not_found()
    result = {
        "id": str(row.id),
        "campaign_id": str(row.campaign_id),
        "snapshot_digest": row.snapshot_digest,
        "status": row.status,
        "snapshot": row.snapshot,
    }
    if row.status == "withdrawn":
        result["notice"] = "this snapshot has been withdrawn; it remains addressable but is not canonical"
    return result


@router.get("/comparisons")
def get_comparison(
    publication_id: UUID,
    entrant_ids: list[str] = Query(..., alias="entrant_ids"),
    session: Session = Depends(get_session),
) -> dict:
    if not (2 <= len(entrant_ids) <= 4):
        raise invalid_request("comparisons require between 2 and 4 entrant_ids")
    row = session.get(PublicationRow, publication_id)
    if row is None:
        raise not_found()
    per_entrant = row.snapshot.get("per_entrant", {})
    entrants = {}
    for entrant_id in entrant_ids:
        if entrant_id in per_entrant:
            entrants[entrant_id] = {"eligible": True, "aggregate": per_entrant[entrant_id]}
        else:
            entrants[entrant_id] = {"eligible": False, "reason": "not present in this publication snapshot"}
    return {
        "publication_id": str(row.id),
        "cohort_comparable": True,
        "entrants": entrants,
    }
