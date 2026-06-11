"""add retry metadata to ticketing activity

Revision ID: 0014_ticketing_retry_queue
Revises: 0013_journey_foundation
Create Date: 2026-04-11 03:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_ticketing_retry_queue"
down_revision = "0013_journey_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticketing_activity",
        sa.Column("retry_status", sa.String(length=32), nullable=False, server_default=sa.text("'none'")),
    )
    op.add_column(
        "ticketing_activity",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ticketing_activity",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ticketing_activity_retry_status", "ticketing_activity", ["retry_status"])
    op.create_index("ix_ticketing_activity_next_retry_at", "ticketing_activity", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_ticketing_activity_next_retry_at", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_retry_status", table_name="ticketing_activity")
    op.drop_column("ticketing_activity", "last_attempted_at")
    op.drop_column("ticketing_activity", "next_retry_at")
    op.drop_column("ticketing_activity", "retry_status")
