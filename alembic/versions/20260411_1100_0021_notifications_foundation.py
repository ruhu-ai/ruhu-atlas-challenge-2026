"""notifications: foundation table

Revision ID: 0021_notifications_foundation
Revises: 0020_knowledge_source_url
Create Date: 2026-04-11 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_notifications_foundation"
down_revision = "0020_knowledge_source_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("notification_id", sa.String(255), primary_key=True),
        sa.Column("organization_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("urgency", sa.String(16), nullable=False, server_default="fyi"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("url_label", sa.String(120), nullable=True),
        sa.Column("source_type", sa.String(64), nullable=True),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("level IN ('info', 'warning', 'error')", name="ck_notifications_level"),
        sa.CheckConstraint("urgency IN ('now', 'soon', 'fyi')", name="ck_notifications_urgency"),
    )

    # Primary query path: list for org + user, newest first
    op.create_index(
        "ix_notifications_org_user",
        "notifications",
        ["organization_id", "user_id", sa.text("created_at DESC")],
    )

    # Unread count query
    op.create_index(
        "ix_notifications_org_read",
        "notifications",
        ["organization_id", "read_at"],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    # Expiry sweep
    op.create_index(
        "ix_notifications_expires",
        "notifications",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    # Source entity lookup
    op.create_index(
        "ix_notifications_source",
        "notifications",
        ["organization_id", "source_type", "source_id"],
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_source", table_name="notifications")
    op.drop_index("ix_notifications_expires", table_name="notifications")
    op.drop_index("ix_notifications_org_read", table_name="notifications")
    op.drop_index("ix_notifications_org_user", table_name="notifications")
    op.drop_table("notifications")
