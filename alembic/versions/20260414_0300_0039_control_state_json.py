"""conversations: add control_state_json column

Persists ConversationControlState (pending actions, artifacts, capture
runtime, focus, suspended frames) as a JSON column on conversations.

Revision ID: 0039_control_state_json
Revises: 0038_audit_events
Create Date: 2026-04-14 03:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_control_state_json"
down_revision = "0038_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("control_state_json", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "control_state_json")
