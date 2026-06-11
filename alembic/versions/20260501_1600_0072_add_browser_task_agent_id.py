"""add browser task agent id

Revision ID: 0072
Revises: 0071
Create Date: 2026-05-01 16:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS agent_id VARCHAR(255)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_browser_tasks_agent_id ON browser_tasks (agent_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_browser_tasks_agent_id")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS agent_id")
