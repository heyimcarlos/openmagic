"""Leased Attempt policy with persistence delegated to one private owner."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime.kernel._persistence.work_authority import (
    AttemptAuthorityRecords,
    renew_once_record,
)
from openmagic_runtime.kernel._persistence.work_claims import (
    AttemptClaimRecords,
    claim_once_record,
)
from openmagic_runtime.kernel._persistence.work_recovery import AttemptRecoveryRecords
from openmagic_runtime.kernel._persistence.work_results import AttemptResultRecords
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
    """Public work policy over cohesive transaction-bound persistence owners."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._claims = AttemptClaimRecords(connection)
        self._authority = AttemptAuthorityRecords(connection)
        self._recovery = AttemptRecoveryRecords(connection)
        self._results = AttemptResultRecords(connection)

    def claim(self, request: ClaimWork) -> ClaimedAttempt | None:
        return self._claims.claim(request)

    def execution_authority(
        self, attempt: ClaimedAttempt, *, worker_id: str
    ) -> AttemptExecutionAuthority:
        return self._authority.execution_authority(attempt, worker_id=worker_id)

    def renew(
        self, attempt: ClaimedAttempt, *, worker_id: str, renewal_id: UUID
    ) -> RenewedAttemptLease:
        return self._authority.renew(attempt, worker_id=worker_id, renewal_id=renewal_id)

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
