"""Closed deterministic Workflow Definition for verification delivery."""

from openmagic_runtime.kernel.definitions import (
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    Route,
    RouteOutput,
    StepTemplate,
    WorkflowDefinition,
)

from example_insurance.verification_policy import VERIFICATION_ATTEMPT_RETRY_POLICY

_DELIVERY_FIELDS = (
    FieldContract("challenge_id", "uuid"),
    FieldContract("protected_workflow_id", "uuid"),
    FieldContract("thread_id", "uuid"),
)

VERIFICATION_DEFINITION = WorkflowDefinition(
    identity=DefinitionIdentity("example_insurance.verification_delivery", 1),
    instance_input_contract=(
        FieldContract("workflow_id", "uuid"),
        *_DELIVERY_FIELDS,
    ),
    step_templates=(
        StepTemplate(
            key="deliver_verification_challenge",
            executor_key="example_insurance.verification_delivery.v1",
            input_contract=_DELIVERY_FIELDS,
            observation_contract=(FieldContract("challenge_id", "uuid"),),
            output_contract=(FieldContract("challenge_id", "uuid"),),
            lease_seconds=1,
            maximum_attempt_seconds=5,
            retry_policy=VERIFICATION_ATTEMPT_RETRY_POLICY,
        ),
    ),
    wait_templates=(),
    routes=(
        Route(
            key="start",
            activation="start",
            activation_contract=_DELIVERY_FIELDS,
            outputs=(
                RouteOutput(
                    slot="delivery",
                    kind="step",
                    template_key="deliver_verification_challenge",
                    input_bindings=tuple(
                        FieldBinding(field.name, field.name) for field in _DELIVERY_FIELDS
                    ),
                ),
            ),
        ),
    ),
)


__all__ = ["VERIFICATION_DEFINITION"]
