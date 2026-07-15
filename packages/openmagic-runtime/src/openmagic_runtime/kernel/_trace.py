"""Shared kernel-internal Trace Event persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest


def append_trace(
    connection: Connection[tuple[Any, ...]],
    *,
    instance_id: UUID,
    event_type: str,
    source_kind: str,
    source_id: UUID,
    input_value: Any,
    receipt: dict[str, Any],
) -> None:
    sequence_row = connection.execute(
        "UPDATE openmagic_runtime.instances SET last_trace_sequence = last_trace_sequence + 1 "
        "WHERE instance_id = %s RETURNING last_trace_sequence",
        (instance_id,),
    ).fetchone()
    if sequence_row is None:
        raise RuntimeError("Instance disappeared while appending Trace Event")
    connection.execute(
        "INSERT INTO openmagic_runtime.trace_events "
        "(trace_event_id, instance_id, sequence, event_type, schema_version, source_kind, "
        "source_id, input_digest, receipt) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)",
        (
            uuid4(),
            instance_id,
            sequence_row[0],
            event_type,
            source_kind,
            source_id,
            canonical_digest(input_value),
            Jsonb(receipt),
        ),
    )


__all__ = ["append_trace"]
