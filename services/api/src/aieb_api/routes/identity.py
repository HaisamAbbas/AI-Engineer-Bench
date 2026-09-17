"""GET /v1/me - authenticated identity and server-resolved roles (Prompt 14).

The admin UI calls this after sign-in to learn WHO the API thinks it is and
WHAT the server will authorize. Roles come exclusively from the server-side
`role_bindings` table resolved against the verified OIDC identity - the browser
never derives authorization from decoded token claims, and this endpoint never
echoes token contents.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from .. import auth
from ..auth import Identity
from ..db import get_session

router = APIRouter(prefix="/v1", tags=["identity"])


class CurrentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True]
    subject: str
    issuer: str
    user_id: str | None
    roles: tuple[str, ...]


@router.get("/me", response_model=CurrentIdentity)
def current_identity(
    identity: Identity = Depends(auth.get_identity), session: Session = Depends(get_session),
) -> CurrentIdentity:
    """401 when unconfigured/unauthenticated (fail closed, via get_identity);
    a provisioned identity returns its server-resolved roles. An identity with
    no `users` row reports zero roles - authenticating grants nothing by
    itself (auth.py's rule), and the UI surfaces that honestly."""
    return CurrentIdentity(
        authenticated=True,
        subject=identity.subject,
        issuer=identity.issuer,
        user_id=auth.current_principal_id(session, identity),
        roles=resolve_role_tuple(session, identity),
    )


def resolve_role_tuple(session: Session, identity: Identity) -> tuple[str, ...]:
    return auth.resolve_roles(session, identity)