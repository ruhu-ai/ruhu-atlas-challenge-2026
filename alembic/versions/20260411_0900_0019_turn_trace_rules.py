"""add rules trace payload to turn traces

Revision ID: 0019_turn_trace_rules
Revises: 0018_rules_foundation
Create Date: 2026-04-11 09:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_turn_trace_rules"
down_revision = "0018_rules_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "turn_traces",
        sa.Column(
            "rules_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text('\'{"evaluations": []}\''),
        ),
    )


def downgrade() -> None:
    op.drop_column("turn_traces", "rules_json")
