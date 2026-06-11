"""add graph settings for evaluation policy

Revision ID: 0008_graph_settings_eval_policy
Revises: 0007_kpi_runtime_sources
Create Date: 2026-04-10 20:35:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_graph_settings_eval_policy"
down_revision = "0007_kpi_runtime_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graphs",
        sa.Column(
            "settings_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("graphs", "settings_json")
