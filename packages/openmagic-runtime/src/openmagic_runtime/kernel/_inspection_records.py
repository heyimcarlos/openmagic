"""Private persistence for consistent kernel inspection projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime.kernel.records import InstanceState, instance_state


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
