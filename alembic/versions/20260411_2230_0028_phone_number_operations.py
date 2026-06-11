"""phone number operations audit trail

Revision ID: 0028_phone_number_operations
Revises: 0027_billing_usage_scope
Create Date: 2026-04-11 22:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0028_phone_number_operations"
down_revision = "0027_billing_usage_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_number_audit_events",
        sa.Column("audit_event_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("phone_number_id", sa.String(length=255), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=128), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["phone_number_id"], ["phone_numbers.phone_number_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("audit_event_id"),
    )
    op.create_index(
        "ix_phone_number_audit_events_organization_id",
        "phone_number_audit_events",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_phone_number_id",
        "phone_number_audit_events",
        ["phone_number_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_actor_type",
        "phone_number_audit_events",
        ["actor_type"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_actor_user_id",
        "phone_number_audit_events",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_action",
        "phone_number_audit_events",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_resource_type",
        "phone_number_audit_events",
        ["resource_type"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_resource_id",
        "phone_number_audit_events",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_phone_number_audit_events_created_at",
        "phone_number_audit_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_phone_number_audit_events_created_at", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_resource_id", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_resource_type", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_action", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_actor_user_id", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_actor_type", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_phone_number_id", table_name="phone_number_audit_events")
    op.drop_index("ix_phone_number_audit_events_organization_id", table_name="phone_number_audit_events")
    op.drop_table("phone_number_audit_events")
