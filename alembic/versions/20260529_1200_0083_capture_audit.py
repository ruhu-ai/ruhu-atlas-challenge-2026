"""add capture audit table

Revision ID: 0083_capture_audit
Revises: 0082_rename_tool_kind_values
Create Date: 2026-05-29 12:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0083_capture_audit"
down_revision = "0082_rename_tool_kind_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=True),
        sa.Column("fact_name", sa.Text(), nullable=False),
        sa.Column("storage_scope", sa.Text(), nullable=False, server_default="conversation"),
        sa.Column("retention_policy", sa.Text(), nullable=False, server_default="conversation"),
        sa.Column("sensitivity", sa.Text(), nullable=False, server_default="personal"),
        sa.Column("audit_raw_policy", sa.Text(), nullable=False, server_default="hash"),
        sa.Column("raw_value_hash", sa.Text(), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.JSON(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("replaced_previous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_capture_audit_conversation", "capture_audit", ["conversation_id", "turn_id"])
    op.create_index("idx_capture_audit_org_fact", "capture_audit", ["organization_id", "fact_name", "created_at"])
    op.create_index("idx_capture_audit_scope", "capture_audit", ["organization_id", "storage_scope", "created_at"])
    op.create_index("idx_capture_audit_created", "capture_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_capture_audit_created", table_name="capture_audit")
    op.drop_index("idx_capture_audit_scope", table_name="capture_audit")
    op.drop_index("idx_capture_audit_org_fact", table_name="capture_audit")
    op.drop_index("idx_capture_audit_conversation", table_name="capture_audit")
    op.drop_table("capture_audit")
