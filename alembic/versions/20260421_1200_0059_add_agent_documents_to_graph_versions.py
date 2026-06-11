"""Add agent_document_json to graph_versions.

Revision ID: 0059
Revises: 0058
Create Date: 2026-04-21 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_versions",
        sa.Column(
            "agent_document_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_versions", "agent_document_json")
