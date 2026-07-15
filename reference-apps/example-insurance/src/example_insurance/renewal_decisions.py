"""Exact renewal presentation decisions and their durable records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause, StateConflict
from openmagic_runtime.evidence import content_fingerprint
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance.renewal_commands import (
    ApproveRenewalDraftInput,
    RequestRenewalRevisionInput,
)
from example_insurance.renewal_effects import RenewalEmailEffect
from example_insurance.renewal_policies import ApprovalDecisionFacts
from example_insurance.renewal_records import actor_record, cause_record


@dataclass(frozen=True)
class DecisionContext:
    instance_id: UUID
    thread_id: UUID
    lifecycle: str
    authorized_actor_kind: str
    authorized_actor_id: str
    authority_revoked: bool
    policy_number: str
    policyholder_name: str
    policyholder_email: str
    renewal_date: str
    expiring_premium_cents: int
    wait_state: str
    presentation_bound: bool
    draft_fingerprint: str
    effect: RenewalEmailEffect


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
    return DecisionContext(
        instance_id=UUID(str(workflow[0])),
        thread_id=UUID(str(workflow[1])),
        lifecycle=str(workflow[2]),
        authorized_actor_kind=str(workflow[3]),
        authorized_actor_id=str(workflow[4]),
        authority_revoked=bool(workflow[5]),
        policy_number=str(workflow[6]),
        policyholder_name=str(workflow[7]),
        policyholder_email=str(workflow[8]),
        renewal_date=workflow[9].isoformat(),
        expiring_premium_cents=int(workflow[10]),
        wait_state=str(presentation[0]),
        presentation_bound=presentation_bound,
        draft_fingerprint=str(presentation[2]),
        effect=RenewalEmailEffect(
            str(presentation[3]),
            str(presentation[4]),
            str(presentation[5]),
        ),
    )


def decision_facts(
    context: DecisionContext,
    actor: Actor,
    value: ApproveRenewalDraftInput | RequestRenewalRevisionInput,
) -> ApprovalDecisionFacts:
    expected = content_fingerprint(context.effect)
    return ApprovalDecisionFacts(
        lifecycle=context.lifecycle,
        actor_matches=context.authorized_actor_kind == actor.kind
        and context.authorized_actor_id == actor.identifier,
        authority_revoked=context.authority_revoked,
        wait_unsatisfied=context.wait_state == "unsatisfied",
        presentation_exact=context.presentation_bound
        and context.draft_fingerprint == expected
        and value.presentation_fingerprint == expected
        and value.proposed_effect == context.effect,
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
        "(decision_id, command_id, workflow_id, wait_id, draft_id, decision_kind, actor, "
        "cause, presentation_fingerprint, proposed_effect, revision_instruction, signal_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            decision_id,
            command_id,
            value.workflow_id,
            value.wait_id,
            value.draft_id,
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


__all__ = ["DecisionContext", "decision_context", "decision_facts", "record_decision"]
