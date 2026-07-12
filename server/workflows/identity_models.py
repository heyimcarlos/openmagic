"""Relational Party records required by V0 Workflow retrieval authority."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class PartyRow(Base):
    __tablename__ = "parties"
    __table_args__ = (sa.CheckConstraint("kind IN ('person', 'organization')", name="kind"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class PartyIdentifierRow(Base):
    __tablename__ = "party_identifiers"
    __table_args__ = (
        sa.Index(
            "uq_party_identifiers_current_value",
            "kind",
            "value",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    party_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class OrganizationMembershipRow(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        sa.Index(
            "uq_organization_memberships_current",
            "person_party_id",
            "organization_party_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    person_party_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=False
    )
    organization_party_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class WorkflowParticipantRow(Base):
    __tablename__ = "workflow_participants"

    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("workflows.id"), primary_key=True
    )
    party_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("parties.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class WorkflowParticipantRoleRow(Base):
    __tablename__ = "workflow_participant_roles"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["workflow_id", "party_id"],
            ["workflow_participants.workflow_id", "workflow_participants.party_id"],
            name="fk_workflow_participant_roles_participant",
        ),
        sa.CheckConstraint(
            "role IN ('Broker', 'Reporter', 'Policyholder', 'Claimant')",
            name="role",
        ),
        sa.Index(
            "uq_workflow_participant_roles_current",
            "workflow_id",
            "party_id",
            "role",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    party_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
