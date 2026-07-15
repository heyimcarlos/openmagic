"""Evidence-backed renewal completion and Instance closure."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import CloseInstance
from psycopg import Connection

from example_insurance.renewal_completion_policy import RenewalCompletionPolicy
from example_insurance.renewal_completion_records import lock_completion_snapshot
from example_insurance.renewal_records import CommandEventLineage, record_event
from example_insurance.renewal_workflow_records import mark_workflow_completed


class RenewalCompletionControl:
    def __init__(self) -> None:
        self._policy = RenewalCompletionPolicy()

    def complete_if_ready(
        self,
        connection: Connection[tuple[Any, ...]],
        workflow_id: UUID,
        lineage: CommandEventLineage,
    ) -> None:
        snapshot = lock_completion_snapshot(connection, workflow_id)
        if snapshot is None or snapshot.lifecycle != "active":
            return
        if not self._policy.is_complete(
            steps=snapshot.steps,
            effects=snapshot.effects,
        ):
            return
        mark_workflow_completed(connection, workflow_id)
        record_event(
            connection,
            event_type="renewal.outreach.completed",
            workflow_id=workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={"instance_id": str(snapshot.instance_id)},
        )
        KernelControl(connection).close(
            CloseInstance(command_id=lineage.command_id, instance_id=snapshot.instance_id)
        )


__all__ = ["RenewalCompletionControl"]
