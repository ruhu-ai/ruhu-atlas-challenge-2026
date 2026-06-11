"""identity_user_avatars: store user avatar blobs

Revision ID: 0022_user_avatar_blobs
Revises: 0021_notifications_foundation
Create Date: 2026-04-11 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_user_avatar_blobs"
down_revision = "0021_notifications_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_user_avatars",
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("identity_user_avatars")
