"""Shared kernel-internal Trace Event persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest


@dataclass(frozen=True)
class TraceIdentity:
    trace_event_id: UUID
    sequence: int


@dataclass(frozen=True)
class AppendedTrace:
    identity: TraceIdentity
    receipt: dict[str, Any]


def append_trace(
    connection: Connection[tuple[Any, ...]],
    *,
    instance_id: UUID,
    event_type: str,
    source_kind: str,
    source_id: UUID,
    input_value: Any,
    receipt: Callable[[TraceIdentity], dict[str, Any]],
) -> AppendedTrace:
    sequence_row = connection.execute(
        "UPDATE openmagic_runtime.instances SET last_trace_sequence = last_trace_sequence + 1 "
        "WHERE instance_id = %s RETURNING last_trace_sequence",
        (instance_id,),
    ).fetchone()
    if sequence_row is None:
        raise RuntimeError("Instance disappeared while appending Trace Event")
    identity = TraceIdentity(uuid4(), int(sequence_row[0]))
    receipt_value = receipt(identity)
    connection.execute(
        "INSERT INTO openmagic_runtime.trace_events "
        "(trace_event_id, instance_id, sequence, event_type, schema_version, source_kind, "
        "source_id, input_digest, receipt) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)",
        (
            identity.trace_event_id,
            instance_id,
            identity.sequence,
            event_type,
            source_kind,
            source_id,
            canonical_digest(input_value),
            Jsonb(receipt_value),
        ),
    )
    return AppendedTrace(identity, receipt_value)


__all__ = ["AppendedTrace", "TraceIdentity", "append_trace"]
