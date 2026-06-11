"""extend identity_api_keys with publishable-key fields

Adds key_type, agent_id, allowed_origins, and environment columns so that
organisation API keys can be scoped to a specific agent (graph) and carry an
allowed-origins list for embedded widget authentication.

Existing rows receive server defaults:
  key_type     = 'secret'   (no change in behaviour)
  agent_id     = NULL       (not graph-bound)
  allowed_origins = '[]'    (no origin restrictions)
  environment  = 'live'

Revision ID: 0032_publishable_api_keys
Revises: 0031_tool_connections
Create Date: 2026-04-13 10:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_publishable_api_keys"
down_revision = "0031_tool_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_api_keys",
        sa.Column(
            "key_type",
            sa.String(length=32),
            nullable=False,
            server_default="secret",
        ),
    )
    op.add_column(
        "identity_api_keys",
        sa.Column("agent_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "identity_api_keys",
        sa.Column(
            "allowed_origins",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "identity_api_keys",
        sa.Column(
            "environment",
            sa.String(length=16),
            nullable=False,
            server_default="live",
        ),
    )

    # FK: agent_id → graphs.graph_id  ON DELETE SET NULL
    # SET NULL keeps the key record on graph deletion (operators should revoke
    # keys explicitly; silent cascade would hide a config error).
    op.create_foreign_key(
        "fk_api_keys_agent_id",
        "identity_api_keys",
        "graphs",
        ["agent_id"],
        ["graph_id"],
        ondelete="SET NULL",
    )

    # Composite index covers: list publishable keys by agent, filter by type.
    op.create_index(
        "ix_identity_api_keys_org_type_agent",
        "identity_api_keys",
        ["organization_id", "key_type", "agent_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_identity_api_keys_org_type_agent", table_name="identity_api_keys")
    op.drop_constraint("fk_api_keys_agent_id", "identity_api_keys", type_="foreignkey")
    op.drop_column("identity_api_keys", "environment")
    op.drop_column("identity_api_keys", "allowed_origins")
    op.drop_column("identity_api_keys", "agent_id")
    op.drop_column("identity_api_keys", "key_type")
