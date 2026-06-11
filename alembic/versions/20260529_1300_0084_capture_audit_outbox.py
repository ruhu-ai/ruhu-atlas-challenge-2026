"""add capture audit outbox table

Revision ID: 0084_capture_audit_outbox
Revises: 0083_capture_audit
Create Date: 2026-05-29 13:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0084_capture_audit_outbox"
down_revision = "0083_capture_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_audit_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_capture_audit_outbox_status_next", "capture_audit_outbox", ["status", "next_attempt_at"])
    op.create_index("idx_capture_audit_outbox_conversation", "capture_audit_outbox", ["conversation_id", "turn_id"])


def downgrade() -> None:
    op.drop_index("idx_capture_audit_outbox_conversation", table_name="capture_audit_outbox")
    op.drop_index("idx_capture_audit_outbox_status_next", table_name="capture_audit_outbox")
    op.drop_table("capture_audit_outbox")
