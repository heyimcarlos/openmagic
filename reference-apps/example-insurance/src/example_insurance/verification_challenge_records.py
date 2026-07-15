"""Transaction-bound persistence for verification challenges and sessions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from openmagic_runtime.delivery import lock_delivery_presentation
from psycopg import Connection
from psycopg.rows import dict_row

ChallengeState = Literal["pending", "accepted", "expired", "delivery_failed"]
ProtectedCommandState = Literal["waiting", "authorized", "rejected"]


def _challenge_state(value: object) -> ChallengeState:
    if value == "pending":
        return "pending"
    if value == "accepted":
        return "accepted"
    if value == "expired":
        return "expired"
    if value == "delivery_failed":
        return "delivery_failed"
    raise RuntimeError("Verification Challenge has an invalid state")


def _protected_command_state(value: object) -> ProtectedCommandState:
    if value == "waiting":
        return "waiting"
    if value == "authorized":
        return "authorized"
    if value == "rejected":
        return "rejected"
    raise RuntimeError("Protected Command has an invalid state")


@dataclass(frozen=True)
class DurableChallenge:
    challenge_id: UUID
    protected_command_id: UUID
    party_id: UUID
    thread_id: UUID
    protected_workflow_id: UUID
    purpose: str
    destination_identifier_id: UUID
    delivery_workflow_id: UUID
    delivery_instance_id: UUID
    state: ChallengeState
    failed_attempts: int
    expires_at: datetime

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DurableChallenge:
        return cls(
            challenge_id=UUID(str(record["challenge_id"])),
            protected_command_id=UUID(str(record["protected_command_id"])),
            party_id=UUID(str(record["party_id"])),
            thread_id=UUID(str(record["thread_id"])),
            protected_workflow_id=UUID(str(record["protected_workflow_id"])),
            purpose=str(record["purpose"]),
            destination_identifier_id=UUID(str(record["destination_identifier_id"])),
            delivery_workflow_id=UUID(str(record["delivery_workflow_id"])),
            delivery_instance_id=UUID(str(record["delivery_instance_id"])),
            state=_challenge_state(record["state"]),
            failed_attempts=int(record["failed_attempts"]),
            expires_at=record["expires_at"],
        )


@dataclass(frozen=True)
class DurableProtectedCommand:
    protected_command_id: UUID
    workflow_id: UUID
    thread_id: UUID
    party_id: UUID
    purpose: str
    approval_grant_id: UUID
    state: ProtectedCommandState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DurableProtectedCommand:
        return cls(
            protected_command_id=UUID(str(record["protected_command_id"])),
            workflow_id=UUID(str(record["workflow_id"])),
            thread_id=UUID(str(record["thread_id"])),
            party_id=UUID(str(record["party_id"])),
            purpose=str(record["purpose"]),
            approval_grant_id=UUID(str(record["approval_grant_id"])),
            state=_protected_command_state(record["state"]),
        )


@dataclass(frozen=True)
class ProtectedRenewalDetails:
    policy_number: str
    policyholder_name: str
    renewal_date: str


@dataclass(frozen=True)
class PendingChallenge:
    challenge_id: UUID
    delivery_workflow_id: UUID
    delivery_instance_id: UUID


def record_authorized_command(
    connection: Connection[tuple[Any, ...]],
    *,
    protected_command_id: UUID,
    party_id: UUID,
    workflow_id: UUID,
    thread_id: UUID,
    purpose: str,
    approval_grant_id: UUID,
    delivery_id: UUID,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.protected_commands "
        "(protected_command_id, workflow_id, thread_id, party_id, purpose, "
        "approval_grant_id, state, outcome, authorized_delivery_id, resolved_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'authorized', 'authorized', %s, "
        "clock_timestamp())",
        (
            protected_command_id,
            workflow_id,
            thread_id,
            party_id,
            purpose,
            approval_grant_id,
            delivery_id,
        ),
    )


def active_session(
    connection: Connection[tuple[Any, ...]], *, party_id: UUID, thread_id: UUID
) -> UUID | None:
    row = connection.execute(
        "SELECT s.session_id FROM example_insurance.verification_sessions AS s "
        "JOIN example_insurance.party_identifiers AS i ON i.identifier_id = s.identifier_id "
        "WHERE s.party_id = %s AND s.thread_id = %s AND s.revoked_at IS NULL "
        "AND s.expires_at > clock_timestamp() AND i.party_id = s.party_id "
        "AND i.verified_at IS NOT NULL AND i.revoked_at IS NULL "
        "ORDER BY s.expires_at DESC LIMIT 1 FOR UPDATE OF s, i",
        (party_id, thread_id),
    ).fetchone()
    return UUID(str(row[0])) if row is not None else None


def pending_challenge(
    connection: Connection[tuple[Any, ...]], *, party_id: UUID, thread_id: UUID
) -> PendingChallenge | None:
    row = connection.execute(
        "SELECT challenge_id, delivery_workflow_id, delivery_instance_id FROM "
        "example_insurance.verification_challenges WHERE party_id = %s AND thread_id = %s "
        "AND state = 'pending' FOR UPDATE",
        (party_id, thread_id),
    ).fetchone()
    if row is None:
        return None
    return PendingChallenge(UUID(str(row[0])), UUID(str(row[1])), UUID(str(row[2])))


def record_challenge(
    connection: Connection[tuple[Any, ...]],
    *,
    protected_command_id: UUID,
    party_id: UUID,
    workflow_id: UUID,
    thread_id: UUID,
    purpose: str,
    approval_grant_id: UUID,
    challenge_id: UUID,
    destination_identifier_id: UUID,
    delivery_workflow_id: UUID,
    delivery_instance_id: UUID,
    challenge_ttl_seconds: int,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.protected_commands "
        "(protected_command_id, workflow_id, thread_id, party_id, purpose, "
        "approval_grant_id, state) VALUES (%s, %s, %s, %s, %s, %s, 'waiting')",
        (
            protected_command_id,
            workflow_id,
            thread_id,
            party_id,
            purpose,
            approval_grant_id,
        ),
    )
    connection.execute(
        "INSERT INTO example_insurance.verification_challenges "
        "(challenge_id, protected_command_id, party_id, thread_id, protected_workflow_id, "
        "purpose, destination_identifier_id, delivery_workflow_id, delivery_instance_id, "
        "state, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', "
        "clock_timestamp() + (%s * interval '1 second'))",
        (
            challenge_id,
            protected_command_id,
            party_id,
            thread_id,
            workflow_id,
            purpose,
            destination_identifier_id,
            delivery_workflow_id,
            delivery_instance_id,
            challenge_ttl_seconds,
        ),
    )
    connection.execute(
        "INSERT INTO example_insurance.verification_workflows "
        "(workflow_id, instance_id, challenge_id, protected_workflow_id, lifecycle) "
        "VALUES (%s, %s, %s, %s, 'active')",
        (delivery_workflow_id, delivery_instance_id, challenge_id, workflow_id),
    )


def lock_challenge_and_command(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> tuple[DurableChallenge, DurableProtectedCommand] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        challenge = cursor.execute(
            "SELECT challenge_id, protected_command_id, party_id, thread_id, "
            "protected_workflow_id, purpose, destination_identifier_id, "
            "delivery_workflow_id, delivery_instance_id, state, failed_attempts, expires_at "
            "FROM example_insurance.verification_challenges WHERE challenge_id = %s FOR UPDATE",
            (challenge_id,),
        ).fetchone()
        if challenge is None:
            return None
        protected = cursor.execute(
            "SELECT protected_command_id, workflow_id, thread_id, party_id, purpose, "
            "approval_grant_id, state FROM example_insurance.protected_commands "
            "WHERE protected_command_id = %s FOR UPDATE",
            (challenge["protected_command_id"],),
        ).fetchone()
    if protected is None:
        raise RuntimeError("Waiting protected Command is unavailable")
    return DurableChallenge.decode(challenge), DurableProtectedCommand.decode(protected)


def read_challenge_identity(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> tuple[UUID, UUID] | None:
    row = connection.execute(
        "SELECT delivery_instance_id, protected_workflow_id FROM "
        "example_insurance.verification_challenges WHERE challenge_id = %s",
        (challenge_id,),
    ).fetchone()
    if row is None:
        return None
    return UUID(str(row[0])), UUID(str(row[1]))


def challenge_is_expired(
    connection: Connection[tuple[Any, ...]], challenge: DurableChallenge
) -> bool:
    row = connection.execute(
        "SELECT %s <= clock_timestamp()",
        (challenge.expires_at,),
    ).fetchone()
    return row is None or bool(row[0])


def challenge_delivery_confirmed(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> bool:
    row = connection.execute(
        "SELECT v.delivery_event_id, c.thread_id FROM "
        "example_insurance.verification_workflows AS v "
        "JOIN example_insurance.verification_challenges AS c "
        "ON c.challenge_id = v.challenge_id WHERE v.challenge_id = %s",
        (challenge_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return False
    presentation = lock_delivery_presentation(
        connection,
        domain_event_id=UUID(str(row[0])),
        thread_id=UUID(str(row[1])),
    )
    return presentation is not None and presentation.status == "delivered"


def expire_challenge(connection: Connection[tuple[Any, ...]], challenge_id: UUID) -> None:
    connection.execute(
        "UPDATE example_insurance.verification_challenges SET state = 'expired' "
        "WHERE challenge_id = %s AND state = 'pending'",
        (challenge_id,),
    )


def record_failed_code(connection: Connection[tuple[Any, ...]], challenge_id: UUID) -> None:
    connection.execute(
        "UPDATE example_insurance.verification_challenges SET failed_attempts = "
        "LEAST(failed_attempts + 1, 5) WHERE challenge_id = %s AND state = 'pending'",
        (challenge_id,),
    )


def establish_session(
    connection: Connection[tuple[Any, ...]],
    *,
    challenge: DurableChallenge,
    session_ttl_seconds: int,
) -> UUID:
    session_id = uuid4()
    connection.execute(
        "UPDATE example_insurance.verification_challenges SET state = 'accepted', "
        "accepted_at = clock_timestamp() WHERE challenge_id = %s AND state = 'pending'",
        (challenge.challenge_id,),
    )
    connection.execute(
        "INSERT INTO example_insurance.verification_sessions "
        "(session_id, challenge_id, party_id, thread_id, identifier_id, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, "
        "clock_timestamp() + (%s * interval '1 second'))",
        (
            session_id,
            challenge.challenge_id,
            challenge.party_id,
            challenge.thread_id,
            challenge.destination_identifier_id,
            session_ttl_seconds,
        ),
    )
    return session_id


def renewal_details(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> ProtectedRenewalDetails:
    row = connection.execute(
        "SELECT policy_number, policyholder_name, renewal_date FROM "
        "example_insurance.renewal_workflows WHERE workflow_id = %s",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Protected renewal details are unavailable")
    return ProtectedRenewalDetails(str(row[0]), str(row[1]), str(row[2]))


def resolve_protected_command(
    connection: Connection[tuple[Any, ...]],
    *,
    protected_command_id: UUID,
    outcome: str,
    delivery_id: UUID | None,
) -> None:
    state = "authorized" if outcome == "authorized" else "rejected"
    connection.execute(
        "UPDATE example_insurance.protected_commands SET state = %s, outcome = %s, "
        "authorized_delivery_id = %s, resolved_at = clock_timestamp() "
        "WHERE protected_command_id = %s AND state = 'waiting'",
        (state, outcome, delivery_id, protected_command_id),
    )


__all__ = [
    "DurableChallenge",
    "DurableProtectedCommand",
    "PendingChallenge",
    "ProtectedRenewalDetails",
    "active_session",
    "challenge_delivery_confirmed",
    "challenge_is_expired",
    "establish_session",
    "expire_challenge",
    "lock_challenge_and_command",
    "pending_challenge",
    "read_challenge_identity",
    "record_authorized_command",
    "record_challenge",
    "record_failed_code",
    "renewal_details",
    "resolve_protected_command",
]
