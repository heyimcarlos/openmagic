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
    ("policyholder_email", "string"),
    ("renewal_date", "date"),
    ("expiring_premium_cents", "integer"),
)

RENEWAL_DEFINITION = WorkflowDefinition(
    identity=DefinitionIdentity("example_insurance.renewal_outreach", 2),
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
                FieldContract("revision_instruction", "string"),
                *POLICY_FIELDS,
            ),
            observation_contract=_fields(("subject", "string"), ("body", "string")),
            output_contract=_fields(
                ("draft_id", "uuid"),
                ("presentation_fingerprint", "string"),
            ),
            lease_seconds=1,
            maximum_attempt_seconds=5,
            retry_policy=RENEWAL_ATTEMPT_RETRY_POLICY,
        ),
        StepTemplate(
            key="send_renewal_email",
            executor_key="example_insurance.email_provider.v1",
            input_contract=_fields(
                ("workflow_id", "uuid"),
                ("draft_id", "uuid"),
                ("approval_grant_id", "uuid"),
                ("effect_fingerprint", "string"),
                ("recipient_email", "string"),
                ("subject", "string"),
                ("body", "string"),
            ),
            observation_contract=_fields(
                ("classification", "string"),
                ("provider_request_id", "string"),
            ),
            output_contract=_fields(
                ("logical_effect_id", "uuid"),
                ("classification", "string"),
            ),
            lease_seconds=1,
            maximum_attempt_seconds=10,
            retry_policy=RENEWAL_ATTEMPT_RETRY_POLICY,
        ),
        StepTemplate(
            key="reconcile_renewal_email",
            executor_key="example_insurance.email_reconciliation.v1",
            input_contract=_fields(
                ("workflow_id", "uuid"),
                ("effect_step_id", "uuid"),
                ("basis_attempt_id", "uuid"),
                ("logical_effect_id", "uuid"),
                ("provider_idempotency_key", "string"),
            ),
            observation_contract=_fields(
                ("classification", "string"),
                ("provider_request_id", "string"),
            ),
            output_contract=_fields(
                ("logical_effect_id", "uuid"),
                ("classification", "string"),
            ),
            lease_seconds=1,
            maximum_attempt_seconds=10,
            retry_policy=RENEWAL_ATTEMPT_RETRY_POLICY,
        ),
    ),
    wait_templates=(
        WaitTemplate(
            key="renewal_draft_approval",
            signal_type="renewal.draft.decision",
            input_contract=_fields(
                ("workflow_id", "uuid"),
                ("draft_id", "uuid"),
                ("presentation_fingerprint", "string"),
                ("recipient_email", "string"),
                ("subject", "string"),
                ("body", "string"),
            ),
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
                        "policyholder_email",
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
                FieldContract("revision_instruction", "string"),
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
                        "revision_instruction",
                        "policy_number",
                        "policyholder_name",
                        "policyholder_email",
                        "renewal_date",
                        "expiring_premium_cents",
                    ),
                ),
            ),
        ),
        Route(
            key="await_approval",
            activation="step",
            activation_contract=_fields(
                ("workflow_id", "uuid"),
                ("draft_id", "uuid"),
                ("presentation_fingerprint", "string"),
                ("recipient_email", "string"),
                ("subject", "string"),
                ("body", "string"),
            ),
            source_template_key="draft_renewal_email",
            outputs=(
                RouteOutput(
                    slot="approval",
                    kind="wait",
                    template_key="renewal_draft_approval",
                    input_bindings=_bindings(
                        "workflow_id",
                        "draft_id",
                        "presentation_fingerprint",
                        "recipient_email",
                        "subject",
                        "body",
                    ),
                ),
            ),
        ),
        Route(
            key="approve_email",
            activation="signal",
            activation_contract=_fields(
                ("workflow_id", "uuid"),
                ("wait_id", "uuid"),
                ("draft_id", "uuid"),
                ("presentation_fingerprint", "string"),
                ("approval_grant_id", "uuid"),
                ("effect_fingerprint", "string"),
                ("recipient_email", "string"),
                ("subject", "string"),
                ("body", "string"),
            ),
            outputs=(
                RouteOutput(
                    slot="email_effect",
                    kind="step",
                    template_key="send_renewal_email",
                    input_bindings=_bindings(
                        "workflow_id",
                        "draft_id",
                        "approval_grant_id",
                        "effect_fingerprint",
                        "recipient_email",
                        "subject",
                        "body",
                    ),
                ),
            ),
        ),
        Route(
            key="revise_email",
            activation="signal",
            activation_contract=(
                FieldContract("workflow_id", "uuid"),
                FieldContract("wait_id", "uuid"),
                FieldContract("draft_id", "uuid"),
                FieldContract("presentation_fingerprint", "string"),
                FieldContract("recipient_email", "string"),
                FieldContract("subject", "string"),
                FieldContract("body", "string"),
                FieldContract("thread_id", "uuid"),
                FieldContract("revision_instruction", "string"),
                *POLICY_FIELDS,
            ),
            outputs=(
                RouteOutput(
                    slot="revision_draft",
                    kind="step",
                    template_key="draft_renewal_email",
                    input_bindings=_bindings(
                        "workflow_id",
                        "thread_id",
                        "revision_instruction",
                        "policy_number",
                        "policyholder_name",
                        "policyholder_email",
                        "renewal_date",
                        "expiring_premium_cents",
                    ),
                ),
            ),
        ),
        Route(
            key="reconcile_email",
            activation="step",
            activation_contract=_fields(
                ("workflow_id", "uuid"),
                ("effect_step_id", "uuid"),
                ("basis_attempt_id", "uuid"),
                ("logical_effect_id", "uuid"),
                ("provider_idempotency_key", "string"),
            ),
            source_template_key="send_renewal_email",
            outputs=(
                RouteOutput(
                    slot="reconciliation",
                    kind="step",
                    template_key="reconcile_renewal_email",
                    input_bindings=_bindings(
                        "workflow_id",
                        "effect_step_id",
                        "basis_attempt_id",
                        "logical_effect_id",
                        "provider_idempotency_key",
                    ),
                ),
            ),
        ),
    ),
)


__all__ = ["RENEWAL_DEFINITION"]
