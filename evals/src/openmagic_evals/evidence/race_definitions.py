"""Canonical Workflow Definitions used by isolated race scenarios."""

from __future__ import annotations

from openmagic_runtime.kernel.definitions import (
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    RetryPolicy,
    Route,
    RouteOutput,
    StepTemplate,
    WaitTemplate,
    WorkflowDefinition,
)


def _step(key: str, executor_key: str, fields: tuple[FieldContract, ...]) -> StepTemplate:
    return StepTemplate(
        key=key,
        executor_key=executor_key,
        input_contract=fields,
        observation_contract=fields,
        output_contract=fields,
        lease_seconds=2,
        maximum_attempt_seconds=5,
        retry_policy=RetryPolicy(()),
    )


def _transition_definition() -> WorkflowDefinition:
    fields = (FieldContract("value", "string"),)
    return WorkflowDefinition(
        identity=DefinitionIdentity("eval.issue71_transition_race", 1),
        instance_input_contract=fields,
        step_templates=(
            _step("origin", "eval.issue71_origin.v1", fields),
            _step("finish", "eval.issue71_finish.v1", fields),
        ),
        wait_templates=(),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=fields,
                outputs=(
                    RouteOutput(
                        slot="origin",
                        kind="step",
                        template_key="origin",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
            Route(
                key="finish_after_origin",
                activation="step",
                activation_contract=fields,
                source_template_key="origin",
                outputs=(
                    RouteOutput(
                        slot="finish",
                        kind="step",
                        template_key="finish",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
        ),
    )


def _signal_definition() -> WorkflowDefinition:
    fields = (FieldContract("value", "string"),)

    def outputs(slot: str) -> tuple[RouteOutput, ...]:
        return (
            RouteOutput(
                slot=slot,
                kind="step",
                template_key="winner",
                input_bindings=(FieldBinding("value", "value"),),
            ),
        )

    return WorkflowDefinition(
        identity=DefinitionIdentity("eval.issue71_signal_race", 1),
        instance_input_contract=fields,
        step_templates=(_step("winner", "eval.issue71_signal_winner.v1", fields),),
        wait_templates=(
            WaitTemplate(
                key="decision",
                signal_type="eval.issue71.decision",
                input_contract=fields,
            ),
        ),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=fields,
                outputs=(
                    RouteOutput(
                        slot="decision",
                        kind="wait",
                        template_key="decision",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
            Route(
                key="approve",
                activation="signal",
                activation_contract=fields,
                outputs=outputs("approved"),
            ),
            Route(
                key="revise",
                activation="signal",
                activation_contract=fields,
                outputs=outputs("revision"),
            ),
        ),
    )


def _release_signal_definition() -> WorkflowDefinition:
    subject = (FieldContract("subject_id", "uuid"),)
    result = (FieldContract("result", "string"),)

    def output(slot: str) -> tuple[RouteOutput, ...]:
        return (
            RouteOutput(
                slot=slot,
                kind="step",
                template_key="winner",
                input_bindings=(FieldBinding("subject_id", "subject_id"),),
            ),
        )

    return WorkflowDefinition(
        identity=DefinitionIdentity("eval.signal_race", 1),
        instance_input_contract=subject,
        step_templates=(
            StepTemplate(
                key="winner",
                executor_key="eval.signal_winner.v1",
                input_contract=subject,
                observation_contract=result,
                output_contract=result,
                lease_seconds=1,
                maximum_attempt_seconds=2,
                retry_policy=RetryPolicy(()),
            ),
        ),
        wait_templates=(
            WaitTemplate(
                key="decision",
                signal_type="eval.signal.decision",
                input_contract=subject,
            ),
        ),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=subject,
                outputs=(
                    RouteOutput(
                        slot="decision",
                        kind="wait",
                        template_key="decision",
                        input_bindings=(FieldBinding("subject_id", "subject_id"),),
                    ),
                ),
            ),
            Route(
                key="approve",
                activation="signal",
                activation_contract=subject,
                outputs=output("approved"),
            ),
            Route(
                key="revise",
                activation="signal",
                activation_contract=subject,
                outputs=output("revision"),
            ),
        ),
    )


TRANSITION_RACE_DEFINITION = _transition_definition()
SIGNAL_RACE_DEFINITION = _signal_definition()
SIGNAL_RELEASE_DEFINITION = _release_signal_definition()


def evidence_race_definitions() -> tuple[WorkflowDefinition, WorkflowDefinition]:
    return TRANSITION_RACE_DEFINITION, SIGNAL_RACE_DEFINITION


__all__ = [
    "SIGNAL_RACE_DEFINITION",
    "SIGNAL_RELEASE_DEFINITION",
    "TRANSITION_RACE_DEFINITION",
    "evidence_race_definitions",
]
