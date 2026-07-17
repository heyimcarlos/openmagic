"""Private transaction-bound persistence for verification authority."""

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
from example_insurance.verification_policy import VerificationAuthorityFacts


@dataclass(frozen=True)
class ProvisionedAuthority:
    identifier_id: UUID
    membership_id: UUID
    participant_id: UUID


@dataclass(frozen=True)
class IdentifierDestination:
    identifier_id: UUID
    party_id: UUID
    canonical_email: str
    delivery_thread_id: UUID

    @classmethod
    def decode(cls, record: dict[str, Any]) -> IdentifierDestination:
        return cls(
            identifier_id=UUID(str(record["identifier_id"])),
            party_id=UUID(str(record["party_id"])),
            canonical_email=str(record["canonical_value"]),
            delivery_thread_id=UUID(str(record["delivery_thread_id"])),
        )


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
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow = cursor.execute(
            "SELECT authorized_actor_kind, authorized_actor_id, thread_id FROM "
            "example_insurance.renewal_workflows "
            "WHERE workflow_id = %s FOR UPDATE",
            (value.workflow_id,),
        ).fetchone()
    if (
        workflow is None
        or workflow["authorized_actor_kind"] != "party"
        or str(workflow["authorized_actor_id"]) != str(value.party_id)
    ):
        raise ValueError("Provisioned Party must be the renewal's authorized Actor")
    if UUID(str(workflow["thread_id"])) == value.delivery_thread_id:
        raise ValueError("Verification requires a distinct identifier email Thread")
    connection.execute(
        "INSERT INTO example_insurance.parties (party_id, party_kind) "
        "VALUES (%s, 'person'), (%s, 'organization') ON CONFLICT DO NOTHING",
        (value.party_id, value.organization_party_id),
    )
    with connection.cursor(row_factory=dict_row) as cursor:
        parties = cursor.execute(
            "SELECT party_id, party_kind FROM example_insurance.parties "
            "WHERE party_id = ANY(%s) FOR UPDATE",
            ([value.party_id, value.organization_party_id],),
        ).fetchall()
    party_kinds = {UUID(str(record["party_id"])): record["party_kind"] for record in parties}
    if party_kinds != {
        value.party_id: "person",
        value.organization_party_id: "organization",
    }:
        raise ValueError("Verification authority requires exact person and organization Parties")
    canonical_email = value.email.strip().casefold()
    with connection.cursor(row_factory=dict_row) as cursor:
        identifier = cursor.execute(
            "SELECT identifier_id FROM example_insurance.party_identifiers "
            "WHERE party_id = %s AND identifier_kind = 'email' AND canonical_value = %s "
            "AND delivery_thread_id = %s AND revoked_at IS NULL FOR UPDATE",
            (value.party_id, canonical_email, value.delivery_thread_id),
        ).fetchone()
    if identifier is None:
        identifier_id = uuid4()
        connection.execute(
            "UPDATE example_insurance.party_identifiers SET revoked_at = clock_timestamp() "
            "WHERE party_id = %s AND identifier_kind = 'email' AND revoked_at IS NULL",
            (value.party_id,),
        )
        connection.execute(
            "WITH timestamp AS (SELECT clock_timestamp() AS value) "
            "INSERT INTO example_insurance.party_identifiers "
            "(identifier_id, party_id, identifier_kind, canonical_value, "
            "delivery_thread_id, verified_at, created_at) "
            "SELECT %s, %s, 'email', %s, %s, value, value FROM timestamp",
            (identifier_id, value.party_id, canonical_email, value.delivery_thread_id),
        )
    else:
        identifier_id = UUID(str(identifier["identifier_id"]))
    with connection.cursor(row_factory=dict_row) as cursor:
        membership = cursor.execute(
            "SELECT membership_id FROM example_insurance.organization_memberships "
            "WHERE party_id = %s AND organization_party_id = %s "
            "AND revoked_at IS NULL FOR UPDATE",
            (value.party_id, value.organization_party_id),
        ).fetchone()
    if membership is None:
        membership_id = uuid4()
        connection.execute(
            "INSERT INTO example_insurance.organization_memberships "
            "(membership_id, party_id, organization_party_id) VALUES (%s, %s, %s)",
            (membership_id, value.party_id, value.organization_party_id),
        )
    else:
        membership_id = UUID(str(membership["membership_id"]))
    with connection.cursor(row_factory=dict_row) as cursor:
        participant = cursor.execute(
            "SELECT participant_id FROM "
            "example_insurance.workflow_participants WHERE workflow_id = %s "
            "AND party_id = %s FOR UPDATE",
            (value.workflow_id, value.party_id),
        ).fetchone()
    if participant is None:
        participant_id = uuid4()
        connection.execute(
            "INSERT INTO example_insurance.workflow_participants "
            "(participant_id, workflow_id, party_id) VALUES (%s, %s, %s)",
            (participant_id, value.workflow_id, value.party_id),
        )
    else:
        participant_id = UUID(str(participant["participant_id"]))
    role_assignment = connection.execute(
        "SELECT role.role_assignment_id FROM "
        "example_insurance.workflow_role_assignments AS role "
        "JOIN example_insurance.organization_memberships AS membership "
        "ON membership.membership_id = role.membership_id "
        "AND membership.party_id = role.party_id "
        "WHERE role.participant_id = %s AND role.membership_id = %s "
        "AND role.role = 'broker' AND role.revoked_at IS NULL "
        "AND membership.revoked_at IS NULL FOR UPDATE OF role, membership",
        (participant_id, membership_id),
    ).fetchone()
    if role_assignment is None:
        connection.execute(
            "INSERT INTO example_insurance.workflow_role_assignments "
            "(role_assignment_id, participant_id, party_id, membership_id, role) "
            "VALUES (%s, %s, %s, %s, 'broker')",
            (uuid4(), participant_id, value.party_id, membership_id),
        )
    return ProvisionedAuthority(
        identifier_id=identifier_id,
        membership_id=membership_id,
        participant_id=participant_id,
    )


