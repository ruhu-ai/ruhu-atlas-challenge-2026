"""Add billing_webhook_events for inbound webhook idempotency.

Revision ID: 0065
Revises: 0064
Create Date: 2026-04-29 12:00:00.000000

Stripe (and other billing webhook providers) explicitly retry undelivered
events. Without an idempotency log a duplicate ``invoice.paid`` event would
double-count revenue. We INSERT the event_id at the start of handling; the
PRIMARY KEY conflict tells the handler the event has already been seen and
must be skipped.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_webhook_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="stripe",
        ),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="received",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_billing_webhook_events_provider",
        "billing_webhook_events",
        ["provider"],
    )
    op.create_index(
        "ix_billing_webhook_events_event_type",
        "billing_webhook_events",
        ["event_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_billing_webhook_events_event_type",
        table_name="billing_webhook_events",
    )
    op.drop_index(
        "ix_billing_webhook_events_provider",
        table_name="billing_webhook_events",
    )
    op.drop_table("billing_webhook_events")
