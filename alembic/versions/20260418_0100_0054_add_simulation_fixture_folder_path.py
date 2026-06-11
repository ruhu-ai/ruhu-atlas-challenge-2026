"""Add folder_path column to simulation_fixtures for hierarchical organization

Adds support for organizing simulation fixtures into folders (e.g., regression/billing/refunds).
Folder paths are hierarchical strings (forward-slash separated) that allow grouping and bulk
operations on fixture sets. Existing fixtures get folder_path=NULL (root).

Revision ID: 0054
Revises: 0053
Create Date: 2026-04-18 01:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add folder_path column and index to simulation_fixtures."""
    op.add_column(
        "simulation_fixtures",
        sa.Column("folder_path", sa.String(512), nullable=True),
    )
    op.create_index(
        "ix_simulation_fixtures_org_graph_folder",
        "simulation_fixtures",
        ["organization_id", "graph_id", "folder_path"],
    )


def downgrade() -> None:
    """Remove folder_path column and index from simulation_fixtures."""
    op.drop_index(
        "ix_simulation_fixtures_org_graph_folder",
        table_name="simulation_fixtures",
    )
    op.drop_column("simulation_fixtures", "folder_path")
