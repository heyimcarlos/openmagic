"""Application Policy for exact delivered renewal approval authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from openmagic_runtime.delivery import DeliveryStatus
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.kernel.inspection import WaitState

from example_insurance.renewal_effect_types import RenewalEmailEffect
from example_insurance.renewal_lifecycle_policy import WorkflowLifecycle

MessageSourceKind = Literal["channel", "delivery", "agent_run", "system"]
ApprovalDecisionKind = Literal["approve", "request_revision"]


def message_source_kind(value: object) -> MessageSourceKind:
    if value == "channel":
        return "channel"
    if value == "delivery":
        return "delivery"
    if value == "agent_run":
        return "agent_run"
    if value == "system":
        return "system"
    raise RuntimeError("Approval Message has an invalid source kind")


def approval_decision_kind(value: object) -> ApprovalDecisionKind:
    if value == "approve":
        return "approve"
    if value == "request_revision":
        return "request_revision"
    raise RuntimeError("Renewal approval has an invalid decision kind")


@dataclass(frozen=True)
class PresentedMessageIdentity:
    message_id: UUID
    thread_sequence: int
    content_fingerprint: str


@dataclass(frozen=True)
class ApprovalPresentationIdentity:
    workflow_id: UUID
    thread_id: UUID
    wait_id: UUID
    draft_id: UUID
    message: PresentedMessageIdentity
    presentation_fingerprint: str
    effect: RenewalEmailEffect


@dataclass(frozen=True)
class RequestedApprovalPresentation:
    workflow_id: UUID
    wait_id: UUID
    draft_id: UUID
    message_id: UUID
    thread_sequence: int
    message_fingerprint: str
    presentation_fingerprint: str
    effect: RenewalEmailEffect

    def identity(self, thread_id: UUID) -> ApprovalPresentationIdentity:
        return ApprovalPresentationIdentity(
            workflow_id=self.workflow_id,
            thread_id=thread_id,
            wait_id=self.wait_id,
            draft_id=self.draft_id,
            message=PresentedMessageIdentity(
                self.message_id,
                self.thread_sequence,
                self.message_fingerprint,
            ),
            presentation_fingerprint=self.presentation_fingerprint,
            effect=self.effect,
        )


@dataclass(frozen=True)
class DeliveredApprovalPresentation:
    delivery_id: UUID
    delivery_thread_id: UUID
    status: DeliveryStatus
    acknowledged: bool
    delivered_message_id: UUID
    message_id: UUID
    message_thread_id: UUID
    sequence: int
    content_fingerprint: str
    source_kind: MessageSourceKind
    source_id: UUID

    def message_identity(self, thread_id: UUID) -> PresentedMessageIdentity | None:
        if self.delivery_thread_id != thread_id or self.message_thread_id != thread_id:
            return None
        if self.status != "delivered" or not self.acknowledged:
            return None
        if self.delivered_message_id != self.message_id:
            return None
        if self.source_kind != "delivery" or self.source_id != self.delivery_id:
            return None
        return PresentedMessageIdentity(
            self.message_id,
            self.sequence,
            self.content_fingerprint,
        )


@dataclass(frozen=True)
class DurableApprovalPresentation:
    workflow_id: UUID
    thread_id: UUID
    wait_id: UUID
    draft_id: UUID
    wait_state: WaitState
    wait_input_matches: bool
    presentation_fingerprint: str
    effect: RenewalEmailEffect
    delivery: DeliveredApprovalPresentation | None

    def identity(self) -> ApprovalPresentationIdentity | None:
        if not self.wait_input_matches or self.delivery is None:
            return None
        message = self.delivery.message_identity(self.thread_id)
        if message is None:
            return None
        if self.presentation_fingerprint != content_fingerprint(self.effect):
            return None
        return ApprovalPresentationIdentity(
            workflow_id=self.workflow_id,
            thread_id=self.thread_id,
            wait_id=self.wait_id,
            draft_id=self.draft_id,
            message=message,
            presentation_fingerprint=self.presentation_fingerprint,
            effect=self.effect,
        )


@dataclass(frozen=True)
class ApprovalDecisionFacts:
    lifecycle: WorkflowLifecycle
    actor_matches: bool
    authority_revoked: bool
    requested: RequestedApprovalPresentation
    durable: DurableApprovalPresentation


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


class RenewalApprovalPolicy:
    @staticmethod
    def decide(
        *,
        decision_kind: ApprovalDecisionKind,
        facts: ApprovalDecisionFacts,
    ) -> ApprovalDecision:
        requested = facts.requested
        durable = facts.durable
        if facts.lifecycle != "active" or facts.authority_revoked:
            return ApprovalRejectedDecision("authority_revoked")
        if not facts.actor_matches:
            return ApprovalRejectedDecision("unauthorized_actor")
        if durable.wait_state != "unsatisfied":
            return ApprovalRejectedDecision("wait_already_satisfied")
        if requested.identity(durable.thread_id) != durable.identity():
            return ApprovalRejectedDecision("stale_presentation")
        route = "approve_email" if decision_kind == "approve" else "revise_email"
        return ApprovalAcceptedDecision(route)


__all__ = [
    "ApprovalAcceptedDecision",
    "ApprovalDecision",
    "ApprovalDecisionFacts",
    "ApprovalDecisionKind",
    "ApprovalPresentationIdentity",
    "ApprovalRejectedDecision",
    "ApprovalRejectionOutcome",
    "DeliveredApprovalPresentation",
    "DeliveryStatus",
    "DurableApprovalPresentation",
    "MessageSourceKind",
    "PresentedMessageIdentity",
    "RenewalApprovalPolicy",
    "RequestedApprovalPresentation",
    "WaitState",
    "approval_decision_kind",
    "message_source_kind",
]
