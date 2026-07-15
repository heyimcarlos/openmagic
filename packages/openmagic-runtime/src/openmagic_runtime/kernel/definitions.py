"""Closed immutable Workflow Definition authoring and registration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest, canonical_value

ValueType = Literal["string", "integer", "uuid", "date"]


@dataclass(frozen=True)
class FieldContract:
    name: str
    value_type: ValueType


@dataclass(frozen=True)
class FieldBinding:
    target: str
    source: str


@dataclass(frozen=True)
class DefinitionIdentity:
    key: str
    version: int


@dataclass(frozen=True)
class RetryPolicy:
    delays_seconds: tuple[int, ...]

    @property
    def max_attempts(self) -> int:
        return len(self.delays_seconds) + 1


@dataclass(frozen=True)
class StepTemplate:
    key: str
    executor_key: str
    input_contract: tuple[FieldContract, ...]
    observation_contract: tuple[FieldContract, ...]
    output_contract: tuple[FieldContract, ...]
    lease_seconds: int
    maximum_attempt_seconds: int
    retry_policy: RetryPolicy


@dataclass(frozen=True)
class WaitTemplate:
    key: str
    signal_type: str
    input_contract: tuple[FieldContract, ...]


@dataclass(frozen=True)
class RouteOutput:
    slot: str
    kind: Literal["step", "wait"]
    template_key: str
    input_bindings: tuple[FieldBinding, ...]
    depends_on_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class Route:
    key: str
    activation: Literal["start", "step", "signal", "command"]
    outputs: tuple[RouteOutput, ...]
    activation_contract: tuple[FieldContract, ...] = ()
    source_template_key: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    identity: DefinitionIdentity
    instance_input_contract: tuple[FieldContract, ...]
    step_templates: tuple[StepTemplate, ...]
    wait_templates: tuple[WaitTemplate, ...]
    routes: tuple[Route, ...]


class DefinitionConflict(RuntimeError):
    pass


def validate_payload(payload: dict[str, Any], contract: tuple[FieldContract, ...]) -> None:
    expected = {field.name for field in contract}
    if set(payload) != expected:
        raise ValueError(f"payload fields do not match contract: {sorted(expected)}")
    for field in contract:
        value = payload[field.name]
        if field.value_type == "string":
            valid = isinstance(value, str)
        elif field.value_type == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif field.value_type == "uuid":
            try:
                valid = isinstance(value, str) and str(UUID(value)) == value
            except ValueError:
                valid = False
        elif field.value_type == "date":
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
        else:
            valid = False
        if not valid:
            raise ValueError(f"payload field {field.name!r} does not match {field.value_type!r}")


def _validate_acyclic(dependencies: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(slot: str) -> None:
        if slot in visiting:
            raise ValueError("Route dependencies must be acyclic")
        if slot in visited:
            return
        visiting.add(slot)
        for dependency in dependencies[slot]:
            visit(dependency)
        visiting.remove(slot)
        visited.add(slot)

    for slot in dependencies:
        visit(slot)


def validate_definition(definition: WorkflowDefinition) -> None:
    stable_key = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    if definition.identity.version <= 0 or not stable_key.fullmatch(definition.identity.key):
        raise ValueError("Definition identity is invalid")
    contracts = [definition.instance_input_contract]
    contracts.extend(template.input_contract for template in definition.step_templates)
    contracts.extend(template.observation_contract for template in definition.step_templates)
    contracts.extend(template.output_contract for template in definition.step_templates)
    contracts.extend(template.input_contract for template in definition.wait_templates)
    contracts.extend(route.activation_contract for route in definition.routes)
    for contract in contracts:
        names = [field.name for field in contract]
        if len(names) != len(set(names)) or any(
            field.value_type not in {"string", "integer", "uuid", "date"} for field in contract
        ):
            raise ValueError("Field contracts must have unique names and known value types")
    step_keys = [item.key for item in definition.step_templates]
    wait_keys = [item.key for item in definition.wait_templates]
    route_keys = [item.key for item in definition.routes]
    if len(step_keys) != len(set(step_keys)) or len(wait_keys) != len(set(wait_keys)):
        raise ValueError("Definition template keys must be unique")
    if len(route_keys) != len(set(route_keys)):
        raise ValueError("Definition Route keys must be unique")
    starts = [route for route in definition.routes if route.activation == "start"]
    if len(starts) != 1 or starts[0].key != "start":
        raise ValueError("Definition must declare exactly one start Route")
    for template in definition.step_templates:
        if template.lease_seconds <= 0 or template.maximum_attempt_seconds <= 0:
            raise ValueError("Step timing must be positive")
        if template.lease_seconds > template.maximum_attempt_seconds:
            raise ValueError("Step lease cannot exceed maximum Attempt duration")
        if any(delay < 0 for delay in template.retry_policy.delays_seconds):
            raise ValueError("Retry delays cannot be negative")
    known = set(step_keys) | set(wait_keys)
    step_templates = {item.key: item for item in definition.step_templates}
    wait_templates = {item.key: item for item in definition.wait_templates}
    for route in definition.routes:
        if route.activation not in {"start", "step", "signal", "command"}:
            raise ValueError("Route activation is unknown")
        if not route.outputs:
            raise ValueError("Routes must materialize a finite non-empty batch")
        if route.activation == "step":
            if route.source_template_key not in step_templates:
                raise ValueError("Step Route must name its exact source Step Template")
        elif route.source_template_key is not None:
            raise ValueError("Only a Step Route may name a source Step Template")
        slots = [output.slot for output in route.outputs]
        if len(slots) != len(set(slots)):
            raise ValueError("Route output slots must be unique")
        for output in route.outputs:
            if output.template_key not in known:
                raise ValueError("Route references an unknown template")
            if output.kind == "step":
                template = step_templates.get(output.template_key)
            elif output.kind == "wait":
                template = wait_templates.get(output.template_key)
            else:
                raise ValueError("Route output kind is unknown")
            if template is None:
                raise ValueError("Route output kind does not match its template")
            if output.kind == "wait" and output.depends_on_slots:
                raise ValueError("Wait output cannot declare Step dependencies")
            target_contract = {field.name: field for field in template.input_contract}
            activation_contract = {field.name: field for field in route.activation_contract}
            binding_targets = [binding.target for binding in output.input_bindings]
            if set(binding_targets) != set(target_contract) or len(binding_targets) != len(
                set(binding_targets)
            ):
                raise ValueError("Route bindings must cover the exact template input contract")
            for binding in output.input_bindings:
                source = activation_contract.get(binding.source)
                target = target_contract[binding.target]
                if source is None or source.value_type != target.value_type:
                    raise ValueError("Route binding is not schema-compatible")
            if not set(output.depends_on_slots).issubset(set(slots)):
                raise ValueError("Route dependency references an unknown output slot")
        dependencies = {output.slot: set(output.depends_on_slots) for output in route.outputs}
        _validate_acyclic(dependencies)


class DefinitionCatalog:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def register(self, definition: WorkflowDefinition) -> str:
        validate_definition(definition)
        manifest = canonical_value(definition)
        digest = canonical_digest(manifest)
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            inserted = connection.execute(
                "INSERT INTO openmagic_runtime.workflow_definitions "
                "(definition_key, definition_version, manifest, manifest_digest) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING manifest_digest",
                (
                    definition.identity.key,
                    definition.identity.version,
                    Jsonb(manifest),
                    digest,
                ),
            ).fetchone()
            if inserted is None:
                existing = connection.execute(
                    "SELECT manifest_digest FROM openmagic_runtime.workflow_definitions "
                    "WHERE definition_key = %s AND definition_version = %s FOR UPDATE",
                    (definition.identity.key, definition.identity.version),
                ).fetchone()
                if existing is None or existing[0] != digest:
                    raise DefinitionConflict("Definition identity has different registered content")
        return digest


def definition_from_manifest(manifest: dict[str, Any]) -> WorkflowDefinition:
    return WorkflowDefinition(
        identity=DefinitionIdentity(**manifest["identity"]),
        instance_input_contract=tuple(
            FieldContract(**item) for item in manifest["instance_input_contract"]
        ),
        step_templates=tuple(
            StepTemplate(
                key=item["key"],
                executor_key=item["executor_key"],
                input_contract=tuple(FieldContract(**field) for field in item["input_contract"]),
                observation_contract=tuple(
                    FieldContract(**field) for field in item["observation_contract"]
                ),
                output_contract=tuple(FieldContract(**field) for field in item["output_contract"]),
                lease_seconds=item["lease_seconds"],
                maximum_attempt_seconds=item["maximum_attempt_seconds"],
                retry_policy=RetryPolicy(tuple(item["retry_policy"]["delays_seconds"])),
            )
            for item in manifest["step_templates"]
        ),
        wait_templates=tuple(
            WaitTemplate(
                key=item["key"],
                signal_type=item["signal_type"],
                input_contract=tuple(FieldContract(**field) for field in item["input_contract"]),
            )
            for item in manifest["wait_templates"]
        ),
        routes=tuple(
            Route(
                key=item["key"],
                activation=item["activation"],
                activation_contract=tuple(
                    FieldContract(**field) for field in item["activation_contract"]
                ),
                source_template_key=item["source_template_key"],
                outputs=tuple(
                    RouteOutput(
                        slot=output["slot"],
                        kind=output["kind"],
                        template_key=output["template_key"],
                        input_bindings=tuple(
                            FieldBinding(**binding) for binding in output["input_bindings"]
                        ),
                        depends_on_slots=tuple(output["depends_on_slots"]),
                    )
                    for output in item["outputs"]
                ),
            )
            for item in manifest["routes"]
        ),
    )


def verified_definition(manifest: dict[str, Any], expected_digest: str) -> WorkflowDefinition:
    if canonical_digest(manifest) != expected_digest:
        raise DefinitionConflict("Registered Definition manifest digest does not match content")
    definition = definition_from_manifest(manifest)
    validate_definition(definition)
    return definition


__all__ = [
    "DefinitionCatalog",
    "DefinitionConflict",
    "DefinitionIdentity",
    "FieldBinding",
    "FieldContract",
    "RetryPolicy",
    "Route",
    "RouteOutput",
    "StepTemplate",
    "ValueType",
    "WaitTemplate",
    "WorkflowDefinition",
    "validate_definition",
    "validate_payload",
    "verified_definition",
]
