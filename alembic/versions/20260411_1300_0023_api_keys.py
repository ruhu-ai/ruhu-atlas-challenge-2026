"""identity_api_keys: organisation API key management

Revision ID: 0023_api_keys
Revises: 0022_user_avatar_blobs
Create Date: 2026-04-11 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_api_keys"
down_revision = "0022_user_avatar_blobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_api_keys",
        sa.Column("key_id", sa.String(255), primary_key=True),
        sa.Column("organization_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_identity_api_keys_key_hash", "identity_api_keys", ["key_hash"], unique=True)
    op.create_index("ix_identity_api_keys_organization_id", "identity_api_keys", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_identity_api_keys_organization_id", table_name="identity_api_keys")
    op.drop_index("ix_identity_api_keys_key_hash", table_name="identity_api_keys")
    op.drop_table("identity_api_keys")
