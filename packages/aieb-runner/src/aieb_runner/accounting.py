"""Role-separated local usage ledger with conservative unknown accounting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from aieb_core.models import BudgetRole


class BudgetEnforcement(StrEnum):
    HARD = "hard"
    ESTIMATED_TIME_LIMITED = "estimated_time_limited"


@dataclass(frozen=True)
class UsageReceipt:
    attempt_id: str
    role: BudgetRole
    request_id: str
    physical_attempt: int
    source: str  # broker or adapter
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: str | None = None
    billing_uncertain: bool = False

    @property
    def identity(self) -> tuple[str, BudgetRole, str, int]:
        return (self.attempt_id, self.role, self.request_id, self.physical_attempt)


@dataclass(frozen=True)
class UsageSummary:
    role: BudgetRole
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: str | None
    billing_uncertain: bool


class UsageLedger:
    """Merge broker/adapter observations without double-charging one request."""

    def __init__(self) -> None:
        self._receipts: dict[tuple[str, BudgetRole, str, int], UsageReceipt] = {}

    def record(self, receipt: UsageReceipt) -> bool:
        """Store or reconcile a receipt. Returns true only for a new request."""
        if receipt.source not in {"broker", "adapter"}:
            raise ValueError("usage source must be broker or adapter")
        if receipt.physical_attempt < 0 or not receipt.request_id:
            raise ValueError("usage receipt requires request ID and physical attempt")
        existing = self._receipts.get(receipt.identity)
        if existing is None:
            self._receipts[receipt.identity] = receipt
            return True
        # Broker data fills/overrides adapter estimates for the same physical
        # request. Neither source adds a second charge.
        preferred, other = (receipt, existing) if receipt.source == "broker" else (existing, receipt)
        self._receipts[receipt.identity] = UsageReceipt(
            attempt_id=preferred.attempt_id,
            role=preferred.role,
            request_id=preferred.request_id,
            physical_attempt=preferred.physical_attempt,
            source=preferred.source,
            input_tokens=preferred.input_tokens if preferred.input_tokens is not None else other.input_tokens,
            output_tokens=preferred.output_tokens if preferred.output_tokens is not None else other.output_tokens,
            cost_usd=preferred.cost_usd if preferred.cost_usd is not None else other.cost_usd,
            billing_uncertain=preferred.billing_uncertain or other.billing_uncertain,
        )
        return False

    def lost_response(self, *, attempt_id: str, role: BudgetRole, request_id: str, physical_attempt: int) -> None:
        self.record(UsageReceipt(attempt_id, role, request_id, physical_attempt, "adapter", billing_uncertain=True))

    def receipts(self) -> tuple[UsageReceipt, ...]:
        return tuple(self._receipts[key] for key in sorted(self._receipts, key=lambda key: tuple(map(str, key))))

    def summary(self, role: BudgetRole) -> UsageSummary:
        selected = [receipt for receipt in self._receipts.values() if receipt.role == role]
        uncertain = any(receipt.billing_uncertain for receipt in selected)
        costs = [receipt.cost_usd for receipt in selected]
        inputs = [receipt.input_tokens for receipt in selected]
        outputs = [receipt.output_tokens for receipt in selected]
        return UsageSummary(
            role,
            None if any(value is None for value in inputs) else sum(value for value in inputs if value is not None),
            None if any(value is None for value in outputs) else sum(value for value in outputs if value is not None),
            None if uncertain or any(value is None for value in costs) else format(sum(Decimal(value) for value in costs if value is not None), "f"),
            uncertain,
        )


@dataclass(frozen=True)
class BudgetReservation:
    enforcement: BudgetEnforcement
    reserved_usd: str | None
    reason: str


def reserve_cost(*, hard_cost_requested: bool, provider_supports_reservation: bool, max_cost_usd: str | None) -> BudgetReservation:
    if hard_cost_requested and provider_supports_reservation and max_cost_usd is not None:
        if Decimal(max_cost_usd) < 0:
            raise ValueError("cost reservation cannot be negative")
        return BudgetReservation(BudgetEnforcement.HARD, max_cost_usd, "provider reservation acquired")
    return BudgetReservation(
        BudgetEnforcement.ESTIMATED_TIME_LIMITED,
        None,
        "provider cannot conservatively reserve hard cost; time-limited estimate only",
    )
