"""Domain values for authoritative renewal business facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID


class StaleRenewalFacts(RuntimeError):
    """Raised when a Workflow assertion conflicts with current business state."""


@dataclass(frozen=True)
class RenewalFacts:
    policy_id: UUID
    policy_number: str
    policyholder_name: str
    policyholder_email: str
    renewal_date: str
    expiring_premium_cents: int

    def __post_init__(self) -> None:
        if not self.policy_number or not self.policyholder_name or not self.policyholder_email:
            raise ValueError("Renewal fact identity and policyholder must be non-empty")
        date.fromisoformat(self.renewal_date)
        if self.expiring_premium_cents <= 0:
            raise ValueError("Renewal premium must be positive")


__all__ = ["RenewalFacts", "StaleRenewalFacts"]
