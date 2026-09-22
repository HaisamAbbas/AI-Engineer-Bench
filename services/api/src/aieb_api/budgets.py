"""Campaign budget reservation (ENG-017).

Honest model: there is no provider reservation integration yet (ENG-008
established hard-cost enforcement is unavailable without provider reservations),
so a hosted reservation records the campaign's declared per-role budget as an
ESTIMATED_TIME_LIMITED intent - it does not place a real hard hold on spend.
The enforcement level is stored explicitly so nothing implies a hard cap.

V2-GAP-004 section 5 additionally requires: the reservation FORMULA and the
frozen budget-profile digest are persisted with the reservation, unknown or
understated bounds refuse the start (a reservation that silently omits a
component is worse than no reservation), and an authorized cap - when one is
configured via ``AIEB_BUDGET_CAP_USD`` - is enforced atomically at start.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .evidence_integrity import evidence_digest
from .models import BudgetReservationRow, CampaignRow


class BudgetError(ValueError):
    """Start-time budget refusal (routes map this to a 409 conflict)."""


class UnknownBudgetBounds(BudgetError):
    """A reservation component (trials, role caps, environment bound, or the
    replacement factor) is unknown, so the true worst case is unknown."""


class BudgetCapExceeded(BudgetError):
    """The worst-case reservation exceeds the authorized cap."""


def _bounds(resolved: dict | None) -> tuple[Decimal, Decimal, int, int, list[dict]] | None:
    """(role_total, environment_total, trial_count, max_replacements,
    per-role component list), or None when any component is unknown."""
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
    components = []
    for role in roles:
        limit = role.get("limit_usd") if isinstance(role, dict) else None
        if limit is None:
            return None
        try:
            amount = Decimal(str(limit))
        except (InvalidOperation, ValueError):
            return None
        role_total += amount
        components.append({"role": role.get("role"), "limit_usd": format(amount, "f")})
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
    return role_total, environment_total, len(trials), replacements, components


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
    bounds = _bounds(resolved)
    if bounds is None:
        return None
    role_total, environment_total, trials, replacements, _components = bounds
    total = (role_total + environment_total) * trials * (1 + replacements)
    return format(total, "f")


def per_cell_allocation_from_resolved(resolved: dict | None) -> str | None:
    """Plan section 3: the worst-case budget allocation persisted on EACH
    materialized cell - the same formula without the trial-count term:

        (role caps + environment upper bound) x (1 + max_replacements)
    """
    bounds = _bounds(resolved)
    if bounds is None:
        return None
    role_total, environment_total, _trials, replacements, _components = bounds
    return format((role_total + environment_total) * (1 + replacements), "f")


def reservation_formula_from_resolved(resolved: dict | None) -> dict | None:
    """Plan section 5: the persisted derivation of the reservation - every
    component value plus the expression itself, so an auditor can recompute
    the total from the frozen manifest without re-deriving the formula."""
    bounds = _bounds(resolved)
    if bounds is None:
        return None
    role_total, environment_total, trials, replacements, components = bounds
    return {
        "schema_version": "aieb.reservation-formula/v1",
        "expression": "planned_trials x (role_caps_total + environment_upper_bound) x (1 + max_replacements)",
        "role_budgets": components,
        "role_caps_total_usd": format(role_total, "f"),
        "environment_upper_bound_usd": format(environment_total, "f"),
        "planned_trials": trials,
        "max_replacements": replacements,
        "reserved_usd": format((role_total + environment_total) * trials * (1 + replacements), "f"),
    }


def budget_profile_digest_from_resolved(resolved: dict | None) -> str | None:
    """Digest of the exact frozen budget profile the reservation derives from."""
    if not isinstance(resolved, dict) or not isinstance(resolved.get("budget"), dict):
        return None
    return evidence_digest({"schema_version": "aieb.budget-profile-binding/v1", "budget": resolved["budget"]})


def authorized_cap_from_env() -> Decimal | None:
    """The operator-configured authorized reservation cap (plan section 5).

    Unset means no cap is configured (documented, not silently assumed to be
    infinity). An unparseable or negative cap FAILS CLOSED: an enforcement
    value the operator cannot rely on must refuse the start, never disable
    the check quietly."""
    raw = os.environ.get("AIEB_BUDGET_CAP_USD")
    if raw is None or not raw.strip():
        return None
    try:
        cap = Decimal(raw.strip())
    except (InvalidOperation, ValueError) as exc:
        raise BudgetError(f"AIEB_BUDGET_CAP_USD is not a valid USD amount: {raw!r}") from exc
    if not cap.is_finite() or cap < 0:
        raise BudgetError(f"AIEB_BUDGET_CAP_USD must be a finite non-negative amount, got {raw!r}")
    return cap


def reserve_campaign_budget(session: Session, campaign: CampaignRow) -> BudgetReservationRow:
    """Create (idempotently) the campaign's budget reservation and stamp
    `campaign.reservation_id`. Returns the existing reservation if one is
    already recorded for this campaign.

    Refuses to reserve when the worst case is UNKNOWN (any bound missing) or
    exceeds the authorized cap (``AIEB_BUDGET_CAP_USD``): starting work whose
    true upper bound is unknown, or above the cap the operator authorized,
    is exactly the understated-reservation failure section 5 forbids."""
    existing = session.execute(
        select(BudgetReservationRow).where(BudgetReservationRow.campaign_id == campaign.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    reserved = reserved_amount_from_resolved(campaign.resolved)
    if reserved is None:
        raise UnknownBudgetBounds(
            "cannot start: the frozen campaign's worst-case reservation is unknown - a role cap, the "
            "environment upper bound, the trial count, or the replacement factor is missing or "
            "unparseable in the frozen budget/protocol manifest. Freeze with complete aieb.budget/v2 "
            "bounds instead of starting an unpriced campaign."
        )
    cap = authorized_cap_from_env()
    if cap is not None and Decimal(reserved) > cap:
        raise BudgetCapExceeded(
            f"cannot start: worst-case reservation {reserved} USD exceeds the authorized cap "
            f"{format(cap, 'f')} USD"
        )
    reservation_id = f"resv-{uuid.uuid4().hex}"
    row = BudgetReservationRow(
        campaign_id=campaign.id,
        reservation_id=reservation_id,
        enforcement="estimated_time_limited",
        reserved_usd=reserved,
        status="active",
        reservation_formula=reservation_formula_from_resolved(campaign.resolved),
        budget_profile_digest=budget_profile_digest_from_resolved(campaign.resolved),
        authorized_cap_usd=format(cap, "f") if cap is not None else None,
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
        # V2-GAP-004 section 5 provenance: how the number was derived, which
        # frozen budget profile it came from, and the cap it was checked against.
        "reservation_formula": row.reservation_formula,
        "budget_profile_digest": row.budget_profile_digest,
        "authorized_cap_usd": format(row.authorized_cap_usd, "f") if row.authorized_cap_usd is not None else None,
    }
