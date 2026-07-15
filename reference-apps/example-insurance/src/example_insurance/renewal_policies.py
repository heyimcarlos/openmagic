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


@dataclass(frozen=True)
class ApprovalDecisionFacts:
    lifecycle: str
    actor_matches: bool
    authority_revoked: bool
    wait_unsatisfied: bool
    presentation_exact: bool


ApprovalRejectionOutcome = Literal[
    "authority_revoked",
    "stale_presentation",
    "unauthorized_actor",
    "wait_already_satisfied",
]


@dataclass(frozen=True)
class ApprovalAcceptedDecision:
    route_key: Literal["approve_email", "revise_email"]


@dataclass(frozen=True)
class ApprovalRejectedDecision:
    outcome: ApprovalRejectionOutcome


ApprovalDecision = ApprovalAcceptedDecision | ApprovalRejectedDecision


@dataclass(frozen=True)
class EffectAuthorizationFacts:
    lifecycle_active: bool
    authority_revoked: bool
    grant_matches_step: bool
    fingerprint_matches_grant: bool
    grant_valid: bool
    grant_consumption_consistent: bool
    durable_claim_matches: bool
    durable_effect_matches: bool
    existing_certainty: str | None


@dataclass(frozen=True)
class CompletionStepFact:
    state: str
    has_accepted_output: bool


@dataclass(frozen=True)
class CompletionEffectFact:
    certainty: str
    has_applied_evidence: bool


@dataclass(frozen=True)
class CancellationFacts:
    lifecycle: str
    actor_authorized: bool
    dispatch_boundary_crossed: bool


class RenewalApprovalPolicy:
    @staticmethod
    def decide(
        *,
        decision_kind: Literal["approve", "request_revision"],
        facts: ApprovalDecisionFacts,
    ) -> ApprovalDecision:
        if facts.lifecycle != "active" or facts.authority_revoked:
            return ApprovalRejectedDecision("authority_revoked")
        if not facts.actor_matches:
            return ApprovalRejectedDecision("unauthorized_actor")
        if not facts.wait_unsatisfied:
            return ApprovalRejectedDecision("wait_already_satisfied")
        if not facts.presentation_exact:
            return ApprovalRejectedDecision("stale_presentation")
        route = "approve_email" if decision_kind == "approve" else "revise_email"
        return ApprovalAcceptedDecision(route)


class RenewalLifecyclePolicy:
    @staticmethod
    def authorizes_revocation(*, actor_kind: str, actor_id: str) -> bool:
        return actor_kind == "system" and actor_id == "authority-administrator"

    @staticmethod
    def actor_can_cancel(
        *,
        actor_kind: str,
        actor_id: str,
        authorized_actor_kind: str,
        authorized_actor_id: str,
    ) -> bool:
        return (actor_kind == authorized_actor_kind and actor_id == authorized_actor_id) or (
            actor_kind == "system" and actor_id == "workflow-administrator"
        )

    @staticmethod
    def cancellation_outcome(
        facts: CancellationFacts,
    ) -> Literal[
        "unauthorized",
        "already_completed",
        "already_cancelled",
        "too_late",
        "cancelled",
    ]:
        if not facts.actor_authorized:
            return "unauthorized"
        if facts.lifecycle == "completed":
            return "already_completed"
        if facts.lifecycle == "cancelled":
            return "already_cancelled"
        if facts.dispatch_boundary_crossed:
            return "too_late"
        return "cancelled"


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
        steps: tuple[CompletionStepFact, ...],
        effects: tuple[CompletionEffectFact, ...],
    ) -> bool:
        return (
            bool(steps)
            and all(step.state == "succeeded" and step.has_accepted_output for step in steps)
            and bool(effects)
            and all(
                effect.certainty == "applied" and effect.has_applied_evidence for effect in effects
            )
        )


class RenewalExternalEffectPolicy:
    maximum_attempts = RENEWAL_ATTEMPT_RETRY_POLICY.max_attempts

    @staticmethod
    def authorize_dispatch(*, facts: EffectAuthorizationFacts) -> None:
        if not facts.lifecycle_active or facts.authority_revoked:
            raise RuntimeError("Renewal Workflow no longer authorizes dispatch")
        if not all(
            (
                facts.grant_matches_step,
                facts.fingerprint_matches_grant,
                facts.grant_valid,
                facts.grant_consumption_consistent,
                facts.durable_claim_matches,
                facts.durable_effect_matches,
            )
        ):
            raise RuntimeError("Exact Approval Grant does not authorize this dispatch")
        if facts.existing_certainty not in {None, "not_applied"}:
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
    "ApprovalAcceptedDecision",
    "ApprovalDecision",
    "ApprovalDecisionFacts",
    "ApprovalRejectedDecision",
    "ApprovalRejectionOutcome",
    "CancellationFacts",
    "CompletionEffectFact",
    "CompletionStepFact",
    "EffectAuthorizationFacts",
    "RecoveryDecision",
    "RenewalApprovalPolicy",
    "RenewalCompletionPolicy",
    "RenewalDeliveryPolicy",
    "RenewalExternalEffectPolicy",
    "RenewalLifecyclePolicy",
    "RenewalWorkflowPolicy",
    "RouteDecision",
]
