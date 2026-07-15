"""Consistent public projections of durable kernel state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class StepView:
    step_id: UUID
    template_key: str
    state: str


@dataclass(frozen=True)
class WaitView:
    wait_id: UUID
    template_key: str
    state: str


@dataclass(frozen=True)
class InstanceSnapshot:
    instance_id: UUID
    definition_key: str
    definition_version: int
    state: str
    observed_through_sequence: int
    steps: tuple[StepView, ...]
    waits: tuple[WaitView, ...]


class KernelInspection:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def snapshot(self, instance_id: UUID) -> InstanceSnapshot:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            instance = connection.execute(
                "SELECT definition_key, definition_version, state, last_trace_sequence "
                "FROM openmagic_runtime.instances WHERE instance_id = %s",
                (instance_id,),
            ).fetchone()
            if instance is None:
                raise KeyError(f"Instance not found: {instance_id}")
            steps = connection.execute(
                "SELECT step_id, template_key, state FROM openmagic_runtime.steps "
                "WHERE instance_id = %s ORDER BY created_at, step_id",
                (instance_id,),
            ).fetchall()
            waits = connection.execute(
                "SELECT wait_id, template_key, state FROM openmagic_runtime.waits "
                "WHERE instance_id = %s ORDER BY created_at, wait_id",
                (instance_id,),
            ).fetchall()
        return InstanceSnapshot(
            instance_id=instance_id,
            definition_key=str(instance[0]),
            definition_version=int(instance[1]),
            state=str(instance[2]),
            observed_through_sequence=int(instance[3]),
            steps=tuple(StepView(UUID(str(row[0])), str(row[1]), str(row[2])) for row in steps),
            waits=tuple(WaitView(UUID(str(row[0])), str(row[1]), str(row[2])) for row in waits),
        )


__all__ = ["InstanceSnapshot", "KernelInspection", "StepView", "WaitView"]
