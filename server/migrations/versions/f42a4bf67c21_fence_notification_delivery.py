"""Fence Notification delivery acknowledgements.

Revision ID: f42a4bf67c21
Revises: d815c00eb002
Create Date: 2026-07-12 17:12:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f42a4bf67c21"
down_revision: str | Sequence[str] | None = "d815c00eb002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("delivered_by", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE notifications SET delivered_by = 'legacy_delivery' WHERE status = 'delivered'"
        )
    )
    op.drop_constraint(op.f("ck_notifications_delivery_shape"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_delivery_shape"),
        "notifications",
        "(status = 'queued' AND claimed_by IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NULL AND delivered_by IS NULL) "
        "OR (status = 'delivering' AND claimed_by IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL "
        "AND delivered_by IS NULL) "
        "OR (status = 'delivered' AND claimed_by IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NOT NULL AND delivered_by IS NOT NULL) "
        "OR (status = 'failed' AND claimed_by IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NULL AND delivered_by IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_notifications_delivery_shape"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_delivery_shape"),
        "notifications",
        "(status = 'queued' AND claimed_by IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NULL) "
        "OR (status = 'delivering' AND claimed_by IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL) "
        "OR (status = 'delivered' AND claimed_by IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NOT NULL) "
        "OR (status = 'failed' AND claimed_by IS NULL AND lease_expires_at IS NULL "
        "AND delivered_at IS NULL)",
    )
    op.drop_column("notifications", "delivered_by")
