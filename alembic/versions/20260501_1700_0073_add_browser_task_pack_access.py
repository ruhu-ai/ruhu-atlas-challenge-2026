"""add browser task pack access policy

Revision ID: 0073
Revises: 0072
Create Date: 2026-05-01 17:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_task_pack_access (
            access_id VARCHAR(255) PRIMARY KEY,
            organization_id VARCHAR(255),
            agent_id VARCHAR(255),
            pack_id VARCHAR(255) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_browser_task_pack_access_scope_pack
        ON browser_task_pack_access (
            COALESCE(organization_id, ''),
            COALESCE(agent_id, ''),
            pack_id
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_browser_task_pack_access_org ON browser_task_pack_access (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_browser_task_pack_access_agent ON browser_task_pack_access (agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_browser_task_pack_access_pack ON browser_task_pack_access (pack_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_browser_task_pack_access_pack")
    op.execute("DROP INDEX IF EXISTS ix_browser_task_pack_access_agent")
    op.execute("DROP INDEX IF EXISTS ix_browser_task_pack_access_org")
    op.execute("DROP INDEX IF EXISTS uq_browser_task_pack_access_scope_pack")
    op.execute("DROP TABLE IF EXISTS browser_task_pack_access")
