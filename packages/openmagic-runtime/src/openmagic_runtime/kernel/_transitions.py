"""Private typed contracts for generic kernel transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True)
class AcceptSignal:
    signal_id: UUID
    instance_id: UUID
    wait_id: UUID
    signal_type: str
    schema_version: int
    payload: dict[str, Any]
    route_key: str


class SignalConflictReason(StrEnum):
    INSTANCE_NOT_FOUND = "instance_not_found"
    WAIT_NOT_FOUND = "wait_not_found"
    WAIT_ALREADY_SATISFIED = "wait_already_satisfied"
    IDENTITY_REUSED = "identity_reused"


_SIGNAL_CONFLICT_MESSAGES = {
    SignalConflictReason.INSTANCE_NOT_FOUND: "Signal target Instance does not exist",
    SignalConflictReason.WAIT_NOT_FOUND: "Signal target Wait does not exist",
    SignalConflictReason.WAIT_ALREADY_SATISFIED: ("Signal target Wait is no longer unsatisfied"),
    SignalConflictReason.IDENTITY_REUSED: ("Signal identity was reused with conflicting input"),
}


class SignalConflict(RuntimeError):
    """A typed rejection caused by durable Signal state or identity."""

    def __init__(self, reason: SignalConflictReason) -> None:
        self.reason = reason
        super().__init__(_SIGNAL_CONFLICT_MESSAGES[reason])


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
    "GuardCurrentAttempt",
    "ResolveDeferredStep",
    "ResolveDeferredStepReceipt",
    "SignalConflict",
    "SignalConflictReason",
    "SignalReceipt",
    "deferred_action",
]
