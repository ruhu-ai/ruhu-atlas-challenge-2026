"""add agent_document_json to graph templates

Revision ID: 0060
Revises: 0059
Create Date: 2026-04-21 14:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_templates",
        sa.Column("agent_document_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("graph_templates", "agent_document_json")
