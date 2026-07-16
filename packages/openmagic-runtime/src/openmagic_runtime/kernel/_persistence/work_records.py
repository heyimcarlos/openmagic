"""Transaction-bound composition of cohesive Attempt persistence owners."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from openmagic_runtime.kernel._persistence.work_authority import AttemptAuthorityRecords
from openmagic_runtime.kernel._persistence.work_claims import AttemptClaimRecords
from openmagic_runtime.kernel._persistence.work_recovery import AttemptRecoveryRecords
from openmagic_runtime.kernel._persistence.work_results import AttemptResultRecords
from openmagic_runtime.kernel._work_contracts import (
    AttemptExecutionAuthority,
    ClaimedAttempt,
    ClaimWork,
    DispositionRequired,
    RenewedAttemptLease,
)


class KernelWorkTransaction:
    """One transaction facade over claim, authority, recovery, and result owners."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._claims = AttemptClaimRecords(connection)
        self._authority = AttemptAuthorityRecords(connection)
        self._recovery = AttemptRecoveryRecords(connection)
        self._results = AttemptResultRecords(connection)

    def claim(self, request: ClaimWork) -> ClaimedAttempt | None:
        return self._claims.claim(request)

    def execution_authority(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
    ) -> AttemptExecutionAuthority:
        return self._authority.execution_authority(attempt, worker_id=worker_id)

    def renew(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
        renewal_id: UUID,
    ) -> RenewedAttemptLease:
        return self._authority.renew(
            attempt,
            worker_id=worker_id,
            renewal_id=renewal_id,
        )

    def recover_expired(self, instance_id: UUID | None = None) -> DispositionRequired | None:
        return self._recovery.recover_expired(instance_id)

    def accept_result(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
        observation: dict[str, Any],
    ) -> DispositionRequired:
        return self._results.accept_result(
            attempt,
            worker_id=worker_id,
            observation=observation,
        )


def claim_once_record(*, database_url: str, request: ClaimWork) -> ClaimedAttempt | None:
    try:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return KernelWorkTransaction(connection).claim(request)
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == "one_leased_attempt_per_step":
            return None
        raise


def renew_once_record(
    *, database_url: str, attempt: ClaimedAttempt, worker_id: str, renewal_id: UUID
) -> RenewedAttemptLease:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return KernelWorkTransaction(connection).renew(
            attempt,
            worker_id=worker_id,
            renewal_id=renewal_id,
        )


__all__ = [
    "KernelWorkTransaction",
    "claim_once_record",
    "renew_once_record",
]
