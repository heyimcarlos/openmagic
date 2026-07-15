"""Example Insurance Workflow execution and durable Attempt observation control."""

from __future__ import annotations

from threading import Event
from typing import Any
from uuid import UUID, uuid4

import psycopg
from openmagic_runtime.agents import AgentRuns
from openmagic_runtime.commands import Actor, Cause, CommandDispatcher, CommandReceipt
from openmagic_runtime.execution import (
    AttemptExecution,
    CancellationToken,
    ExecutionAuthorityLost,
    Executor,
    execute_with_renewable_authority,
)
from openmagic_runtime.kernel.work import ClaimedAttempt, ClaimWork, claim_once, renew_once

from example_insurance.renewal_attempt_control import RenewalAttemptControl
from example_insurance.renewal_attempts import prepare_workflow_attempt
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


class WorkflowWorkerControl:
    def __init__(
        self,
        *,
        database_url: str,
        dispatcher: CommandDispatcher,
        executors: dict[str, Executor],
        attempts: RenewalAttemptControl,
        verification_attempts: VerificationAttemptControl | None = None,
    ) -> None:
        self._database_url = database_url
        self._dispatcher = dispatcher
        self._executors = executors
        self._attempts = attempts
        self._verification_attempts = verification_attempts

    def authorize_dispatch(
        self, *, attempt: ClaimedAttempt, worker_id: str
    ) -> CommandReceipt[ExternalEffectPermit]:
        return self._dispatcher.execute(
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
        return self._dispatcher.execute(
            command_type="renewal.accept_effect_observation",
            schema_version=1,
            command=command,
        )

    def run_once(
        self, *, worker_id: str, worker_shutdown: Event | None = None
    ) -> WorkflowAttemptResult | None:
        self.recover_expired()
        attempt = self.claim(worker_id=worker_id, claim_request_id=uuid4())
        if attempt is None:
            return None
        return self.complete(
            attempt=attempt,
            worker_id=worker_id,
            worker_shutdown=worker_shutdown,
        )

    def recover_expired(self) -> bool:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            if (
                self._verification_attempts is not None
                and self._verification_attempts.recover_expired(connection)
            ):
                return True
            return self._attempts.recover_expired(connection)

    def claim(self, *, worker_id: str, claim_request_id: UUID) -> ClaimedAttempt | None:
        return claim_once(
            database_url=self._database_url,
            request=ClaimWork(
                claim_request_id=claim_request_id,
                worker_id=worker_id,
                executor_keys=tuple(self._executors),
            ),
        )

    def complete(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        worker_shutdown: Event | None = None,
    ) -> WorkflowAttemptResult:
        prepared = prepare_workflow_attempt(
            database_url=self._database_url,
            attempt=attempt,
            worker_id=worker_id,
        )
        durable_attempt = prepared.claim
        if prepared.replay_observation is not None:
            return self._accept_replay(
                durable_attempt,
                worker_id,
                prepared.replay_observation,
            )
        execution_input = durable_attempt.input
        if durable_attempt.template_key == "send_renewal_email":
            permit = self.authorize_dispatch(attempt=durable_attempt, worker_id=worker_id)
            execution_input = committed_permit_execution_input(permit)
        executor = self._executors[durable_attempt.executor_key]
        try:
            observation = execute_with_renewable_authority(
                executor=executor,
                execution=AttemptExecution(
                    instance_id=durable_attempt.instance_id,
                    step_id=durable_attempt.step_id,
                    attempt_id=durable_attempt.attempt_id,
                    attempt_number=durable_attempt.attempt_number,
                    template_key=durable_attempt.template_key,
                    executor_key=durable_attempt.executor_key,
                    input=execution_input,
                    agent_input=prepared.agent_input,
                ),
                cancellation=CancellationToken(),
                renew=lambda: renew_once(
                    database_url=self._database_url,
                    attempt=durable_attempt,
                    worker_id=worker_id,
                    renewal_id=uuid4(),
                ),
                lease_seconds=durable_attempt.lease_seconds,
                worker_shutdown=worker_shutdown,
            )
        except ExecutionAuthorityLost:
            raise
        except Exception as error:
            self._record_agent_failure(durable_attempt, prepared.agent_run_id, error)
            raise
        return self._accept_replay(durable_attempt, worker_id, observation.value)

    def submit_observation(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            return self._attempts.accept_observation(
                connection,
                attempt=attempt,
                worker_id=worker_id,
                observation=observation,
            )

    def _accept_replay(
        self,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        if attempt.template_key == VerificationAttemptControl.template_key:
            if self._verification_attempts is None:
                raise RuntimeError("Verification delivery support is not configured")
            with psycopg.connect(self._database_url) as connection, connection.transaction():
                return self._verification_attempts.accept_observation(
                    connection,
                    attempt=attempt,
                    worker_id=worker_id,
                    observation=observation,
                )
        if attempt.template_key in {"send_renewal_email", "reconcile_renewal_email"}:
            return self._commit_effect_observation(attempt, worker_id, observation)
        return self.submit_observation(
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )

    def _commit_effect_observation(
        self,
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

    def _record_agent_failure(
        self,
        attempt: ClaimedAttempt,
        agent_run_id: UUID | None,
        error: Exception,
    ) -> None:
        if agent_run_id is None:
            return
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            AgentRuns(connection).fail_for_attempt(
                attempt.attempt_id,
                {"class": type(error).__name__},
            )


__all__ = ["WorkflowWorkerControl"]
