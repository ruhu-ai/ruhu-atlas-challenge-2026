"""Add browser task pack execution fields.

Revision ID: 0069
Revises: 0068
Create Date: 2026-05-01 13:00:00
"""

from __future__ import annotations

from alembic import op


revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS task_pack_id VARCHAR(255)")
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS task_pack_version VARCHAR(64)")
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS start_url TEXT")
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS input_json JSON DEFAULT '{}'::json")
    op.execute("CREATE INDEX IF NOT EXISTS ix_browser_tasks_task_pack_id ON browser_tasks (task_pack_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_browser_tasks_task_pack_id")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS input_json")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS start_url")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS task_pack_version")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS task_pack_id")
