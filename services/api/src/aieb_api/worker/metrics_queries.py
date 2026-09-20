"""Read-only queries computing gauge metrics fresh from the database at scrape time
(ENG-020 gap 6, spec section 39).

These back the gauges that MUST reflect true current database state rather than
in-process history (`worker/metrics.py`'s module docstring explains why): a freshly
started API process must report the kill switch's real state, every non-terminal
campaign's real consecutive-infrastructure-failure count, every currently-leased work
item's real heartbeat age, and every active budget reservation's real age, without
needing any prior event replayed into it. `routes/metrics.py` calls these at every
`GET /metrics` request and loads the results into `worker/metrics.py`'s gauge registry
via `replace_gauge_family()` immediately before rendering.

No write ever happens here - every function takes a `Session` and only ever executes
SELECTs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BudgetReservationRow, CampaignRow, WorkItemRow
from . import repository

# Non-terminal campaign states (spec section 39): a campaign that has reached one of
# the CampaignRow.state check constraint's terminal values no longer needs its
# consecutive-infrastructure-failure count surfaced as an active operational signal.
_NON_TERMINAL_CAMPAIGN_STATES = ("draft", "frozen", "running", "paused", "cancelling")


def kill_switch_active(session: Session) -> int:
    """1 if the global kill switch is active, 0 otherwise - reuses
    `repository.is_kill_switch_active`'s fail-closed behavior (a missing singleton row
    is treated as active) so the metric can never under-report an unsafe state."""
    return 1 if repository.is_kill_switch_active(session) else 0


def campaign_consecutive_infrastructure_failures(session: Session) -> list[tuple[dict[str, str], float]]:
    """One label-series per non-terminal campaign: `{campaign_id=...}` ->
    its current `consecutive_infrastructure_failures` counter value."""
    rows = session.execute(
        select(CampaignRow.id, CampaignRow.consecutive_infrastructure_failures).where(
            CampaignRow.state.in_(_NON_TERMINAL_CAMPAIGN_STATES)
        )
    ).all()
    return [({"campaign_id": str(campaign_id)}, float(count)) for campaign_id, count in rows]


def worker_heartbeat_ages(session: Session, *, now: datetime | None = None) -> list[tuple[dict[str, str], float]]:
    """One label-series per currently-leased work item: `{work_item_id, worker_id,
    work_type}` -> seconds since its last heartbeat.

    `work_item` stores no separate "last heartbeat" timestamp - only `lease_expiry`,
    which every heartbeat (and the initial claim) sets to `now + lease_seconds`
    (`repository.DEFAULT_LEASE_SECONDS` everywhere in this codebase; no caller
    overrides it with a different value in production paths). The last heartbeat time
    is therefore reconstructed as `lease_expiry - DEFAULT_LEASE_SECONDS`, and age is
    `now - that`. This is an approximation that is exact whenever heartbeats use the
    default lease duration (true today), and is documented here rather than silently
    assumed - it is not a real recorded heartbeat timestamp."""
    current_time = now if now is not None else datetime.now(timezone.utc)
    rows = session.execute(
        select(WorkItemRow.id, WorkItemRow.worker_id, WorkItemRow.type, WorkItemRow.lease_expiry).where(
            WorkItemRow.state == "leased", WorkItemRow.worker_id.isnot(None), WorkItemRow.lease_expiry.isnot(None)
        )
    ).all()
    series = []
    for work_item_id, worker_id, work_type, lease_expiry in rows:
        last_heartbeat_at = lease_expiry - timedelta(seconds=repository.DEFAULT_LEASE_SECONDS)
        age_seconds = max(0.0, (current_time - last_heartbeat_at).total_seconds())
        series.append(
            (
                {"work_item_id": str(work_item_id), "worker_id": str(worker_id), "work_type": str(work_type)},
                age_seconds,
            )
        )
    return series


def active_budget_reservation_ages(session: Session, *, now: datetime | None = None) -> list[tuple[dict[str, str], float]]:
    """One label-series per ACTIVE budget reservation: `{campaign_id, status="active"}`
    -> seconds since it was created. Only `status='active'` rows are surfaced - the
    alert rule (`UnresolvedBudgetReservationAge`) filters on that same label, and a
    released/consumed reservation's age is no longer an operational signal."""
    current_time = now if now is not None else datetime.now(timezone.utc)
    rows = session.execute(
        select(BudgetReservationRow.campaign_id, BudgetReservationRow.created_at).where(
            BudgetReservationRow.status == "active"
        )
    ).all()
    return [
        ({"campaign_id": str(campaign_id), "status": "active"}, max(0.0, (current_time - created_at).total_seconds()))
        for campaign_id, created_at in rows
    ]
