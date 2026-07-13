"""Fence Workflow graph proposals by authenticated Cause.

Revision ID: c91e6c45f0ac
Revises: a734d2a724bb
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c91e6c45f0ac"
down_revision: str | Sequence[str] | None = "a734d2a724bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_workflow_events_proposal_cause",
        "workflow_events",
        ["cause_type", "cause_id"],
        unique=True,
        postgresql_where=("event_type = 'workflow_jobs_proposed' AND data ? 'proposal_digest'"),
    )


def downgrade() -> None:
    op.drop_index("uq_workflow_events_proposal_cause", table_name="workflow_events")
