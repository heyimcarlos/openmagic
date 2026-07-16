"""Private durable Instance closure transition."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._persistence.control_support import (
    lock_instance,
    lock_source_identity,
    require_open_instance,
)
from openmagic_runtime.kernel._persistence.trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._transitions import CloseInstance, CloseInstanceReceipt


def _receipt(payload: dict[str, Any]) -> CloseInstanceReceipt:
    return CloseInstanceReceipt(
        instance_id=UUID(payload["instance_id"]),
        cancelled_step_ids=tuple(UUID(value) for value in payload["cancelled_step_ids"]),
        cancelled_attempt_ids=tuple(UUID(value) for value in payload["cancelled_attempt_ids"]),
        cancelled_wait_ids=tuple(UUID(value) for value in payload["cancelled_wait_ids"]),
        trace_event_id=UUID(payload["trace_event_id"]),
        trace_sequence=int(payload["trace_sequence"]),
    )


def close_instance(
    connection: Connection[tuple[Any, ...]], request: CloseInstance
) -> CloseInstanceReceipt:
    transition_input = {
        "command_id": str(request.command_id),
        "instance_id": str(request.instance_id),
    }
    digest = canonical_digest(transition_input)
    instance_state = lock_instance(connection, request.instance_id)
    lock_source_identity(
        connection,
        source_kind="instance_closure",
        source_id=request.command_id,
    )
    replay = read_trace_replay(
        connection,
        source_kind="instance_closure",
        source_id=request.command_id,
    )
    if replay is not None:
        if replay.input_digest != digest:
            raise ValueError("Instance closure identity was reused with conflicting input")
        return _receipt(replay.receipt)
    require_open_instance(instance_state)
    with connection.cursor(row_factory=dict_row) as cursor:
        cancelled_attempts = cursor.execute(
            "UPDATE openmagic_runtime.attempts SET state = 'cancelled', "
            "completed_at = clock_timestamp() WHERE instance_id = %s AND state = 'leased' "
            "RETURNING attempt_id",
            (request.instance_id,),
        ).fetchall()
        cancelled_steps = cursor.execute(
            "UPDATE openmagic_runtime.steps SET state = 'cancelled', "
            "terminal_at = clock_timestamp(), claimable_at = NULL, deferred_attempt_id = NULL "
            "WHERE instance_id = %s AND state = 'pending' RETURNING step_id",
            (request.instance_id,),
        ).fetchall()
        cancelled_waits = cursor.execute(
            "UPDATE openmagic_runtime.waits SET state = 'cancelled' "
            "WHERE instance_id = %s AND state = 'unsatisfied' RETURNING wait_id",
            (request.instance_id,),
        ).fetchall()
    connection.execute(
        "UPDATE openmagic_runtime.instances SET state = 'closed', "
        "closed_at = clock_timestamp() WHERE instance_id = %s",
        (request.instance_id,),
    )
    appended = append_trace(
        connection,
        instance_id=request.instance_id,
        event_type="instance_closed",
        source_kind="instance_closure",
        source_id=request.command_id,
        input_value=transition_input,
        receipt=lambda identity: {
            "instance_id": str(request.instance_id),
            "cancelled_step_ids": [str(record["step_id"]) for record in cancelled_steps],
            "cancelled_attempt_ids": [str(record["attempt_id"]) for record in cancelled_attempts],
            "cancelled_wait_ids": [str(record["wait_id"]) for record in cancelled_waits],
            "trace_event_id": str(identity.trace_event_id),
            "trace_sequence": identity.sequence,
        },
    )
    return _receipt(appended.receipt)


__all__ = ["close_instance"]
