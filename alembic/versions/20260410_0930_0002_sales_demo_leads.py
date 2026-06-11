"""add sales demo leads

Revision ID: 0002_sales_demo_leads
Revises: 0001_initial_postgres
Create Date: 2026-04-10 09:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_sales_demo_leads"
down_revision = "0001_initial_postgres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_demo_leads",
        sa.Column("lead_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("requested_channel", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lead_id"),
        sa.UniqueConstraint(
            "organization_id",
            "conversation_id",
            "email",
            name="uq_sales_demo_leads_conversation_email",
        ),
    )
    op.create_index("ix_sales_demo_leads_organization_id", "sales_demo_leads", ["organization_id"], unique=False)
    op.create_index("ix_sales_demo_leads_conversation_id", "sales_demo_leads", ["conversation_id"], unique=False)
    op.create_index("ix_sales_demo_leads_email", "sales_demo_leads", ["email"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sales_demo_leads_email", table_name="sales_demo_leads")
    op.drop_index("ix_sales_demo_leads_conversation_id", table_name="sales_demo_leads")
    op.drop_index("ix_sales_demo_leads_organization_id", table_name="sales_demo_leads")
    op.drop_table("sales_demo_leads")
