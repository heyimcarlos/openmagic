"""Canonical persistence owner for kernel control transitions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._control_contracts import StartInstance, StartInstanceReceipt
from openmagic_runtime.kernel._persistence.attempt_guard import (
    CurrentAttemptGuard,
    guard_current_attempt,
)
from openmagic_runtime.kernel._persistence.closure import close_instance
from openmagic_runtime.kernel._persistence.control_support import (
    instance_definition,
    lock_open_instance,
    materialize_route,
    materialize_step_route,
    validate_disposition,
)
from openmagic_runtime.kernel._persistence.deferred import defer_step, resolve_deferred_step
from openmagic_runtime.kernel._persistence.signals import accept_signal
from openmagic_runtime.kernel._persistence.step_mutations import (
    CurrentStep,
    fail_step,
    retry_step,
    succeed_step,
)
from openmagic_runtime.kernel._persistence.trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._persistence.transition_records import RuntimeDefinitionRecord
from openmagic_runtime.kernel._transitions import (
    AcceptSignal,
    CloseInstance,
    CloseInstanceReceipt,
    GuardCurrentAttempt,
    ResolveDeferredStep,
    ResolveDeferredStepReceipt,
    SignalReceipt,
)
from openmagic_runtime.kernel._work_contracts import DispositionRequired
from openmagic_runtime.kernel.definitions import validate_payload, verified_definition


class KernelControlTransaction:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def start(self, request: StartInstance) -> StartInstanceReceipt:
        source_digest = canonical_digest(request)
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(request.command_id),),
        )
        replay = read_trace_replay(
            self._connection,
            source_kind="command_start",
            source_id=request.command_id,
        )
        if replay is not None:
            if replay.input_digest != source_digest:
                raise ValueError("start source identity was reused with conflicting input")
            receipt = replay.receipt
            return StartInstanceReceipt(
                instance_id=UUID(receipt["instance_id"]),
                definition_key=receipt["definition_key"],
                definition_version=receipt["definition_version"],
                steps={key: UUID(value) for key, value in receipt["steps"].items()},
                waits={key: UUID(value) for key, value in receipt["waits"].items()},
                trace_event_id=UUID(receipt["trace_event_id"]),
                trace_sequence=receipt["trace_sequence"],
            )
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT manifest, manifest_digest "
                "FROM openmagic_runtime.workflow_definitions "
                "WHERE definition_key = %s AND definition_version = %s",
                (request.definition_key, request.definition_version),
            ).fetchone()
        if record is None:
            raise ValueError("pinned Workflow Definition is unavailable")
        definition_record = RuntimeDefinitionRecord.decode(record)
        definition = verified_definition(
            definition_record.manifest,
            definition_record.manifest_digest,
        )
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
        replay = read_trace_replay(
            self._connection,
            source_kind="step_outcome",
            source_id=required.attempt_id,
        )
        if replay is not None:
            if replay.input_digest != canonical_digest(transition_input):
                raise ValueError("Step outcome source was reused with conflicting input")
            receipt = replay.receipt
            required.consumed = True
            required.replayed = True
            return (
                {key: UUID(value) for key, value in receipt["steps"].items()},
                {key: UUID(value) for key, value in receipt["waits"].items()},
            )
        if required.consumed:
            raise RuntimeError("Attempt disposition was already consumed")
        definition = instance_definition(self._connection, required.instance_id)
        template = next(
            item for item in definition.step_templates if item.key == required.template_key
        )
        validate_payload(output, template.output_contract)
        target = CurrentStep(required.instance_id, required.step_id)
        if not succeed_step(self._connection, target, output=output):
            raise RuntimeError("Step outcome cannot target a terminal or missing Step")
        steps, waits = materialize_step_route(
            self._connection,
            definition=definition,
            required=required,
            route_key=outcome_route,
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
        definition = instance_definition(self._connection, required.instance_id)
        template = next(
            item for item in definition.step_templates if item.key == required.template_key
        )
        delay_index = required.attempt_number - 1
        if delay_index >= len(template.retry_policy.delays_seconds):
            raise RuntimeError("Attempt retry budget is exhausted")
        delay = template.retry_policy.delays_seconds[delay_index]
        if not retry_step(
            self._connection,
            CurrentStep(required.instance_id, required.step_id),
            delay_seconds=delay,
        ):
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
        if not fail_step(
            self._connection,
            CurrentStep(required.instance_id, required.step_id),
            failure=failure,
        ):
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


def start_instance_record(*, database_url: str, request: StartInstance) -> StartInstanceReceipt:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return KernelControlTransaction(connection).start(request)


__all__ = [
    "KernelControlTransaction",
    "start_instance_record",
]
