"""ENG-020 (spec section 48): operator-facing kill-switch control endpoints.

The kill switch is a GLOBAL, singleton flag (kill_switch.id=1). When active,
dispatch of any new work stops platform-wide and bounded teardown of active
work is requested through the existing cancellation machinery. It is
distinct from per-campaign auto-pause (`CampaignRow.auto_paused`), which is
scoped to one campaign and its own cause.

Activating/deactivating requires the `administrator` role (spec section 5/ENG-020).
The repository functions (`repository.activate_kill_switch`,
`deactivate_kill_switch`, `is_kill_switch_active`) hold the singleton-row
FOR UPDATE lock, so concurrent operator calls are correctly serialized by
PostgreSQL rather than by application-level timing.

Idempotency (spec section 33/API-01): mutating routes replay an
`Idempotency-Key` to defend against network-lost responses, exactly like
campaigns.py's state-transition routes. The idempotency body includes the
action and reason, so a key reused with a different reason is a 409.

Transaction atomicity (API-01): the repository state transition is staged
(non-committing) and committed BY `finalize()` together with the idempotency
record, so the kill-switch change and its replay handle can never be
separated by a crash between them.

Authorization honesty (matches scripts/fence_advance.py): there is NO
dedicated least-privilege operator database role or `current_user`
validation beyond `require_role("administrator")` on the control-plane route.
The route reports the resolved `current_user` so the identity that performed
the action is visible in the audit trail, and `activated_by_user_id` (from
the OIDC identity) is passed to the repository for the audit row - it is
authenticated attribution via the bearer token, recorded on the singleton.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Identity, require_role
from ..db import get_session
from ..errors import conflict, service_unavailable
from ..idempotency import check_or_reserve, finalize, principal_scope
from ..models import KillSwitchRow, User
from ..schemas import KillSwitchRequest, KillSwitchStatus
from ..worker import repository

router = APIRouter(prefix="/v1/kill-switch", tags=["kill_switch"])


def _current_user_id(session: Session, identity: Identity) -> uuid.UUID | None:
    return session.execute(
        select(User.id).where(User.oidc_issuer == identity.issuer, User.oidc_subject == identity.subject)
    ).scalar_one_or_none()


def _status_response(
    session: Session, *, campaigns_teardown_requested: int | None = None,
) -> KillSwitchStatus:
    row = session.get(KillSwitchRow, 1)
    if row is None:
        # This migration-seeded singleton is the platform-wide dispatch safety
        # control. A missing row is never evidence that dispatch is safe.
        raise service_unavailable("kill-switch control record is missing; run migrations and investigate before dispatching work")
    # `reason`/`activated_at` are audit fields on the singleton that persist
    # after deactivation (the deactivate function only clears `active`); they
    # are surfaced only while the switch is active so an inactive status
    # does not carry a stale activation reason.
    active = row.active
    return KillSwitchStatus(
        active=active,
        reason=row.reason if active else None,
        activated_at=row.activated_at.isoformat() if (active and row.activated_at is not None) else None,
        campaigns_teardown_requested=campaigns_teardown_requested,
    )


@router.get("", response_model=KillSwitchStatus)
def get_kill_switch_status(
    identity: Identity = Depends(require_role("operator", "reviewer", "administrator")),
    session: Session = Depends(get_session),
) -> KillSwitchStatus:
    """Check whether the platform-wide kill switch is active.

    Authenticated: any operator/reviewer/administrator may read the global
    dispatch-control state. The response is the singleton kill_switch row's
    current flags - a read, never a mutation."""
    return _status_response(session)


@router.post("/activate", response_model=KillSwitchStatus)
def activate_kill_switch(
    body: KillSwitchRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("administrator")),
    session: Session = Depends(get_session),
) -> KillSwitchStatus:
    """ENG-020 (spec sections 39/48): activate the global kill switch, stopping
    ALL new dispatch platform-wide and requesting bounded teardown of active
    work (cancelling every non-terminal campaign through the existing,
    already-tested cancellation machinery).

    Requires the `administrator` role and an `Idempotency-Key` header. The
    repository locks the singleton row FOR UPDATE, stages the state
    transition + campaign cancellations (non-committing), and `finalize()`
    commits them together with the idempotency record in one transaction -
    concurrent calls are serialized by PostgreSQL's row lock, and a crash
    between the state change and the idempotency record cannot leave the
    kill switch active without a replay handle."""
    request_body = body.model_dump(mode="json")
    scope = principal_scope("POST /v1/kill-switch/activate", str(_current_user_id(session, identity)))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return KillSwitchStatus.model_validate(cached)

    # commit=False stages the kill-switch mutation + campaign cancellations
    # without committing; finalize() below commits them atomically with the
    # idempotency record (API-01 transaction rule). The repository locks the
    # singleton row FOR UPDATE and raises ValueError if already active - the
    # conflict check is atomic, no separate unlocked pre-read.
    try:
        campaigns_teardown_requested = repository.activate_kill_switch(
            session,
            activated_by_user_id=_current_user_id(session, identity),
            reason=body.reason,
            commit=False,
        )
    except ValueError as exc:
        # A concurrent request with the same fresh key can pass the initial
        # read, then block on this row while the winner commits its state and
        # idempotency record. Re-check before reporting a real conflict.
        replay_after_race = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
        if replay_after_race is not None:
            return KillSwitchStatus.model_validate(replay_after_race)
        raise conflict(str(exc)) from exc
    except RuntimeError as exc:
        raise service_unavailable(str(exc)) from exc
    response = _status_response(session, campaigns_teardown_requested=campaigns_teardown_requested)
    replay = finalize(
        session,
        scope=scope,
        key=idempotency_key,
        body=request_body,
        status_code=200,
        response_body=response.model_dump(mode="json"),
    )
    return response if replay is None else KillSwitchStatus.model_validate(replay)


@router.post("/deactivate", response_model=KillSwitchStatus)
def deactivate_kill_switch(
    body: KillSwitchRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    identity: Identity = Depends(require_role("administrator")),
    session: Session = Depends(get_session),
) -> KillSwitchStatus:
    """ENG-020 (spec sections 39/48): deactivate the global kill switch, clearing
    the flag only.

    This does NOT resume any campaign the kill switch drove to
    `cancelling`/`cancelled`, and does NOT clear any campaign's own
    `auto_paused` flag - both are separate, deliberate operator decisions
    (see the provider-outage runbook in runbooks.md).

    Requires the `administrator` role and an `Idempotency-Key` header. The
    Idempotency-Key is REQUIRED for every mutation, including the idempotent
    no-op when the switch is already inactive, so that all mutate paths are
    replay-safe. The repository stages the deactivation (non-committing);
    `finalize()` commits it together with the idempotency record."""
    request_body = body.model_dump(mode="json") if body is not None else {}
    scope = principal_scope("POST /v1/kill-switch/deactivate", str(_current_user_id(session, identity)))
    cached = check_or_reserve(session, scope=scope, key=idempotency_key, body=request_body)
    if cached is not None:
        return KillSwitchStatus.model_validate(cached)

    # commit=False stages the deactivation (if active) without committing;
    # finalize() commits it atomically with the idempotency record. The
    # repository function locks FOR UPDATE and handles the already-inactive
    # case atomically (returns False for a no-op).
    try:
        repository.deactivate_kill_switch(session, commit=False)
    except RuntimeError as exc:
        raise service_unavailable(str(exc)) from exc
    response = _status_response(session)
    replay = finalize(
        session,
        scope=scope,
        key=idempotency_key,
        body=request_body,
        status_code=200,
        response_body=response.model_dump(mode="json"),
    )
    return response if replay is None else KillSwitchStatus.model_validate(replay)
