"""tool connections, definitions, and agent assignments tables

Revision ID: 0031_tool_connections_definitions
Revises: 0030_phone_route_priority
Create Date: 2026-04-13 09:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0031_tool_connections"
down_revision = "0030_phone_route_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── api_connections ────────────────────────────────────────────────────────
    op.create_table(
        "api_connections",
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("auth_type", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=True),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("oauth_token_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("connection_id"),
        sa.UniqueConstraint("organization_id", "display_name", name="uq_api_connections_org_name"),
    )
    op.create_index("ix_api_connections_organization_id", "api_connections", ["organization_id"])
    op.create_index("ix_api_connections_provider", "api_connections", ["provider"])
    op.create_index("ix_api_connections_status", "api_connections", ["status"])

    # ── tool_definitions ───────────────────────────────────────────────────────
    op.create_table(
        "tool_definitions",
        sa.Column("tool_definition_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("tool_ref", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("endpoint_path", sa.String(length=1024), nullable=False),
        sa.Column("http_method", sa.String(length=16), nullable=False, server_default="POST"),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("output_schema_json", sa.JSON(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["api_connections.connection_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tool_definition_id"),
        sa.UniqueConstraint("organization_id", "tool_ref", name="uq_tool_definitions_org_ref"),
    )
    op.create_index("ix_tool_definitions_organization_id", "tool_definitions", ["organization_id"])
    op.create_index("ix_tool_definitions_connection_id", "tool_definitions", ["connection_id"])
    op.create_index("ix_tool_definitions_tool_ref", "tool_definitions", ["tool_ref"])
    op.create_index("ix_tool_definitions_enabled", "tool_definitions", ["enabled"])

    # ── tool_agent_assignments ─────────────────────────────────────────────────
    op.create_table(
        "tool_agent_assignments",
        sa.Column("assignment_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("tool_definition_id", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tool_definition_id"],
            ["tool_definitions.tool_definition_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.UniqueConstraint(
            "organization_id",
            "agent_id",
            "tool_definition_id",
            name="uq_tool_agent_assignments_org_agent_tool",
        ),
    )
    op.create_index(
        "ix_tool_agent_assignments_organization_id",
        "tool_agent_assignments",
        ["organization_id"],
    )
    op.create_index(
        "ix_tool_agent_assignments_agent_id",
        "tool_agent_assignments",
        ["agent_id"],
    )
    op.create_index(
        "ix_tool_agent_assignments_tool_definition_id",
        "tool_agent_assignments",
        ["tool_definition_id"],
    )
    op.create_index(
        "ix_tool_agent_assignments_enabled",
        "tool_agent_assignments",
        ["enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_agent_assignments_enabled", table_name="tool_agent_assignments")
    op.drop_index("ix_tool_agent_assignments_tool_definition_id", table_name="tool_agent_assignments")
    op.drop_index("ix_tool_agent_assignments_agent_id", table_name="tool_agent_assignments")
    op.drop_index("ix_tool_agent_assignments_organization_id", table_name="tool_agent_assignments")
    op.drop_table("tool_agent_assignments")

    op.drop_index("ix_tool_definitions_enabled", table_name="tool_definitions")
    op.drop_index("ix_tool_definitions_tool_ref", table_name="tool_definitions")
    op.drop_index("ix_tool_definitions_connection_id", table_name="tool_definitions")
    op.drop_index("ix_tool_definitions_organization_id", table_name="tool_definitions")
    op.drop_table("tool_definitions")

    op.drop_index("ix_api_connections_status", table_name="api_connections")
    op.drop_index("ix_api_connections_provider", table_name="api_connections")
    op.drop_index("ix_api_connections_organization_id", table_name="api_connections")
    op.drop_table("api_connections")
