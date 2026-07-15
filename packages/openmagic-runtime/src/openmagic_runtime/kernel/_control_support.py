"""Shared persistence primitives for kernel control transitions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel.definitions import Route
from openmagic_runtime.kernel.work import DispositionRequired


def validate_disposition(
    connection: Connection[tuple[Any, ...]],
    required: DispositionRequired,
    *,
    expected_attempt_state: str,
) -> None:
    row = connection.execute(
        "SELECT a.instance_id, a.step_id, a.attempt_number, a.state, a.observation_digest, "
        "s.template_key FROM openmagic_runtime.attempts AS a "
        "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
        "WHERE a.attempt_id = %s FOR UPDATE OF a, s",
        (required.attempt_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Attempt disposition source does not exist")
    if (
        UUID(str(row[0])) != required.instance_id
        or UUID(str(row[1])) != required.step_id
        or int(row[2]) != required.attempt_number
        or str(row[3]) != expected_attempt_state
        or str(row[5]) != required.template_key
    ):
        raise RuntimeError("Attempt disposition does not match its durable source")
    if expected_attempt_state == "completed" and str(row[4]) != canonical_digest(
        required.observation
    ):
        raise RuntimeError("Attempt disposition observation conflicts with durable source")


def lock_instance(connection: Connection[tuple[Any, ...]], instance_id: UUID) -> str:
    row = connection.execute(
        "SELECT state FROM openmagic_runtime.instances WHERE instance_id = %s FOR UPDATE",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Instance not found")
    return str(row[0])


def require_open_instance(state: str) -> None:
    if state != "open":
        raise RuntimeError("Instance is closed")


def lock_open_instance(connection: Connection[tuple[Any, ...]], instance_id: UUID) -> None:
    require_open_instance(lock_instance(connection, instance_id))


def lock_source_identity(
    connection: Connection[tuple[Any, ...]],
    *,
    source_kind: str,
    source_id: UUID,
) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{source_kind}:{source_id}",),
    )


def materialize_route(
    connection: Connection[tuple[Any, ...]],
    *,
    instance_id: UUID,
    route: Route,
    source_kind: str,
    source_id: UUID,
    route_input: dict[str, Any],
) -> tuple[dict[str, UUID], dict[str, UUID]]:
    steps: dict[str, UUID] = {}
    waits: dict[str, UUID] = {}
    for output in route.outputs:
        payload = {binding.target: route_input[binding.source] for binding in output.input_bindings}
        occurrence_id = uuid4()
        if output.kind == "step":
            connection.execute(
                "INSERT INTO openmagic_runtime.steps "
                "(step_id, instance_id, template_key, route_key, activation_source_kind, "
                "activation_source_id, output_slot, input, input_digest, state, claimable_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', "
                "clock_timestamp())",
                (
                    occurrence_id,
                    instance_id,
                    output.template_key,
                    route.key,
                    source_kind,
                    source_id,
                    output.slot,
                    Jsonb(payload),
                    canonical_digest(payload),
                ),
            )
            steps[output.slot] = occurrence_id
        else:
            connection.execute(
                "INSERT INTO openmagic_runtime.waits "
                "(wait_id, instance_id, template_key, route_key, activation_source_kind, "
                "activation_source_id, output_slot, input, input_digest, state) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'unsatisfied')",
                (
                    occurrence_id,
                    instance_id,
                    output.template_key,
                    route.key,
                    source_kind,
                    source_id,
                    output.slot,
                    Jsonb(payload),
                    canonical_digest(payload),
                ),
            )
            waits[output.slot] = occurrence_id
    for output in route.outputs:
        if output.kind != "step":
            continue
        for prerequisite_slot in output.depends_on_slots:
            prerequisite_id = steps.get(prerequisite_slot)
            if prerequisite_id is None:
                raise ValueError("Step dependency must reference a Step output")
            connection.execute(
                "INSERT INTO openmagic_runtime.step_dependencies "
                "(instance_id, step_id, prerequisite_step_id) VALUES (%s, %s, %s)",
                (instance_id, steps[output.slot], prerequisite_id),
            )
    return steps, waits
