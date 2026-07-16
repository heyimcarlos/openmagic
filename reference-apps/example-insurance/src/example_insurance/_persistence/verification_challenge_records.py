"""Private transaction-bound persistence for verification challenges and sessions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance.verification_commands import (
    ChallengeTerminalResolution,
    ProtectedCommandOutcome,
)

ChallengeState = Literal[
    "pending",
    "accepted",
    "expired",
    "delivery_failed",
    "attempts_exhausted",
    "rejected",
]
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
    if value == "attempts_exhausted":
        return "attempts_exhausted"
    if value == "rejected":
        return "rejected"
    raise RuntimeError("Verification Challenge has an invalid state")


def _protected_command_state(value: object) -> ProtectedCommandState:
    if value == "waiting":
        return "waiting"
    if value == "authorized":
        return "authorized"
    if value == "rejected":
        return "rejected"
    raise RuntimeError("Protected Command has an invalid state")


def _protected_command_outcome(value: object) -> ProtectedCommandOutcome | None:
    if value is None:
        return None
    if value == "authorized":
        return "authorized"
    if value == "approval_required":
        return "approval_required"
    if value == "authority_revoked":
        return "authority_revoked"
    if value == "identifier_revoked":
        return "identifier_revoked"
    if value == "workflow_closed":
        return "workflow_closed"
    if value == "wrong_party":
        return "wrong_party"
    if value == "wrong_purpose":
        return "wrong_purpose"
    if value == "wrong_thread":
        return "wrong_thread"
    if value == "verification_expired":
        return "verification_expired"
    if value == "verification_delivery_failed":
        return "verification_delivery_failed"
    if value == "verification_attempts_exhausted":
        return "verification_attempts_exhausted"
    raise RuntimeError("Protected Command has an invalid outcome")


@dataclass(frozen=True)
class DurableChallenge:
    challenge_id: UUID
    protected_command_id: UUID
    party_id: UUID
    thread_id: UUID
    protected_workflow_id: UUID
    purpose: str
    destination_identifier_id: UUID
    destination_thread_id: UUID
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
            destination_thread_id=UUID(str(record["destination_thread_id"])),
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
    outcome: ProtectedCommandOutcome | None

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
            outcome=_protected_command_outcome(record["outcome"]),
        )


@dataclass(frozen=True)
class ChallengeIdentity:
    delivery_instance_id: UUID
    protected_workflow_id: UUID

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ChallengeIdentity:
        return cls(
            delivery_instance_id=UUID(str(record["delivery_instance_id"])),
            protected_workflow_id=UUID(str(record["protected_workflow_id"])),
        )


@dataclass(frozen=True)
class PendingChallengeIdentity:
    challenge_id: UUID
    delivery_instance_id: UUID

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> PendingChallengeIdentity:
        return cls(
            challenge_id=UUID(str(record["challenge_id"])),
            delivery_instance_id=UUID(str(record["delivery_instance_id"])),
        )


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
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT s.session_id FROM example_insurance.verification_sessions AS s "
            "JOIN example_insurance.party_identifiers AS i "
            "ON i.identifier_id = s.identifier_id "
            "WHERE s.party_id = %s AND s.thread_id = %s AND s.revoked_at IS NULL "
            "AND s.expires_at > clock_timestamp() AND i.party_id = s.party_id "
            "AND i.delivery_thread_id = s.identifier_thread_id "
            "AND i.verified_at IS NOT NULL AND i.revoked_at IS NULL "
            "ORDER BY s.expires_at DESC LIMIT 1 FOR UPDATE OF s, i",
            (party_id, thread_id),
        ).fetchone()
    return UUID(str(record["session_id"])) if record is not None else None


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
    destination_thread_id: UUID,
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
        "purpose, destination_identifier_id, destination_thread_id, delivery_workflow_id, "
        "delivery_instance_id, state, expires_at) VALUES "
        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', "
        "clock_timestamp() + (%s * interval '1 second'))",
        (
            challenge_id,
            protected_command_id,
            party_id,
            thread_id,
            workflow_id,
            purpose,
            destination_identifier_id,
            destination_thread_id,
            delivery_workflow_id,
            delivery_instance_id,
            challenge_ttl_seconds,
        ),
    )


def lock_challenge(connection: Connection[tuple[Any, ...]], challenge_id: UUID) -> DurableChallenge:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT challenge_id, protected_command_id, party_id, thread_id, "
            "protected_workflow_id, purpose, destination_identifier_id, "
            "destination_thread_id, delivery_workflow_id, delivery_instance_id, state, "
            "failed_attempts, expires_at FROM example_insurance.verification_challenges "
            "WHERE challenge_id = %s FOR UPDATE",
            (challenge_id,),
        ).fetchone()
    if record is None:
        raise RuntimeError("Verification Challenge is unavailable")
    return DurableChallenge.decode(record)


def lock_challenge_and_command(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> tuple[DurableChallenge, DurableProtectedCommand] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        challenge = cursor.execute(
            "SELECT challenge_id, protected_command_id, party_id, thread_id, "
            "protected_workflow_id, purpose, destination_identifier_id, destination_thread_id, "
            "delivery_workflow_id, delivery_instance_id, state, failed_attempts, expires_at "
            "FROM example_insurance.verification_challenges WHERE challenge_id = %s FOR UPDATE",
            (challenge_id,),
        ).fetchone()
        if challenge is None:
            return None
        protected = cursor.execute(
            "SELECT protected_command_id, workflow_id, thread_id, party_id, purpose, "
            "approval_grant_id, state, outcome FROM example_insurance.protected_commands "
            "WHERE protected_command_id = %s FOR UPDATE",
            (challenge["protected_command_id"],),
        ).fetchone()
    if protected is None:
        raise RuntimeError("Waiting protected Command is unavailable")
    return DurableChallenge.decode(challenge), DurableProtectedCommand.decode(protected)


def read_challenge_identity(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> ChallengeIdentity | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT delivery_instance_id, protected_workflow_id FROM "
            "example_insurance.verification_challenges WHERE challenge_id = %s",
            (challenge_id,),
        ).fetchone()
    return ChallengeIdentity.decode(record) if record is not None else None


def pending_challenge_identities(
    connection: Connection[tuple[Any, ...]],
    *,
    party_id: UUID,
    thread_id: UUID | None,
    protected_workflow_id: UUID,
) -> tuple[PendingChallengeIdentity, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        if thread_id is None:
            records = cursor.execute(
                "SELECT challenge_id, delivery_instance_id FROM "
                "example_insurance.verification_challenges WHERE party_id = %s "
                "AND protected_workflow_id = %s AND state = 'pending' "
                "ORDER BY created_at, challenge_id",
                (party_id, protected_workflow_id),
            ).fetchall()
        else:
            records = cursor.execute(
                "SELECT challenge_id, delivery_instance_id FROM "
                "example_insurance.verification_challenges WHERE party_id = %s "
                "AND thread_id = %s AND protected_workflow_id = %s "
                "AND state = 'pending' ORDER BY created_at, challenge_id",
                (party_id, thread_id, protected_workflow_id),
            ).fetchall()
    return tuple(PendingChallengeIdentity.decode(record) for record in records)


def challenge_is_expired(
    connection: Connection[tuple[Any, ...]], challenge: DurableChallenge
) -> bool:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT %s <= clock_timestamp() AS expired",
            (challenge.expires_at,),
        ).fetchone()
    return record is None or bool(record["expired"])


def record_failed_code(
    connection: Connection[tuple[Any, ...]],
    challenge: DurableChallenge,
    *,
    maximum_attempts: int,
) -> bool:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "UPDATE example_insurance.verification_challenges SET failed_attempts = "
            "LEAST(failed_attempts + 1, %s), state = CASE WHEN failed_attempts + 1 >= %s "
            "THEN 'attempts_exhausted' ELSE state END "
            "WHERE challenge_id = %s AND state = 'pending' RETURNING state",
            (maximum_attempts, maximum_attempts, challenge.challenge_id),
        ).fetchone()
    exhausted = record is not None and record["state"] == "attempts_exhausted"
    if exhausted:
        resolve_protected_command(
            connection,
            protected_command_id=challenge.protected_command_id,
            outcome="verification_attempts_exhausted",
            delivery_id=None,
        )
    return exhausted


def resolve_terminal_challenge(
    connection: Connection[tuple[Any, ...]],
    *,
    challenge: DurableChallenge,
    resolution: ChallengeTerminalResolution,
) -> None:
    if resolution == "verification_expired":
        state: ChallengeState = "expired"
    elif resolution == "verification_delivery_failed":
        state = "delivery_failed"
    elif resolution == "verification_attempts_exhausted":
        state = "attempts_exhausted"
    else:
        state = "rejected"
    connection.execute(
        "UPDATE example_insurance.verification_challenges SET state = %s "
        "WHERE challenge_id = %s AND state = 'pending'",
        (state, challenge.challenge_id),
    )
    resolve_protected_command(
        connection,
        protected_command_id=challenge.protected_command_id,
        outcome=resolution,
        delivery_id=None,
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
        "(session_id, challenge_id, party_id, thread_id, identifier_id, "
        "identifier_thread_id, expires_at) VALUES (%s, %s, %s, %s, %s, %s, "
        "clock_timestamp() + (%s * interval '1 second'))",
        (
            session_id,
            challenge.challenge_id,
            challenge.party_id,
            challenge.thread_id,
            challenge.destination_identifier_id,
            challenge.destination_thread_id,
            session_ttl_seconds,
        ),
    )
    return session_id


def resolve_protected_command(
    connection: Connection[tuple[Any, ...]],
    *,
    protected_command_id: UUID,
    outcome: ProtectedCommandOutcome,
    delivery_id: UUID | None,
) -> None:
    if (outcome == "authorized") != (delivery_id is not None):
        raise ValueError("Protected Command outcome and Delivery must agree")
    state = "authorized" if outcome == "authorized" else "rejected"
    connection.execute(
        "UPDATE example_insurance.protected_commands SET state = %s, outcome = %s, "
        "authorized_delivery_id = %s, resolved_at = clock_timestamp() "
        "WHERE protected_command_id = %s AND state = 'waiting'",
        (state, outcome, delivery_id, protected_command_id),
    )


__all__ = [
    "ChallengeIdentity",
    "DurableChallenge",
    "DurableProtectedCommand",
    "PendingChallengeIdentity",
    "active_session",
    "challenge_is_expired",
    "establish_session",
    "lock_challenge",
    "lock_challenge_and_command",
    "pending_challenge_identities",
    "read_challenge_identity",
    "record_authorized_command",
    "record_challenge",
    "record_failed_code",
    "resolve_protected_command",
    "resolve_terminal_challenge",
]
