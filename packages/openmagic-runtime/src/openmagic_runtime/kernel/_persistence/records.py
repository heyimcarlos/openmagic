"""Private typed transaction-scoped reads over kernel persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime.kernel._record_decoding import (
    decode_runtime_attempt,
    decode_runtime_instance,
    decode_runtime_step,
    decode_runtime_wait,
)
from openmagic_runtime.kernel.inspection_types import (
    ActivatedOccurrences,
    InstanceState,
    RuntimeAttempt,
    RuntimeInstance,
    RuntimeStep,
    RuntimeWait,
    StepState,
    WaitState,
)


def read_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstance | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT instance_id, state FROM openmagic_runtime.instances WHERE instance_id = %s",
            (instance_id,),
        ).fetchone()
    return decode_runtime_instance(record) if record is not None else None


def lock_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstance | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT instance_id, state FROM openmagic_runtime.instances "
            "WHERE instance_id = %s FOR UPDATE",
            (instance_id,),
        ).fetchone()
    return decode_runtime_instance(record) if record is not None else None


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
    return tuple(decode_runtime_wait(record) for record in records)


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
    return decode_runtime_wait(record) if record is not None else None


def read_step(connection: Connection[tuple[Any, ...]], step_id: UUID) -> RuntimeStep | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT step_id, instance_id, template_key, state, input, "
            "output_digest IS NOT NULL AS output_recorded "
            "FROM openmagic_runtime.steps WHERE step_id = %s",
            (step_id,),
        ).fetchone()
    return decode_runtime_step(record) if record is not None else None


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
    return tuple(decode_runtime_step(record) for record in records)


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
    return decode_runtime_attempt(record) if record is not None else None


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
