"""Canonical writes for committed renewal approval decisions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance._persistence.application_event_records import actor_record, cause_record
from example_insurance.renewal_approval_policy import ApprovalDecisionKind
from example_insurance.renewal_commands import (
    ApproveRenewalDraftInput,
    RequestRenewalRevisionInput,
)


def record_decision(
    connection: Connection[tuple[Any, ...]],
    *,
    decision_id: UUID,
    command_id: UUID,
    actor: Actor,
    cause: Cause,
    decision_kind: ApprovalDecisionKind,
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


__all__ = ["record_decision"]
