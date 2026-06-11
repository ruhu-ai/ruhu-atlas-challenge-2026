"""add graphs and graph versions

Revision ID: 0003_graph_versions
Revises: 0002_sales_demo_leads
Create Date: 2026-04-10 12:10:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_graph_versions"
down_revision = "0002_sales_demo_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graphs",
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("current_draft_version_id", sa.String(length=255), nullable=True),
        sa.Column("current_published_version_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("graph_id"),
    )
    op.create_index("ix_graphs_organization_id", "graphs", ["organization_id"], unique=False)
    op.create_index("ix_graphs_current_draft_version_id", "graphs", ["current_draft_version_id"], unique=False)
    op.create_index(
        "ix_graphs_current_published_version_id",
        "graphs",
        ["current_published_version_id"],
        unique=False,
    )

    op.create_table(
        "graph_versions",
        sa.Column("version_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("based_on_version_id", sa.String(length=255), nullable=True),
        sa.Column("state_graph_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint(
            "graph_id",
            "version_number",
            name="uq_graph_versions_graph_version_number",
        ),
    )
    op.create_index("ix_graph_versions_graph_id", "graph_versions", ["graph_id"], unique=False)
    op.create_index("ix_graph_versions_organization_id", "graph_versions", ["organization_id"], unique=False)
    op.create_index("ix_graph_versions_status", "graph_versions", ["status"], unique=False)
    op.create_index("ix_graph_versions_based_on_version_id", "graph_versions", ["based_on_version_id"], unique=False)
    op.create_index("ix_graph_versions_published_at", "graph_versions", ["published_at"], unique=False)

    op.add_column("conversations", sa.Column("graph_version_id", sa.String(length=255), nullable=False))
    op.add_column(
        "conversations",
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="live"),
    )
    op.create_index("ix_conversations_graph_version_id", "conversations", ["graph_version_id"], unique=False)

    op.add_column("turn_traces", sa.Column("graph_version_id", sa.String(length=255), nullable=True))
    op.create_index("ix_turn_traces_graph_version_id", "turn_traces", ["graph_version_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_turn_traces_graph_version_id", table_name="turn_traces")
    op.drop_column("turn_traces", "graph_version_id")

    op.drop_index("ix_conversations_graph_version_id", table_name="conversations")
    op.drop_column("conversations", "mode")
    op.drop_column("conversations", "graph_version_id")

    op.drop_index("ix_graph_versions_published_at", table_name="graph_versions")
    op.drop_index("ix_graph_versions_based_on_version_id", table_name="graph_versions")
    op.drop_index("ix_graph_versions_status", table_name="graph_versions")
    op.drop_index("ix_graph_versions_organization_id", table_name="graph_versions")
    op.drop_index("ix_graph_versions_graph_id", table_name="graph_versions")
    op.drop_table("graph_versions")

    op.drop_index("ix_graphs_current_published_version_id", table_name="graphs")
    op.drop_index("ix_graphs_current_draft_version_id", table_name="graphs")
    op.drop_index("ix_graphs_organization_id", table_name="graphs")
    op.drop_table("graphs")
