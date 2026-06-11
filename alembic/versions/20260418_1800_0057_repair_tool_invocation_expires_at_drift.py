"""Repair tool_invocations.expires_at drift for stamped databases.

Some local databases were advanced past revision 0050 while the
``tool_invocations.expires_at`` column and its supporting index were still
missing. This migration repairs that drift idempotently.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    if not _has_column("tool_invocations", "expires_at"):
        op.add_column(
            "tool_invocations",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_index("tool_invocations", "ix_tool_invocations_expires_at"):
        op.create_index(
            "ix_tool_invocations_expires_at",
            "tool_invocations",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_index("tool_invocations", "ix_tool_invocations_expires_at"):
        op.drop_index("ix_tool_invocations_expires_at", table_name="tool_invocations")
    if _has_column("tool_invocations", "expires_at"):
        op.drop_column("tool_invocations", "expires_at")
