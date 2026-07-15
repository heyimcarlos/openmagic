"""Transaction-bound persistence for renewal authority and lifecycle changes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import StateConflict
from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance.renewal_grant_records import invalidate_unconsumed_grants
from example_insurance.renewal_lifecycle_policy import (
    WorkflowLifecycle,
    workflow_lifecycle,
)
from example_insurance.renewal_workflow_records import (
    lock_instance_for_workflow,
    mark_workflow_authority_revoked,
    mark_workflow_cancelled,
)


@dataclass(frozen=True)
class RevocationAuthority:
    workflow_id: UUID
    authorized_actor_id: str
    already_revoked: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RevocationAuthority:
        return cls(
            workflow_id=UUID(str(record["workflow_id"])),
            authorized_actor_id=str(record["authorized_actor_id"]),
            already_revoked=bool(record["already_revoked"]),
        )


@dataclass(frozen=True)
class LifecycleAuthority:
    workflow_id: UUID
    instance_id: UUID
    lifecycle: WorkflowLifecycle
    authorized_actor_kind: str
    authorized_actor_id: str
    dispatch_boundary_crossed: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> LifecycleAuthority:
        return cls(
            workflow_id=UUID(str(record["workflow_id"])),
            instance_id=UUID(str(record["instance_id"])),
            lifecycle=workflow_lifecycle(record["lifecycle"]),
            authorized_actor_kind=str(record["authorized_actor_kind"]),
            authorized_actor_id=str(record["authorized_actor_id"]),
            dispatch_boundary_crossed=bool(record["dispatch_boundary_crossed"]),
        )


def lock_revocation_authority(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> RevocationAuthority:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT workflow_id, authorized_actor_id, "
            "authority_revoked_at IS NOT NULL AS already_revoked "
            "FROM example_insurance.renewal_workflows "
            "WHERE workflow_id = %s FOR UPDATE",
            (workflow_id,),
        ).fetchone()
    if record is None:
        raise StateConflict("Renewal Workflow does not exist")
    return RevocationAuthority.decode(record)


def lock_lifecycle_authority(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> LifecycleAuthority:
    identity = lock_instance_for_workflow(connection, workflow_id)
    if identity is None:
        raise StateConflict("Renewal Workflow does not exist")
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT r.workflow_id, r.instance_id, r.lifecycle, r.authorized_actor_kind, "
            "r.authorized_actor_id "
            "FROM example_insurance.renewal_workflows r "
            "WHERE r.workflow_id = %s FOR UPDATE OF r",
            (workflow_id,),
        ).fetchone()
    if record is None:
        raise StateConflict("Renewal Workflow does not exist")
    with connection.cursor(row_factory=dict_row) as cursor:
        boundary = cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM example_insurance.external_effects "
            "WHERE workflow_id = %s) AS dispatch_boundary_crossed",
            (workflow_id,),
        ).fetchone()
    if boundary is None:
        raise RuntimeError("Dispatch boundary observation is unavailable")
    authority = LifecycleAuthority.decode({**record, **boundary})
    if authority.instance_id != identity.instance_id:
        raise StateConflict("Renewal Workflow identity changed while locking")
    return authority


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
