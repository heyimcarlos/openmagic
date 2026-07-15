from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from openmagic_runtime.kernel.definitions import (
    DefinitionConflict,
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    RetryPolicy,
    Route,
    RouteOutput,
    StepTemplate,
    WorkflowDefinition,
    definition_manifest,
    definition_manifest_digest,
    validate_definition,
    validate_payload,
    verified_definition,
)


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        identity=DefinitionIdentity("example.workflow", 1),
        instance_input_contract=(FieldContract("subject_id", "uuid"),),
        step_templates=(
            StepTemplate(
                key="first",
                executor_key="example.first.v1",
                input_contract=(FieldContract("subject_id", "uuid"),),
                observation_contract=(FieldContract("value", "string"),),
                output_contract=(FieldContract("value", "string"),),
                lease_seconds=1,
                maximum_attempt_seconds=2,
                retry_policy=RetryPolicy(()),
            ),
            StepTemplate(
                key="second",
                executor_key="example.second.v1",
                input_contract=(FieldContract("subject_id", "uuid"),),
                observation_contract=(FieldContract("value", "string"),),
                output_contract=(FieldContract("value", "string"),),
                lease_seconds=1,
                maximum_attempt_seconds=2,
                retry_policy=RetryPolicy(()),
            ),
        ),
        wait_templates=(),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=(FieldContract("subject_id", "uuid"),),
                outputs=(
                    RouteOutput(
                        "first",
                        "step",
                        "first",
                        (FieldBinding("subject_id", "subject_id"),),
                    ),
                    RouteOutput(
                        "second",
                        "step",
                        "second",
                        (FieldBinding("subject_id", "subject_id"),),
                        ("first",),
                    ),
                ),
            ),
        ),
    )


def test_closed_definition_accepts_acyclic_exact_contracts() -> None:
    validate_definition(_definition())


def test_closed_definition_rejects_cycles_and_kind_mismatches() -> None:
    definition = _definition()
    start = definition.routes[0]
    cyclic = replace(
        definition,
        routes=(
            replace(
                start,
                outputs=(
                    replace(start.outputs[0], depends_on_slots=("second",)),
                    start.outputs[1],
                ),
            ),
        ),
    )
    kind_mismatch = replace(
        definition,
        routes=(
            replace(
                start,
                outputs=(replace(start.outputs[0], kind="wait"), start.outputs[1]),
            ),
        ),
    )

    with pytest.raises(ValueError, match="acyclic"):
        validate_definition(cyclic)
    with pytest.raises(ValueError, match="does not match"):
        validate_definition(kind_mismatch)


def test_closed_definition_rejects_schema_incompatible_bindings_and_values() -> None:
    definition = _definition()
    incompatible = replace(
        definition,
        routes=(
            replace(
                definition.routes[0],
                activation_contract=(FieldContract("subject_id", "string"),),
            ),
        ),
    )

    with pytest.raises(ValueError, match="schema-compatible"):
        validate_definition(incompatible)
    with pytest.raises(ValueError, match="does not match 'uuid'"):
        validate_payload(
            {"subject_id": "not-a-uuid"},
            definition.instance_input_contract,
        )


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "future_construct"),
        (("identity",), "edition"),
        (("step_templates", 0), "fallback_executor"),
        (("step_templates", 0, "retry_policy"), "retry_forever"),
        (("routes", 0, "outputs", 0, "input_bindings", 0), "transform"),
    ],
)
def test_closed_definition_fails_closed_on_unknown_fields(
    path: tuple[str | int, ...], field: str
) -> None:
    manifest = definition_manifest(_definition())
    target: Any = manifest
    for part in path:
        target = target[part]
    assert isinstance(target, dict)
    target[field] = True

    with pytest.raises(DefinitionConflict, match="fields are not closed"):
        verified_definition(manifest, definition_manifest_digest(manifest))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("identity", "version"), True),
        (("step_templates", 0, "lease_seconds"), True),
        (("step_templates", 0, "maximum_attempt_seconds"), True),
        (("step_templates", 0, "retry_policy", "delays_seconds"), [True]),
    ],
)
def test_closed_definition_rejects_boolean_integer_fields(
    path: tuple[str | int, ...], value: object
) -> None:
    manifest = definition_manifest(_definition())
    target: Any = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=r"invalid|positive|negative"):
        verified_definition(manifest, definition_manifest_digest(manifest))


def test_closed_definition_requires_canonical_array_shapes() -> None:
    manifest = definition_manifest(_definition())
    manifest["routes"] = tuple(manifest["routes"])

    with pytest.raises(DefinitionConflict, match="must be an array"):
        verified_definition(manifest, definition_manifest_digest(manifest))
