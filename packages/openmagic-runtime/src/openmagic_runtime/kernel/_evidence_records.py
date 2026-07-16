"""Private persistence for runtime Instance evidence projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime.kernel._record_decoding import (
    agent_run_state,
    attempt_state,
    instance_state,
    step_state,
    wait_state,
)
from openmagic_runtime.kernel.inspection_types import (
    AgentRunState,
    AttemptState,
    InstanceState,
    StepState,
    WaitState,
)


@dataclass(frozen=True)
class RuntimeStepEvidence:
    step_id: UUID
    template_key: str
    state: StepState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeStepEvidence:
        return cls(
            step_id=UUID(str(record["step_id"])),
            template_key=str(record["template_key"]),
            state=step_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeWaitEvidence:
    wait_id: UUID
    template_key: str
    state: WaitState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeWaitEvidence:
        return cls(
            wait_id=UUID(str(record["wait_id"])),
            template_key=str(record["template_key"]),
            state=wait_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeAttemptEvidence:
    attempt_id: UUID
    state: AttemptState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAttemptEvidence:
        return cls(
            attempt_id=UUID(str(record["attempt_id"])),
            state=attempt_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeAgentRunEvidence:
    agent_run_id: UUID
    attempt_id: UUID
    state: AgentRunState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAgentRunEvidence:
        return cls(
            agent_run_id=UUID(str(record["agent_run_id"])),
            attempt_id=UUID(str(record["attempt_id"])),
            state=agent_run_state(record["status"]),
        )


@dataclass(frozen=True)
class RuntimeInstanceEvidence:
    state: InstanceState
    steps: tuple[RuntimeStepEvidence, ...]
    attempts: tuple[RuntimeAttemptEvidence, ...]
    agent_runs: tuple[RuntimeAgentRunEvidence, ...]
    waits: tuple[RuntimeWaitEvidence, ...]


def read_instance_evidence(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstanceEvidence:
    with connection.cursor(row_factory=dict_row) as cursor:
        instance = cursor.execute(
            "SELECT state FROM openmagic_runtime.instances WHERE instance_id = %s",
            (instance_id,),
        ).fetchone()
        if instance is None:
            raise KeyError(f"Runtime Instance not found: {instance_id}")
        steps = cursor.execute(
            "SELECT step_id, template_key, state FROM openmagic_runtime.steps "
            "WHERE instance_id = %s ORDER BY created_at, step_id",
            (instance_id,),
        ).fetchall()
        attempts = cursor.execute(
            "SELECT attempt_id, state FROM openmagic_runtime.attempts "
            "WHERE instance_id = %s ORDER BY created_at, attempt_id",
            (instance_id,),
        ).fetchall()
        waits = cursor.execute(
            "SELECT wait_id, template_key, state FROM openmagic_runtime.waits "
            "WHERE instance_id = %s ORDER BY created_at, wait_id",
            (instance_id,),
        ).fetchall()
        agent_runs = cursor.execute(
            "SELECT r.agent_run_id, r.attempt_id, r.status "
            "FROM openmagic_runtime.agent_runs AS r "
            "JOIN openmagic_runtime.attempts AS a ON a.attempt_id = r.attempt_id "
            "WHERE a.instance_id = %s ORDER BY r.created_at, r.agent_run_id",
            (instance_id,),
        ).fetchall()
    return RuntimeInstanceEvidence(
        state=instance_state(instance["state"]),
        steps=tuple(RuntimeStepEvidence.decode(record) for record in steps),
        attempts=tuple(RuntimeAttemptEvidence.decode(record) for record in attempts),
        agent_runs=tuple(RuntimeAgentRunEvidence.decode(record) for record in agent_runs),
        waits=tuple(RuntimeWaitEvidence.decode(record) for record in waits),
    )
