"""Transaction-scoped kernel control transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._control_support import (
    lock_open_instance,
    materialize_route,
    validate_disposition,
)
from openmagic_runtime.kernel._trace import append_trace
from openmagic_runtime.kernel.attempt_guard import (
    CurrentAttemptGuard,
    guard_current_attempt,
)
from openmagic_runtime.kernel.closure import close_instance
from openmagic_runtime.kernel.deferred import defer_step, resolve_deferred_step
from openmagic_runtime.kernel.definitions import (
    WorkflowDefinition,
    validate_payload,
    verified_definition,
)
from openmagic_runtime.kernel.signals import accept_signal
from openmagic_runtime.kernel.transitions import (
    AcceptSignal,
    CloseInstance,
    CloseInstanceReceipt,
    GuardCurrentAttempt,
    ResolveDeferredStep,
    ResolveDeferredStepReceipt,
    SignalReceipt,
)
from openmagic_runtime.kernel.work import DispositionRequired


@dataclass(frozen=True)
class StartInstance:
    command_id: UUID
    definition_key: str
    definition_version: int
    instance_input: dict[str, Any]
    route_input: dict[str, Any]


@dataclass(frozen=True)
class StartInstanceReceipt:
    instance_id: UUID
    definition_key: str
    definition_version: int
    steps: dict[str, UUID]
    waits: dict[str, UUID]
    trace_event_id: UUID
    trace_sequence: int


def _materialize_optional_step_route(
    connection: Connection[tuple[Any, ...]],
    *,
    definition: WorkflowDefinition,
    required: DispositionRequired,
    outcome_route: str | None,
    route_input: dict[str, Any] | None,
) -> tuple[dict[str, UUID], dict[str, UUID]]:
    if outcome_route is None:
        if route_input is not None:
            raise ValueError("Route input cannot be supplied without an Outcome Route")
        return {}, {}
    if route_input is None:
        raise ValueError("Outcome Route requires typed Route input")
    route = next(item for item in definition.routes if item.key == outcome_route)
    if route.activation != "step" or route.source_template_key != required.template_key:
        raise ValueError("Outcome Route does not accept this Step Template")
    validate_payload(route_input, route.activation_contract)
    return materialize_route(
        connection,
        instance_id=required.instance_id,
        route=route,
        source_kind="step",
        source_id=required.attempt_id,
        route_input=route_input,
    )


class KernelControl:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def start(self, request: StartInstance) -> StartInstanceReceipt:
        source_digest = canonical_digest(request)
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(request.command_id),),
        )
        replay = self._connection.execute(
            "SELECT receipt, input_digest FROM openmagic_runtime.trace_events "
            "WHERE source_kind = 'command_start' AND source_id = %s",
            (request.command_id,),
        ).fetchone()
        if replay is not None:
            if replay[1] != source_digest:
                raise ValueError("start source identity was reused with conflicting input")
            receipt = dict(replay[0])
            return StartInstanceReceipt(
                instance_id=UUID(receipt["instance_id"]),
                definition_key=receipt["definition_key"],
                definition_version=receipt["definition_version"],
                steps={key: UUID(value) for key, value in receipt["steps"].items()},
                waits={key: UUID(value) for key, value in receipt["waits"].items()},
                trace_event_id=UUID(receipt["trace_event_id"]),
                trace_sequence=receipt["trace_sequence"],
            )
        row = self._connection.execute(
            "SELECT manifest, manifest_digest FROM openmagic_runtime.workflow_definitions "
            "WHERE definition_key = %s AND definition_version = %s",
            (request.definition_key, request.definition_version),
        ).fetchone()
        if row is None:
            raise ValueError("pinned Workflow Definition is unavailable")
        definition = verified_definition(dict(row[0]), str(row[1]))
        validate_payload(request.instance_input, definition.instance_input_contract)
        route = next(item for item in definition.routes if item.key == "start")
        validate_payload(request.route_input, route.activation_contract)
        instance_id = uuid4()
        self._connection.execute(
            "INSERT INTO openmagic_runtime.instances "
            "(instance_id, definition_key, definition_version, input, input_digest, state) "
            "VALUES (%s, %s, %s, %s, %s, 'open')",
            (
                instance_id,
                request.definition_key,
                request.definition_version,
                Jsonb(request.instance_input),
                canonical_digest(request.instance_input),
            ),
        )
        steps, waits = materialize_route(
            self._connection,
            instance_id=instance_id,
            route=route,
            source_kind="command",
            source_id=request.command_id,
            route_input=request.route_input,
        )
        appended = append_trace(
            self._connection,
            instance_id=instance_id,
            event_type="instance_started",
            source_kind="command_start",
            source_id=request.command_id,
            input_value=request,
            receipt=lambda identity: {
                "instance_id": str(instance_id),
                "definition_key": request.definition_key,
                "definition_version": request.definition_version,
                "steps": {key: str(value) for key, value in steps.items()},
                "waits": {key: str(value) for key, value in waits.items()},
                "trace_event_id": str(identity.trace_event_id),
                "trace_sequence": identity.sequence,
            },
        )
        return StartInstanceReceipt(
            instance_id=instance_id,
            definition_key=request.definition_key,
            definition_version=request.definition_version,
            steps=steps,
            waits=waits,
            trace_event_id=appended.identity.trace_event_id,
            trace_sequence=appended.identity.sequence,
        )

    def succeed(
        self,
        required: DispositionRequired,
        *,
        output: dict[str, Any],
        outcome_route: str | None = None,
        route_input: dict[str, Any] | None = None,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        lock_open_instance(self._connection, required.instance_id)
        validate_disposition(
            self._connection,
            required,
            expected_attempt_state="completed",
        )
        transition_input = {
            "output": output,
            "route": outcome_route,
            "route_input": route_input,
        }
        replay = self._connection.execute(
            "SELECT input_digest, receipt FROM openmagic_runtime.trace_events "
            "WHERE source_kind = 'step_outcome' AND source_id = %s",
            (required.attempt_id,),
        ).fetchone()
        if replay is not None:
            if str(replay[0]) != canonical_digest(transition_input):
                raise ValueError("Step outcome source was reused with conflicting input")
            receipt = dict(replay[1])
            required.consumed = True
            required.replayed = True
            return (
                {key: UUID(value) for key, value in receipt["steps"].items()},
                {key: UUID(value) for key, value in receipt["waits"].items()},
            )
        if required.consumed:
            raise RuntimeError("Attempt disposition was already consumed")
        output_digest = canonical_digest(output)
        updated = self._connection.execute(
            "UPDATE openmagic_runtime.steps SET state = 'succeeded', output = %s, "
            "output_digest = %s, "
            "terminal_at = clock_timestamp(), claimable_at = NULL "
            "WHERE step_id = %s AND instance_id = %s AND state = 'pending' RETURNING step_id",
            (Jsonb(output), output_digest, required.step_id, required.instance_id),
        ).fetchone()
        if updated is None:
            raise RuntimeError("Step outcome cannot target a terminal or missing Step")
        definition_row = self._connection.execute(
            "SELECT d.manifest, d.manifest_digest FROM openmagic_runtime.instances AS i "
            "JOIN openmagic_runtime.workflow_definitions AS d "
            "ON d.definition_key = i.definition_key AND d.definition_version = i.definition_version "
            "WHERE i.instance_id = %s",
            (required.instance_id,),
        ).fetchone()
        if definition_row is None:
            raise RuntimeError("Pinned Workflow Definition is unavailable")
        definition = verified_definition(dict(definition_row[0]), str(definition_row[1]))
        template = next(
            item for item in definition.step_templates if item.key == required.template_key
        )
        validate_payload(output, template.output_contract)
        steps, waits = _materialize_optional_step_route(
            self._connection,
            definition=definition,
            required=required,
            outcome_route=outcome_route,
            route_input=route_input,
        )
        receipt = {
            "step_id": str(required.step_id),
            "steps": {key: str(value) for key, value in steps.items()},
            "waits": {key: str(value) for key, value in waits.items()},
        }
        append_trace(
            self._connection,
            instance_id=required.instance_id,
            event_type="step_succeeded",
            source_kind="step_outcome",
            source_id=required.attempt_id,
            input_value=transition_input,
            receipt=lambda _: receipt,
        )
        required.consumed = True
        return steps, waits

    def retry(self, required: DispositionRequired) -> None:
        if required.consumed:
            raise RuntimeError("Attempt disposition was already consumed")
        lock_open_instance(self._connection, required.instance_id)
        validate_disposition(
            self._connection,
            required,
            expected_attempt_state=required.basis_state,
        )
        definition_row = self._connection.execute(
            "SELECT d.manifest, d.manifest_digest FROM openmagic_runtime.instances AS i "
            "JOIN openmagic_runtime.workflow_definitions AS d "
            "ON d.definition_key = i.definition_key AND d.definition_version = i.definition_version "
            "WHERE i.instance_id = %s",
            (required.instance_id,),
        ).fetchone()
        if definition_row is None:
            raise RuntimeError("Pinned Workflow Definition is unavailable")
        definition = verified_definition(dict(definition_row[0]), str(definition_row[1]))
        template = next(
            item for item in definition.step_templates if item.key == required.template_key
        )
        delay_index = required.attempt_number - 1
        if delay_index >= len(template.retry_policy.delays_seconds):
            raise RuntimeError("Attempt retry budget is exhausted")
        delay = template.retry_policy.delays_seconds[delay_index]
        updated = self._connection.execute(
            "UPDATE openmagic_runtime.steps SET claimable_at = "
            "clock_timestamp() + (%s * interval '1 second') "
            "WHERE step_id = %s AND instance_id = %s AND state = 'pending' RETURNING step_id",
            (delay, required.step_id, required.instance_id),
        ).fetchone()
        if updated is None:
            raise RuntimeError("Retry cannot target a terminal Step")
        append_trace(
            self._connection,
            instance_id=required.instance_id,
            event_type="step_retry_authorized",
            source_kind="recovery_disposition",
            source_id=required.attempt_id,
            input_value={"attempt_id": str(required.attempt_id), "delay_seconds": delay},
            receipt=lambda _: {"step_id": str(required.step_id), "delay_seconds": delay},
        )
        required.consumed = True

    def fail(self, required: DispositionRequired, *, failure: dict[str, Any]) -> None:
        if required.consumed:
            raise RuntimeError("Attempt disposition was already consumed")
        lock_open_instance(self._connection, required.instance_id)
        validate_disposition(
            self._connection,
            required,
            expected_attempt_state=required.basis_state,
        )
        updated = self._connection.execute(
            "UPDATE openmagic_runtime.steps SET state = 'failed', failure = %s, "
            "failure_digest = %s, terminal_at = clock_timestamp(), claimable_at = NULL "
            "WHERE step_id = %s AND instance_id = %s AND state = 'pending' RETURNING step_id",
            (
                Jsonb(failure),
                canonical_digest(failure),
                required.step_id,
                required.instance_id,
            ),
        ).fetchone()
        if updated is None:
            raise RuntimeError("Failure cannot target a terminal or missing Step")
        append_trace(
            self._connection,
            instance_id=required.instance_id,
            event_type="step_failed",
            source_kind="recovery_disposition",
            source_id=required.attempt_id,
            input_value=failure,
            receipt=lambda _: {"step_id": str(required.step_id), "failure": failure},
        )
        required.consumed = True

    def accept_signal(self, request: AcceptSignal) -> SignalReceipt:
        return accept_signal(self._connection, request)

    def guard_current_attempt(self, request: GuardCurrentAttempt) -> CurrentAttemptGuard:
        return guard_current_attempt(self._connection, request)

    def defer(
        self,
        required: DispositionRequired,
        *,
        outcome_route: str | None = None,
        route_input: dict[str, Any] | None = None,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        return defer_step(
            self._connection,
            required,
            outcome_route=outcome_route,
            route_input=route_input,
        )

    def resolve_deferred(self, request: ResolveDeferredStep) -> ResolveDeferredStepReceipt:
        return resolve_deferred_step(self._connection, request)

    def close(self, request: CloseInstance) -> CloseInstanceReceipt:
        return close_instance(self._connection, request)


def start_instance(*, database_url: str, request: StartInstance) -> StartInstanceReceipt:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return KernelControl(connection).start(request)


__all__ = [
    "KernelControl",
    "StartInstance",
    "StartInstanceReceipt",
    "start_instance",
]
