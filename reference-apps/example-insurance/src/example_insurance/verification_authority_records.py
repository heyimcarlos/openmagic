"""Transaction-bound persistence for verification authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance.verification_commands import (
    ProvisionVerificationAuthorityInput,
    VerificationAuthorityTarget,
)


@dataclass(frozen=True)
class AuthoritySnapshot:
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID
    lifecycle: Literal["active", "cancelled", "completed"]
    authorized_actor_id: str
    workflow_authority_revoked: bool
    party_exists: bool
    identifier_id: UUID | None
    identifier_current_and_verified: bool
    active_membership: bool
    active_broker_role: bool
    exact_approval_grant: bool


@dataclass(frozen=True)
class ProvisionedAuthority:
    identifier_id: UUID
    membership_id: UUID
    participant_id: UUID


def _renewal_lifecycle(value: object) -> Literal["active", "cancelled", "completed"]:
    if value == "active":
        return "active"
    if value == "cancelled":
        return "cancelled"
    if value == "completed":
        return "completed"
    raise RuntimeError("Renewal Workflow has an invalid lifecycle")


def provision_authority(
    connection: Connection[tuple[Any, ...]], value: ProvisionVerificationAuthorityInput
) -> ProvisionedAuthority:
    workflow = connection.execute(
        "SELECT authorized_actor_id FROM example_insurance.renewal_workflows "
        "WHERE workflow_id = %s FOR UPDATE",
        (value.workflow_id,),
    ).fetchone()
    if workflow is None or str(workflow[0]) != str(value.party_id):
        raise ValueError("Provisioned Party must be the renewal's authorized Actor")
    identifier_id = uuid4()
    membership_id = uuid4()
    participant_id = uuid4()
    connection.execute(
        "INSERT INTO example_insurance.parties (party_id, party_kind) "
        "VALUES (%s, 'person'), (%s, 'organization')",
        (value.party_id, value.organization_party_id),
    )
    connection.execute(
        "WITH timestamp AS (SELECT clock_timestamp() AS value) "
        "INSERT INTO example_insurance.party_identifiers "
        "(identifier_id, party_id, identifier_kind, canonical_value, verified_at, created_at) "
        "SELECT %s, %s, 'email', %s, value, value FROM timestamp",
        (identifier_id, value.party_id, value.email.strip().casefold()),
    )
    connection.execute(
        "INSERT INTO example_insurance.organization_memberships "
        "(membership_id, party_id, organization_party_id) VALUES (%s, %s, %s)",
        (membership_id, value.party_id, value.organization_party_id),
    )
    connection.execute(
        "INSERT INTO example_insurance.workflow_participants "
        "(participant_id, workflow_id, party_id, role) VALUES (%s, %s, %s, 'broker')",
        (participant_id, value.workflow_id, value.party_id),
    )
    return ProvisionedAuthority(identifier_id, membership_id, participant_id)


def revoke_authority(
    connection: Connection[tuple[Any, ...]],
    *,
    party_id: UUID,
    workflow_id: UUID,
    target: VerificationAuthorityTarget,
) -> bool:
    if target == "identifier":
        row = connection.execute(
            "UPDATE example_insurance.party_identifiers SET revoked_at = clock_timestamp() "
            "WHERE identifier_id = (SELECT identifier_id FROM "
            "example_insurance.party_identifiers WHERE party_id = %s "
            "AND identifier_kind = 'email' AND revoked_at IS NULL "
            "ORDER BY created_at DESC, identifier_id LIMIT 1 FOR UPDATE) "
            "RETURNING identifier_id",
            (party_id,),
        ).fetchone()
    elif target == "membership":
        row = connection.execute(
            "UPDATE example_insurance.organization_memberships SET revoked_at = "
            "clock_timestamp() WHERE membership_id = (SELECT membership_id FROM "
            "example_insurance.organization_memberships WHERE party_id = %s "
            "AND revoked_at IS NULL ORDER BY joined_at, membership_id LIMIT 1 FOR UPDATE) "
            "RETURNING membership_id",
            (party_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "UPDATE example_insurance.workflow_participants SET revoked_at = clock_timestamp() "
            "WHERE participant_id = (SELECT participant_id FROM "
            "example_insurance.workflow_participants WHERE workflow_id = %s AND party_id = %s "
            "AND role = 'broker' AND revoked_at IS NULL ORDER BY assigned_at, participant_id "
            "LIMIT 1 FOR UPDATE) RETURNING participant_id",
            (workflow_id, party_id),
        ).fetchone()
    return row is not None


def lock_authority(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    party_id: UUID,
    approval_grant_id: UUID,
) -> AuthoritySnapshot | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow = cursor.execute(
            "SELECT workflow_id, instance_id, thread_id, lifecycle, authorized_actor_id, "
            "authority_revoked_at IS NOT NULL AS authority_revoked "
            "FROM example_insurance.renewal_workflows WHERE workflow_id = %s FOR UPDATE",
            (workflow_id,),
        ).fetchone()
    if workflow is None:
        return None
    party = connection.execute(
        "SELECT party_id FROM example_insurance.parties WHERE party_id = %s FOR UPDATE",
        (party_id,),
    ).fetchone()
    with connection.cursor(row_factory=dict_row) as cursor:
        identifier = cursor.execute(
            "SELECT identifier_id, verified_at, revoked_at FROM "
            "example_insurance.party_identifiers WHERE party_id = %s "
            "AND identifier_kind = 'email' ORDER BY created_at DESC, identifier_id "
            "LIMIT 1 FOR UPDATE",
            (party_id,),
        ).fetchone()
    membership = connection.execute(
        "SELECT membership_id FROM example_insurance.organization_memberships "
        "WHERE party_id = %s AND revoked_at IS NULL ORDER BY joined_at, membership_id "
        "LIMIT 1 FOR UPDATE",
        (party_id,),
    ).fetchone()
    participant = connection.execute(
        "SELECT participant_id FROM example_insurance.workflow_participants "
        "WHERE workflow_id = %s AND party_id = %s AND role = 'broker' "
        "AND revoked_at IS NULL LIMIT 1 FOR UPDATE",
        (workflow_id, party_id),
    ).fetchone()
    grant = connection.execute(
        "SELECT approval_grant_id FROM example_insurance.approval_grants "
        "WHERE approval_grant_id = %s AND workflow_id = %s AND invalidated_at IS NULL "
        "FOR UPDATE",
        (approval_grant_id, workflow_id),
    ).fetchone()
    identifier_id = UUID(str(identifier["identifier_id"])) if identifier is not None else None
    return AuthoritySnapshot(
        workflow_id=UUID(str(workflow["workflow_id"])),
        instance_id=UUID(str(workflow["instance_id"])),
        thread_id=UUID(str(workflow["thread_id"])),
        lifecycle=_renewal_lifecycle(workflow["lifecycle"]),
        authorized_actor_id=str(workflow["authorized_actor_id"]),
        workflow_authority_revoked=bool(workflow["authority_revoked"]),
        party_exists=party is not None,
        identifier_id=identifier_id,
        identifier_current_and_verified=(
            identifier is not None
            and identifier["verified_at"] is not None
            and identifier["revoked_at"] is None
        ),
        active_membership=membership is not None,
        active_broker_role=participant is not None,
        exact_approval_grant=grant is not None,
    )


__all__ = [
    "AuthoritySnapshot",
    "ProvisionedAuthority",
    "lock_authority",
    "provision_authority",
    "revoke_authority",
]
