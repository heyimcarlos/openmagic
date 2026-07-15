"""Transaction-bound persistence for renewal Workflow orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor
from openmagic_runtime.kernel.records import (
    activated_by_attempt,
    expired_attempt_instances,
    lock_instance,
)
from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance.renewal_commands import StartRenewalOutreachInput


@dataclass(frozen=True)
class WorkflowIdentity:
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> WorkflowIdentity:
        return cls(
            workflow_id=UUID(str(record["workflow_id"])),
            instance_id=UUID(str(record["instance_id"])),
            thread_id=UUID(str(record["thread_id"])),
        )


@dataclass(frozen=True)
class DurableDraft:
    draft_id: UUID
    agent_run_id: UUID
    presentation_fingerprint: str
    policyholder_email: str
    subject: str
    body: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DurableDraft:
        return cls(
            draft_id=UUID(str(record["draft_id"])),
            agent_run_id=UUID(str(record["agent_run_id"])),
            presentation_fingerprint=str(record["presentation_fingerprint"]),
            policyholder_email=str(record["policyholder_email"]),
            subject=str(record["subject"]),
            body=str(record["body"]),
        )


@dataclass(frozen=True)
class ActivationReceipt:
    steps: dict[str, UUID]
    waits: dict[str, UUID]


@dataclass(frozen=True)
class ProtectedRenewalDetails:
    policy_number: str
    policyholder_name: str
    renewal_date: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ProtectedRenewalDetails:
        return cls(
            policy_number=str(record["policy_number"]),
            policyholder_name=str(record["policyholder_name"]),
            renewal_date=str(record["renewal_date"]),
        )


def workflow_exists(connection: Connection[tuple[Any, ...]], workflow_id: UUID) -> bool:
    row = connection.execute(
        "SELECT 1 FROM example_insurance.renewal_workflows WHERE workflow_id = %s",
        (workflow_id,),
    ).fetchone()
    return row is not None


def _read_workflow_identity(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> WorkflowIdentity | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT workflow_id, instance_id, thread_id "
            "FROM example_insurance.renewal_workflows WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchone()
    return WorkflowIdentity.decode(record) if record is not None else None


def lock_instance_for_workflow(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> WorkflowIdentity | None:
    identity = _read_workflow_identity(connection, workflow_id)
    if identity is None or lock_instance(connection, identity.instance_id) is None:
        return None
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT workflow_id, instance_id, thread_id FROM "
            "example_insurance.renewal_workflows WHERE workflow_id = %s FOR UPDATE",
            (workflow_id,),
        ).fetchone()
    if record is None:
        return None
    locked = WorkflowIdentity.decode(record)
    if locked.instance_id != identity.instance_id:
        raise RuntimeError("Renewal Workflow changed its exact Instance binding")
    return locked


def protected_renewal_details(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> ProtectedRenewalDetails:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT policy_number, policyholder_name, renewal_date FROM "
            "example_insurance.renewal_workflows WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchone()
    if record is None:
        raise RuntimeError("Protected renewal details are unavailable")
    return ProtectedRenewalDetails.decode(record)


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


def expired_workflow_instances(
    connection: Connection[tuple[Any, ...]],
) -> tuple[UUID, ...]:
    candidates = expired_attempt_instances(connection)
    if not candidates:
        return ()
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT instance_id FROM example_insurance.renewal_workflows "
            "WHERE instance_id = ANY(%s) AND lifecycle = 'active' "
            "ORDER BY created_at, workflow_id",
            (list(candidates),),
        ).fetchall()
    return tuple(UUID(str(record["instance_id"])) for record in records)


def lock_workflow_after_instance(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> WorkflowIdentity:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT workflow_id, instance_id, thread_id "
            "FROM example_insurance.renewal_workflows "
            "WHERE instance_id = %s FOR UPDATE",
            (instance_id,),
        ).fetchone()
    if record is None:
        raise RuntimeError("Renewal Workflow is unavailable")
    return WorkflowIdentity.decode(record)


def activation_receipt(
    connection: Connection[tuple[Any, ...]],
    *,
    instance_id: UUID,
    source_attempt_id: UUID,
) -> ActivationReceipt:
    activated = activated_by_attempt(
        connection,
        instance_id=instance_id,
        attempt_id=source_attempt_id,
    )
    return ActivationReceipt(steps=activated.steps, waits=activated.waits)


def load_draft_for_step(connection: Connection[tuple[Any, ...]], step_id: UUID) -> DurableDraft:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT draft_id, agent_run_id, presentation_fingerprint, "
            "policyholder_email, subject, body FROM example_insurance.renewal_drafts "
            "WHERE step_id = %s",
            (step_id,),
        ).fetchone()
    if record is None:
        raise RuntimeError("Accepted draft result has no durable draft")
    return DurableDraft.decode(record)


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


def bind_draft_ready_event(
    connection: Connection[tuple[Any, ...]], *, draft_id: UUID, event_id: UUID
) -> None:
    row = connection.execute(
        "UPDATE example_insurance.renewal_drafts SET ready_event_id = %s "
        "WHERE draft_id = %s AND ready_event_id IS NULL RETURNING draft_id",
        (event_id, draft_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Renewal Draft ready event is already bound or unavailable")


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
    "ProtectedRenewalDetails",
    "WorkflowIdentity",
    "activation_receipt",
    "bind_draft_ready_event",
    "expired_workflow_instances",
    "load_draft_for_step",
    "lock_instance_for_workflow",
    "lock_workflow_after_instance",
    "mark_workflow_authority_revoked",
    "mark_workflow_cancelled",
    "mark_workflow_completed",
    "protected_renewal_details",
    "record_draft",
    "record_workflow",
    "workflow_exists",
]