def lock_identifier_destination(
    connection: Connection[tuple[Any, ...]], *, party_id: UUID
) -> IdentifierDestination | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT identifier_id, party_id, canonical_value, delivery_thread_id "
            "FROM example_insurance.party_identifiers WHERE party_id = %s "
            "AND identifier_kind = 'email' AND verified_at IS NOT NULL "
            "AND revoked_at IS NULL ORDER BY created_at DESC, identifier_id DESC "
            "LIMIT 1 FOR UPDATE",
            (party_id,),
        ).fetchone()
    return IdentifierDestination.decode(record) if record is not None else None


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
            "WITH target AS (SELECT role.role_assignment_id, role.membership_id FROM "
            "example_insurance.workflow_role_assignments AS role "
            "JOIN example_insurance.workflow_participants AS participant "
            "ON participant.participant_id = role.participant_id "
            "AND participant.party_id = role.party_id "
            "JOIN example_insurance.organization_memberships AS membership "
            "ON membership.membership_id = role.membership_id "
            "AND membership.party_id = role.party_id "
            "WHERE participant.workflow_id = %s AND participant.party_id = %s "
            "AND role.role = 'broker' AND role.revoked_at IS NULL "
            "AND membership.revoked_at IS NULL "
            "ORDER BY role.assigned_at, role.role_assignment_id LIMIT 1 "
            "FOR UPDATE OF participant, role, membership), revoked_role AS ("
            "UPDATE example_insurance.workflow_role_assignments AS role "
            "SET revoked_at = clock_timestamp() FROM target "
            "WHERE role.role_assignment_id = target.role_assignment_id "
            "RETURNING target.membership_id) "
            "UPDATE example_insurance.organization_memberships AS membership "
            "SET revoked_at = clock_timestamp() FROM revoked_role "
            "WHERE membership.membership_id = revoked_role.membership_id "
            "RETURNING membership.membership_id",
            (workflow_id, party_id),
        ).fetchone()
    else:
        row = connection.execute(
            "UPDATE example_insurance.workflow_role_assignments AS role "
            "SET revoked_at = clock_timestamp() WHERE role.role_assignment_id = ("
            "SELECT assignment.role_assignment_id FROM "
            "example_insurance.workflow_role_assignments AS assignment "
            "JOIN example_insurance.workflow_participants AS participant "
            "ON participant.participant_id = assignment.participant_id "
            "WHERE participant.workflow_id = %s AND participant.party_id = %s "
            "AND assignment.role = 'broker' AND assignment.revoked_at IS NULL "
            "ORDER BY assignment.assigned_at, assignment.role_assignment_id "
            "LIMIT 1 FOR UPDATE OF assignment, participant) RETURNING role_assignment_id",
            (workflow_id, party_id),
        ).fetchone()
    return row is not None


