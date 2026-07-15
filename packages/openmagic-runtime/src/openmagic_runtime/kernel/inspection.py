"""Consistent public projections of durable kernel state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from openmagic_runtime.kernel._inspection_records import read_instance_inspection
from openmagic_runtime.kernel.records import (
    InstanceState,
    StepState,
    WaitState,
    steps_for_instance,
    waits_for_instance,
)


@dataclass(frozen=True)
class StepView:
    step_id: UUID
    template_key: str
    state: StepState


@dataclass(frozen=True)
class WaitView:
    wait_id: UUID
    template_key: str
    state: WaitState


@dataclass(frozen=True)
class InstanceSnapshot:
    instance_id: UUID
    definition_key: str
    definition_version: int
    state: InstanceState
    observed_through_sequence: int
    steps: tuple[StepView, ...]
    waits: tuple[WaitView, ...]


class KernelInspection:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def snapshot(self, instance_id: UUID) -> InstanceSnapshot:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            instance = read_instance_inspection(connection, instance_id)
            if instance is None:
                raise KeyError(f"Instance not found: {instance_id}")
            steps = steps_for_instance(connection, instance_id)
            waits = waits_for_instance(connection, instance_id)
        return InstanceSnapshot(
            instance_id=instance_id,
            definition_key=instance.definition_key,
            definition_version=instance.definition_version,
            state=instance.state,
            observed_through_sequence=instance.observed_through_sequence,
            steps=tuple(StepView(step.step_id, step.template_key, step.state) for step in steps),
            waits=tuple(WaitView(wait.wait_id, wait.template_key, wait.state) for wait in waits),
        )


__all__ = ["InstanceSnapshot", "KernelInspection", "StepView", "WaitView"]
