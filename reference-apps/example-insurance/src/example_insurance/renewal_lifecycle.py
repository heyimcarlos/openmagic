"""Renewal authority revocation and lifecycle cancellation."""

from __future__ import annotations

from typing import Any

from openmagic_runtime.commands import StateConflict
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import CloseInstance
from psycopg import Connection

from example_insurance.renewal_commands import (
    CancelRenewalOutreach,
    CancelRenewalOutreachResult,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityResult,
)
from example_insurance.renewal_lifecycle_policy import (
    CancellationFacts,
    RenewalLifecyclePolicy,
)
from example_insurance.renewal_lifecycle_records import (
    cancel_workflow,
    lock_lifecycle_authority,
    lock_revocation_authority,
    revoke_authority,
)
from example_insurance.renewal_records import CommandEventLineage, record_event


class RenewalLifecycleControl:
    def __init__(self) -> None:
        self._policy = RenewalLifecyclePolicy()

    def revoke(
        self,
        command: RevokeRenewalAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeRenewalAuthorityResult:
        authority = lock_revocation_authority(connection, command.input.workflow_id)
        if not self._policy.authorizes_revocation(
            actor_kind=command.actor.kind,
            actor_id=command.actor.identifier,
        ):
            raise StateConflict("Actor is not authorized to revoke approval authority")
        if authority.authorized_actor_id != command.input.actor_id:
            raise StateConflict("Authority revocation targets another Actor")
        if authority.already_revoked:
            return RevokeRenewalAuthorityResult("already_revoked", command.input.workflow_id)
        revoke_authority(connection, command.input.workflow_id)
        lineage = CommandEventLineage(command.actor, command.command_id)
        record_event(
            connection,
            event_type="renewal.approval_authority.revoked",
            workflow_id=command.input.workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={"authorized_actor_id": command.input.actor_id},
        )
        return RevokeRenewalAuthorityResult("revoked", command.input.workflow_id)

    def cancel(
        self,
        command: CancelRenewalOutreach,
        connection: Connection[tuple[Any, ...]],
    ) -> CancelRenewalOutreachResult:
        authority = lock_lifecycle_authority(connection, command.input.workflow_id)
        actor_authorized = self._policy.actor_can_cancel(
            actor_kind=command.actor.kind,
            actor_id=command.actor.identifier,
            authorized_actor_kind=authority.authorized_actor_kind,
            authorized_actor_id=authority.authorized_actor_id,
        )
        outcome = self._policy.cancellation_outcome(
            CancellationFacts(
                lifecycle=authority.lifecycle,
                actor_authorized=actor_authorized,
                dispatch_boundary_crossed=authority.dispatch_boundary_crossed,
            )
        )
        if outcome == "unauthorized":
            raise StateConflict("Actor is not authorized to cancel renewal outreach")
        if outcome == "already_completed":
            return CancelRenewalOutreachResult(
                "already_completed", command.input.workflow_id, authority.instance_id
            )
        if outcome == "already_cancelled":
            return CancelRenewalOutreachResult(
                "already_cancelled", command.input.workflow_id, authority.instance_id
            )
        if outcome == "too_late":
            return CancelRenewalOutreachResult(
                "too_late", command.input.workflow_id, authority.instance_id
            )
        cancel_workflow(connection, command.input.workflow_id)
        lineage = CommandEventLineage(command.actor, command.command_id)
        record_event(
            connection,
            event_type="renewal.outreach.cancelled",
            workflow_id=command.input.workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={"instance_id": str(authority.instance_id)},
        )
        KernelControl(connection).close(CloseInstance(command.command_id, authority.instance_id))
        return CancelRenewalOutreachResult(
            "cancelled", command.input.workflow_id, authority.instance_id
        )


__all__ = ["RenewalLifecycleControl"]
