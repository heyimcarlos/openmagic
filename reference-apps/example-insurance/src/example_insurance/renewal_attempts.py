"""Durable preparation of renewal Workflow Attempt execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentExecutionInput,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentRuns,
    AgentTask,
)
from openmagic_runtime.kernel.work import ClaimedAttempt, KernelWork
from openmagic_runtime.threads import ThreadAccess


@dataclass(frozen=True)
class PreparedWorkflowAttempt:
    claim: ClaimedAttempt
    replay_observation: dict[str, Any] | None
    agent_run_id: UUID | None
    agent_input: AgentExecutionInput | None


def prepare_workflow_attempt(
    *,
    database_url: str,
    attempt: ClaimedAttempt,
    worker_id: str,
) -> PreparedWorkflowAttempt:
    with psycopg.connect(database_url) as connection, connection.transaction():
        authority = KernelWork(connection).execution_authority(
            attempt,
            worker_id=worker_id,
        )
        durable_attempt = authority.claim
        if authority.directive == "replay":
            if authority.accepted_observation is None:
                raise RuntimeError("Completed Attempt has no accepted observation")
            return PreparedWorkflowAttempt(
                claim=durable_attempt,
                replay_observation=authority.accepted_observation,
                agent_run_id=None,
                agent_input=None,
            )
        if durable_attempt.executor_key != "example_insurance.renewal_draft_agent.v1":
            return PreparedWorkflowAttempt(durable_attempt, None, None, None)
        agent_runs = AgentRuns(connection)
        if agent_runs.find_by_attempt(durable_attempt.attempt_id) is not None:
            raise RuntimeError("Agent Attempt already has a durable Agent Run")
        thread_id = UUID(durable_attempt.input["thread_id"])
        cutoff = ThreadAccess(connection).context_cutoff(thread_id)
        agent_run = agent_runs.start(
            attempt_id=durable_attempt.attempt_id,
            input=AgentRunInput(
                configuration=AgentConfiguration(
                    agent_key="example_insurance.renewal_draft",
                    agent_version=1,
                    instruction_key="example_insurance.renewal_draft.en_ca.v1",
                ),
                task=AgentTask(
                    task_type="renewal.draft",
                    task_version=1,
                    input=AgentRecord(
                        schema_key="example_insurance.renewal_draft.input",
                        schema_version=1,
                        fields=tuple(
                            AgentField(name=key, value=value)
                            for key, value in sorted(durable_attempt.input.items())
                        ),
                    ),
                ),
                thread_id=thread_id,
                context_through_sequence=cutoff,
                domain_event_context=(),
                audience_context=AgentAudience(
                    kind="workflow_role",
                    identifier="broker",
                ),
                locale="en-CA",
            ),
        )
        return PreparedWorkflowAttempt(
            claim=durable_attempt,
            replay_observation=None,
            agent_run_id=agent_run.agent_run_id,
            agent_input=agent_runs.execution_input_for_attempt(durable_attempt.attempt_id),
        )


__all__ = ["PreparedWorkflowAttempt", "prepare_workflow_attempt"]
