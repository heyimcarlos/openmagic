"""Application-owned durable renewal business facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row


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


@dataclass(frozen=True)
class DurableRenewalFacts:
    policy_number: str
    policyholder_name: str
    policyholder_email: str
    renewal_date: str
    expiring_premium_cents: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DurableRenewalFacts:
        renewal_date = record["renewal_date"]
        if not isinstance(renewal_date, date):
            raise RuntimeError("Renewal facts date has an invalid type")
        return cls(
            policy_number=str(record["policy_number"]),
            policyholder_name=str(record["policyholder_name"]),
            policyholder_email=str(record["policyholder_email"]),
            renewal_date=renewal_date.isoformat(),
            expiring_premium_cents=int(record["expiring_premium_cents"]),
        )

    def command_assertion(self) -> dict[str, Any]:
        return {
            "policy_number": self.policy_number,
            "policyholder_name": self.policyholder_name,
            "renewal_date": self.renewal_date,
            "expiring_premium_cents": self.expiring_premium_cents,
            "policyholder_email": self.policyholder_email,
        }


class RenewalFactSource:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def replace(self, facts: RenewalFacts) -> None:
        """Record the current authoritative business facts for one policy."""
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            self.replace_on(connection, facts)

    def replace_on(
        self,
        connection: Connection[tuple[Any, ...]],
        facts: RenewalFacts,
    ) -> None:
        """Replace changed facts inside the caller-owned transaction."""
        connection.execute(
            "INSERT INTO example_insurance.policy_renewal_facts "
            "(policy_id, policy_number, policyholder_name, renewal_date, "
            "expiring_premium_cents, revision, policyholder_email) "
            "VALUES (%s, %s, %s, %s, %s, 1, %s) "
            "ON CONFLICT (policy_id) DO UPDATE SET "
            "policy_number = EXCLUDED.policy_number, "
            "policyholder_name = EXCLUDED.policyholder_name, "
            "renewal_date = EXCLUDED.renewal_date, "
            "expiring_premium_cents = EXCLUDED.expiring_premium_cents, "
            "policyholder_email = EXCLUDED.policyholder_email, "
            "revision = example_insurance.policy_renewal_facts.revision + 1, "
            "updated_at = clock_timestamp() "
            "WHERE (example_insurance.policy_renewal_facts.policy_number, "
            "example_insurance.policy_renewal_facts.policyholder_name, "
            "example_insurance.policy_renewal_facts.renewal_date, "
            "example_insurance.policy_renewal_facts.expiring_premium_cents, "
            "example_insurance.policy_renewal_facts.policyholder_email) IS DISTINCT FROM "
            "(EXCLUDED.policy_number, EXCLUDED.policyholder_name, EXCLUDED.renewal_date, "
            "EXCLUDED.expiring_premium_cents, EXCLUDED.policyholder_email)",
            (
                facts.policy_id,
                facts.policy_number,
                facts.policyholder_name,
                facts.renewal_date,
                facts.expiring_premium_cents,
                facts.policyholder_email,
            ),
        )

    def gather(self, assertion: dict[str, Any]) -> dict[str, Any]:
        policy_id = UUID(str(assertion["policy_id"]))
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            with connection.cursor(row_factory=dict_row) as cursor:
                record = cursor.execute(
                    "SELECT policy_number, policyholder_name, renewal_date, "
                    "expiring_premium_cents, policyholder_email "
                    "FROM example_insurance.policy_renewal_facts "
                    "WHERE policy_id = %s",
                    (policy_id,),
                ).fetchone()
        if record is None:
            raise StaleRenewalFacts("Renewal business facts are unavailable")
        durable = DurableRenewalFacts.decode(record).command_assertion()
        asserted = {key: assertion[key] for key in durable}
        if asserted != durable:
            raise StaleRenewalFacts("Renewal business facts changed after the Command assertion")
        return durable


__all__ = [
    "DurableRenewalFacts",
    "RenewalFactSource",
    "RenewalFacts",
    "StaleRenewalFacts",
]
