"""Transaction-bound persistence for deterministic verification Workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from openmagic_runtime.kernel.records import expired_attempt_instances
from psycopg import Connection
from psycopg.rows import dict_row

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
    delivery_id: UUID | None


def lock_verification_attempt(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> VerificationAttemptState:
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow = cursor.execute(
            "SELECT workflow_id, challenge_id, lifecycle, delivery_id FROM "
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
    delivery_id = workflow["delivery_id"]
    return VerificationAttemptState(
        workflow_id=UUID(str(workflow["workflow_id"])),
        challenge=DurableChallenge.decode(challenge),
        lifecycle=_workflow_lifecycle(workflow["lifecycle"]),
        delivery_id=UUID(str(delivery_id)) if delivery_id is not None else None,
    )


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
    "lock_verification_attempt",
]
