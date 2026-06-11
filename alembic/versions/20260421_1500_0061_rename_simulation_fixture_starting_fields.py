"""Rename simulation fixture starting fields to step/scenario terminology.

Revision ID: 0061
Revises: 0060
Create Date: 2026-04-21 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "simulation_fixtures",
        "starting_state_id",
        new_column_name="starting_step_id",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )
    op.add_column(
        "simulation_fixtures",
        sa.Column("starting_scenario_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("simulation_fixtures", "starting_scenario_id")
    op.alter_column(
        "simulation_fixtures",
        "starting_step_id",
        new_column_name="starting_state_id",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )
