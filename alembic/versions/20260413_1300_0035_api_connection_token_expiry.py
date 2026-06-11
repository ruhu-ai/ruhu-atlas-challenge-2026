"""api_connections: add token_expires_at for OAuth refresh scheduling

Adds a nullable, timezone-aware ``token_expires_at`` column to
``api_connections`` so the OAuth token-refresh worker can efficiently query
for connections whose access tokens are about to expire without a full-table
scan.

Design notes:
  - NULL for non-OAuth connections (api_key, basic) or when the provider did
    not return an ``expires_in`` field.
  - An index on the column makes the refresh worker's WHERE clause
    (``auth_type='oauth2' AND token_expires_at <= $cutoff``) index-only for
    the common case where the table is large and only a handful of rows are
    near expiry.
  - No backfill required: existing oauth2 rows remain NULL; the worker treats
    NULL as "never expires" and skips them until the next successful token
    exchange writes a real timestamp.

Revision ID: 0035_api_connection_token_expiry
Revises: 0034_widget_analytics
Create Date: 2026-04-13 13:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0035_api_connection_token_expiry"
down_revision = "0034_widget_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_connections",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_api_connections_token_expires_at",
        "api_connections",
        ["token_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_connections_token_expires_at", table_name="api_connections")
    op.drop_column("api_connections", "token_expires_at")
