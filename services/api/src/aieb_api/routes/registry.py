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

import hashlib
from uuid import UUID

from aieb_core.models import EntrantRevision, TaskRevision
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import not_found
from ..errors import service_unavailable
from ..evidence_integrity import task_revision_digest
from ..models import EntrantRevisionRow, ProtocolRevisionRow, TaskRevisionRow
from ..pagination import clamp_limit, decode_cursor, page
from ..revisions import validate_stored_manifest
from ..schemas import (
    EntrantRevisionResponse,
    MethodologyRevisionResponse,
    MethodologyRevisionSummary,
    Page,
    TaskCatalogEntry,
    TaskRevisionResponse,
)

router = APIRouter(prefix="/v1", tags=["registry"])


@router.get("/tasks", response_model=Page[TaskCatalogEntry])
def list_tasks(
    cursor: str | None = None,
    limit: int | None = None,
    category: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Page[TaskCatalogEntry]:
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
        TaskCatalogEntry(
            id=row.id, slug=row.slug, version=row.version, family_id=row.family_id,
            category=row.category, activity=row.manifest.get("activity"), created_at=row.created_at.isoformat(),
        )
        for row in page_rows
    ]
    return Page(items=items, next_cursor=next_cursor)


@router.get("/tasks/{slug}/revisions/{version}", response_model=TaskRevisionResponse)
def get_task_revision(slug: str, version: str, session: Session = Depends(get_session)) -> TaskRevisionResponse:
    row = session.execute(
        select(TaskRevisionRow).where(TaskRevisionRow.slug == slug, TaskRevisionRow.version == version)
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    manifest = validate_stored_manifest(TaskRevision, row.manifest, kind="task", row_id=row.id)
    expected = task_revision_digest(row.manifest, row.ticket_text)
    if expected != row.revision_digest:
        raise service_unavailable("stored task revision ticket or manifest failed its identity digest check")
    ticket_digest = hashlib.sha256(row.ticket_text.encode("utf-8")).hexdigest() if row.ticket_text is not None else None
    return TaskRevisionResponse(
        id=row.id, manifest=manifest, ticket_text=row.ticket_text,
        ticket_digest=ticket_digest, revision_digest=row.revision_digest,
    )


@router.get("/methodology", response_model=list[MethodologyRevisionSummary])
def list_methodology_revisions(session: Session = Depends(get_session)) -> list[MethodologyRevisionSummary]:
    rows = session.execute(
        select(ProtocolRevisionRow).order_by(ProtocolRevisionRow.created_at.desc(), ProtocolRevisionRow.version)
    ).scalars().all()
    return [
        MethodologyRevisionSummary(
            version=row.version, scoring_digest=row.scoring_digest, created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@router.get("/methodology/{version}", response_model=MethodologyRevisionResponse)
def get_methodology_revision(version: str, session: Session = Depends(get_session)) -> MethodologyRevisionResponse:
    row = session.execute(
        select(ProtocolRevisionRow).where(ProtocolRevisionRow.version == version)
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    return MethodologyRevisionResponse(
        version=row.version,
        scoring_digest=row.scoring_digest,
        manifest=row.manifest,
        created_at=row.created_at.isoformat(),
    )


@router.get("/entrants/by-slug/{slug}", response_model=EntrantRevisionResponse)
def get_entrant_revision_by_slug(slug: str, session: Session = Depends(get_session)) -> EntrantRevisionResponse:
    """Public entrant profile pages link by slug, not the internal UUID
    (`GET /entrants/{entrant_id}` below): aieb_analysis snapshots key
    `per_entrant` by the entrant's slug (the same identifier a frozen
    campaign's `entrant_ids` names), which carries no version - so this
    resolves to the MOST RECENT revision for that slug. A specific
    historical result's exact configuration can differ from "most recent"
    once an entrant slug has more than one revision; this is a real,
    disclosed limitation of not having a per-result revision pointer in the
    snapshot, not a silent guess presented as exact.
    """
    row = session.execute(
        select(EntrantRevisionRow).where(EntrantRevisionRow.slug == slug).order_by(EntrantRevisionRow.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    manifest = validate_stored_manifest(EntrantRevision, row.manifest, kind="entrant", row_id=row.id)
    return EntrantRevisionResponse(id=row.id, manifest=manifest)


@router.get("/entrants/{entrant_id}", response_model=EntrantRevisionResponse)
def get_entrant_revision(entrant_id: UUID, session: Session = Depends(get_session)) -> EntrantRevisionResponse:
    row = session.get(EntrantRevisionRow, entrant_id)
    if row is None:
        raise not_found()
    manifest = validate_stored_manifest(EntrantRevision, row.manifest, kind="entrant", row_id=row.id)
    return EntrantRevisionResponse(id=row.id, manifest=manifest)
