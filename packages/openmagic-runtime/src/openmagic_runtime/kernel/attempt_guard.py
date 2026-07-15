"""Transaction-scoped current Attempt authority guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus

from openmagic_runtime.kernel._control_support import lock_open_instance
from openmagic_runtime.kernel.transitions import GuardCurrentAttempt


@dataclass(frozen=True)
class AttemptAuthorityRecord:
    step_id: UUID
    attempt_number: int
    leased: bool
    lease_valid: bool
    deadline_valid: bool
    step_pending: bool

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> AttemptAuthorityRecord:
        return cls(
            step_id=UUID(str(row[0])),
            attempt_number=int(row[1]),
            leased=bool(row[2]),
            lease_valid=bool(row[3]),
            deadline_valid=bool(row[4]),
            step_pending=bool(row[5]),
        )

    def authorizes(self, request: GuardCurrentAttempt) -> bool:
        return (
            self.step_id == request.step_id
            and self.attempt_number == request.attempt_number
            and self.leased
            and self.lease_valid
            and self.deadline_valid
            and self.step_pending
        )


class CurrentAttemptGuard:
    __slots__ = ("_connection", "_transaction_id", "attempt_id")

    def __init__(
        self,
        connection: Connection[tuple[Any, ...]],
        attempt_id: UUID,
        transaction_id: int,
    ) -> None:
        self._connection = connection
        self.attempt_id = attempt_id
        self._transaction_id = transaction_id

    def require_usable(self) -> None:
        if (
            self._connection.closed
            or self._connection.info.transaction_status is TransactionStatus.IDLE
        ):
            raise RuntimeError("Current Attempt guard is no longer transaction-scoped")
        current = self._connection.execute("SELECT txid_current()").fetchone()
        if current is None or int(current[0]) != self._transaction_id:
            raise RuntimeError("Current Attempt guard belongs to an earlier transaction")

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("Current Attempt guards cannot be serialized")


def guard_current_attempt(
    connection: Connection[tuple[Any, ...]], request: GuardCurrentAttempt
) -> CurrentAttemptGuard:
    lock_open_instance(connection, request.instance_id)
    row = connection.execute(
        "SELECT a.step_id, a.attempt_number, a.state = 'leased', "
        "a.lease_expires_at > clock_timestamp(), a.hard_deadline > clock_timestamp(), "
        "s.state = 'pending' FROM openmagic_runtime.attempts AS a "
        "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
        "WHERE a.attempt_id = %s AND a.instance_id = %s FOR UPDATE OF a, s",
        (request.attempt_id, request.instance_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Current Attempt guard target does not exist")
    current = AttemptAuthorityRecord.from_row(row)
    if not current.authorizes(request):
        raise RuntimeError("Attempt is not current")
    transaction = connection.execute("SELECT txid_current()").fetchone()
    if transaction is None:
        raise RuntimeError("Current transaction identity is unavailable")
    return CurrentAttemptGuard(connection, request.attempt_id, int(transaction[0]))


__all__ = ["AttemptAuthorityRecord", "CurrentAttemptGuard", "guard_current_attempt"]
