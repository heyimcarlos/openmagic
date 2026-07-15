"""Qualified application policy for renewal drafting orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from uuid import UUID

from openmagic_runtime.delivery import DeliveryRetryPolicy
from openmagic_runtime.kernel.definitions import RetryPolicy

RENEWAL_ATTEMPT_RETRY_POLICY = RetryPolicy((0, 0))


@dataclass(frozen=True)
class RouteDecision:
    outcome_route: str
    output: dict[str, Any]
    route_input: dict[str, Any]


@dataclass(frozen=True)
class RecoveryDecision:
    action: Literal["retry", "fail"]
    failure: dict[str, Any] | None = None


class RenewalWorkflowPolicy:
    definition_key = "example_insurance.renewal_outreach"
    definition_version = 2

    def facts_succeeded(
        self,
        *,
        workflow_id: UUID,
        thread_id: UUID,
        observation: dict[str, Any],
    ) -> RouteDecision:
        return RouteDecision(
            outcome_route="draft_after_facts",
            output=dict(observation),
            route_input={
                "workflow_id": str(workflow_id),
                "thread_id": str(thread_id),
                "revision_instruction": "",
                **observation,
            },
        )

    def draft_succeeded(
        self,
        *,
        workflow_id: UUID,
        draft_id: UUID,
        presentation_fingerprint: str,
        recipient_email: str,
        subject: str,
        body: str,
    ) -> RouteDecision:
        return RouteDecision(
            outcome_route="await_approval",
            output={
                "draft_id": str(draft_id),
                "presentation_fingerprint": presentation_fingerprint,
            },
            route_input={
                "workflow_id": str(workflow_id),
                "draft_id": str(draft_id),
                "presentation_fingerprint": presentation_fingerprint,
                "recipient_email": recipient_email,
                "subject": subject,
                "body": body,
            },
        )

    @staticmethod
    def expired_attempt(*, template_key: str, attempt_number: int) -> RecoveryDecision:
        if template_key not in {
            "gather_renewal_facts",
            "draft_renewal_email",
            "reconcile_renewal_email",
        }:
            return RecoveryDecision(
                action="fail",
                failure={"class": "unknown_step_template"},
            )
        if attempt_number < RENEWAL_ATTEMPT_RETRY_POLICY.max_attempts:
            return RecoveryDecision(action="retry")
        return RecoveryDecision(
            action="fail",
            failure={"class": "attempt_budget_exhausted"},
        )


class RenewalDeliveryPolicy:
    audience: ClassVar[dict[str, str]] = {
        "kind": "workflow_role",
        "identifier": "broker",
    }
    message_author: ClassVar[dict[str, str]] = {
        "kind": "system",
        "identifier": "example-insurance",
    }
    retry_policy = DeliveryRetryPolicy(
        version=1,
        max_attempts=3,
        delays_seconds=(0, 1),
        lease_seconds=1,
        retryable_failure_classes=("transient_rendering", "transient_database"),
        terminal_failure_classes=("invalid_content", "policy_rejected"),
    )

    def content_descriptor(self, observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "template_key": "example_insurance.renewal_draft.v1",
            "template_version": 1,
            "locale": "en-CA",
            "input": dict(observation),
        }

    @staticmethod
    def render_message(observation: dict[str, Any]) -> str:
        return f"{observation['subject']}\n\n{observation['body']}"


class RenewalCompletionPolicy:
    @staticmethod
    def is_complete(
        *,
        required_step_states: tuple[str, ...],
        effect_certainties: tuple[str, ...],
    ) -> bool:
        return (
            bool(required_step_states)
            and all(state == "succeeded" for state in required_step_states)
            and bool(effect_certainties)
            and all(certainty == "applied" for certainty in effect_certainties)
        )


class RenewalExternalEffectPolicy:
    @staticmethod
    def authorize_dispatch(*, lifecycle: str, existing_certainty: str | None) -> None:
        if lifecycle != "active":
            raise RuntimeError("Renewal Workflow no longer authorizes dispatch")
        if existing_certainty not in {None, "not_applied"}:
            raise RuntimeError("External Effect is not safe to dispatch")

    @staticmethod
    def result_disposition(
        *, classification: str, attempt_number: int, maximum_attempts: int
    ) -> Literal["succeed", "retry", "fail", "defer"]:
        if classification == "applied":
            return "succeed"
        if classification == "not_applied":
            return "retry" if attempt_number < maximum_attempts else "fail"
        return "defer"

    @staticmethod
    def reconciliation_disposition(
        *,
        classification: str,
        effect_attempt_number: int,
        reconciliation_attempt_number: int,
        maximum_effect_attempts: int,
        maximum_reconciliation_attempts: int,
    ) -> Literal[
        "confirm",
        "retry_effect",
        "fail_effect",
        "retry_reconciliation",
        "defer",
    ]:
        if classification == "applied":
            return "confirm"
        if classification == "not_applied":
            if effect_attempt_number < maximum_effect_attempts:
                return "retry_effect"
            return "fail_effect"
        if reconciliation_attempt_number < maximum_reconciliation_attempts:
            return "retry_reconciliation"
        return "defer"


__all__ = [
    "RENEWAL_ATTEMPT_RETRY_POLICY",
    "RecoveryDecision",
    "RenewalCompletionPolicy",
    "RenewalDeliveryPolicy",
    "RenewalExternalEffectPolicy",
    "RenewalWorkflowPolicy",
    "RouteDecision",
]
