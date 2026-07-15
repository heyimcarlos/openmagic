"""Typed application routes for Workflow Attempt execution and recovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from openmagic_runtime.commands import Actor, Cause, CommandDispatcher, CommandReceipt
from openmagic_runtime.kernel.work import ClaimedAttempt
from psycopg import Connection

from example_insurance.renewal_attempt_control import RenewalAttemptControl
from example_insurance.renewal_commands import (
    AcceptRenewalEffectObservation,
    AcceptRenewalEffectObservationInput,
    AuthorizeRenewalEmailDispatch,
    AuthorizeRenewalEmailDispatchInput,
    RenewalEffectObservation,
    WorkflowAttemptResult,
    dispatch_command_id,
    effect_observation_command_id,
)
from example_insurance.renewal_effect_types import ExternalEffectPermit
from example_insurance.renewal_effects import committed_permit_execution_input
from example_insurance.verification_attempt_control import VerificationAttemptControl


class AttemptRoute(Protocol):
    @property
    def template_keys(self) -> tuple[str, ...]: ...

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]: ...

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult: ...


class AttemptRecovery(Protocol):
    def recover_expired(self) -> bool: ...


@dataclass(frozen=True)
class OrdinaryRenewalRoute:
    database_url: str
    attempts: RenewalAttemptControl
    template_keys: tuple[str, ...] = ("gather_renewal_facts", "draft_renewal_email")

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        del attempt, worker_id
        return default

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            return self.attempts.accept_observation(
                connection,
                attempt=attempt,
                worker_id=worker_id,
                observation=observation,
            )


@dataclass(frozen=True)
class RenewalEffectRoute:
    dispatcher: CommandDispatcher
    template_keys: tuple[str, ...] = ("send_renewal_email",)

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        del default
        return committed_permit_execution_input(
            self.authorize_dispatch(attempt=attempt, worker_id=worker_id)
        )

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        value = RenewalEffectObservation(
            classification=observation["classification"],
            provider_request_id=str(observation["provider_request_id"]),
        )
        return self.accept_effect_observation(
            AcceptRenewalEffectObservation(
                command_id=effect_observation_command_id(attempt.attempt_id),
                actor=Actor("system", worker_id),
                cause=Cause("attempt", str(attempt.attempt_id)),
                input=AcceptRenewalEffectObservationInput(attempt, worker_id, value),
            )
        ).result

    def authorize_dispatch(
        self, *, attempt: ClaimedAttempt, worker_id: str
    ) -> CommandReceipt[ExternalEffectPermit]:
        return self.dispatcher.execute(
            command_type="renewal.authorize_email_dispatch",
            schema_version=1,
            command=AuthorizeRenewalEmailDispatch(
                command_id=dispatch_command_id(attempt.attempt_id),
                actor=Actor("system", worker_id),
                cause=Cause("attempt", str(attempt.attempt_id)),
                input=AuthorizeRenewalEmailDispatchInput(attempt, worker_id),
            ),
        )

    def accept_effect_observation(
        self, command: AcceptRenewalEffectObservation
    ) -> CommandReceipt[WorkflowAttemptResult]:
        return self.dispatcher.execute(
            command_type="renewal.accept_effect_observation",
            schema_version=1,
            command=command,
        )


@dataclass(frozen=True)
class RenewalReconciliationRoute:
    effects: RenewalEffectRoute
    template_keys: tuple[str, ...] = ("reconcile_renewal_email",)

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        del attempt, worker_id
        return default

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        return self.effects.accept(
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )


@dataclass(frozen=True)
class VerificationRoute:
    database_url: str
    attempts: VerificationAttemptControl
    template_keys: tuple[str, ...] = ("deliver_verification_challenge",)

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        del attempt, worker_id
        return default

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            return self.attempts.accept_observation(
                connection,
                attempt=attempt,
                worker_id=worker_id,
                observation=observation,
            )


@dataclass(frozen=True)
class TransactionalRecovery:
    database_url: str
    recover: Callable[[Connection[tuple[Any, ...]]], bool]

    def recover_expired(self) -> bool:
        with psycopg.connect(self.database_url) as connection, connection.transaction():
            return self.recover(connection)


class AttemptObservationDispatcher:
    def __init__(
        self,
        *,
        routes: Iterable[AttemptRoute],
        recoveries: Iterable[AttemptRecovery],
        effects: RenewalEffectRoute,
        ordinary: OrdinaryRenewalRoute,
    ) -> None:
        self._routes: dict[str, AttemptRoute] = {}
        for route in routes:
            for template_key in route.template_keys:
                if template_key in self._routes:
                    raise ValueError(f"Duplicate Attempt route: {template_key}")
                self._routes[template_key] = route
        self._recoveries = tuple(recoveries)
        self._effects = effects
        self._ordinary = ordinary

    def execution_input(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        return self._route(attempt).execution_input(
            attempt=attempt,
            worker_id=worker_id,
            default=default,
        )

    def accept(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        return self._route(attempt).accept(
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )

    def recover_expired(self) -> bool:
        return any(recovery.recover_expired() for recovery in self._recoveries)

    def authorize_dispatch(
        self, *, attempt: ClaimedAttempt, worker_id: str
    ) -> CommandReceipt[ExternalEffectPermit]:
        return self._effects.authorize_dispatch(attempt=attempt, worker_id=worker_id)

    def accept_effect_observation(
        self, command: AcceptRenewalEffectObservation
    ) -> CommandReceipt[WorkflowAttemptResult]:
        return self._effects.accept_effect_observation(command)

    def submit_ordinary_observation(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        return self._ordinary.accept(
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )

    def _route(self, attempt: ClaimedAttempt) -> AttemptRoute:
        try:
            return self._routes[attempt.template_key]
        except KeyError as error:
            raise RuntimeError(f"Unsupported Attempt route: {attempt.template_key}") from error


__all__ = [
    "AttemptObservationDispatcher",
    "AttemptRecovery",
    "AttemptRoute",
    "OrdinaryRenewalRoute",
    "RenewalEffectRoute",
    "RenewalReconciliationRoute",
    "TransactionalRecovery",
    "VerificationRoute",
]
