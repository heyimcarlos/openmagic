"""Delivered renewal presentation and exact approval decisions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import AcceptSignal
from psycopg import Connection

from example_insurance.renewal_approval_policy import (
    ApprovalRejectedDecision,
    RenewalApprovalPolicy,
)
from example_insurance.renewal_approval_records import (
    load_approval_presentation_snapshot,
    lock_approval_decision_snapshot,
)
from example_insurance.renewal_commands import (
    ApproveRenewalDraft,
    ApproveRenewalDraftResult,
    RequestRenewalRevision,
    RequestRenewalRevisionResult,
)
from example_insurance.renewal_decisions import decision_facts, record_decision
from example_insurance.renewal_effect_types import RenewalApprovalPresentation
from example_insurance.renewal_grant_records import record_approval_grant
from example_insurance.renewal_records import CommandEventLineage, record_event


class RenewalReviewControl:
    def __init__(self) -> None:
        self._policy = RenewalApprovalPolicy()

    @staticmethod
    def presentation(
        connection: Connection[tuple[Any, ...]], workflow_id: UUID
    ) -> RenewalApprovalPresentation:
        return load_approval_presentation_snapshot(connection, workflow_id).presentation()

    def approve(
        self,
        command: ApproveRenewalDraft,
        connection: Connection[tuple[Any, ...]],
    ) -> ApproveRenewalDraftResult:
        snapshot = lock_approval_decision_snapshot(
            connection,
            workflow_id=command.input.workflow_id,
            draft_id=command.input.draft_id,
            wait_id=command.input.wait_id,
        )
        decision = self._policy.decide(
            decision_kind="approve",
            facts=decision_facts(snapshot, command.actor, command.input),
        )
        if isinstance(decision, ApprovalRejectedDecision):
            return ApproveRenewalDraftResult(
                outcome=decision.outcome,
                workflow_id=command.input.workflow_id,
                wait_id=command.input.wait_id,
                approval_grant_id=None,
                effect_step_id=None,
            )
        decision_id = uuid4()
        approval_grant_id = uuid4()
        lineage = CommandEventLineage(command.actor, command.command_id)
        signal = KernelControl(connection).accept_signal(
            AcceptSignal(
                signal_id=command.command_id,
                instance_id=snapshot.workflow.instance_id,
                wait_id=command.input.wait_id,
                signal_type="renewal.draft.decision",
                schema_version=1,
                payload={
                    "workflow_id": str(command.input.workflow_id),
                    "wait_id": str(command.input.wait_id),
                    "draft_id": str(command.input.draft_id),
                    "presentation_fingerprint": command.input.presentation_fingerprint,
                    "approval_grant_id": str(approval_grant_id),
                    "effect_fingerprint": command.input.presentation_fingerprint,
                    "recipient_email": command.input.proposed_effect.recipient_email,
                    "subject": command.input.proposed_effect.subject,
                    "body": command.input.proposed_effect.body,
                },
                route_key=decision.route_key,
            )
        )
        effect_step_id = signal.steps["email_effect"]
        record_decision(
            connection,
            decision_id=decision_id,
            command_id=command.command_id,
            actor=command.actor,
            cause=command.cause,
            decision_kind="approve",
            value=command.input,
            revision_instruction=None,
        )
        record_approval_grant(
            connection,
            approval_grant_id=approval_grant_id,
            decision_id=decision_id,
            workflow_id=command.input.workflow_id,
            step_id=effect_step_id,
            effect_fingerprint=command.input.presentation_fingerprint,
            actor=command.actor,
            cause=command.cause,
        )
        record_event(
            connection,
            event_type="renewal.draft.approved",
            workflow_id=command.input.workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={
                "decision_id": str(decision_id),
                "approval_grant_id": str(approval_grant_id),
                "draft_id": str(command.input.draft_id),
                "step_id": str(effect_step_id),
            },
        )
        return ApproveRenewalDraftResult(
            outcome="approved",
            workflow_id=command.input.workflow_id,
            wait_id=command.input.wait_id,
            approval_grant_id=approval_grant_id,
            effect_step_id=effect_step_id,
        )

    def request_revision(
        self,
        command: RequestRenewalRevision,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestRenewalRevisionResult:
        snapshot = lock_approval_decision_snapshot(
            connection,
            workflow_id=command.input.workflow_id,
            draft_id=command.input.draft_id,
            wait_id=command.input.wait_id,
        )
        decision = self._policy.decide(
            decision_kind="request_revision",
            facts=decision_facts(snapshot, command.actor, command.input),
        )
        if isinstance(decision, ApprovalRejectedDecision):
            return RequestRenewalRevisionResult(
                outcome=decision.outcome,
                workflow_id=command.input.workflow_id,
                wait_id=command.input.wait_id,
                revision_step_id=None,
            )
        signal = KernelControl(connection).accept_signal(
            AcceptSignal(
                signal_id=command.command_id,
                instance_id=snapshot.workflow.instance_id,
                wait_id=command.input.wait_id,
                signal_type="renewal.draft.decision",
                schema_version=1,
                payload={
                    "workflow_id": str(command.input.workflow_id),
                    "wait_id": str(command.input.wait_id),
                    "draft_id": str(command.input.draft_id),
                    "presentation_fingerprint": command.input.presentation_fingerprint,
                    "recipient_email": command.input.proposed_effect.recipient_email,
                    "subject": command.input.proposed_effect.subject,
                    "body": command.input.proposed_effect.body,
                    "thread_id": str(snapshot.workflow.thread_id),
                    "revision_instruction": command.input.revision_instruction,
                    "policy_number": snapshot.workflow.policy_number,
                    "policyholder_name": snapshot.workflow.policyholder_name,
                    "policyholder_email": snapshot.workflow.policyholder_email,
                    "renewal_date": snapshot.workflow.renewal_date,
                    "expiring_premium_cents": snapshot.workflow.expiring_premium_cents,
                },
                route_key=decision.route_key,
            )
        )
        decision_id = uuid4()
        lineage = CommandEventLineage(command.actor, command.command_id)
        record_decision(
            connection,
            decision_id=decision_id,
            command_id=command.command_id,
            actor=command.actor,
            cause=command.cause,
            decision_kind="request_revision",
            value=command.input,
            revision_instruction=command.input.revision_instruction,
        )
        revision_step_id = signal.steps["revision_draft"]
        record_event(
            connection,
            event_type="renewal.draft.revision_requested",
            workflow_id=command.input.workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={
                "decision_id": str(decision_id),
                "draft_id": str(command.input.draft_id),
                "revision_step_id": str(revision_step_id),
            },
        )
        return RequestRenewalRevisionResult(
            outcome="revision_requested",
            workflow_id=command.input.workflow_id,
            wait_id=command.input.wait_id,
            revision_step_id=revision_step_id,
        )


__all__ = ["RenewalReviewControl"]
