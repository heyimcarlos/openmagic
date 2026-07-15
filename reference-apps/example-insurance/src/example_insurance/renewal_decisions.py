"""Exact renewal presentation decisions and their durable records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause, StateConflict
from openmagic_runtime.evidence import content_fingerprint
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance.renewal_approval_policy import (
    ApprovalDecisionFacts,
    DeliveredApprovalPresentation,
    DurableApprovalPresentation,
    RequestedApprovalPresentation,
    delivery_status,
    message_source_kind,
    wait_state,
)
from example_insurance.renewal_commands import (
    ApproveRenewalDraftInput,
    RequestRenewalRevisionInput,
)
from example_insurance.renewal_effects import RenewalApprovalPresentation, RenewalEmailEffect
from example_insurance.renewal_lifecycle_policy import (
    WorkflowLifecycle,
    workflow_lifecycle,
)
from example_insurance.renewal_records import actor_record, cause_record


def approval_presentation(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> RenewalApprovalPresentation:
    row = connection.execute(
        "SELECT w.wait_id, d.draft_id, d.presentation_fingerprint, "
        "d.policyholder_email, d.subject, d.body, m.message_id, m.sequence, m.content "
        "FROM example_insurance.renewal_workflows r "
        "JOIN example_insurance.renewal_drafts d ON d.workflow_id = r.workflow_id "
        "JOIN openmagic_runtime.waits w ON w.instance_id = r.instance_id "
        "AND (w.input->>'draft_id')::uuid = d.draft_id "
        "JOIN example_insurance.domain_events e ON e.workflow_id = r.workflow_id "
        "AND e.event_type = 'renewal.draft.ready' "
        "AND (e.payload->>'draft_id')::uuid = d.draft_id "
        "JOIN openmagic_runtime.deliveries x ON x.domain_event_id = e.event_id "
        "AND x.thread_id = r.thread_id AND x.status = 'delivered' "
        "AND x.acknowledged_at IS NOT NULL "
        "JOIN openmagic_runtime.messages m ON m.message_id = x.delivered_message_id "
        "AND m.thread_id = r.thread_id AND m.source_kind = 'delivery' "
        "AND m.source_id = x.delivery_id "
        "WHERE r.workflow_id = %s AND w.state = 'unsatisfied' "
        "ORDER BY w.created_at DESC, w.wait_id DESC LIMIT 1",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Renewal approval presentation not found: {workflow_id}")
    return RenewalApprovalPresentation(
        workflow_id=workflow_id,
        wait_id=UUID(str(row[0])),
        draft_id=UUID(str(row[1])),
        message_id=UUID(str(row[6])),
        thread_sequence=int(row[7]),
        message_fingerprint=content_fingerprint(str(row[8])),
        presentation_fingerprint=str(row[2]),
        proposed_effect=RenewalEmailEffect(
            recipient_email=str(row[3]),
            subject=str(row[4]),
            body=str(row[5]),
        ),
    )


@dataclass(frozen=True)
class DecisionContext:
    instance_id: UUID
    thread_id: UUID
    lifecycle: WorkflowLifecycle
    authorized_actor_kind: str
    authorized_actor_id: str
    authority_revoked: bool
    policy_number: str
    policyholder_name: str
    policyholder_email: str
    renewal_date: str
    expiring_premium_cents: int
    approval: DurableApprovalPresentation


def decision_context(
    connection: Connection[tuple[Any, ...]],
    value: ApproveRenewalDraftInput | RequestRenewalRevisionInput,
) -> DecisionContext:
    workflow = connection.execute(
        "SELECT r.instance_id, r.thread_id, r.lifecycle, r.authorized_actor_kind, "
        "r.authorized_actor_id, r.authority_revoked_at IS NOT NULL, r.policy_number, "
        "r.policyholder_name, f.policyholder_email, r.renewal_date, "
        "r.expiring_premium_cents FROM example_insurance.renewal_workflows r "
        "JOIN example_insurance.policy_renewal_facts f ON f.policy_id = r.policy_id "
        "JOIN openmagic_runtime.instances i ON i.instance_id = r.instance_id "
        "WHERE r.workflow_id = %s FOR UPDATE OF r, i",
        (value.workflow_id,),
    ).fetchone()
    if workflow is None:
        raise StateConflict("Exact Workflow does not exist")
    presentation = connection.execute(
        "SELECT w.state, w.input, d.presentation_fingerprint, d.policyholder_email, "
        "d.subject, d.body FROM openmagic_runtime.waits w "
        "JOIN example_insurance.renewal_drafts d ON d.draft_id = %s "
        "AND d.workflow_id = %s WHERE w.wait_id = %s AND w.instance_id = %s "
        "FOR UPDATE OF w, d",
        (value.draft_id, value.workflow_id, value.wait_id, workflow[0]),
    ).fetchone()
    if presentation is None:
        raise StateConflict("Exact Workflow, Wait, or Draft does not exist")
    wait_input = dict(presentation[1])
    presentation_bound = wait_input == {
        "workflow_id": str(value.workflow_id),
        "draft_id": str(value.draft_id),
        "presentation_fingerprint": str(presentation[2]),
        "recipient_email": str(presentation[3]),
        "subject": str(presentation[4]),
        "body": str(presentation[5]),
    }
    delivery = connection.execute(
        "SELECT x.thread_id, x.status, x.acknowledged_at, x.delivery_id, "
        "x.delivered_message_id, m.message_id, m.thread_id, m.sequence, m.content, "
        "m.source_kind, m.source_id FROM example_insurance.domain_events e "
        "JOIN openmagic_runtime.deliveries x ON x.domain_event_id = e.event_id "
        "JOIN openmagic_runtime.messages m ON m.message_id = x.delivered_message_id "
        "WHERE e.workflow_id = %s AND e.event_type = 'renewal.draft.ready' "
        "AND (e.payload->>'draft_id')::uuid = %s "
        "ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1 FOR UPDATE OF x, m",
        (value.workflow_id, value.draft_id),
    ).fetchone()
    effect = RenewalEmailEffect(
        str(presentation[3]),
        str(presentation[4]),
        str(presentation[5]),
    )
    delivered = (
        DeliveredApprovalPresentation(
            delivery_id=UUID(str(delivery[3])),
            delivery_thread_id=UUID(str(delivery[0])),
            status=delivery_status(delivery[1]),
            acknowledged=delivery[2] is not None,
            delivered_message_id=UUID(str(delivery[4])),
            message_id=UUID(str(delivery[5])),
            message_thread_id=UUID(str(delivery[6])),
            sequence=int(delivery[7]),
            content_fingerprint=content_fingerprint(str(delivery[8])),
            source_kind=message_source_kind(delivery[9]),
            source_id=UUID(str(delivery[10])),
        )
        if delivery is not None
        else None
    )
    return DecisionContext(
        instance_id=UUID(str(workflow[0])),
        thread_id=UUID(str(workflow[1])),
        lifecycle=workflow_lifecycle(workflow[2]),
        authorized_actor_kind=str(workflow[3]),
        authorized_actor_id=str(workflow[4]),
        authority_revoked=bool(workflow[5]),
        policy_number=str(workflow[6]),
        policyholder_name=str(workflow[7]),
        policyholder_email=str(workflow[8]),
        renewal_date=workflow[9].isoformat(),
        expiring_premium_cents=int(workflow[10]),
        approval=DurableApprovalPresentation(
            workflow_id=value.workflow_id,
            thread_id=UUID(str(workflow[1])),
            wait_id=value.wait_id,
            draft_id=value.draft_id,
            wait_state=wait_state(presentation[0]),
            wait_input_matches=presentation_bound,
            presentation_fingerprint=str(presentation[2]),
            effect=effect,
            delivery=delivered,
        ),
    )


def decision_facts(
    context: DecisionContext,
    actor: Actor,
    value: ApproveRenewalDraftInput | RequestRenewalRevisionInput,
) -> ApprovalDecisionFacts:
    return ApprovalDecisionFacts(
        lifecycle=context.lifecycle,
        actor_matches=context.authorized_actor_kind == actor.kind
        and context.authorized_actor_id == actor.identifier,
        authority_revoked=context.authority_revoked,
        requested=RequestedApprovalPresentation(
            workflow_id=value.workflow_id,
            wait_id=value.wait_id,
            draft_id=value.draft_id,
            message_id=value.message_id,
            thread_sequence=value.thread_sequence,
            message_fingerprint=value.message_fingerprint,
            presentation_fingerprint=value.presentation_fingerprint,
            effect=value.proposed_effect,
        ),
        durable=context.approval,
    )


def record_decision(
    connection: Connection[tuple[Any, ...]],
    *,
    decision_id: UUID,
    command_id: UUID,
    actor: Actor,
    cause: Cause,
    decision_kind: str,
    value: ApproveRenewalDraftInput | RequestRenewalRevisionInput,
    revision_instruction: str | None,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.renewal_decisions "
        "(decision_id, command_id, workflow_id, wait_id, draft_id, presented_message_id, "
        "thread_sequence, message_fingerprint, decision_kind, actor, cause, "
        "presentation_fingerprint, proposed_effect, revision_instruction, signal_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            decision_id,
            command_id,
            value.workflow_id,
            value.wait_id,
            value.draft_id,
            value.message_id,
            value.thread_sequence,
            value.message_fingerprint,
            decision_kind,
            Jsonb(actor_record(actor)),
            Jsonb(cause_record(cause)),
            value.presentation_fingerprint,
            Jsonb(
                {
                    "recipient_email": value.proposed_effect.recipient_email,
                    "subject": value.proposed_effect.subject,
                    "body": value.proposed_effect.body,
                }
            ),
            revision_instruction,
            command_id,
        ),
    )


__all__ = [
    "DecisionContext",
    "approval_presentation",
    "decision_context",
    "decision_facts",
    "record_decision",
]
