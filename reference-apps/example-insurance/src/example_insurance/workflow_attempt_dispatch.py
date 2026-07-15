"""Typed callback routing for application Workflow Attempts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from openmagic_runtime.kernel.work import ClaimedAttempt
from psycopg import Connection

from example_insurance.renewal_commands import WorkflowAttemptResult

AttemptInputOverride = Callable[[ClaimedAttempt, str, dict[str, Any]], dict[str, Any]]
AttemptAcceptor = Callable[[ClaimedAttempt, str, dict[str, Any]], WorkflowAttemptResult]
AttemptRecovery = Callable[[], bool]


class TransactionalObservation(Protocol):
    def __call__(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult: ...


@dataclass(frozen=True)
class AttemptHandler:
    accept: AttemptAcceptor
    input_override: AttemptInputOverride | None = None


class AttemptObservationDispatcher:
    def __init__(
        self,
        *,
        handlers: Mapping[str, AttemptHandler],
        recoveries: Iterable[AttemptRecovery],
    ) -> None:
        if not handlers:
            raise ValueError("Attempt routing requires at least one handler")
        self._handlers = dict(handlers)
        self._recoveries = tuple(recoveries)

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        override = self._handler(attempt).input_override
        return default if override is None else override(attempt, worker_id, default)

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        return self._handler(attempt).accept(attempt, worker_id, observation)

    def recover_expired(self) -> bool:
        return any(recover() for recover in self._recoveries)

    def _handler(self, attempt: ClaimedAttempt) -> AttemptHandler:
        try:
            return self._handlers[attempt.template_key]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Attempt route: {attempt.template_key}") from error


def transactional_acceptor(
    database_url: str,
    accept: TransactionalObservation,
) -> AttemptAcceptor:
    def routed(
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return accept(
                connection,
                attempt=attempt,
                worker_id=worker_id,
                observation=observation,
            )

    return routed


def transactional_recovery(
    database_url: str,
    recover: Callable[[Connection[tuple[Any, ...]]], bool],
) -> AttemptRecovery:
    def routed() -> bool:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return recover(connection)

    return routed


__all__ = [
    "AttemptAcceptor",
    "AttemptHandler",
    "AttemptInputOverride",
    "AttemptObservationDispatcher",
    "AttemptRecovery",
    "transactional_acceptor",
    "transactional_recovery",
]
