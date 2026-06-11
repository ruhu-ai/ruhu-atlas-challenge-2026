"""rename tool_definition.kind values: custom_api -> api, system -> builtin

Part of the Library/Tooling redesign. The DB column ``kind`` previously
carried the legacy string values ``custom_api`` and ``system``. The new
authoring surface uses the cleaner names ``api`` and ``builtin`` (matching
what users see in the Library UI). All other kind values (``code``,
``composite``, ``integration``, ``mcp``) are unchanged.

This is a value-only rename — the column type and constraints stay the
same. The default is also updated for raw SQL inserts (the SQLAlchemy
model-level default already moved to ``"api"``).

Revision ID: 0082_rename_tool_kind_values
Revises: 0081
Create Date: 2026-05-10 12:00:00+00:00
"""

from __future__ import annotations

from alembic import op

revision = "0082_rename_tool_kind_values"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE tool_definitions SET kind = 'api' WHERE kind = 'custom_api'"
    )
    op.execute(
        "UPDATE tool_definitions SET kind = 'builtin' WHERE kind = 'system'"
    )
    # Move the column-level server default to the new canonical name so a
    # raw INSERT that omits ``kind`` lands as 'api' (was 'custom_api').
    op.alter_column(
        "tool_definitions",
        "kind",
        server_default="api",
    )


def downgrade() -> None:
    op.alter_column(
        "tool_definitions",
        "kind",
        server_default="custom_api",
    )
    op.execute(
        "UPDATE tool_definitions SET kind = 'system' WHERE kind = 'builtin'"
    )
    op.execute(
        "UPDATE tool_definitions SET kind = 'custom_api' WHERE kind = 'api'"
    )
