"""Example Insurance Workflow execution and durable Attempt observation control."""

from __future__ import annotations

from threading import Event
from typing import Any
from uuid import UUID, uuid4

import psycopg
from openmagic_runtime.agents import AgentRuns
from openmagic_runtime.execution import (
    AttemptExecution,
    CancellationToken,
    ExecutionAuthorityLost,
    Executor,
    execute_with_renewable_authority,
)
from openmagic_runtime.kernel.work import ClaimedAttempt, ClaimWork, claim_once, renew_once

from example_insurance.renewal_attempts import prepare_workflow_attempt
from example_insurance.renewal_commands import (
    WorkflowAttemptResult,
)
from example_insurance.workflow_attempt_dispatch import AttemptObservationDispatcher


class WorkflowWorkerControl:
    def __init__(
        self,
        *,
        database_url: str,
        executors: dict[str, Executor],
        attempts: AttemptObservationDispatcher,
    ) -> None:
        self._database_url = database_url
        self._executors = executors
        self._attempts = attempts

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
        return self._attempts.recover_expired()

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
        execution_input = self._attempts.execution_input(
            attempt=durable_attempt,
            worker_id=worker_id,
            default=durable_attempt.input,
        )
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

    def _accept_replay(
        self,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        return self._attempts.accept(
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )

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
