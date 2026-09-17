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
    """Sum the four per-role budget limits from the frozen resolved manifest.

    Returns a decimal string, or None if the manifest has no budget or ANY role
    limit is absent/unparseable - an incomplete budget is unknown, not a
    silently-smaller number.
    """
    if not isinstance(resolved, dict):
        return None
    budget = resolved.get("budget")
    if not isinstance(budget, dict):
        return None
    roles = budget.get("per_role_budget_usd")
    if not isinstance(roles, list) or not roles:
        return None
    total = Decimal("0")
    for role in roles:
        limit = role.get("limit_usd") if isinstance(role, dict) else None
        if limit is None:
            return None
        try:
            total += Decimal(str(limit))
        except (InvalidOperation, ValueError):
            return None
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

    row = session.execute(
        select(BudgetReservationRow).where(BudgetReservationRow.campaign_id == campaign_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = status
    if status == "released":
        row.released_at = datetime.now(timezone.utc)
    session.flush()
    return row


def reservation_summary(row: BudgetReservationRow | None) -> dict | None:
    if row is None:
        return None
    return {
        "reservation_id": row.reservation_id,
        "enforcement": row.enforcement,
        "reserved_usd": format(row.reserved_usd, "f") if row.reserved_usd is not None else None,
        "status": row.status,
    }
