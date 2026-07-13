"""Add durable step-up verification challenges.

Revision ID: 0d7c8a6f4b21
Revises: c91e6c45f0ac
Create Date: 2026-07-13 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0d7c8a6f4b21"
down_revision: str | Sequence[str] | None = "c91e6c45f0ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verification_challenges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_party_id", sa.UUID(), nullable=False),
        sa.Column("interaction_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column("operation_arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("operation_fingerprint", sa.Text(), nullable=False),
        sa.Column("request_cause_id", sa.Text(), nullable=False),
        sa.Column("destination_identifier_id", sa.UUID(), nullable=False),
        sa.Column("created_event_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_cause_id", sa.Text(), nullable=True),
        sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "failed_attempts <= max_attempts",
            name=op.f("ck_verification_challenges_attempt_budget"),
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name=op.f("ck_verification_challenges_failed_attempts_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name=op.f("ck_verification_challenges_max_attempts_positive"),
        ),
        sa.CheckConstraint(
            "purpose IN ('sensitive_read', 'sensitive_write')",
            name=op.f("ck_verification_challenges_purpose"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'superseded', 'expired', 'failed')",
            name=op.f("ck_verification_challenges_status"),
        ),
        sa.CheckConstraint(
            "(status = 'verified' AND verified_at IS NOT NULL "
            "AND verified_cause_id IS NOT NULL AND authorization_expires_at IS NOT NULL) "
            "OR (status <> 'verified' AND verified_at IS NULL "
            "AND verified_cause_id IS NULL AND authorization_expires_at IS NULL)",
            name=op.f("ck_verification_challenges_verification_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_party_id"],
            ["parties.id"],
            name="fk_verification_challenges_actor_party",
        ),
        sa.ForeignKeyConstraint(
            ["created_event_id"],
            ["workflow_events.id"],
            name="fk_verification_challenges_created_event",
        ),
        sa.ForeignKeyConstraint(
            ["destination_identifier_id"],
            ["party_identifiers.id"],
            name="fk_verification_challenges_destination_identifier",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_verification_challenges_workflow",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_challenges")),
    )
    op.create_index(
        "ix_verification_challenges_authorization",
        "verification_challenges",
        [
            "actor_party_id",
            "interaction_id",
            "workflow_id",
            "purpose",
            "authorization_expires_at",
        ],
        unique=False,
        postgresql_where=sa.text("status = 'verified'"),
    )
    op.create_index(
        "uq_verification_challenges_pending_interaction",
        "verification_challenges",
        ["actor_party_id", "interaction_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_verification_challenges_pending_interaction",
        table_name="verification_challenges",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        "ix_verification_challenges_authorization",
        table_name="verification_challenges",
        postgresql_where=sa.text("status = 'verified'"),
    )
    op.drop_table("verification_challenges")
