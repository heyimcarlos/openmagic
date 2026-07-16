"""Private durable Signal acceptance transition."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._control_support import (
    instance_definition,
    lock_source_identity,
    materialize_route,
    require_open_instance,
)
from openmagic_runtime.kernel._records import lock_instance, lock_wait
from openmagic_runtime.kernel._trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._transitions import AcceptSignal, SignalReceipt
from openmagic_runtime.kernel.definitions import Route, validate_payload


def _receipt(payload: dict[str, Any]) -> SignalReceipt:
    return SignalReceipt(
        signal_id=UUID(payload["signal_id"]),
        instance_id=UUID(payload["instance_id"]),
        wait_id=UUID(payload["wait_id"]),
        steps={key: UUID(value) for key, value in payload["steps"].items()},
        waits={key: UUID(value) for key, value in payload["waits"].items()},
        trace_event_id=UUID(payload["trace_event_id"]),
        trace_sequence=int(payload["trace_sequence"]),
    )


def _validated_route(connection: Connection[tuple[Any, ...]], request: AcceptSignal) -> Route:
    if request.schema_version != 1:
        raise ValueError("Signal schema version is unsupported")
    wait = lock_wait(
        connection,
        wait_id=request.wait_id,
        instance_id=request.instance_id,
    )
    if wait is None:
        raise RuntimeError("Signal target Wait does not exist")
    if wait.state != "unsatisfied":
        raise RuntimeError("Signal target Wait is no longer unsatisfied")
    definition = instance_definition(connection, request.instance_id)
    wait_template = next(
        item for item in definition.wait_templates if item.key == wait.template_key
    )
    if wait_template.signal_type != request.signal_type:
        raise ValueError("Signal Type does not match the target Wait")
    route = next(item for item in definition.routes if item.key == request.route_key)
    if route.activation != "signal":
        raise ValueError("Signal Route is not a Signal activation")
    validate_payload(request.payload, route.activation_contract)
    return route


def accept_signal(connection: Connection[tuple[Any, ...]], request: AcceptSignal) -> SignalReceipt:
    transition_input = {
        "instance_id": str(request.instance_id),
        "wait_id": str(request.wait_id),
        "signal_type": request.signal_type,
        "schema_version": request.schema_version,
        "payload": request.payload,
        "route_key": request.route_key,
    }
    input_digest = canonical_digest(transition_input)
    instance = lock_instance(connection, request.instance_id)
    if instance is None:
        raise RuntimeError("Instance not found")
    lock_source_identity(
        connection,
        source_kind="signal_acceptance",
        source_id=request.signal_id,
    )
    replay = read_trace_replay(
        connection,
        source_kind="signal_acceptance",
        source_id=request.signal_id,
    )
    if replay is not None:
        if replay.input_digest != input_digest:
            raise ValueError("Signal identity was reused with conflicting input")
        return _receipt(replay.receipt)
    require_open_instance(instance.state)
    route = _validated_route(connection, request)
    inserted = connection.execute(
        "INSERT INTO openmagic_runtime.signals "
        "(signal_id, instance_id, wait_id, signal_type, schema_version, payload, "
        "payload_digest) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING signal_id",
        (
            request.signal_id,
            request.instance_id,
            request.wait_id,
            request.signal_type,
            request.schema_version,
            Jsonb(request.payload),
            canonical_digest(request.payload),
        ),
    ).fetchone()
    if inserted is None:
        raise RuntimeError("Signal was not recorded")
    connection.execute(
        "UPDATE openmagic_runtime.waits SET state = 'satisfied', satisfying_signal_id = %s, "
        "satisfied_at = clock_timestamp() WHERE wait_id = %s",
        (request.signal_id, request.wait_id),
    )
    steps, waits = materialize_route(
        connection,
        instance_id=request.instance_id,
        route=route,
        source_kind="signal",
        source_id=request.signal_id,
        route_input=request.payload,
    )
    appended = append_trace(
        connection,
        instance_id=request.instance_id,
        event_type="signal_accepted",
        source_kind="signal_acceptance",
        source_id=request.signal_id,
        input_value=transition_input,
        receipt=lambda identity: {
            "signal_id": str(request.signal_id),
            "instance_id": str(request.instance_id),
            "wait_id": str(request.wait_id),
            "steps": {key: str(value) for key, value in steps.items()},
            "waits": {key: str(value) for key, value in waits.items()},
            "trace_event_id": str(identity.trace_event_id),
            "trace_sequence": identity.sequence,
        },
    )
    return _receipt(appended.receipt)


__all__ = ["accept_signal"]
