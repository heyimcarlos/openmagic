"""Transaction-scoped kernel control policy over private persistence records."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime.kernel._control_contracts import StartInstance, StartInstanceReceipt
from openmagic_runtime.kernel._persistence.attempt_guard import CurrentAttemptGuard
from openmagic_runtime.kernel._persistence.control_records import (
    KernelControlTransaction,
    start_instance_record,
)
from openmagic_runtime.kernel._transitions import (
    AcceptSignal,
    CloseInstance,
    CloseInstanceReceipt,
    GuardCurrentAttempt,
    ResolveDeferredStep,
    ResolveDeferredStepReceipt,
    SignalConflict,
    SignalConflictReason,
    SignalReceipt,
)
from openmagic_runtime.kernel._work_contracts import DispositionRequired


class KernelControl:
    """Public transition interface whose atomic records have one private owner."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._transaction = KernelControlTransaction(connection)

    def start(self, request: StartInstance) -> StartInstanceReceipt:
        return self._transaction.start(request)

    def succeed(
        self,
        required: DispositionRequired,
        *,
        output: dict[str, Any],
        outcome_route: str | None = None,
        route_input: dict[str, Any] | None = None,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        return self._transaction.succeed(
            required,
            output=output,
            outcome_route=outcome_route,
            route_input=route_input,
        )

    def retry(self, required: DispositionRequired) -> None:
        self._transaction.retry(required)

    def fail(self, required: DispositionRequired, *, failure: dict[str, Any]) -> None:
        self._transaction.fail(required, failure=failure)

    def accept_signal(self, request: AcceptSignal) -> SignalReceipt:
        return self._transaction.accept_signal(request)

    def guard_current_attempt(self, request: GuardCurrentAttempt) -> CurrentAttemptGuard:
        return self._transaction.guard_current_attempt(request)

    def defer(
        self,
        required: DispositionRequired,
        *,
        outcome_route: str | None = None,
        route_input: dict[str, Any] | None = None,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        return self._transaction.defer(
            required,
            outcome_route=outcome_route,
            route_input=route_input,
        )

    def resolve_deferred(self, request: ResolveDeferredStep) -> ResolveDeferredStepReceipt:
        return self._transaction.resolve_deferred(request)

    def close(self, request: CloseInstance) -> CloseInstanceReceipt:
        return self._transaction.close(request)


def start_instance(*, database_url: str, request: StartInstance) -> StartInstanceReceipt:
    return start_instance_record(database_url=database_url, request=request)


__all__ = [
    "AcceptSignal",
    "CloseInstance",
    "CloseInstanceReceipt",
    "CurrentAttemptGuard",
    "GuardCurrentAttempt",
    "KernelControl",
    "ResolveDeferredStep",
    "ResolveDeferredStepReceipt",
    "SignalConflict",
    "SignalConflictReason",
    "SignalReceipt",
    "StartInstance",
    "StartInstanceReceipt",
    "start_instance",
]
