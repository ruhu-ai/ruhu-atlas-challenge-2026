"""tooling redesign: kind, function_name, read_only, nullable connection_id, agent_tool_bindings

Implements the entity model changes from the Tooling System Redesign spec:
- Adds ``kind`` column to tool_definitions for classifying tools as
  custom_api, integration, or system.
- Adds ``function_name`` for the callable name in action-state code sandbox.
- Adds ``read_only`` flag for LLM auto-use eligibility in conversation states.
- Makes ``connection_id`` nullable on tool_definitions (system capabilities
  have no external connection).
- Makes ``endpoint_path`` nullable (system capabilities have no HTTP endpoint).
- Creates ``agent_tool_bindings`` table for per-agent connection overrides.

Revision ID: 0037_tooling_redesign_entity_model
Revises: 0036_widget_config_columns
Create Date: 2026-04-14 01:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_tooling_redesign"
down_revision = "0036_widget_config_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tool_definitions: add new columns ────────────────────────────
    op.add_column(
        "tool_definitions",
        sa.Column("kind", sa.String(32), nullable=False, server_default="custom_api"),
    )
    op.create_index("ix_tool_definitions_kind", "tool_definitions", ["kind"])

    op.add_column(
        "tool_definitions",
        sa.Column("function_name", sa.String(255), nullable=True),
    )
    op.create_index("ix_tool_definitions_function_name", "tool_definitions", ["function_name"])

    op.add_column(
        "tool_definitions",
        sa.Column("read_only", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_tool_definitions_read_only", "tool_definitions", ["read_only"])

    # ── tool_definitions: make connection_id nullable ────────────────
    op.alter_column(
        "tool_definitions",
        "connection_id",
        existing_type=sa.String(255),
        nullable=True,
    )

    # ── tool_definitions: make endpoint_path nullable ────────────────
    op.alter_column(
        "tool_definitions",
        "endpoint_path",
        existing_type=sa.String(1024),
        nullable=True,
    )

    # ── agent_tool_bindings: new table ───────────────────────────────
    op.create_table(
        "agent_tool_bindings",
        sa.Column("binding_id", sa.String(255), primary_key=True),
        sa.Column("organization_id", sa.String(255), nullable=False),
        sa.Column(
            "agent_id",
            sa.String(255),
            sa.ForeignKey("graphs.graph_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_definition_id",
            sa.String(255),
            sa.ForeignKey("tool_definitions.tool_definition_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.String(255),
            sa.ForeignKey("api_connections.connection_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "agent_id",
            "tool_definition_id",
            name="uq_agent_tool_bindings_org_agent_tool",
        ),
    )
    op.create_index("ix_agent_tool_bindings_agent_id", "agent_tool_bindings", ["agent_id"])
    op.create_index("ix_agent_tool_bindings_tool_id", "agent_tool_bindings", ["tool_definition_id"])
    op.create_index("ix_agent_tool_bindings_connection_id", "agent_tool_bindings", ["connection_id"])
    op.create_index("ix_agent_tool_bindings_org", "agent_tool_bindings", ["organization_id"])


def downgrade() -> None:
    op.drop_table("agent_tool_bindings")

    op.alter_column(
        "tool_definitions",
        "endpoint_path",
        existing_type=sa.String(1024),
        nullable=False,
    )

    op.alter_column(
        "tool_definitions",
        "connection_id",
        existing_type=sa.String(255),
        nullable=False,
    )

    op.drop_index("ix_tool_definitions_read_only", table_name="tool_definitions")
    op.drop_column("tool_definitions", "read_only")

    op.drop_index("ix_tool_definitions_function_name", table_name="tool_definitions")
    op.drop_column("tool_definitions", "function_name")

    op.drop_index("ix_tool_definitions_kind", table_name="tool_definitions")
    op.drop_column("tool_definitions", "kind")
