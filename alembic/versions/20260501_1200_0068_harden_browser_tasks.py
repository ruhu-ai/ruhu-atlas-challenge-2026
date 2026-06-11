"""Harden browser task domain.

Revision ID: 0068
Revises: 0067
Create Date: 2026-05-01 12:00:00
"""

from __future__ import annotations

from alembic import op


revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_tasks (
            task_id VARCHAR(255) PRIMARY KEY,
            organization_id VARCHAR(255),
            conversation_id VARCHAR(255) NOT NULL,
            title VARCHAR(500) NOT NULL,
            summary TEXT,
            requested_channel VARCHAR(32) NOT NULL,
            state VARCHAR(32) NOT NULL,
            approval_state VARCHAR(32) NOT NULL,
            current_approval_id VARCHAR(255),
            lease_owner VARCHAR(255),
            lease_expires_at TIMESTAMP WITH TIME ZONE,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            metadata_json JSON DEFAULT '{}'::json,
            result_json JSON DEFAULT '{}'::json,
            error TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            started_at TIMESTAMP WITH TIME ZONE,
            finished_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_task_approvals (
            approval_id VARCHAR(255) PRIMARY KEY,
            task_id VARCHAR(255) NOT NULL REFERENCES browser_tasks(task_id) ON DELETE CASCADE,
            organization_id VARCHAR(255),
            conversation_id VARCHAR(255) NOT NULL,
            kind VARCHAR(64) NOT NULL,
            state VARCHAR(32) NOT NULL,
            prompt TEXT NOT NULL,
            decision_reason TEXT,
            requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE,
            decided_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_task_events (
            event_id VARCHAR(255) PRIMARY KEY,
            task_id VARCHAR(255) NOT NULL REFERENCES browser_tasks(task_id) ON DELETE CASCADE,
            organization_id VARCHAR(255),
            conversation_id VARCHAR(255) NOT NULL,
            event_sequence INTEGER NOT NULL DEFAULT 0,
            event_type VARCHAR(128) NOT NULL,
            message TEXT NOT NULL,
            metadata_json JSON DEFAULT '{}'::json,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(255)")
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE browser_task_approvals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE browser_task_events ADD COLUMN IF NOT EXISTS event_sequence INTEGER NOT NULL DEFAULT 0")
    op.execute(
        """
        WITH ordered AS (
            SELECT event_id, row_number() OVER (PARTITION BY task_id ORDER BY created_at, event_id) AS seq
            FROM browser_task_events
        )
        UPDATE browser_task_events AS e
        SET event_sequence = ordered.seq
        FROM ordered
        WHERE e.event_id = ordered.event_id AND COALESCE(e.event_sequence, 0) = 0
        """
    )
    for table, column in [
        ("browser_tasks", "conversation_id"),
        ("browser_tasks", "requested_channel"),
        ("browser_tasks", "state"),
        ("browser_tasks", "approval_state"),
        ("browser_tasks", "current_approval_id"),
        ("browser_tasks", "lease_owner"),
        ("browser_tasks", "lease_expires_at"),
        ("browser_task_approvals", "task_id"),
        ("browser_task_approvals", "conversation_id"),
        ("browser_task_approvals", "state"),
        ("browser_task_approvals", "expires_at"),
        ("browser_task_events", "task_id"),
        ("browser_task_events", "conversation_id"),
        ("browser_task_events", "event_type"),
        ("browser_task_events", "created_at"),
    ]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_browser_tasks_lease_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_browser_tasks_lease_owner")
    op.execute("DROP INDEX IF EXISTS ix_browser_task_approvals_expires_at")
    op.execute("ALTER TABLE browser_task_events DROP COLUMN IF EXISTS event_sequence")
    op.execute("ALTER TABLE browser_task_approvals DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS attempt_count")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS lease_expires_at")
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS lease_owner")
