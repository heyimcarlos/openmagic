"""Record original authenticated interaction Causes.

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
        "interaction_causes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("cause_type", sa.Text(), nullable=False),
        sa.Column("actor_party_id", sa.UUID(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cause_type IN ('message', 'ui_action')",
            name=op.f("ck_interaction_causes_cause_type"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_party_id"],
            ["parties.id"],
            name="fk_interaction_causes_actor_party",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interaction_causes")),
    )
    op.drop_index(
        "uq_workflow_events_approval_cause",
        table_name="workflow_events",
        postgresql_where=sa.text("event_type = 'approval_granted'"),
    )
    op.create_index(
        "uq_workflow_events_approval_cause",
        "workflow_events",
        ["cause_type", "cause_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'approval_granted'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workflow_events_approval_cause",
        table_name="workflow_events",
        postgresql_where=sa.text("event_type = 'approval_granted'"),
    )
    op.create_index(
        "uq_workflow_events_approval_cause",
        "workflow_events",
        ["job_id", "cause_type", "cause_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'approval_granted'"),
    )
    op.drop_table("interaction_causes")
