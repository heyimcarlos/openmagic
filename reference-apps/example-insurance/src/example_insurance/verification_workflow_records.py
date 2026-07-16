"""Transaction-bound persistence for deterministic verification Workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.kernel.inspection import KernelTransactionInspection
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from example_insurance.application_event_records import actor_record, cause_record

VerificationWorkflowLifecycle = Literal["active", "completed", "failed"]


def _workflow_lifecycle(value: object) -> VerificationWorkflowLifecycle:
    if value == "active":
        return "active"
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    raise RuntimeError("Verification Workflow has an invalid lifecycle")


@dataclass(frozen=True)
class VerificationAttemptState:
    workflow_id: UUID
    challenge_id: UUID
    lifecycle: VerificationWorkflowLifecycle

    @classmethod
    def decode(cls, record: dict[str, Any]) -> VerificationAttemptState:
        return cls(
            workflow_id=UUID(str(record["workflow_id"])),
            challenge_id=UUID(str(record["challenge_id"])),
            lifecycle=_workflow_lifecycle(record["lifecycle"]),
        )


@dataclass(frozen=True)
class VerificationDeliveryIdentity:
    delivery_event_id: UUID | None

    @classmethod
    def decode(cls, record: dict[str, Any]) -> VerificationDeliveryIdentity:
        value = record["delivery_event_id"]
        return cls(delivery_event_id=UUID(str(value)) if value is not None else None)


def lock_verification_attempt(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> VerificationAttemptState:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT workflow_id, challenge_id, lifecycle FROM "
            "example_insurance.verification_workflows WHERE instance_id = %s FOR UPDATE",
            (instance_id,),
        ).fetchone()
    if record is None:
        raise RuntimeError("Verification Workflow is unavailable")
    return VerificationAttemptState.decode(record)


def record_verification_workflow(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    instance_id: UUID,
    challenge_id: UUID,
    protected_workflow_id: UUID,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.verification_workflows "
        "(workflow_id, instance_id, challenge_id, protected_workflow_id, lifecycle) "
        "VALUES (%s, %s, %s, %s, 'active')",
        (workflow_id, instance_id, challenge_id, protected_workflow_id),
    )


def verification_delivery_identity(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> VerificationDeliveryIdentity | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT delivery_event_id FROM example_insurance.verification_workflows "
            "WHERE challenge_id = %s FOR UPDATE",
            (challenge_id,),
        ).fetchone()
    return VerificationDeliveryIdentity.decode(record) if record is not None else None


def record_verification_event(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    actor: Actor,
    cause: Cause,
    payload: dict[str, Any],
) -> UUID:
    event_id = uuid4()
    connection.execute(
        "INSERT INTO example_insurance.verification_events "
        "(event_id, workflow_id, event_type, schema_version, actor, cause, payload) "
        "VALUES (%s, %s, 'verification.challenge.delivery_ready', 1, %s, %s, %s)",
        (
            event_id,
            workflow_id,
            Jsonb(actor_record(actor)),
            Jsonb(cause_record(cause)),
            Jsonb(payload),
        ),
    )
    return event_id


def complete_verification_workflow(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    event_id: UUID,
    delivery_id: UUID,
) -> None:
    connection.execute(
        "UPDATE example_insurance.verification_workflows SET lifecycle = 'completed', "
        "delivery_event_id = %s, delivery_id = %s, completed_at = clock_timestamp() "
        "WHERE workflow_id = %s AND lifecycle = 'active'",
        (event_id, delivery_id, workflow_id),
    )


def expired_verification_instances(
    connection: Connection[tuple[Any, ...]],
) -> tuple[UUID, ...]:
    candidates = KernelTransactionInspection(connection).expired_attempt_instances()
    if not candidates:
        return ()
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT instance_id FROM example_insurance.verification_workflows "
            "WHERE instance_id = ANY(%s) AND lifecycle = 'active' "
            "ORDER BY created_at, workflow_id",
            (list(candidates),),
        ).fetchall()
    return tuple(UUID(str(record["instance_id"])) for record in records)


def has_active_verification_workflows(
    connection: Connection[tuple[Any, ...]],
) -> bool:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM example_insurance.verification_workflows "
            "WHERE lifecycle = 'active') AS active"
        ).fetchone()
    return record is not None and bool(record["active"])


def fail_verification_workflow(
    connection: Connection[tuple[Any, ...]], *, workflow_id: UUID
) -> None:
    connection.execute(
        "UPDATE example_insurance.verification_workflows SET lifecycle = 'failed', "
        "completed_at = clock_timestamp() WHERE workflow_id = %s AND lifecycle = 'active'",
        (workflow_id,),
    )


__all__ = [
    "VerificationAttemptState",
    "VerificationDeliveryIdentity",
    "complete_verification_workflow",
    "expired_verification_instances",
    "fail_verification_workflow",
    "has_active_verification_workflows",
    "lock_verification_attempt",
    "record_verification_event",
    "record_verification_workflow",
    "verification_delivery_identity",
]
