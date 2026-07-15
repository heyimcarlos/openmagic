"""Durable deferred Step resolution transition."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._control_support import (
    lock_instance,
    lock_open_instance,
    lock_source_identity,
    materialize_route,
    require_open_instance,
    validate_disposition,
)
from openmagic_runtime.kernel._trace import append_trace
from openmagic_runtime.kernel.definitions import StepTemplate, validate_payload, verified_definition
from openmagic_runtime.kernel.transitions import (
    ResolveDeferredStep,
    ResolveDeferredStepReceipt,
    deferred_action,
)
from openmagic_runtime.kernel.work import DispositionRequired


def defer_step(
    connection: Connection[tuple[Any, ...]],
    required: DispositionRequired,
    *,
    outcome_route: str | None = None,
    route_input: dict[str, Any] | None = None,
) -> tuple[dict[str, UUID], dict[str, UUID]]:
    if required.consumed:
        raise RuntimeError("Attempt disposition was already consumed")
    lock_open_instance(connection, required.instance_id)
    validate_disposition(
        connection,
        required,
        expected_attempt_state=required.basis_state,
    )
    updated = connection.execute(
        "UPDATE openmagic_runtime.steps SET claimable_at = NULL, deferred_attempt_id = %s "
        "WHERE step_id = %s AND instance_id = %s AND state = 'pending' "
        "RETURNING step_id",
        (required.attempt_id, required.step_id, required.instance_id),
    ).fetchone()
    if updated is None:
        raise RuntimeError("Deferral cannot target a terminal or missing Step")
    steps: dict[str, UUID] = {}
    waits: dict[str, UUID] = {}
    if outcome_route is not None:
        if route_input is None:
            raise ValueError("Deferral Route requires typed Route input")
        definition_row = connection.execute(
            "SELECT d.manifest, d.manifest_digest FROM openmagic_runtime.instances AS i "
            "JOIN openmagic_runtime.workflow_definitions AS d ON "
            "d.definition_key = i.definition_key AND "
            "d.definition_version = i.definition_version WHERE i.instance_id = %s",
            (required.instance_id,),
        ).fetchone()
        if definition_row is None:
            raise RuntimeError("Pinned Workflow Definition is unavailable")
        definition = verified_definition(dict(definition_row[0]), str(definition_row[1]))
        route = next(item for item in definition.routes if item.key == outcome_route)
        if route.activation != "step" or route.source_template_key != required.template_key:
            raise ValueError("Deferral Route does not accept this Step Template")
        validate_payload(route_input, route.activation_contract)
        steps, waits = materialize_route(
            connection,
            instance_id=required.instance_id,
            route=route,
            source_kind="step",
            source_id=required.attempt_id,
            route_input=route_input,
        )
    elif route_input is not None:
        raise ValueError("Route input cannot be supplied without a Deferral Route")
    append_trace(
        connection,
        instance_id=required.instance_id,
        event_type="step_deferred",
        source_kind="step_deferral",
        source_id=required.attempt_id,
        input_value={"route": outcome_route, "route_input": route_input},
        receipt=lambda _: {
            "step_id": str(required.step_id),
            "steps": {key: str(value) for key, value in steps.items()},
            "waits": {key: str(value) for key, value in waits.items()},
        },
    )
    required.consumed = True
    return steps, waits


def _apply_resolution(
    connection: Connection[tuple[Any, ...]],
    *,
    request: ResolveDeferredStep,
    template: StepTemplate,
) -> None:
    if request.action == "succeed":
        if request.output is None or request.failure is not None:
            raise ValueError("Successful deferred resolution requires typed output")
        validate_payload(request.output, template.output_contract)
        connection.execute(
            "UPDATE openmagic_runtime.steps SET state = 'succeeded', output = %s, "
            "output_digest = %s, terminal_at = clock_timestamp(), claimable_at = NULL, "
            "deferred_attempt_id = NULL WHERE step_id = %s",
            (Jsonb(request.output), canonical_digest(request.output), request.step_id),
        )
        return
    if request.action == "retry":
        if request.output is not None or request.failure is not None:
            raise ValueError("Retry resolution cannot include Step output")
        attempt_count = connection.execute(
            "SELECT count(*) FROM openmagic_runtime.attempts WHERE step_id = %s",
            (request.step_id,),
        ).fetchone()
        if attempt_count is None or int(attempt_count[0]) >= template.retry_policy.max_attempts:
            raise RuntimeError("Deferred Step retry budget is exhausted")
        connection.execute(
            "UPDATE openmagic_runtime.steps SET claimable_at = clock_timestamp(), "
            "deferred_attempt_id = NULL WHERE step_id = %s",
            (request.step_id,),
        )
        return
    if request.output is not None or request.failure is None:
        raise ValueError("Failed deferred resolution requires typed failure")
    connection.execute(
        "UPDATE openmagic_runtime.steps SET state = 'failed', failure = %s, "
        "failure_digest = %s, terminal_at = clock_timestamp(), claimable_at = NULL, "
        "deferred_attempt_id = NULL WHERE step_id = %s",
        (Jsonb(request.failure), canonical_digest(request.failure), request.step_id),
    )


def resolve_deferred_step(
    connection: Connection[tuple[Any, ...]], request: ResolveDeferredStep
) -> ResolveDeferredStepReceipt:
    input_digest = canonical_digest(request)
    instance_state = lock_instance(connection, request.instance_id)
    lock_source_identity(
        connection,
        source_kind="deferred_resolution",
        source_id=request.source_id,
    )
    replay = connection.execute(
        "SELECT input_digest, receipt FROM openmagic_runtime.trace_events "
        "WHERE source_kind = 'deferred_resolution' AND source_id = %s",
        (request.source_id,),
    ).fetchone()
    if replay is not None:
        if str(replay[0]) != input_digest:
            raise ValueError("Deferred resolution identity was reused with conflicting input")
        receipt = dict(replay[1])
        return ResolveDeferredStepReceipt(
            step_id=UUID(str(receipt["step_id"])),
            action=deferred_action(receipt["action"]),
        )
    require_open_instance(instance_state)
    row = connection.execute(
        "SELECT template_key, state, deferred_attempt_id FROM openmagic_runtime.steps "
        "WHERE step_id = %s AND instance_id = %s FOR UPDATE",
        (request.step_id, request.instance_id),
    ).fetchone()
    if (
        row is None
        or str(row[1]) != "pending"
        or row[2] is None
        or UUID(str(row[2])) != request.basis_attempt_id
    ):
        raise RuntimeError("Deferred Step basis is no longer authoritative")
    definition_row = connection.execute(
        "SELECT d.manifest, d.manifest_digest FROM openmagic_runtime.instances AS i "
        "JOIN openmagic_runtime.workflow_definitions AS d ON "
        "d.definition_key = i.definition_key AND d.definition_version = i.definition_version "
        "WHERE i.instance_id = %s",
        (request.instance_id,),
    ).fetchone()
    if definition_row is None:
        raise RuntimeError("Pinned Workflow Definition is unavailable")
    definition = verified_definition(dict(definition_row[0]), str(definition_row[1]))
    template = next(item for item in definition.step_templates if item.key == str(row[0]))
    _apply_resolution(connection, request=request, template=template)
    append_trace(
        connection,
        instance_id=request.instance_id,
        event_type=f"deferred_step_{request.action}",
        source_kind="deferred_resolution",
        source_id=request.source_id,
        input_value=request,
        receipt=lambda _: {"step_id": str(request.step_id), "action": request.action},
    )
    return ResolveDeferredStepReceipt(request.step_id, request.action)


__all__ = ["defer_step", "resolve_deferred_step"]
