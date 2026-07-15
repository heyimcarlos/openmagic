"""Transaction-bound persistence for renewal authority and lifecycle changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import StateConflict
from psycopg import Connection

from example_insurance.renewal_grant_records import invalidate_unconsumed_grants
from example_insurance.renewal_lifecycle_policy import (
    WorkflowLifecycle,
    workflow_lifecycle,
)
from example_insurance.renewal_workflow_records import (
    mark_workflow_authority_revoked,
    mark_workflow_cancelled,
)


@dataclass(frozen=True)
class RevocationAuthority:
    workflow_id: UUID
    authorized_actor_id: str
    already_revoked: bool


@dataclass(frozen=True)
class LifecycleAuthority:
    workflow_id: UUID
    instance_id: UUID
    lifecycle: WorkflowLifecycle
    authorized_actor_kind: str
    authorized_actor_id: str
    dispatch_boundary_crossed: bool


def lock_revocation_authority(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> RevocationAuthority:
    row = connection.execute(
        "SELECT authorized_actor_id, authority_revoked_at IS NOT NULL "
        "FROM example_insurance.renewal_workflows WHERE workflow_id = %s FOR UPDATE",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise StateConflict("Renewal Workflow does not exist")
    return RevocationAuthority(workflow_id, str(row[0]), bool(row[1]))


def lock_lifecycle_authority(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> LifecycleAuthority:
    row = connection.execute(
        "SELECT r.instance_id, r.lifecycle, r.authorized_actor_kind, "
        "r.authorized_actor_id "
        "FROM example_insurance.renewal_workflows r "
        "WHERE r.workflow_id = %s FOR UPDATE OF r",
        (workflow_id,),
    ).fetchone()
    if row is None:
        raise StateConflict("Renewal Workflow does not exist")
    crossed = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM example_insurance.external_effects WHERE workflow_id = %s)",
        (workflow_id,),
    ).fetchone()
    if crossed is None:
        raise RuntimeError("Dispatch boundary observation is unavailable")
    return LifecycleAuthority(
        workflow_id=workflow_id,
        instance_id=UUID(str(row[0])),
        lifecycle=workflow_lifecycle(row[1]),
        authorized_actor_kind=str(row[2]),
        authorized_actor_id=str(row[3]),
        dispatch_boundary_crossed=bool(crossed[0]),
    )


def revoke_authority(connection: Connection[tuple[Any, ...]], workflow_id: UUID) -> None:
    mark_workflow_authority_revoked(connection, workflow_id)
    invalidate_unconsumed_grants(connection, workflow_id)


def cancel_workflow(connection: Connection[tuple[Any, ...]], workflow_id: UUID) -> None:
    invalidate_unconsumed_grants(connection, workflow_id)
    mark_workflow_cancelled(connection, workflow_id)


__all__ = [
    "LifecycleAuthority",
    "RevocationAuthority",
    "cancel_workflow",
    "lock_lifecycle_authority",
    "lock_revocation_authority",
    "revoke_authority",
]
