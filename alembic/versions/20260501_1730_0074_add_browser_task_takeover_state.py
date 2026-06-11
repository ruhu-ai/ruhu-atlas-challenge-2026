"""add browser task takeover state

Revision ID: 0074
Revises: 0073
Create Date: 2026-05-01 17:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "browser_tasks",
        sa.Column("operator_takeover_owner_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "browser_tasks",
        sa.Column("operator_takeover_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_browser_tasks_operator_takeover_owner_id",
        "browser_tasks",
        ["operator_takeover_owner_id"],
    )
    op.create_index(
        "ix_browser_tasks_operator_takeover_expires_at",
        "browser_tasks",
        ["operator_takeover_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_browser_tasks_operator_takeover_expires_at", table_name="browser_tasks")
    op.drop_index("ix_browser_tasks_operator_takeover_owner_id", table_name="browser_tasks")
    op.drop_column("browser_tasks", "operator_takeover_expires_at")
    op.drop_column("browser_tasks", "operator_takeover_owner_id")
