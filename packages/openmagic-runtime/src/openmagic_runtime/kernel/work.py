"""Leased Attempt policy with persistence delegated to one private owner."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime.kernel._persistence.work_records import (
    KernelWorkTransaction,
    claim_once_record,
    renew_once_record,
)
from openmagic_runtime.kernel._work_contracts import (
    AttemptExecutionAuthority,
    AttemptResultConflict,
    ClaimedAttempt,
    ClaimWork,
    DispositionRequired,
    RenewedAttemptLease,
    StaleAuthority,
)


class KernelWork:
    """Public work policy interface backed by one transaction record owner."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._transaction = KernelWorkTransaction(connection)

    def claim(self, request: ClaimWork) -> ClaimedAttempt | None:
        return self._transaction.claim(request)

    def execution_authority(
        self, attempt: ClaimedAttempt, *, worker_id: str
    ) -> AttemptExecutionAuthority:
        return self._transaction.execution_authority(attempt, worker_id=worker_id)

    def renew(
        self, attempt: ClaimedAttempt, *, worker_id: str, renewal_id: UUID
    ) -> RenewedAttemptLease:
        return self._transaction.renew(attempt, worker_id=worker_id, renewal_id=renewal_id)

    def recover_expired(self, instance_id: UUID | None = None) -> DispositionRequired | None:
        return self._transaction.recover_expired(instance_id)

    def accept_result(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
        observation: dict[str, Any],
    ) -> DispositionRequired:
        return self._transaction.accept_result(
            attempt,
            worker_id=worker_id,
            observation=observation,
        )


def claim_once(*, database_url: str, request: ClaimWork) -> ClaimedAttempt | None:
    return claim_once_record(database_url=database_url, request=request)


def renew_once(
    *, database_url: str, attempt: ClaimedAttempt, worker_id: str, renewal_id: UUID
) -> RenewedAttemptLease:
    return renew_once_record(
        database_url=database_url,
        attempt=attempt,
        worker_id=worker_id,
        renewal_id=renewal_id,
    )


__all__ = [
    "AttemptExecutionAuthority",
    "AttemptResultConflict",
    "ClaimWork",
    "ClaimedAttempt",
    "DispositionRequired",
    "KernelWork",
    "RenewedAttemptLease",
    "StaleAuthority",
    "claim_once",
    "renew_once",
]
