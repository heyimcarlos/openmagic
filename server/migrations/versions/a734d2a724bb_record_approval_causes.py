"""Record authenticated Approval Causes.

Revision ID: a734d2a724bb
Revises: f42a4bf67c21
Create Date: 2026-07-12 18:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a734d2a724bb"
down_revision: str | Sequence[str] | None = "f42a4bf67c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_causes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("cause_type", sa.Text(), nullable=False),
        sa.Column("actor_party_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cause_type IN ('message', 'ui_action')",
            name=op.f("ck_approval_causes_cause_type"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_party_id"],
            ["parties.id"],
            name="fk_approval_causes_actor_party",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_causes")),
    )


def downgrade() -> None:
    op.drop_table("approval_causes")
