"""Canonical guarded Step outcome mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest


@dataclass(frozen=True)
class CurrentStep:
    instance_id: UUID
    step_id: UUID


@dataclass(frozen=True)
class DeferredStep:
    instance_id: UUID
    step_id: UUID
    basis_attempt_id: UUID


StepMutationTarget = CurrentStep | DeferredStep


def _guard(target: StepMutationTarget) -> tuple[sql.SQL, tuple[UUID, ...]]:
    if isinstance(target, DeferredStep):
        return sql.SQL("AND deferred_attempt_id = %s"), (target.basis_attempt_id,)
    return sql.SQL("AND deferred_attempt_id IS NULL"), ()


def succeed_step(
    connection: Connection[tuple[Any, ...]],
    target: StepMutationTarget,
    *,
    output: dict[str, Any],
) -> bool:
    guard_sql, guard_parameters = _guard(target)
    row = connection.execute(
        sql.SQL(
            "UPDATE openmagic_runtime.steps SET state = 'succeeded', output = %s, "
            "output_digest = %s, terminal_at = clock_timestamp(), claimable_at = NULL, "
            "deferred_attempt_id = NULL WHERE step_id = %s AND instance_id = %s "
            "AND state = 'pending' {} RETURNING step_id"
        ).format(guard_sql),
        (
            Jsonb(output),
            canonical_digest(output),
            target.step_id,
            target.instance_id,
            *guard_parameters,
        ),
    ).fetchone()
    return row is not None


def retry_step(
    connection: Connection[tuple[Any, ...]],
    target: StepMutationTarget,
    *,
    delay_seconds: int,
) -> bool:
    guard_sql, guard_parameters = _guard(target)
    row = connection.execute(
        sql.SQL(
            "UPDATE openmagic_runtime.steps SET claimable_at = "
            "clock_timestamp() + (%s * interval '1 second'), deferred_attempt_id = NULL "
            "WHERE step_id = %s AND instance_id = %s AND state = 'pending' "
            "{} RETURNING step_id"
        ).format(guard_sql),
        (
            delay_seconds,
            target.step_id,
            target.instance_id,
            *guard_parameters,
        ),
    ).fetchone()
    return row is not None


def fail_step(
    connection: Connection[tuple[Any, ...]],
    target: StepMutationTarget,
    *,
    failure: dict[str, Any],
) -> bool:
    guard_sql, guard_parameters = _guard(target)
    row = connection.execute(
        sql.SQL(
            "UPDATE openmagic_runtime.steps SET state = 'failed', failure = %s, "
            "failure_digest = %s, terminal_at = clock_timestamp(), claimable_at = NULL, "
            "deferred_attempt_id = NULL WHERE step_id = %s AND instance_id = %s "
            "AND state = 'pending' {} RETURNING step_id"
        ).format(guard_sql),
        (
            Jsonb(failure),
            canonical_digest(failure),
            target.step_id,
            target.instance_id,
            *guard_parameters,
        ),
    ).fetchone()
    return row is not None


__all__ = [
    "CurrentStep",
    "DeferredStep",
    "StepMutationTarget",
    "fail_step",
    "retry_step",
    "succeed_step",
]
