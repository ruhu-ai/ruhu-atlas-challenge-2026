"""add browser operator commands

Revision ID: 0076
Revises: 0075
Create Date: 2026-05-01 18:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_operator_commands",
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("command_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("operator_id", sa.String(length=255), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["browser_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("command_id"),
    )
    op.create_index(
        "ix_browser_operator_commands_organization_id",
        "browser_operator_commands",
        ["organization_id"],
    )
    op.create_index("ix_browser_operator_commands_task_id", "browser_operator_commands", ["task_id"])
    op.create_index(
        "ix_browser_operator_commands_conversation_id",
        "browser_operator_commands",
        ["conversation_id"],
    )
    op.create_index(
        "ix_browser_operator_commands_operator_id",
        "browser_operator_commands",
        ["operator_id"],
    )
    op.create_index(
        "ix_browser_operator_commands_command_type",
        "browser_operator_commands",
        ["command_type"],
    )
    op.create_index("ix_browser_operator_commands_state", "browser_operator_commands", ["state"])
    op.create_index("ix_browser_operator_commands_created_at", "browser_operator_commands", ["created_at"])
    op.create_index(
        "ix_browser_operator_commands_delivered_at",
        "browser_operator_commands",
        ["delivered_at"],
    )
    op.create_index(
        "ix_browser_operator_commands_task_state_created",
        "browser_operator_commands",
        ["task_id", "state", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_browser_operator_commands_task_state_created", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_delivered_at", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_created_at", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_state", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_command_type", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_operator_id", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_conversation_id", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_task_id", table_name="browser_operator_commands")
    op.drop_index("ix_browser_operator_commands_organization_id", table_name="browser_operator_commands")
    op.drop_table("browser_operator_commands")
