"""Add expression field to rule definition revisions.

Revision ID: 0055
Revises: 0054
Create Date: 2026-04-18 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_definition_revisions",
        sa.Column("expression_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rule_definition_revisions", "expression_json")
