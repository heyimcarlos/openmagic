"""Consistent public projections of durable kernel state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from openmagic_runtime.kernel._inspection_records import read_kernel_snapshot
from openmagic_runtime.kernel._records import (
    steps_for_instance,
    waits_for_instance,
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
        records = read_kernel_snapshot(self._database_url, instance_id)
        if records is None:
            raise KeyError(f"Instance not found: {instance_id}")
        instance, steps, waits = records
        return InstanceSnapshot(
            instance_id=instance_id,
            definition_key=instance.definition_key,
            definition_version=instance.definition_version,
            state=instance.state,
            observed_through_sequence=instance.observed_through_sequence,
            steps=tuple(StepView(step.step_id, step.template_key, step.state) for step in steps),
            waits=tuple(WaitView(wait.wait_id, wait.template_key, wait.state) for wait in waits),
        )


class KernelTransactionInspection:
    """Typed kernel reads that participate in an application-owned transaction."""

    def __init__(self, connection: Connection[tuple[object, ...]]) -> None:
        self._connection = connection

    def lock_instance(self, instance_id: UUID) -> RuntimeInstance | None:
        from openmagic_runtime.kernel._records import lock_instance

        return lock_instance(self._connection, instance_id)

    def lock_wait(self, *, instance_id: UUID, wait_id: UUID) -> RuntimeWait | None:
        from openmagic_runtime.kernel._records import lock_wait

        return lock_wait(self._connection, instance_id=instance_id, wait_id=wait_id)

    def waits_for_instance(self, instance_id: UUID) -> tuple[RuntimeWait, ...]:
        return waits_for_instance(self._connection, instance_id)

    def steps_for_instance(self, instance_id: UUID) -> tuple[RuntimeStep, ...]:
        return steps_for_instance(self._connection, instance_id)

    def read_attempt(self, attempt_id: UUID) -> RuntimeAttempt | None:
        from openmagic_runtime.kernel._records import read_attempt

        return read_attempt(self._connection, attempt_id)

    def read_step(self, step_id: UUID) -> RuntimeStep | None:
        from openmagic_runtime.kernel._records import read_step

        return read_step(self._connection, step_id)

    def expired_attempt_instances(self) -> tuple[UUID, ...]:
        from openmagic_runtime.kernel._records import expired_attempt_instances

        return expired_attempt_instances(self._connection)

    def activated_by_attempt(self, *, instance_id: UUID, attempt_id: UUID) -> ActivatedOccurrences:
        from openmagic_runtime.kernel._records import activated_by_attempt

        return activated_by_attempt(
            self._connection,
            instance_id=instance_id,
            attempt_id=attempt_id,
        )


__all__ = [
    "ActivatedOccurrences",
    "InstanceSnapshot",
    "InstanceState",
    "KernelInspection",
    "KernelTransactionInspection",
    "RuntimeAttempt",
    "RuntimeInstance",
    "RuntimeStep",
    "RuntimeWait",
    "StepState",
    "StepView",
    "WaitState",
    "WaitView",
]
