"""Atomic provisioning and Command submission for renewal entry points."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from openmagic_runtime.commands import CommandReceipt, StateConflict
from openmagic_runtime.threads import CreateThread, ThreadAccess

from example_insurance.renewal_commands import (
    StartRenewalOutreach,
    StartRenewalOutreachResult,
)
from example_insurance.renewal_facts import RenewalFacts, RenewalFactSource
from example_insurance.renewals import ExampleInsurance


@dataclass(frozen=True)
class RenewalSubmission:
    """One internally consistent Thread, fact, and Command provision."""

    thread: CreateThread
    facts: RenewalFacts
    command: StartRenewalOutreach

    def __post_init__(self) -> None:
        value = self.command.input
        if self.thread.thread_id != value.thread_id:
            raise ValueError("Renewal submission Thread does not match the Command")
        asserted_facts = RenewalFacts(
            policy_id=value.policy_id,
            policy_number=value.policy_number,
            policyholder_name=value.policyholder_name,
            policyholder_email=value.policyholder_email,
            renewal_date=value.renewal_date,
            expiring_premium_cents=value.expiring_premium_cents,
        )
        if self.facts != asserted_facts:
            raise ValueError("Renewal submission facts do not match the Command")


class RenewalSubmissionApplication:
    """Application entry point that commits provisioning with Command dispatch."""

    def __init__(self, *, database_url: str) -> None:
        self._application = ExampleInsurance(database_url=database_url)
        self._submission_database_url = database_url
        self._submission_facts = RenewalFactSource(database_url=database_url)

    def prepare(self) -> None:
        self._application.prepare()

    def provision_and_start_renewal(
        self,
        submission: RenewalSubmission,
    ) -> CommandReceipt[StartRenewalOutreachResult]:
        """Provision and dispatch atomically, including idempotent replay."""
        with (
            psycopg.connect(self._submission_database_url) as connection,
            connection.transaction(),
        ):
            try:
                ThreadAccess(connection).provision(submission.thread)
            except ValueError as error:
                raise StateConflict(str(error)) from error
            self._submission_facts.replace_on(connection, submission.facts)
            return self._application.start_renewal_outreach_on(
                connection,
                submission.command,
            )


__all__ = [
    "RenewalSubmission",
    "RenewalSubmissionApplication",
]
