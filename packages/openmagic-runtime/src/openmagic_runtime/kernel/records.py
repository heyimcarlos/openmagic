"""Typed transaction-scoped reads over private kernel persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

InstanceState = Literal["open", "closed"]
StepState = Literal["pending", "succeeded", "failed", "cancelled"]
WaitState = Literal["unsatisfied", "satisfied", "cancelled"]
AttemptState = Literal["leased", "completed", "abandoned", "cancelled"]
AgentRunState = Literal["running", "completed", "failed", "abandoned"]


def instance_state(value: object) -> InstanceState:
    if value == "open":
        return "open"
    if value == "closed":
        return "closed"
    raise RuntimeError("Instance has an invalid state")


def step_state(value: object) -> StepState:
    if value == "pending":
        return "pending"
    if value == "succeeded":
        return "succeeded"
    if value == "failed":
        return "failed"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Step has an invalid state")


def wait_state(value: object) -> WaitState:
    if value == "unsatisfied":
        return "unsatisfied"
    if value == "satisfied":
        return "satisfied"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Wait has an invalid state")


def attempt_state(value: object) -> AttemptState:
    if value == "leased":
        return "leased"
    if value == "completed":
        return "completed"
    if value == "abandoned":
        return "abandoned"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Attempt has an invalid state")


def agent_run_state(value: object) -> AgentRunState:
    if value == "running":
        return "running"
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    if value == "abandoned":
        return "abandoned"
    raise RuntimeError("Agent Run has an invalid state")


@dataclass(frozen=True)
class RuntimeInstance:
    instance_id: UUID
    state: InstanceState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeInstance:
        return cls(
            instance_id=UUID(str(record["instance_id"])),
            state=instance_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeWait:
    wait_id: UUID
    instance_id: UUID
    template_key: str
    state: WaitState
    input: dict[str, Any]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeWait:
        return cls(
            wait_id=UUID(str(record["wait_id"])),
            instance_id=UUID(str(record["instance_id"])),
            template_key=str(record["template_key"]),
            state=wait_state(record["state"]),
            input=dict(record["input"]),
        )


@dataclass(frozen=True)
class RuntimeStep:
    step_id: UUID
    instance_id: UUID
    template_key: str
    state: StepState
    input: dict[str, Any]
    output_recorded: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeStep:
        return cls(
            step_id=UUID(str(record["step_id"])),
            instance_id=UUID(str(record["instance_id"])),
            template_key=str(record["template_key"]),
            state=step_state(record["state"]),
            input=dict(record["input"]),
            output_recorded=bool(record["output_recorded"]),
        )


@dataclass(frozen=True)
class RuntimeAttempt:
    attempt_id: UUID
    instance_id: UUID
    step_id: UUID
    attempt_number: int
    worker_id: str
    template_key: str
    step_input: dict[str, Any]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAttempt:
        return cls(
            attempt_id=UUID(str(record["attempt_id"])),
            instance_id=UUID(str(record["instance_id"])),
            step_id=UUID(str(record["step_id"])),
            attempt_number=int(record["attempt_number"]),
            worker_id=str(record["worker_id"]),
            template_key=str(record["template_key"]),
            step_input=dict(record["step_input"]),
        )


@dataclass(frozen=True)
class ActivatedOccurrences:
    steps: dict[str, UUID]
    waits: dict[str, UUID]


def read_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstance | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT instance_id, state FROM openmagic_runtime.instances WHERE instance_id = %s",
            (instance_id,),
        ).fetchone()
    return RuntimeInstance.decode(record) if record is not None else None


def lock_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstance | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT instance_id, state FROM openmagic_runtime.instances "
            "WHERE instance_id = %s FOR UPDATE",
            (instance_id,),
        ).fetchone()
    return RuntimeInstance.decode(record) if record is not None else None


def waits_for_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> tuple[RuntimeWait, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT wait_id, instance_id, template_key, state, input "
            "FROM openmagic_runtime.waits "
            "WHERE instance_id = %s ORDER BY created_at, wait_id",
            (instance_id,),
        ).fetchall()
    return tuple(RuntimeWait.decode(record) for record in records)


def lock_wait(
    connection: Connection[tuple[Any, ...]], *, instance_id: UUID, wait_id: UUID
) -> RuntimeWait | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT wait_id, instance_id, template_key, state, input "
            "FROM openmagic_runtime.waits "
            "WHERE wait_id = %s AND instance_id = %s FOR UPDATE",
            (wait_id, instance_id),
        ).fetchone()
    return RuntimeWait.decode(record) if record is not None else None


def read_step(connection: Connection[tuple[Any, ...]], step_id: UUID) -> RuntimeStep | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT step_id, instance_id, template_key, state, input, "
            "output_digest IS NOT NULL AS output_recorded "
            "FROM openmagic_runtime.steps WHERE step_id = %s",
            (step_id,),
        ).fetchone()
    return RuntimeStep.decode(record) if record is not None else None


def steps_for_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> tuple[RuntimeStep, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT step_id, instance_id, template_key, state, input, "
            "output_digest IS NOT NULL AS output_recorded "
            "FROM openmagic_runtime.steps "
            "WHERE instance_id = %s ORDER BY created_at, step_id",
            (instance_id,),
        ).fetchall()
    return tuple(RuntimeStep.decode(record) for record in records)


def read_attempt(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> RuntimeAttempt | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT a.attempt_id, a.instance_id, a.step_id, a.attempt_number, "
            "a.worker_id, s.template_key, s.input AS step_input "
            "FROM openmagic_runtime.attempts a "
            "JOIN openmagic_runtime.steps s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s",
            (attempt_id,),
        ).fetchone()
    return RuntimeAttempt.decode(record) if record is not None else None


def activated_by_attempt(
    connection: Connection[tuple[Any, ...]],
    *,
    instance_id: UUID,
    attempt_id: UUID,
) -> ActivatedOccurrences:
    with connection.cursor(row_factory=dict_row) as cursor:
        step_records = cursor.execute(
            "SELECT output_slot, step_id FROM openmagic_runtime.steps "
            "WHERE instance_id = %s AND activation_source_kind = 'step' "
            "AND activation_source_id = %s",
            (instance_id, attempt_id),
        ).fetchall()
        wait_records = cursor.execute(
            "SELECT output_slot, wait_id FROM openmagic_runtime.waits "
            "WHERE instance_id = %s AND activation_source_kind = 'step' "
            "AND activation_source_id = %s",
            (instance_id, attempt_id),
        ).fetchall()
    return ActivatedOccurrences(
        steps={str(record["output_slot"]): UUID(str(record["step_id"])) for record in step_records},
        waits={str(record["output_slot"]): UUID(str(record["wait_id"])) for record in wait_records},
    )


def expired_attempt_instances(
    connection: Connection[tuple[Any, ...]],
) -> tuple[UUID, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT DISTINCT instance_id FROM openmagic_runtime.attempts "
            "WHERE state = 'leased' AND (lease_expires_at <= clock_timestamp() "
            "OR hard_deadline <= clock_timestamp())"
        ).fetchall()
    return tuple(UUID(str(record["instance_id"])) for record in records)


__all__ = [
    "ActivatedOccurrences",
    "InstanceState",
    "RuntimeAttempt",
    "RuntimeInstance",
    "RuntimeStep",
    "RuntimeWait",
    "StepState",
    "WaitState",
    "activated_by_attempt",
    "expired_attempt_instances",
    "lock_instance",
    "lock_wait",
    "read_attempt",
    "read_instance",
    "read_step",
    "steps_for_instance",
    "waits_for_instance",
]
