"""Private durable deferred Step resolution transition."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._control_support import (
    instance_definition,
    lock_open_instance,
    lock_source_identity,
    materialize_step_route,
    require_open_instance,
    validate_disposition,
)
from openmagic_runtime.kernel._records import lock_instance
from openmagic_runtime.kernel._step_mutations import (
    DeferredStep,
    fail_step,
    retry_step,
    succeed_step,
)
from openmagic_runtime.kernel._trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._transition_records import (
    attempt_count_for_step,
    lock_deferred_step,
)
from openmagic_runtime.kernel._transitions import (
    ResolveDeferredStep,
    ResolveDeferredStepReceipt,
    deferred_action,
)
from openmagic_runtime.kernel.definitions import StepTemplate, validate_payload
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
    definition = instance_definition(connection, required.instance_id)
    steps, waits = materialize_step_route(
        connection,
        definition=definition,
        required=required,
        route_key=outcome_route,
        route_input=route_input,
    )
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
    target = DeferredStep(request.instance_id, request.step_id, request.basis_attempt_id)
    if request.action == "succeed":
        if request.output is None or request.failure is not None:
            raise ValueError("Successful deferred resolution requires typed output")
        validate_payload(request.output, template.output_contract)
        if not succeed_step(connection, target, output=request.output):
            raise RuntimeError("Deferred Step basis is no longer authoritative")
        return
    if request.action == "retry":
        if request.output is not None or request.failure is not None:
            raise ValueError("Retry resolution cannot include Step output")
        if (
            attempt_count_for_step(connection, request.step_id)
            >= template.retry_policy.max_attempts
        ):
            raise RuntimeError("Deferred Step retry budget is exhausted")
        if not retry_step(connection, target, delay_seconds=0):
            raise RuntimeError("Deferred Step basis is no longer authoritative")
        return
    if request.output is not None or request.failure is None:
        raise ValueError("Failed deferred resolution requires typed failure")
    if not fail_step(connection, target, failure=request.failure):
        raise RuntimeError("Deferred Step basis is no longer authoritative")


def resolve_deferred_step(
    connection: Connection[tuple[Any, ...]], request: ResolveDeferredStep
) -> ResolveDeferredStepReceipt:
    input_digest = canonical_digest(request)
    instance = lock_instance(connection, request.instance_id)
    if instance is None:
        raise RuntimeError("Instance not found")
    lock_source_identity(
        connection,
        source_kind="deferred_resolution",
        source_id=request.source_id,
    )
    replay = read_trace_replay(
        connection,
        source_kind="deferred_resolution",
        source_id=request.source_id,
    )
    if replay is not None:
        if replay.input_digest != input_digest:
            raise ValueError("Deferred resolution identity was reused with conflicting input")
        receipt = replay.receipt
        return ResolveDeferredStepReceipt(
            step_id=UUID(str(receipt["step_id"])),
            action=deferred_action(receipt["action"]),
        )
    require_open_instance(instance.state)
    step = lock_deferred_step(
        connection,
        step_id=request.step_id,
        instance_id=request.instance_id,
    )
    if (
        step is None
        or step.state != "pending"
        or step.deferred_attempt_id != request.basis_attempt_id
    ):
        raise RuntimeError("Deferred Step basis is no longer authoritative")
    definition = instance_definition(connection, request.instance_id)
    template = next(item for item in definition.step_templates if item.key == step.template_key)
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
