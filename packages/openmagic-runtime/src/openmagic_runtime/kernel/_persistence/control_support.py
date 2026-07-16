"""Shared persistence primitives for kernel control transitions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._persistence.records import (
    lock_instance as lock_instance_record,
)
from openmagic_runtime.kernel._persistence.transition_records import (
    lock_disposition_source,
    read_instance_definition,
)
from openmagic_runtime.kernel._work_contracts import DispositionRequired
from openmagic_runtime.kernel.definitions import (
    Route,
    WorkflowDefinition,
    validate_payload,
    verified_definition,
)
from openmagic_runtime.kernel.inspection_types import (
    AttemptState,
    InstanceState,
)


def validate_disposition(
    connection: Connection[tuple[Any, ...]],
    required: DispositionRequired,
    *,
    expected_attempt_state: AttemptState,
) -> None:
    source = lock_disposition_source(connection, required.attempt_id)
    if source is None:
        raise RuntimeError("Attempt disposition source does not exist")
    if (
        source.instance_id != required.instance_id
        or source.step_id != required.step_id
        or source.attempt_number != required.attempt_number
        or source.state != expected_attempt_state
        or source.template_key != required.template_key
    ):
        raise RuntimeError("Attempt disposition does not match its durable source")
    if expected_attempt_state == "completed" and source.observation_digest != canonical_digest(
        required.observation
    ):
        raise RuntimeError("Attempt disposition observation conflicts with durable source")


def require_open_instance(state: InstanceState) -> None:
    if state != "open":
        raise RuntimeError("Instance is closed")


def lock_instance(connection: Connection[tuple[Any, ...]], instance_id: UUID) -> InstanceState:
    instance = lock_instance_record(connection, instance_id)
    if instance is None:
        raise RuntimeError("Instance not found")
    return instance.state


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


def instance_definition(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> WorkflowDefinition:
    record = read_instance_definition(connection, instance_id)
    if record is None:
        raise RuntimeError("Pinned Workflow Definition is unavailable")
    return verified_definition(record.manifest, record.manifest_digest)


def materialize_step_route(
    connection: Connection[tuple[Any, ...]],
    *,
    definition: WorkflowDefinition,
    required: DispositionRequired,
    route_key: str | None,
    route_input: dict[str, Any] | None,
) -> tuple[dict[str, UUID], dict[str, UUID]]:
    if route_key is None:
        if route_input is not None:
            raise ValueError("Route input cannot be supplied without a Step Route")
        return {}, {}
    if route_input is None:
        raise ValueError("Step Route requires typed Route input")
    route = next(item for item in definition.routes if item.key == route_key)
    if route.activation != "step" or route.source_template_key != required.template_key:
        raise ValueError("Step Route does not accept this Step Template")
    validate_payload(route_input, route.activation_contract)
    return materialize_route(
        connection,
        instance_id=required.instance_id,
        route=route,
        source_kind="step",
        source_id=required.attempt_id,
        route_input=route_input,
    )
