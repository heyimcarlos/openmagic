"""Closed Workflow Definition for Example Insurance renewal drafting."""

from __future__ import annotations

from openmagic_runtime.kernel.definitions import (
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    Route,
    RouteOutput,
    StepTemplate,
    ValueType,
    WaitTemplate,
    WorkflowDefinition,
)

from example_insurance.renewal_policies import RENEWAL_ATTEMPT_RETRY_POLICY


def _fields(*items: tuple[str, ValueType]) -> tuple[FieldContract, ...]:
    return tuple(FieldContract(name, value_type) for name, value_type in items)


def _bindings(*names: str) -> tuple[FieldBinding, ...]:
    return tuple(FieldBinding(name, name) for name in names)


POLICY_FIELDS = _fields(
    ("policy_number", "string"),
    ("policyholder_name", "string"),
    ("renewal_date", "date"),
    ("expiring_premium_cents", "integer"),
)

RENEWAL_DEFINITION = WorkflowDefinition(
    identity=DefinitionIdentity("example_insurance.renewal_outreach", 1),
    instance_input_contract=_fields(
        ("workflow_id", "uuid"),
        ("thread_id", "uuid"),
        ("policy_id", "uuid"),
    ),
    step_templates=(
        StepTemplate(
            key="gather_renewal_facts",
            executor_key="example_insurance.renewal_facts.v1",
            input_contract=(FieldContract("policy_id", "uuid"), *POLICY_FIELDS),
            observation_contract=POLICY_FIELDS,
            output_contract=POLICY_FIELDS,
            lease_seconds=1,
            maximum_attempt_seconds=5,
            retry_policy=RENEWAL_ATTEMPT_RETRY_POLICY,
        ),
        StepTemplate(
            key="draft_renewal_email",
            executor_key="example_insurance.renewal_draft_agent.v1",
            input_contract=(
                FieldContract("workflow_id", "uuid"),
                FieldContract("thread_id", "uuid"),
                *POLICY_FIELDS,
            ),
            observation_contract=_fields(("subject", "string"), ("body", "string")),
            output_contract=_fields(("draft_id", "uuid")),
            lease_seconds=1,
            maximum_attempt_seconds=5,
            retry_policy=RENEWAL_ATTEMPT_RETRY_POLICY,
        ),
    ),
    wait_templates=(
        WaitTemplate(
            key="renewal_draft_approval",
            signal_type="renewal.draft.decision",
            input_contract=_fields(("workflow_id", "uuid"), ("draft_id", "uuid")),
        ),
    ),
    routes=(
        Route(
            key="start",
            activation="start",
            activation_contract=(FieldContract("policy_id", "uuid"), *POLICY_FIELDS),
            outputs=(
                RouteOutput(
                    slot="facts",
                    kind="step",
                    template_key="gather_renewal_facts",
                    input_bindings=_bindings(
                        "policy_id",
                        "policy_number",
                        "policyholder_name",
                        "renewal_date",
                        "expiring_premium_cents",
                    ),
                ),
            ),
        ),
        Route(
            key="draft_after_facts",
            activation="step",
            activation_contract=(
                FieldContract("workflow_id", "uuid"),
                FieldContract("thread_id", "uuid"),
                *POLICY_FIELDS,
            ),
            source_template_key="gather_renewal_facts",
            outputs=(
                RouteOutput(
                    slot="draft",
                    kind="step",
                    template_key="draft_renewal_email",
                    input_bindings=_bindings(
                        "workflow_id",
                        "thread_id",
                        "policy_number",
                        "policyholder_name",
                        "renewal_date",
                        "expiring_premium_cents",
                    ),
                ),
            ),
        ),
        Route(
            key="await_approval",
            activation="step",
            activation_contract=_fields(("workflow_id", "uuid"), ("draft_id", "uuid")),
            source_template_key="draft_renewal_email",
            outputs=(
                RouteOutput(
                    slot="approval",
                    kind="wait",
                    template_key="renewal_draft_approval",
                    input_bindings=_bindings("workflow_id", "draft_id"),
                ),
            ),
        ),
    ),
)


__all__ = ["RENEWAL_DEFINITION"]
