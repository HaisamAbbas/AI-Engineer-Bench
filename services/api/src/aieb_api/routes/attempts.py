"""ENG-020 (spec section 37): per-attempt, per-role scoped credential verification.

These endpoints are the ONLY places a scoped attempt credential proves identity to the control
plane - deliberately NOT operator-authenticated. The presented token IS the authentication:
a candidate- or verifier-role process that holds its attempt's short-lived token can prove
exactly that role for exactly that attempt - and use the credential-authorized capability
below - and nothing more. The dedicated verifier subprocess uses this instead of holding a
long-lived operator credential, so a leaked verifier secret can never impersonate the
operator or play another role/attempt (codex-audit finding 1: the capability must be real,
not introspection-only, which is why GET /{attempt_id}/candidate exists here)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..db import get_session
from ..worker.repository import (
    attempt_credential_status,
    load_stored_candidate,
    verify_attempt_credential,
)

router = APIRouter(prefix="/v1/attempts", tags=["attempts"])

_ATTEMPT_UUID_TYPE = uuid.UUID


def _parse_attempt_or_404(attempt_id: str) -> uuid.UUID:
    """A malformed UUID path is a bad resource reference, not a valid request - 404 rather
    than an unhandled ValueError reaching the error handler (codex-audit finding 5)."""
    try:
        return _ATTEMPT_UUID_TYPE(attempt_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="attempt not found")


def _try_authenticate(session: Session, attempt_id: uuid.UUID, authorization: str | None) -> str:
    """Authenticate a presented `Authorization: Bearer <token>` as a scoped attempt credential
    for EITHER role of THIS attempt. Returns the proven role, or raises 401. Fails closed on
    any malformed header, missing token, or revoked/expired/wrong-token case - and reveals
    nothing about WHY (no row / revoked / expired / wrong token / wrong role)."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="credential required")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="credential required")
    if verify_attempt_credential(session, attempt_id=attempt_id, actor_role="candidate", token=token):
        return "candidate"
    if verify_attempt_credential(session, attempt_id=attempt_id, actor_role="verifier", token=token):
        return "verifier"
    raise HTTPException(status_code=401, detail="credential required")


def _require_role(
    session: Session, attempt_id: uuid.UUID, authorization: str | None, *, actor_role: Literal["candidate", "verifier"],
) -> None:
    """Authenticate the presented credential and then REQUIRE it proves the exact requested
    role (codex-audit finding 3, second review round): capabilities are role-differentiated -
    a valid candidate-role credential is 403 on a verifier-only capability, not silently as
    authoritative as the verifier role. 401 when the credential is absent/invalid; 403 when it
    is valid but for the wrong role."""
    proven = _try_authenticate(session, attempt_id=attempt_id, authorization=authorization)
    if proven != actor_role:
        raise HTTPException(status_code=403, detail="credential does not authorize this role")


class CredentialVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_role: Literal["candidate", "verifier"]
    token: str


class CredentialVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    actor_role: Literal["candidate", "verifier"]
    expires_at: str | None


@router.post("/{attempt_id}/credentials/verify", response_model=CredentialVerifyResponse)
def verify_attempt_credential_endpoint(
    attempt_id: str, body: CredentialVerifyRequest, session: Session = Depends(get_session),
) -> CredentialVerifyResponse:
    """Validate a presented scoped credential. `valid` is a boolean so the response leaks
    nothing about WHY it failed (no row / revoked / expired / wrong token / wrong role).
    The expiry is returned ONLY on success: on a failed validation it is None, so a caller
    probing with an invalid token learns nothing about the roll's lifetime (codex-audit
    finding 5). Per-role status details are available to evidence tooling via
    attempt_credential_status."""
    attempt = _parse_attempt_or_404(attempt_id)
    valid = verify_attempt_credential(session, attempt_id=attempt, actor_role=body.actor_role, token=body.token)
    if not valid:
        return CredentialVerifyResponse(valid=False, actor_role=body.actor_role, expires_at=None)
    status = attempt_credential_status(session, attempt_id=attempt, actor_role=body.actor_role)
    return CredentialVerifyResponse(
        valid=True, actor_role=body.actor_role,
        expires_at=None if status.expires_at is None else status.expires_at.isoformat(),
    )


class CandidateArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    candidate_id: str
    tree_digest: str
    manifest_digest: str
    stored_candidate_digest: str
    stored_candidate: dict


@router.get("/{attempt_id}/candidate", response_model=CandidateArtifactResponse)
def get_attempt_candidate_endpoint(
    attempt_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> CandidateArtifactResponse:
    """Credential-authorized, VERIFIER-ROLE capability (codex-audit finding 1/3): return the
    persisted candidate artifact for THIS attempt - including the FULL stored_candidate
    payload the verification phase actually consumes, not just digests - to a process holding
    a valid VERIFIER-role credential for exactly that attempt. Role-differentiated (second
    review round): a candidate-role credential is 403 here - the verifier is the reader of the
    persisted candidate, the candidate is its producer. This is the actual resource a scoped
    identity can reach: verification consumes this same capability through
    load_stored_candidate_authorized, and a stolen verifier secret is worthless outside its
    one attempt. 404 when no candidate is yet persisted; 401 when no valid credential
    accompanies the request; 403 when the credential is valid but not verifier-role."""
    attempt = _parse_attempt_or_404(attempt_id)
    _require_role(session, attempt_id=attempt, authorization=authorization, actor_role="verifier")
    loaded = load_stored_candidate(session, attempt_id=attempt)
    if loaded is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return CandidateArtifactResponse(
        attempt_id=str(attempt),
        candidate_id=str(loaded.candidate_id),
        tree_digest=loaded.tree_digest,
        manifest_digest=loaded.manifest_digest,
        stored_candidate_digest=loaded.stored_candidate_digest,
        stored_candidate=loaded.stored_candidate,
    )