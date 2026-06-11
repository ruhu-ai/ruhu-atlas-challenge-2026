"""add browser approval context

Revision ID: 0071
Revises: 0070
Create Date: 2026-05-01 15:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE browser_task_approvals ADD COLUMN IF NOT EXISTS context_json JSON NOT NULL DEFAULT '{}'::json")
    op.execute("ALTER TABLE browser_task_approvals ALTER COLUMN context_json DROP DEFAULT")


def downgrade() -> None:
    op.drop_column("browser_task_approvals", "context_json")
