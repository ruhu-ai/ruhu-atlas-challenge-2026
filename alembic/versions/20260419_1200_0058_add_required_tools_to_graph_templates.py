"""Add required_tools_json column to graph_templates.

P6 follow-up — onboarding metadata per
docs/templates/Template-Required-Tools-Onboarding-Spec.md.  The column
holds onboarding/UX metadata (display name, description, provider
hints, setup URL) for every external tool a template's graph
references.  The graph itself remains the operational source of truth
for which tools are required to run.

Revision ID: 0058
Revises: 0057
Create Date: 2026-04-19 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_templates",
        sa.Column(
            "required_tools_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_templates", "required_tools_json")
