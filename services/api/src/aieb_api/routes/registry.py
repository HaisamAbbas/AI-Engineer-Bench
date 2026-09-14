"""GET /v1/tasks/{slug}/revisions/{version} and GET /v1/entrants/{id} (public reads)."""

from __future__ import annotations

from uuid import UUID

from aieb_core.models import EntrantRevision, TaskRevision
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import not_found
from ..models import EntrantRevisionRow, TaskRevisionRow
from ..revisions import validate_stored_manifest
from ..schemas import EntrantRevisionResponse, TaskRevisionResponse

router = APIRouter(prefix="/v1", tags=["registry"])


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
