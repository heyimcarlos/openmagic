"""Application-owned durable renewal business facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg


class StaleRenewalFacts(RuntimeError):
    """Raised when a Workflow assertion conflicts with current business state."""


@dataclass(frozen=True)
class RenewalFacts:
    policy_id: UUID
    policy_number: str
    policyholder_name: str
    renewal_date: str
    expiring_premium_cents: int

    def __post_init__(self) -> None:
        if not self.policy_number or not self.policyholder_name:
            raise ValueError("Renewal fact identity and policyholder must be non-empty")
        date.fromisoformat(self.renewal_date)
        if self.expiring_premium_cents <= 0:
            raise ValueError("Renewal premium must be positive")


class RenewalFactSource:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def replace(self, facts: RenewalFacts) -> None:
        """Record the current authoritative business facts for one policy."""
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute(
                "INSERT INTO example_insurance.policy_renewal_facts "
                "(policy_id, policy_number, policyholder_name, renewal_date, "
                "expiring_premium_cents, revision) VALUES (%s, %s, %s, %s, %s, 1) "
                "ON CONFLICT (policy_id) DO UPDATE SET "
                "policy_number = EXCLUDED.policy_number, "
                "policyholder_name = EXCLUDED.policyholder_name, "
                "renewal_date = EXCLUDED.renewal_date, "
                "expiring_premium_cents = EXCLUDED.expiring_premium_cents, "
                "revision = example_insurance.policy_renewal_facts.revision + 1, "
                "updated_at = clock_timestamp()",
                (
                    facts.policy_id,
                    facts.policy_number,
                    facts.policyholder_name,
                    facts.renewal_date,
                    facts.expiring_premium_cents,
                ),
            )

    def gather(self, assertion: dict[str, Any]) -> dict[str, Any]:
        policy_id = UUID(str(assertion["policy_id"]))
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT policy_number, policyholder_name, renewal_date, "
                "expiring_premium_cents FROM example_insurance.policy_renewal_facts "
                "WHERE policy_id = %s",
                (policy_id,),
            ).fetchone()
        if row is None:
            raise StaleRenewalFacts("Renewal business facts are unavailable")
        durable = {
            "policy_number": str(row[0]),
            "policyholder_name": str(row[1]),
            "renewal_date": row[2].isoformat(),
            "expiring_premium_cents": int(row[3]),
        }
        asserted = {key: assertion[key] for key in durable}
        if asserted != durable:
            raise StaleRenewalFacts("Renewal business facts changed after the Command assertion")
        return durable


__all__ = ["RenewalFactSource", "RenewalFacts", "StaleRenewalFacts"]
