"""identity_organizations: add account closure / deletion state columns

Revision ID: 0024_org_deletion_state
Revises: 0023_api_keys
Create Date: 2026-04-11 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_org_deletion_state"
down_revision = "0023_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_organizations",
        sa.Column("deletion_state", sa.String(32), nullable=False, server_default="active"),
    )
    op.add_column(
        "identity_organizations",
        sa.Column("deletion_scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "identity_organizations",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "identity_organizations",
        sa.Column("deletion_requested_by", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_identity_organizations_deletion_scheduled_for",
        "identity_organizations",
        ["deletion_scheduled_for"],
    )


def downgrade() -> None:
    op.drop_index("ix_identity_organizations_deletion_scheduled_for", table_name="identity_organizations")
    op.drop_column("identity_organizations", "deletion_requested_by")
    op.drop_column("identity_organizations", "deletion_requested_at")
    op.drop_column("identity_organizations", "deletion_scheduled_for")
    op.drop_column("identity_organizations", "deletion_state")
