"""drop_sales_demo_leads: remove internal demo lead persistence

The sales.create_demo_lead builtin tool is removed in favor of HTTP tools
pointing to real CRM systems (HubSpot, Salesforce, webhooks). Conversation
traces remain the source of truth; no need for a dedicated demo_leads table.

Revision ID: 0053
Revises: 0052
Create Date: 2026-04-17 15:00:00+00:00
"""

from __future__ import annotations

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_sales_demo_leads_organization_id", table_name="sales_demo_leads")
    op.drop_index("ix_sales_demo_leads_conversation_id", table_name="sales_demo_leads")
    op.drop_index("ix_sales_demo_leads_email", table_name="sales_demo_leads")
    op.drop_table("sales_demo_leads")


def downgrade() -> None:
    pass
