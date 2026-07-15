"""Atomic terminal reconciliation for pending verification Challenges."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from openmagic_runtime.delivery import lock_delivery_presentation
from openmagic_runtime.kernel.records import lock_instance
from psycopg import Connection

from example_insurance.verification_challenge_records import (
    DurableChallenge,
    challenge_is_expired,
    expire_challenge,
    lock_challenge_and_command,
    mark_challenge_terminal,
    pending_challenge_identities,
    resolve_protected_command,
)
from example_insurance.verification_commands import ProtectedOutcome
from example_insurance.verification_workflow_records import verification_delivery_identity

DeliveryStatus = Literal["pending", "delivered", "failed", "unavailable"]


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
    ) -> None:
        identities = pending_challenge_identities(
            connection,
            party_id=party_id,
            thread_id=thread_id,
            protected_workflow_id=workflow_id,
        )
        for identity in identities:
            lock_instance(connection, identity.delivery_instance_id)

    def reconcile_pending(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        party_id: UUID,
        thread_id: UUID | None,
        workflow_id: UUID,
        terminal_outcome: ProtectedOutcome | None,
    ) -> None:
        identities = pending_challenge_identities(
            connection,
            party_id=party_id,
            thread_id=thread_id,
            protected_workflow_id=workflow_id,
        )
        for identity in identities:
            if lock_instance(connection, identity.delivery_instance_id) is None:
                continue
            locked = lock_challenge_and_command(connection, identity.challenge_id)
            if locked is None:
                continue
            challenge, protected = locked
            if challenge.state != "pending":
                continue
            if terminal_outcome is not None:
                mark_challenge_terminal(connection, challenge.challenge_id, "rejected")
                resolve_protected_command(
                    connection,
                    protected_command_id=protected.protected_command_id,
                    outcome=terminal_outcome,
                    delivery_id=None,
                )
            elif challenge_is_expired(connection, challenge):
                expire_challenge(connection, challenge.challenge_id)
                resolve_protected_command(
                    connection,
                    protected_command_id=protected.protected_command_id,
                    outcome="verification_expired",
                    delivery_id=None,
                )
            elif self.delivery_status(connection, challenge) == "failed":
                mark_challenge_terminal(connection, challenge.challenge_id, "delivery_failed")
                resolve_protected_command(
                    connection,
                    protected_command_id=protected.protected_command_id,
                    outcome="verification_delivery_failed",
                    delivery_id=None,
                )


__all__ = ["DeliveryStatus", "VerificationChallengeLifecycle"]
