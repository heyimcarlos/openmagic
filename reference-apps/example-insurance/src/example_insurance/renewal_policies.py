"""Qualified application policy for renewal drafting orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal
from uuid import UUID

from openmagic_runtime.delivery import DeliveryRetryPolicy


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
    definition_version = 1

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
                **observation,
            },
        )

    def draft_succeeded(self, *, workflow_id: UUID, draft_id: UUID) -> RouteDecision:
        return RouteDecision(
            outcome_route="await_approval",
            output={"draft_id": str(draft_id)},
            route_input={"workflow_id": str(workflow_id), "draft_id": str(draft_id)},
        )

    @staticmethod
    def expired_attempt(*, template_key: str, attempt_number: int) -> RecoveryDecision:
        if template_key not in {"gather_renewal_facts", "draft_renewal_email"}:
            return RecoveryDecision(
                action="fail",
                failure={"class": "unknown_step_template"},
            )
        if attempt_number < 3:
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
    """Defines completion without granting External Effect authority."""

    @staticmethod
    def is_complete(*, approval_wait_state: str, external_effect_count: int) -> bool:
        return approval_wait_state == "satisfied" and external_effect_count > 0


__all__ = [
    "RecoveryDecision",
    "RenewalCompletionPolicy",
    "RenewalDeliveryPolicy",
    "RenewalWorkflowPolicy",
    "RouteDecision",
]
