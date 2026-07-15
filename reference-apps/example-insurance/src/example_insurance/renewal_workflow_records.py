"""Transaction-bound persistence for renewal Workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor
from psycopg import Connection

from example_insurance.renewal_commands import StartRenewalOutreachInput


@dataclass(frozen=True)
class WorkflowIdentity:
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID


@dataclass(frozen=True)
class DurableDraft:
    draft_id: UUID
    agent_run_id: UUID
    presentation_fingerprint: str
    policyholder_email: str
    subject: str
    body: str


@dataclass(frozen=True)
class ActivationReceipt:
    steps: dict[str, UUID]
    waits: dict[str, UUID]


def workflow_exists(connection: Connection[tuple[Any, ...]], workflow_id: UUID) -> bool:
    row = connection.execute(
        "SELECT 1 FROM example_insurance.renewal_workflows WHERE workflow_id = %s",
        (workflow_id,),
    ).fetchone()
    return row is not None


def record_workflow(
    connection: Connection[tuple[Any, ...]],
    *,
    command_id: UUID,
    instance_id: UUID,
    actor: Actor,
    value: StartRenewalOutreachInput,
) -> WorkflowIdentity:
    connection.execute(
        "INSERT INTO example_insurance.renewal_workflows "
        "(workflow_id, start_command_id, instance_id, thread_id, policy_id, policy_number, "
        "policyholder_name, renewal_date, expiring_premium_cents, lifecycle, "
        "authorized_actor_kind, authorized_actor_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)",
        (
            value.workflow_id,
            command_id,
            instance_id,
            value.thread_id,
            value.policy_id,
            value.policy_number,
            value.policyholder_name,
            value.renewal_date,
            value.expiring_premium_cents,
            actor.kind,
            actor.identifier,
        ),
    )
    return WorkflowIdentity(value.workflow_id, instance_id, value.thread_id)


def lock_next_expired_workflow_instance(
    connection: Connection[tuple[Any, ...]],
) -> UUID | None:
    row = connection.execute(
        "SELECT r.instance_id FROM example_insurance.renewal_workflows r "
        "WHERE r.lifecycle = 'active' AND EXISTS ("
        "SELECT 1 FROM openmagic_runtime.attempts a WHERE a.instance_id = r.instance_id "
        "AND a.state = 'leased' AND (a.lease_expires_at <= clock_timestamp() "
        "OR a.hard_deadline <= clock_timestamp())) "
        "ORDER BY r.created_at, r.workflow_id FOR UPDATE SKIP LOCKED LIMIT 1"
    ).fetchone()
    return UUID(str(row[0])) if row is not None else None


def lock_workflow_for_attempt(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> WorkflowIdentity:
    row = connection.execute(
        "SELECT workflow_id, instance_id, thread_id "
        "FROM example_insurance.renewal_workflows "
        "WHERE instance_id = %s FOR UPDATE",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Renewal Workflow is unavailable")
    return WorkflowIdentity(
        workflow_id=UUID(str(row[0])),
        instance_id=UUID(str(row[1])),
        thread_id=UUID(str(row[2])),
    )


def activation_receipt(
    connection: Connection[tuple[Any, ...]],
    *,
    instance_id: UUID,
    source_attempt_id: UUID,
) -> ActivationReceipt:
    step_rows = connection.execute(
        "SELECT output_slot, step_id FROM openmagic_runtime.steps "
        "WHERE instance_id = %s AND activation_source_kind = 'step' "
        "AND activation_source_id = %s",
        (instance_id, source_attempt_id),
    ).fetchall()
    wait_rows = connection.execute(
        "SELECT output_slot, wait_id FROM openmagic_runtime.waits "
        "WHERE instance_id = %s AND activation_source_kind = 'step' "
        "AND activation_source_id = %s",
        (instance_id, source_attempt_id),
    ).fetchall()
    return ActivationReceipt(
        steps={str(row[0]): UUID(str(row[1])) for row in step_rows},
        waits={str(row[0]): UUID(str(row[1])) for row in wait_rows},
    )


def load_draft_for_step(connection: Connection[tuple[Any, ...]], step_id: UUID) -> DurableDraft:
    row = connection.execute(
        "SELECT draft_id, agent_run_id, presentation_fingerprint, "
        "policyholder_email, subject, body FROM example_insurance.renewal_drafts "
        "WHERE step_id = %s",
        (step_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Accepted draft result has no durable draft")
    return DurableDraft(
        draft_id=UUID(str(row[0])),
        agent_run_id=UUID(str(row[1])),
        presentation_fingerprint=str(row[2]),
        policyholder_email=str(row[3]),
        subject=str(row[4]),
        body=str(row[5]),
    )


def record_draft(
    connection: Connection[tuple[Any, ...]],
    *,
    draft_id: UUID,
    workflow_id: UUID,
    step_id: UUID,
    agent_run_id: UUID,
    policyholder_email: str,
    subject: str,
    body: str,
    presentation_fingerprint: str,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.renewal_drafts "
        "(draft_id, workflow_id, step_id, agent_run_id, subject, body, "
        "policyholder_email, presentation_fingerprint) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            draft_id,
            workflow_id,
            step_id,
            agent_run_id,
            subject,
            body,
            policyholder_email,
            presentation_fingerprint,
        ),
    )


def mark_workflow_authority_revoked(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> None:
    connection.execute(
        "UPDATE example_insurance.renewal_workflows SET authority_revoked_at = "
        "clock_timestamp() WHERE workflow_id = %s",
        (workflow_id,),
    )


def mark_workflow_cancelled(connection: Connection[tuple[Any, ...]], workflow_id: UUID) -> None:
    connection.execute(
        "UPDATE example_insurance.renewal_workflows SET lifecycle = 'cancelled' "
        "WHERE workflow_id = %s",
        (workflow_id,),
    )


def mark_workflow_completed(connection: Connection[tuple[Any, ...]], workflow_id: UUID) -> None:
    connection.execute(
        "UPDATE example_insurance.renewal_workflows SET lifecycle = 'completed' "
        "WHERE workflow_id = %s",
        (workflow_id,),
    )


__all__ = [
    "ActivationReceipt",
    "DurableDraft",
    "WorkflowIdentity",
    "activation_receipt",
    "load_draft_for_step",
    "lock_next_expired_workflow_instance",
    "lock_workflow_for_attempt",
    "mark_workflow_authority_revoked",
    "mark_workflow_cancelled",
    "mark_workflow_completed",
    "record_draft",
    "record_workflow",
    "workflow_exists",
]
