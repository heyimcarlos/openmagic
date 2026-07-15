"""Closed immutable Workflow Definition authoring and registration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast
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


def definition_manifest(definition: WorkflowDefinition) -> dict[str, Any]:
    value = canonical_value(definition)
    if not isinstance(value, dict):
        raise TypeError("Workflow Definition did not encode as an object")
    return cast(dict[str, Any], value)


def definition_manifest_digest(manifest: dict[str, Any]) -> str:
    """Return the canonical digest used to verify a persisted Definition manifest."""
    return canonical_digest(manifest)


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
    local_key = re.compile(r"^[a-z][a-z0-9_]*$")
    if (
        type(definition.identity) is not DefinitionIdentity
        or type(definition.instance_input_contract) is not tuple
        or type(definition.step_templates) is not tuple
        or type(definition.wait_templates) is not tuple
        or type(definition.routes) is not tuple
        or type(definition.identity.version) is not int
        or definition.identity.version <= 0
        or not isinstance(definition.identity.key, str)
        or not stable_key.fullmatch(definition.identity.key)
    ):
        raise ValueError("Definition identity is invalid")
    contracts = [definition.instance_input_contract]
    contracts.extend(template.input_contract for template in definition.step_templates)
    contracts.extend(template.observation_contract for template in definition.step_templates)
    contracts.extend(template.output_contract for template in definition.step_templates)
    contracts.extend(template.input_contract for template in definition.wait_templates)
    contracts.extend(route.activation_contract for route in definition.routes)
    for contract in contracts:
        names = [field.name for field in contract]
        if (
            type(contract) is not tuple
            or len(names) != len(set(names))
            or any(
                type(field) is not FieldContract
                or not isinstance(field.name, str)
                or not local_key.fullmatch(field.name)
                or not isinstance(field.value_type, str)
                or field.value_type not in {"string", "integer", "uuid", "date"}
                for field in contract
            )
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
        if (
            type(template) is not StepTemplate
            or not isinstance(template.key, str)
            or not local_key.fullmatch(template.key)
            or not isinstance(template.executor_key, str)
            or not stable_key.fullmatch(template.executor_key)
        ):
            raise ValueError("Step Template identity is invalid")
        if (
            type(template.lease_seconds) is not int
            or type(template.maximum_attempt_seconds) is not int
            or template.lease_seconds <= 0
            or template.maximum_attempt_seconds <= 0
        ):
            raise ValueError("Step timing must be positive")
        if template.lease_seconds > template.maximum_attempt_seconds:
            raise ValueError("Step lease cannot exceed maximum Attempt duration")
        if (
            type(template.retry_policy) is not RetryPolicy
            or type(template.retry_policy.delays_seconds) is not tuple
        ):
            raise ValueError("Retry Policy contract is invalid")
        if any(
            type(delay) is not int or delay < 0 for delay in template.retry_policy.delays_seconds
        ):
            raise ValueError("Retry delays cannot be negative")
    known = set(step_keys) | set(wait_keys)
    step_templates = {item.key: item for item in definition.step_templates}
    wait_templates = {item.key: item for item in definition.wait_templates}
    for template in definition.wait_templates:
        if (
            type(template) is not WaitTemplate
            or not isinstance(template.key, str)
            or not local_key.fullmatch(template.key)
            or not isinstance(template.signal_type, str)
            or not stable_key.fullmatch(template.signal_type)
        ):
            raise ValueError("Wait Template identity is invalid")
    for route in definition.routes:
        if (
            type(route) is not Route
            or not isinstance(route.key, str)
            or not local_key.fullmatch(route.key)
            or not isinstance(route.activation, str)
            or (
                route.source_template_key is not None
                and not isinstance(route.source_template_key, str)
            )
        ):
            raise ValueError("Route identity is invalid")
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
            if (
                type(output) is not RouteOutput
                or not isinstance(output.slot, str)
                or not local_key.fullmatch(output.slot)
                or not isinstance(output.kind, str)
                or not isinstance(output.template_key, str)
                or type(output.input_bindings) is not tuple
                or type(output.depends_on_slots) is not tuple
                or any(
                    type(binding) is not FieldBinding
                    or not isinstance(binding.target, str)
                    or not local_key.fullmatch(binding.target)
                    or not isinstance(binding.source, str)
                    or not local_key.fullmatch(binding.source)
                    for binding in output.input_bindings
                )
                or any(
                    not isinstance(dependency, str) or not local_key.fullmatch(dependency)
                    for dependency in output.depends_on_slots
                )
            ):
                raise ValueError("Route output contract is invalid")
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
        manifest = definition_manifest(definition)
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
    def exact(value: object, fields: set[str], path: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise DefinitionConflict(f"Definition {path} must be an object")
        actual = set(value)
        if actual != fields:
            unknown = sorted(actual - fields)
            missing = sorted(fields - actual)
            raise DefinitionConflict(
                f"Definition {path} fields are not closed: unknown={unknown}, missing={missing}"
            )
        return cast(dict[str, Any], value)

    def array(value: object, path: str) -> list[Any]:
        if not isinstance(value, list):
            raise DefinitionConflict(f"Definition {path} must be an array")
        return value

    def field_contract(value: object, path: str) -> FieldContract:
        item = exact(value, {"name", "value_type"}, path)
        return FieldContract(name=item["name"], value_type=item["value_type"])

    def field_binding(value: object, path: str) -> FieldBinding:
        item = exact(value, {"target", "source"}, path)
        return FieldBinding(target=item["target"], source=item["source"])

    root = exact(
        manifest,
        {"identity", "instance_input_contract", "step_templates", "wait_templates", "routes"},
        "manifest",
    )
    identity = exact(root["identity"], {"key", "version"}, "identity")
    return WorkflowDefinition(
        identity=DefinitionIdentity(key=identity["key"], version=identity["version"]),
        instance_input_contract=tuple(
            field_contract(item, f"instance_input_contract[{index}]")
            for index, item in enumerate(
                array(root["instance_input_contract"], "instance_input_contract")
            )
        ),
        step_templates=tuple(
            StepTemplate(
                key=step["key"],
                executor_key=step["executor_key"],
                input_contract=tuple(
                    field_contract(field, f"step_templates[{index}].input_contract[{field_index}]")
                    for field_index, field in enumerate(
                        array(step["input_contract"], f"step_templates[{index}].input_contract")
                    )
                ),
                observation_contract=tuple(
                    field_contract(
                        field,
                        f"step_templates[{index}].observation_contract[{field_index}]",
                    )
                    for field_index, field in enumerate(
                        array(
                            step["observation_contract"],
                            f"step_templates[{index}].observation_contract",
                        )
                    )
                ),
                output_contract=tuple(
                    field_contract(field, f"step_templates[{index}].output_contract[{field_index}]")
                    for field_index, field in enumerate(
                        array(step["output_contract"], f"step_templates[{index}].output_contract")
                    )
                ),
                lease_seconds=step["lease_seconds"],
                maximum_attempt_seconds=step["maximum_attempt_seconds"],
                retry_policy=RetryPolicy(
                    tuple(
                        array(
                            retry["delays_seconds"],
                            f"step_templates[{index}].retry_policy.delays_seconds",
                        )
                    )
                ),
            )
            for index, raw_step in enumerate(array(root["step_templates"], "step_templates"))
            for step in (
                exact(
                    raw_step,
                    {
                        "key",
                        "executor_key",
                        "input_contract",
                        "observation_contract",
                        "output_contract",
                        "lease_seconds",
                        "maximum_attempt_seconds",
                        "retry_policy",
                    },
                    f"step_templates[{index}]",
                ),
            )
            for retry in (
                exact(
                    step["retry_policy"],
                    {"delays_seconds"},
                    f"step_templates[{index}].retry_policy",
                ),
            )
        ),
        wait_templates=tuple(
            WaitTemplate(
                key=wait["key"],
                signal_type=wait["signal_type"],
                input_contract=tuple(
                    field_contract(field, f"wait_templates[{index}].input_contract[{field_index}]")
                    for field_index, field in enumerate(
                        array(wait["input_contract"], f"wait_templates[{index}].input_contract")
                    )
                ),
            )
            for index, raw_wait in enumerate(array(root["wait_templates"], "wait_templates"))
            for wait in (
                exact(
                    raw_wait,
                    {"key", "signal_type", "input_contract"},
                    f"wait_templates[{index}]",
                ),
            )
        ),
        routes=tuple(
            Route(
                key=route["key"],
                activation=route["activation"],
                activation_contract=tuple(
                    field_contract(field, f"routes[{index}].activation_contract[{field_index}]")
                    for field_index, field in enumerate(
                        array(
                            route["activation_contract"],
                            f"routes[{index}].activation_contract",
                        )
                    )
                ),
                source_template_key=route["source_template_key"],
                outputs=tuple(
                    RouteOutput(
                        slot=output["slot"],
                        kind=output["kind"],
                        template_key=output["template_key"],
                        input_bindings=tuple(
                            field_binding(
                                binding,
                                f"routes[{index}].outputs[{output_index}].input_bindings[{binding_index}]",
                            )
                            for binding_index, binding in enumerate(
                                array(
                                    output["input_bindings"],
                                    f"routes[{index}].outputs[{output_index}].input_bindings",
                                )
                            )
                        ),
                        depends_on_slots=tuple(
                            array(
                                output["depends_on_slots"],
                                f"routes[{index}].outputs[{output_index}].depends_on_slots",
                            )
                        ),
                    )
                    for output_index, raw_output in enumerate(
                        array(route["outputs"], f"routes[{index}].outputs")
                    )
                    for output in (
                        exact(
                            raw_output,
                            {
                                "slot",
                                "kind",
                                "template_key",
                                "input_bindings",
                                "depends_on_slots",
                            },
                            f"routes[{index}].outputs[{output_index}]",
                        ),
                    )
                ),
            )
            for index, raw_route in enumerate(array(root["routes"], "routes"))
            for route in (
                exact(
                    raw_route,
                    {
                        "key",
                        "activation",
                        "activation_contract",
                        "source_template_key",
                        "outputs",
                    },
                    f"routes[{index}]",
                ),
            )
        ),
    )


def verified_definition(manifest: dict[str, Any], expected_digest: str) -> WorkflowDefinition:
    if definition_manifest_digest(manifest) != expected_digest:
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
    "definition_manifest",
    "definition_manifest_digest",
    "validate_definition",
    "validate_payload",
    "verified_definition",
]
