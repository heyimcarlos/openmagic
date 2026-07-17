"""Private persistence for consistent kernel inspection projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime.kernel._persistence.records import steps_for_instance, waits_for_instance
from openmagic_runtime.kernel._record_decoding import instance_state
from openmagic_runtime.kernel.inspection_types import InstanceState, RuntimeStep, RuntimeWait


@dataclass(frozen=True)
class RuntimeInstanceInspection:
    instance_id: UUID
    definition_key: str
    definition_version: int
    state: InstanceState
    observed_through_sequence: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeInstanceInspection:
        return cls(
            instance_id=UUID(str(record["instance_id"])),
            definition_key=str(record["definition_key"]),
            definition_version=int(record["definition_version"]),
            state=instance_state(record["state"]),
            observed_through_sequence=int(record["last_trace_sequence"]),
        )


def read_instance_inspection(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeInstanceInspection | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT instance_id, definition_key, definition_version, state, "
            "last_trace_sequence FROM openmagic_runtime.instances WHERE instance_id = %s",
            (instance_id,),
        ).fetchone()
    return RuntimeInstanceInspection.decode(record) if record is not None else None


def read_kernel_snapshot(
    database_url: str, instance_id: UUID
) -> tuple[RuntimeInstanceInspection, tuple[RuntimeStep, ...], tuple[RuntimeWait, ...]] | None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        instance = read_instance_inspection(connection, instance_id)
        if instance is None:
            return None
        return (
            instance,
            steps_for_instance(connection, instance_id),
            waits_for_instance(connection, instance_id),
        )


__all__ = ["RuntimeInstanceInspection", "read_instance_inspection", "read_kernel_snapshot"]