def lock_authority(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow_id: UUID,
    party_id: UUID,
    approval_grant_id: UUID,
) -> VerificationAuthorityFacts | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow = cursor.execute(
            "SELECT workflow_id, instance_id, thread_id, lifecycle, authorized_actor_kind, "
            "authorized_actor_id, authority_revoked_at IS NOT NULL AS authority_revoked "
            "FROM example_insurance.renewal_workflows WHERE workflow_id = %s FOR UPDATE",
            (workflow_id,),
        ).fetchone()
    if workflow is None:
        return None
    with connection.cursor(row_factory=dict_row) as cursor:
        party = cursor.execute(
            "SELECT party_id, party_kind FROM example_insurance.parties "
            "WHERE party_id = %s FOR UPDATE",
            (party_id,),
        ).fetchone()
    with connection.cursor(row_factory=dict_row) as cursor:
        identifier = cursor.execute(
            "SELECT identifier_id, delivery_thread_id, verified_at, revoked_at FROM "
            "example_insurance.party_identifiers WHERE party_id = %s "
            "AND identifier_kind = 'email' ORDER BY created_at DESC, identifier_id "
            "LIMIT 1 FOR UPDATE",
            (party_id,),
        ).fetchone()
    broker_authority = connection.execute(
        "SELECT p.participant_id FROM example_insurance.workflow_participants AS p "
        "JOIN example_insurance.workflow_role_assignments AS r "
        "ON r.participant_id = p.participant_id AND r.party_id = p.party_id "
        "JOIN example_insurance.organization_memberships AS m "
        "ON m.membership_id = r.membership_id AND m.party_id = r.party_id "
        "JOIN example_insurance.parties AS person ON person.party_id = p.party_id "
        "AND person.party_kind = 'person' "
        "JOIN example_insurance.parties AS organization "
        "ON organization.party_id = m.organization_party_id "
        "AND organization.party_kind = 'organization' "
        "WHERE p.workflow_id = %s AND p.party_id = %s AND r.role = 'broker' "
        "AND r.revoked_at IS NULL AND m.revoked_at IS NULL "
        "FOR UPDATE OF p, r, m, person, organization",
        (workflow_id, party_id),
    ).fetchone()
    grant = connection.execute(
        "SELECT approval_grant_id FROM example_insurance.approval_grants "
        "WHERE approval_grant_id = %s AND workflow_id = %s AND invalidated_at IS NULL "
        "FOR UPDATE",
        (approval_grant_id, workflow_id),
    ).fetchone()
    identifier_id = UUID(str(identifier["identifier_id"])) if identifier is not None else None
    identifier_thread_id = (
        UUID(str(identifier["delivery_thread_id"])) if identifier is not None else None
    )
    return VerificationAuthorityFacts(
        workflow_id=UUID(str(workflow["workflow_id"])),
        instance_id=UUID(str(workflow["instance_id"])),
        thread_id=UUID(str(workflow["thread_id"])),
        lifecycle=_renewal_lifecycle(workflow["lifecycle"]),
        authorized_actor_kind=str(workflow["authorized_actor_kind"]),
        authorized_actor_id=str(workflow["authorized_actor_id"]),
        workflow_authority_revoked=bool(workflow["authority_revoked"]),
        party_is_person=party is not None and party["party_kind"] == "person",
        identifier_id=identifier_id,
        identifier_delivery_thread_id=identifier_thread_id,
        identifier_current_and_verified=(
            identifier is not None
            and identifier["verified_at"] is not None
            and identifier["revoked_at"] is None
        ),
        active_broker_authority=broker_authority is not None,
        exact_approval_grant=grant is not None,
    )


__all__ = [
    "IdentifierDestination",
    "ProvisionedAuthority",
    "lock_authority",
    "lock_identifier_destination",
    "provision_authority",
    "revoke_authority",
]
