"""Campaign budget reservation (ENG-017).

Honest model: there is no provider reservation integration yet (ENG-008
established hard-cost enforcement is unavailable without provider reservations),
so a hosted reservation records the campaign's declared per-role budget as an
ESTIMATED_TIME_LIMITED intent - it does not place a real hard hold on spend.
The enforcement level is stored explicitly so nothing implies a hard cap.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import BudgetReservationRow, CampaignRow


def reserved_amount_from_resolved(resolved: dict | None) -> str | None:
    """Sum the campaign's declared upper-bound reservation across the FULL
    planned matrix (architecture section 19's reservation formula):

        planned trials x (role caps + environment upper bound)
            x (1 + max_replacements)

    The declared per-role budget is a per-trial ceiling: a campaign with N
    planned trials can lawfully run up to (1 + protocol.max_replacements)
    attempts per trial, and every attempt may bill its role limit, so both
    the role sum AND the declared environment upper bound multiply by the
    planned trial count and the replacement factor. Understating the
    reservation would make it smaller than the work the operator approved.

    The environment upper bound comes from the frozen `aieb.budget/v2`
    manifest's `environment_upper_bound_usd` (`aieb.budget/v1` has no such
    field). A missing or unparseable component - trials, budget, role limits,
    or the environment bound - makes the reservation UNKNOWN (None), never a
    silently-smaller number: a reservation that omits the environment term is
    not the declared complete upper bound.
    """
    if not isinstance(resolved, dict):
        return None
    budget = resolved.get("budget")
    trials = resolved.get("trials")
    if not isinstance(budget, dict) or not isinstance(trials, list) or not trials:
        return None
    roles = budget.get("per_role_budget_usd")
    if not isinstance(roles, list) or not roles:
        return None
    role_total = Decimal("0")
    for role in roles:
        limit = role.get("limit_usd") if isinstance(role, dict) else None
        if limit is None:
            return None
        try:
            role_total += Decimal(str(limit))
        except (InvalidOperation, ValueError):
            return None
    environment = budget.get("environment_upper_bound_usd")
    if not isinstance(environment, str):
        return None
    try:
        environment_total = Decimal(environment)
    except (InvalidOperation, ValueError):
        return None
    if not environment_total.is_finite() or environment_total < 0:
        return None
    protocol = resolved.get("protocol")
    replacements = protocol.get("max_replacements") if isinstance(protocol, dict) else None
    if not isinstance(replacements, int) or replacements < 0:
        return None
    total = (role_total + environment_total) * len(trials) * (1 + replacements)
    return format(total, "f")


def reserve_campaign_budget(session: Session, campaign: CampaignRow) -> BudgetReservationRow:
    """Create (idempotently) the campaign's budget reservation and stamp
    `campaign.reservation_id`. Returns the existing reservation if one is
    already recorded for this campaign."""
    existing = session.execute(
        select(BudgetReservationRow).where(BudgetReservationRow.campaign_id == campaign.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    reservation_id = f"resv-{uuid.uuid4().hex}"
    row = BudgetReservationRow(
        campaign_id=campaign.id,
        reservation_id=reservation_id,
        enforcement="estimated_time_limited",
        reserved_usd=reserved_amount_from_resolved(campaign.resolved),
        status="active",
    )
    session.add(row)
    session.execute(update(CampaignRow).where(CampaignRow.id == campaign.id).values(reservation_id=reservation_id))
    session.flush()
    return row


def set_reservation_status(session: Session, campaign_id: uuid.UUID, status: str) -> BudgetReservationRow | None:
    """Move a campaign's reservation to `released` (cancel) or `consumed`
    (completion). No-op if the campaign has no reservation."""
    from datetime import datetime, timezone

    if status not in ("released", "consumed"):
        raise ValueError("reservation settlement must be released or consumed")
    values = {"status": status}
    if status == "released":
        values["released_at"] = datetime.now(timezone.utc)
    session.execute(
        update(BudgetReservationRow)
        .where(BudgetReservationRow.campaign_id == campaign_id, BudgetReservationRow.status == "active")
        .values(**values)
    )
    return session.execute(
        select(BudgetReservationRow).where(BudgetReservationRow.campaign_id == campaign_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def reservation_summary(row: BudgetReservationRow | None) -> dict | None:
    if row is None:
        return None
    return {
        "reservation_id": row.reservation_id,
        "enforcement": row.enforcement,
        "reserved_usd": format(row.reserved_usd, "f") if row.reserved_usd is not None else None,
        "status": row.status,
    }
