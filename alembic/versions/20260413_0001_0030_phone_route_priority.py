"""phone route priority: replace partial unique index with composite resolve index

The old partial unique index (uq_phone_number_routes_enabled_channel) enforced at
most one enabled route per (phone_number_id, channel) at the database level. This
prevented configuring multiple routes with different priorities for failover or A/B
routing. resolve_route() now picks the lowest-priority enabled route via LIMIT 1, so
the uniqueness constraint is no longer needed.

Revision ID: 0030_phone_route_priority
Revises: 0029_graph_templates
Create Date: 2026-04-13 00:01:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0030_phone_route_priority"
down_revision = "0029_graph_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the partial unique index that blocked multi-route configurations.
    op.drop_index("uq_phone_number_routes_enabled_channel", table_name="phone_number_routes")

    # Add a composite index that covers the resolve_route() query:
    #   WHERE phone_number_id = ? AND channel = ? AND enabled = true
    #   ORDER BY priority ASC, updated_at DESC LIMIT 1
    op.create_index(
        "ix_phone_number_routes_resolve",
        "phone_number_routes",
        ["phone_number_id", "channel", "enabled", "priority"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_phone_number_routes_resolve", table_name="phone_number_routes")

    op.create_index(
        "uq_phone_number_routes_enabled_channel",
        "phone_number_routes",
        ["phone_number_id", "channel"],
        unique=True,
        postgresql_where=sa.text("enabled = true"),
    )
