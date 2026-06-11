"""graph templates table

Revision ID: 0029_graph_templates
Revises: 0028_phone_number_operations
Create Date: 2026-04-12 09:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0029_graph_templates"
down_revision = "0028_phone_number_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_templates",
        sa.Column("template_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=128), nullable=False, server_default="general"),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("state_graph_json", sa.JSON(), nullable=False),
        sa.Column("default_agent_settings", sa.JSON(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("template_id"),
        sa.UniqueConstraint("slug", name="uq_graph_templates_slug"),
    )
    op.create_index(
        "ix_graph_templates_organization_id",
        "graph_templates",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_templates_category",
        "graph_templates",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_graph_templates_is_published",
        "graph_templates",
        ["is_published"],
        unique=False,
    )
    op.create_index(
        "ix_graph_templates_is_featured",
        "graph_templates",
        ["is_featured"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_graph_templates_is_featured", table_name="graph_templates")
    op.drop_index("ix_graph_templates_is_published", table_name="graph_templates")
    op.drop_index("ix_graph_templates_category", table_name="graph_templates")
    op.drop_index("ix_graph_templates_organization_id", table_name="graph_templates")
    op.drop_table("graph_templates")
