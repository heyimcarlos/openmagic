"""Application Policy for fenced renewal email External Effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from openmagic_runtime.evidence import content_fingerprint

from example_insurance.renewal_effects import RenewalEmailEffect
from example_insurance.renewal_lifecycle_policy import WorkflowLifecycle
from example_insurance.renewal_policies import RENEWAL_ATTEMPT_RETRY_POLICY

EffectCertainty = Literal["dispatching", "applied", "not_applied", "uncertain"]
EffectObservation = Literal["applied", "not_applied", "uncertain"]


def effect_certainty(value: object) -> EffectCertainty:
    if value == "dispatching":
        return "dispatching"
    if value == "applied":
        return "applied"
    if value == "not_applied":
        return "not_applied"
    if value == "uncertain":
        return "uncertain"
    raise RuntimeError("External Effect has an invalid certainty")


def effect_observation(value: object) -> EffectObservation:
    if value == "applied":
        return "applied"
    if value == "not_applied":
        return "not_applied"
    if value == "uncertain":
        return "uncertain"
    raise RuntimeError("Provider observation classification is unsupported")


@dataclass(frozen=True)
class DurableWorkflowAuthority:
    workflow_id: UUID
    instance_id: UUID
    lifecycle: WorkflowLifecycle
    authority_revoked: bool

    def require_dispatch_authority(self, claim: DispatchClaim) -> None:
        if self.lifecycle != "active" or self.authority_revoked:
            raise RuntimeError("Renewal Workflow no longer authorizes dispatch")
        if claim.instance_id != self.instance_id:
            raise RuntimeError("Dispatch claim belongs to another Workflow Instance")


@dataclass(frozen=True)
class DurableApprovalGrant:
    approval_grant_id: UUID
    workflow_id: UUID
    step_id: UUID
    effect_fingerprint: str
    invalidated: bool
    consumed: bool

    def require_dispatch_authority(
        self,
        *,
        workflow: DurableWorkflowAuthority,
        claim: DispatchClaim,
        effect_exists: bool,
    ) -> None:
        if self.workflow_id != workflow.workflow_id or self.step_id != claim.step_id:
            raise RuntimeError("Approval Grant belongs to another Workflow or Step")
        if claim.approval_grant_id != self.approval_grant_id:
            raise RuntimeError("Dispatch claim names another Approval Grant")
        if claim.effect_fingerprint != self.effect_fingerprint:
            raise RuntimeError("Dispatch claim names another approved effect")
        if content_fingerprint(claim.effect) != self.effect_fingerprint:
            raise RuntimeError("Dispatch provider input differs from the approved effect")
        if self.invalidated:
            raise RuntimeError("Exact Approval Grant does not authorize this dispatch")
        if self.consumed != effect_exists:
            problem = (
                "Consumed Approval Grant has no durable External Effect"
                if self.consumed
                else "Durable External Effect has an unconsumed Approval Grant"
            )
            raise RuntimeError(problem)


@dataclass(frozen=True)
class DispatchClaim:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int
    worker_id: str
    template_key: str
    approval_grant_id: UUID
    effect_fingerprint: str
    effect: RenewalEmailEffect

    def require_exact_request(self, requested: DispatchClaim) -> None:
        if requested != self:
            raise RuntimeError("Durable Attempt claim does not match dispatch request")
        if requested.template_key != "send_renewal_email":
            raise RuntimeError("Dispatch claim does not target the email effect Step")


@dataclass(frozen=True)
class DurableExternalEffect:
    logical_effect_id: UUID
    workflow_id: UUID
    step_id: UUID
    approval_grant_id: UUID
    effect_fingerprint: str
    provider_idempotency_key: str
    dispatch_attempt_id: UUID
    dispatch_attempt_number: int
    certainty: EffectCertainty

    def require_safe_dispatch(
        self,
        *,
        workflow: DurableWorkflowAuthority,
        grant: DurableApprovalGrant,
        claim: DispatchClaim,
        expected_logical_effect_id: UUID,
    ) -> None:
        if self.logical_effect_id != expected_logical_effect_id:
            raise RuntimeError("External Effect has another logical identity")
        if self.workflow_id != workflow.workflow_id or self.step_id != claim.step_id:
            raise RuntimeError("External Effect belongs to another Workflow or Step")
        if self.approval_grant_id != grant.approval_grant_id:
            raise RuntimeError("External Effect belongs to another Approval Grant")
        if self.effect_fingerprint != grant.effect_fingerprint:
            raise RuntimeError("External Effect differs from the approved effect")
        if self.provider_idempotency_key != str(self.logical_effect_id):
            raise RuntimeError("External Effect idempotency identity is not canonical")
        if self.certainty != "not_applied":
            raise RuntimeError("External Effect is not safe to dispatch")


@dataclass(frozen=True)
class DispatchAuthority:
    workflow: DurableWorkflowAuthority
    grant: DurableApprovalGrant
    requested_claim: DispatchClaim
    durable_claim: DispatchClaim
    expected_logical_effect_id: UUID
    effect: DurableExternalEffect | None


class RenewalExternalEffectPolicy:
    maximum_attempts = RENEWAL_ATTEMPT_RETRY_POLICY.max_attempts

    @staticmethod
    def authorize_dispatch(*, authority: DispatchAuthority) -> None:
        workflow = authority.workflow
        grant = authority.grant
        requested = authority.requested_claim
        durable = authority.durable_claim
        effect = authority.effect
        workflow.require_dispatch_authority(requested)
        durable.require_exact_request(requested)
        grant.require_dispatch_authority(
            workflow=workflow,
            claim=requested,
            effect_exists=effect is not None,
        )
        if effect is None:
            return
        effect.require_safe_dispatch(
            workflow=workflow,
            grant=grant,
            claim=requested,
            expected_logical_effect_id=authority.expected_logical_effect_id,
        )

    @staticmethod
    def result_disposition(
        *, classification: EffectObservation, attempt_number: int, maximum_attempts: int
    ) -> Literal["succeed", "retry", "fail", "defer"]:
        if classification == "applied":
            return "succeed"
        if classification == "not_applied":
            return "retry" if attempt_number < maximum_attempts else "fail"
        return "defer"

    @staticmethod
    def reconciliation_disposition(
        *,
        classification: EffectObservation,
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
    "DispatchAuthority",
    "DispatchClaim",
    "DurableApprovalGrant",
    "DurableExternalEffect",
    "DurableWorkflowAuthority",
    "EffectCertainty",
    "EffectObservation",
    "RenewalExternalEffectPolicy",
    "effect_certainty",
    "effect_observation",
]
