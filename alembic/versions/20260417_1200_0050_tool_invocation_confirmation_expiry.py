"""tool_invocations: add confirmation expiry timestamp

Adds ``expires_at`` so confirmation-required tool invocations can time out
server-side instead of remaining actionable indefinitely.

Revision ID: 0050
Revises: 0049
Create Date: 2026-04-17 12:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tool_invocations_expires_at", "tool_invocations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_expires_at", table_name="tool_invocations")
    op.drop_column("tool_invocations", "expires_at")
