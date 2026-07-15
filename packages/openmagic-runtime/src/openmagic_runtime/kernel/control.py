"""Transaction-scoped kernel control transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._trace import append_trace
from openmagic_runtime.kernel.definitions import Route, validate_payload, verified_definition
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


def _lock_open_instance(connection: Connection[tuple[Any, ...]], instance_id: UUID) -> None:
    row = connection.execute(
        "SELECT state FROM openmagic_runtime.instances WHERE instance_id = %s FOR UPDATE",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Instance not found")
    if row[0] != "open":
        raise RuntimeError("Instance is closed")


def _validate_disposition(
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


def _materialize_route(
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
        steps, waits = _materialize_route(
            self._connection,
            instance_id=instance_id,
            route=route,
            source_kind="command",
            source_id=request.command_id,
            route_input=request.route_input,
        )
        trace_event_id = uuid4()
        receipt_payload = {
            "instance_id": str(instance_id),
            "definition_key": request.definition_key,
            "definition_version": request.definition_version,
            "steps": {key: str(value) for key, value in steps.items()},
            "waits": {key: str(value) for key, value in waits.items()},
            "trace_event_id": str(trace_event_id),
            "trace_sequence": 1,
        }
        self._connection.execute(
            "UPDATE openmagic_runtime.instances SET last_trace_sequence = 1 WHERE instance_id = %s",
            (instance_id,),
        )
        self._connection.execute(
            "INSERT INTO openmagic_runtime.trace_events "
            "(trace_event_id, instance_id, sequence, event_type, schema_version, source_kind, "
            "source_id, input_digest, receipt) VALUES "
            "(%s, %s, 1, 'instance_started', 1, 'command_start', %s, %s, %s)",
            (
                trace_event_id,
                instance_id,
                request.command_id,
                source_digest,
                Jsonb(receipt_payload),
            ),
        )
        return StartInstanceReceipt(
            instance_id=instance_id,
            definition_key=request.definition_key,
            definition_version=request.definition_version,
            steps=steps,
            waits=waits,
            trace_event_id=trace_event_id,
            trace_sequence=1,
        )

    def succeed(
        self,
        required: DispositionRequired,
        *,
        output: dict[str, Any],
        outcome_route: str,
        route_input: dict[str, Any],
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        _lock_open_instance(self._connection, required.instance_id)
        _validate_disposition(
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
        route = next(item for item in definition.routes if item.key == outcome_route)
        if route.activation != "step":
            raise ValueError("Outcome Route is not a Step activation")
        if route.source_template_key != required.template_key:
            raise ValueError("Outcome Route does not accept this Step Template")
        validate_payload(route_input, route.activation_contract)
        steps, waits = _materialize_route(
            self._connection,
            instance_id=required.instance_id,
            route=route,
            source_kind="step",
            source_id=required.attempt_id,
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
            receipt=receipt,
        )
        required.consumed = True
        return steps, waits

    def retry(self, required: DispositionRequired) -> None:
        if required.consumed:
            raise RuntimeError("Attempt disposition was already consumed")
        _lock_open_instance(self._connection, required.instance_id)
        _validate_disposition(
            self._connection,
            required,
            expected_attempt_state="abandoned",
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
            receipt={"step_id": str(required.step_id), "delay_seconds": delay},
        )
        required.consumed = True

    def fail(self, required: DispositionRequired, *, failure: dict[str, Any]) -> None:
        if required.consumed:
            raise RuntimeError("Attempt disposition was already consumed")
        _lock_open_instance(self._connection, required.instance_id)
        _validate_disposition(
            self._connection,
            required,
            expected_attempt_state="abandoned",
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
            receipt={"step_id": str(required.step_id), "failure": failure},
        )
        required.consumed = True


def start_instance(*, database_url: str, request: StartInstance) -> StartInstanceReceipt:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return KernelControl(connection).start(request)


__all__ = ["KernelControl", "StartInstance", "StartInstanceReceipt", "start_instance"]
