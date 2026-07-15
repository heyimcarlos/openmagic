from __future__ import annotations

from dataclasses import replace

import pytest
from openmagic_runtime.kernel.definitions import (
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    RetryPolicy,
    Route,
    RouteOutput,
    StepTemplate,
    WorkflowDefinition,
    validate_definition,
    validate_payload,
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
