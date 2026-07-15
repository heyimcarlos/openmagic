"""Atomic terminal reconciliation for pending verification Challenges."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from openmagic_runtime.delivery import lock_delivery_presentation
from openmagic_runtime.kernel.records import lock_instance
from psycopg import Connection

from example_insurance.verification_challenge_records import (
    DurableChallenge,
    PendingChallengeIdentity,
    challenge_is_expired,
    lock_challenge_and_command,
    pending_challenge_identities,
    resolve_terminal_challenge,
)
from example_insurance.verification_commands import ProtectedPolicyRejection
from example_insurance.verification_workflow_records import verification_delivery_identity

DeliveryStatus = Literal["pending", "delivered", "failed", "unavailable"]
LockedPendingChallenges = tuple[PendingChallengeIdentity, ...]


class VerificationChallengeLifecycle:
    @staticmethod
    def delivery_status(
        connection: Connection[tuple[Any, ...]],
        challenge: DurableChallenge,
    ) -> DeliveryStatus:
        identity = verification_delivery_identity(connection, challenge.challenge_id)
        if identity is None or identity.delivery_event_id is None:
            return "unavailable"
        presentation = lock_delivery_presentation(
            connection,
            domain_event_id=identity.delivery_event_id,
            thread_id=challenge.destination_thread_id,
        )
        if presentation is None:
            return "unavailable"
        if presentation.status == "delivered":
            return "delivered"
        if presentation.status in {"failed", "suppressed"}:
            return "failed"
        return "pending"

    @staticmethod
    def lock_pending_instances(
        connection: Connection[tuple[Any, ...]],
        *,
        party_id: UUID,
        thread_id: UUID | None,
        workflow_id: UUID,
    ) -> LockedPendingChallenges:
        identities = pending_challenge_identities(
            connection,
            party_id=party_id,
            thread_id=thread_id,
            protected_workflow_id=workflow_id,
        )
        locked: list[PendingChallengeIdentity] = []
        for identity in identities:
            if lock_instance(connection, identity.delivery_instance_id) is not None:
                locked.append(identity)
        return tuple(locked)

    def reconcile_superseded_identifier(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        pending: LockedPendingChallenges,
        current_identifier_id: UUID,
    ) -> None:
        for identity in pending:
            locked = lock_challenge_and_command(connection, identity.challenge_id)
            if locked is None:
                continue
            challenge, _ = locked
            if (
                challenge.state != "pending"
                or challenge.destination_identifier_id == current_identifier_id
            ):
                continue
            resolve_terminal_challenge(
                connection,
                challenge=challenge,
                resolution="identifier_revoked",
            )

    def reconcile_pending(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        pending: LockedPendingChallenges,
        terminal_outcome: ProtectedPolicyRejection | None,
    ) -> None:
        for identity in pending:
            locked = lock_challenge_and_command(connection, identity.challenge_id)
            if locked is None:
                continue
            challenge, _ = locked
            if challenge.state != "pending":
                continue
            if terminal_outcome is not None:
                resolve_terminal_challenge(
                    connection,
                    challenge=challenge,
                    resolution=terminal_outcome,
                )
            elif challenge_is_expired(connection, challenge):
                resolve_terminal_challenge(
                    connection,
                    challenge=challenge,
                    resolution="verification_expired",
                )
            elif self.delivery_status(connection, challenge) == "failed":
                resolve_terminal_challenge(
                    connection,
                    challenge=challenge,
                    resolution="verification_delivery_failed",
                )


__all__ = ["DeliveryStatus", "LockedPendingChallenges", "VerificationChallengeLifecycle"]
