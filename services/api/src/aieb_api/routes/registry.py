"""GET /v1/tasks, GET /v1/tasks/{slug}/revisions/{version} and GET /v1/entrants/{id}
(public reads).

The task catalog list is deliberately a thin projection - slug/version/
family_id/category plus the manifest's own `activity` - not the full
manifest (source digests, submission policy, requirements): those are
public too, but the catalog is a listing/filtering surface, not a task
detail page, and the detail route already exists for that.

Nothing in the current schema tracks a task's retired/deprecated status
(spec section 5's "public/retired status" column) - `task_revision` has no
such column, and no ticket has added one - so this listing shows every
revision that exists with no retired/deprecated distinction. That is a
disclosed gap, not silently invented.
"""

from __future__ import annotations

from uuid import UUID

from aieb_core.models import EntrantRevision, TaskRevision
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import not_found
from ..models import EntrantRevisionRow, TaskRevisionRow
from ..pagination import clamp_limit, decode_cursor, page
from ..revisions import validate_stored_manifest
from ..schemas import EntrantRevisionResponse, TaskRevisionResponse
from ..schemas import Page as PageEnvelope

router = APIRouter(prefix="/v1", tags=["registry"])


@router.get("/tasks", response_model=PageEnvelope)
def list_tasks(
    cursor: str | None = None,
    limit: int | None = None,
    category: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> PageEnvelope:
    effective_limit = clamp_limit(limit)
    decoded = decode_cursor(cursor)
    query = select(TaskRevisionRow).order_by(TaskRevisionRow.created_at, TaskRevisionRow.id).limit(effective_limit + 1)
    if category is not None:
        query = query.where(TaskRevisionRow.category == category)
    if decoded is not None:
        created_at, row_id = decoded
        query = query.where(tuple_(TaskRevisionRow.created_at, TaskRevisionRow.id) > (created_at, row_id))
    rows = list(session.execute(query).scalars())
    page_rows, next_cursor = page(rows, effective_limit)
    items = [
        {
            "id": str(row.id),
            "slug": row.slug,
            "version": row.version,
            "family_id": row.family_id,
            "category": row.category,
            "activity": row.manifest.get("activity"),
            "created_at": row.created_at.isoformat(),
        }
        for row in page_rows
    ]
    return PageEnvelope(items=items, next_cursor=next_cursor)


@router.get("/tasks/{slug}/revisions/{version}", response_model=TaskRevisionResponse)
def get_task_revision(slug: str, version: str, session: Session = Depends(get_session)) -> TaskRevisionResponse:
    row = session.execute(
        select(TaskRevisionRow).where(TaskRevisionRow.slug == slug, TaskRevisionRow.version == version)
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    manifest = validate_stored_manifest(TaskRevision, row.manifest, kind="task", row_id=row.id)
    return TaskRevisionResponse(id=row.id, manifest=manifest)


@router.get("/entrants/{entrant_id}", response_model=EntrantRevisionResponse)
def get_entrant_revision(entrant_id: UUID, session: Session = Depends(get_session)) -> EntrantRevisionResponse:
    row = session.get(EntrantRevisionRow, entrant_id)
    if row is None:
        raise not_found()
    manifest = validate_stored_manifest(EntrantRevision, row.manifest, kind="entrant", row_id=row.id)
    return EntrantRevisionResponse(id=row.id, manifest=manifest)
