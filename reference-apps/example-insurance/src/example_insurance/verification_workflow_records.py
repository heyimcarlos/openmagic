"""Transaction-bound persistence for deterministic verification Workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.kernel.records import expired_attempt_instances
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from example_insurance.application_event_records import actor_record, cause_record
from example_insurance.verification_challenge_records import DurableChallenge

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
    challenge: DurableChallenge
    lifecycle: VerificationWorkflowLifecycle


def lock_verification_attempt(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> VerificationAttemptState:
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow = cursor.execute(
            "SELECT workflow_id, challenge_id, lifecycle FROM "
            "example_insurance.verification_workflows WHERE instance_id = %s FOR UPDATE",
            (instance_id,),
        ).fetchone()
        if workflow is None:
            raise RuntimeError("Verification Workflow is unavailable")
        challenge = cursor.execute(
            "SELECT challenge_id, protected_command_id, party_id, thread_id, "
            "protected_workflow_id, purpose, destination_identifier_id, "
            "delivery_workflow_id, delivery_instance_id, state, failed_attempts, expires_at "
            "FROM example_insurance.verification_challenges WHERE challenge_id = %s FOR UPDATE",
            (workflow["challenge_id"],),
        ).fetchone()
    if challenge is None:
        raise RuntimeError("Verification Challenge is unavailable")
    return VerificationAttemptState(
        workflow_id=UUID(str(workflow["workflow_id"])),
        challenge=DurableChallenge.decode(challenge),
        lifecycle=_workflow_lifecycle(workflow["lifecycle"]),
    )


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
    candidates = expired_attempt_instances(connection)
    if not candidates:
        return ()
    rows = connection.execute(
        "SELECT instance_id FROM example_insurance.verification_workflows "
        "WHERE instance_id = ANY(%s) AND lifecycle = 'active' "
        "ORDER BY created_at, workflow_id",
        (list(candidates),),
    ).fetchall()
    return tuple(UUID(str(row[0])) for row in rows)


def has_active_verification_workflows(
    connection: Connection[tuple[Any, ...]],
) -> bool:
    row = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM example_insurance.verification_workflows "
        "WHERE lifecycle = 'active')"
    ).fetchone()
    return row is not None and bool(row[0])


def fail_verification_workflow(
    connection: Connection[tuple[Any, ...]], *, workflow_id: UUID, challenge_id: UUID
) -> None:
    connection.execute(
        "UPDATE example_insurance.verification_workflows SET lifecycle = 'failed', "
        "completed_at = clock_timestamp() WHERE workflow_id = %s AND lifecycle = 'active'",
        (workflow_id,),
    )
    connection.execute(
        "UPDATE example_insurance.verification_challenges SET state = 'delivery_failed' "
        "WHERE challenge_id = %s AND state = 'pending'",
        (challenge_id,),
    )


__all__ = [
    "VerificationAttemptState",
    "complete_verification_workflow",
    "expired_verification_instances",
    "fail_verification_workflow",
    "has_active_verification_workflows",
    "lock_verification_attempt",
    "record_verification_event",
]
