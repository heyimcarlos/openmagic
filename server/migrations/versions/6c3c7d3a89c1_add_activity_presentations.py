"""Add bounded Interaction Agent activity presentations.

Revision ID: 6c3c7d3a89c1
Revises: b3a91ef6d245
Create Date: 2026-07-13 11:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6c3c7d3a89c1"
down_revision: str | Sequence[str] | None = "b3a91ef6d245"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interaction_activity_receipts",
        sa.Column("input_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "interaction_activity_receipts",
        sa.Column("presentation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interaction_activity_receipts", "presentation")
    op.drop_column("interaction_activity_receipts", "input_summary")
