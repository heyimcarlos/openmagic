"""Typed contracts for generic kernel Signal, guard, deferral, and closure transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus


@dataclass(frozen=True)
class AcceptSignal:
    signal_id: UUID
    instance_id: UUID
    wait_id: UUID
    signal_type: str
    schema_version: int
    payload: dict[str, Any]
    route_key: str


@dataclass(frozen=True)
class SignalReceipt:
    signal_id: UUID
    instance_id: UUID
    wait_id: UUID
    steps: dict[str, UUID]
    waits: dict[str, UUID]
    trace_event_id: UUID
    trace_sequence: int


@dataclass(frozen=True)
class CloseInstance:
    command_id: UUID
    instance_id: UUID


@dataclass(frozen=True)
class CloseInstanceReceipt:
    instance_id: UUID
    cancelled_step_ids: tuple[UUID, ...]
    cancelled_attempt_ids: tuple[UUID, ...]
    cancelled_wait_ids: tuple[UUID, ...]
    trace_event_id: UUID
    trace_sequence: int


@dataclass(frozen=True)
class GuardCurrentAttempt:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int


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


@dataclass(frozen=True)
class ResolveDeferredStep:
    source_id: UUID
    instance_id: UUID
    step_id: UUID
    basis_attempt_id: UUID
    action: Literal["retry", "succeed", "fail"]
    output: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResolveDeferredStepReceipt:
    step_id: UUID
    action: Literal["retry", "succeed", "fail"]


def deferred_action(value: object) -> Literal["retry", "succeed", "fail"]:
    if value == "retry":
        return "retry"
    if value == "succeed":
        return "succeed"
    if value == "fail":
        return "fail"
    raise RuntimeError("Deferred resolution receipt has an invalid action")


__all__ = [
    "AcceptSignal",
    "CloseInstance",
    "CloseInstanceReceipt",
    "CurrentAttemptGuard",
    "GuardCurrentAttempt",
    "ResolveDeferredStep",
    "ResolveDeferredStepReceipt",
    "SignalReceipt",
    "deferred_action",
]
