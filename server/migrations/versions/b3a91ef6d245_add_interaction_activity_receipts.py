"""Add sanitized Interaction Agent activity receipts.

Revision ID: b3a91ef6d245
Revises: 0d7c8a6f4b21
Create Date: 2026-07-13 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3a91ef6d245"
down_revision: str | Sequence[str] | None = "0d7c8a6f4b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interaction_activity_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cause_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sequence > 0",
            name=op.f("ck_interaction_activity_receipts_sequence_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_interaction_activity_receipts_status"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) "
            "OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL)",
            name=op.f("ck_interaction_activity_receipts_terminal_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["cause_id"],
            ["interaction_causes.id"],
            name="fk_interaction_activity_receipts_cause",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_interaction_activity_receipts_workflow",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interaction_activity_receipts")),
        sa.UniqueConstraint(
            "cause_id",
            "sequence",
            name="uq_interaction_activity_receipts_cause_sequence",
        ),
    )


def downgrade() -> None:
    op.drop_table("interaction_activity_receipts")
