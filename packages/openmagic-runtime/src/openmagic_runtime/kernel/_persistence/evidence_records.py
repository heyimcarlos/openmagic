"""Private persistence for runtime Instance evidence projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._persistence.durable_values import (
    boolean_value,
    integer_value,
    nonempty_string,
    timestamp_value,
    uuid_value,
)
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
            step_id=uuid_value(record["step_id"]),
            template_key=nonempty_string(record["template_key"]),
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
            wait_id=uuid_value(record["wait_id"]),
            template_key=nonempty_string(record["template_key"]),
            state=wait_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeAttemptEvidence:
    attempt_id: UUID
    worker_id: str
    state: AttemptState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAttemptEvidence:
        return cls(
            attempt_id=uuid_value(record["attempt_id"]),
            worker_id=nonempty_string(record["worker_id"]),
            state=attempt_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeAttemptLeaseEvidence:
    attempt_id: UUID
    checked_at: datetime
    lease_expires_at: datetime
    lease_valid: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAttemptLeaseEvidence:
        return cls(
            attempt_id=uuid_value(record["attempt_id"]),
            checked_at=timestamp_value(record["checked_at"]),
            lease_expires_at=timestamp_value(record["lease_expires_at"]),
            lease_valid=boolean_value(record["lease_valid"]),
        )


@dataclass(frozen=True)
class RuntimeAgentRunEvidence:
    agent_run_id: UUID
    attempt_id: UUID
    state: AgentRunState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAgentRunEvidence:
        return cls(
            agent_run_id=uuid_value(record["agent_run_id"]),
            attempt_id=uuid_value(record["attempt_id"]),
            state=agent_run_state(record["status"]),
        )


@dataclass(frozen=True)
class RuntimeInstanceEvidence:
    instance_id: UUID
    definition_key: str
    definition_version: int
    state: InstanceState
    steps: tuple[RuntimeStepEvidence, ...]
    attempts: tuple[RuntimeAttemptEvidence, ...]
    agent_runs: tuple[RuntimeAgentRunEvidence, ...]
    waits: tuple[RuntimeWaitEvidence, ...]
    trace_event_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if self.definition_version <= 0:
            raise ValueError("Runtime Instance Definition version must be positive")
        identity_groups = (
            tuple(item.step_id for item in self.steps),
            tuple(item.attempt_id for item in self.attempts),
            tuple(item.agent_run_id for item in self.agent_runs),
            tuple(item.wait_id for item in self.waits),
            self.trace_event_ids,
        )
        if any(len(values) != len(set(values)) for values in identity_groups):
            raise ValueError("Runtime Instance evidence contains duplicate durable identities")
        attempt_ids = {item.attempt_id for item in self.attempts}
        if any(item.attempt_id not in attempt_ids for item in self.agent_runs):
            raise ValueError("Runtime Agent Run evidence references an unrelated Attempt")


def read_instance_evidence(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstanceEvidence:
    with connection.cursor(row_factory=dict_row) as cursor:
        instance = cursor.execute(
            "SELECT instance_id, definition_key, definition_version, state "
            "FROM openmagic_runtime.instances WHERE instance_id = %s",
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
            "SELECT attempt_id, worker_id, state FROM openmagic_runtime.attempts "
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
        trace_events = cursor.execute(
            "SELECT trace_event_id FROM openmagic_runtime.trace_events "
            "WHERE instance_id = %s ORDER BY sequence, trace_event_id",
            (instance_id,),
        ).fetchall()
    return RuntimeInstanceEvidence(
        instance_id=uuid_value(instance["instance_id"]),
        definition_key=nonempty_string(instance["definition_key"]),
        definition_version=integer_value(instance["definition_version"]),
        state=instance_state(instance["state"]),
        steps=tuple(RuntimeStepEvidence.decode(record) for record in steps),
        attempts=tuple(RuntimeAttemptEvidence.decode(record) for record in attempts),
        agent_runs=tuple(RuntimeAgentRunEvidence.decode(record) for record in agent_runs),
        waits=tuple(RuntimeWaitEvidence.decode(record) for record in waits),
        trace_event_ids=tuple(uuid_value(record["trace_event_id"]) for record in trace_events),
    )


def read_attempt_lease_evidence(
    connection: Connection[tuple[Any, ...]],
    attempt_id: UUID,
) -> RuntimeAttemptLeaseEvidence:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "WITH observed AS MATERIALIZED (SELECT clock_timestamp() AS checked_at) "
            "SELECT attempt_id, observed.checked_at, lease_expires_at, "
            "lease_expires_at > observed.checked_at AS lease_valid "
            "FROM openmagic_runtime.attempts CROSS JOIN observed WHERE attempt_id = %s",
            (attempt_id,),
        ).fetchone()
    if record is None:
        raise KeyError(f"Runtime Attempt not found: {attempt_id}")
    return RuntimeAttemptLeaseEvidence.decode(record)
