"""Add browser task credential references.

Revision ID: 0070
Revises: 0069
Create Date: 2026-05-01 14:00:00
"""

from __future__ import annotations

from alembic import op


revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE browser_tasks ADD COLUMN IF NOT EXISTS credential_refs_json JSON DEFAULT '{}'::json")


def downgrade() -> None:
    op.execute("ALTER TABLE browser_tasks DROP COLUMN IF EXISTS credential_refs_json")
