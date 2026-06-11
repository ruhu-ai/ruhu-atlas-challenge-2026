"""api_connections: add per-connection OAuth client credentials

Some providers register OAuth clients per-tenant — each customer's
Zendesk subdomain, for example, has its own OAuth client registration
and credentials. The platform's single client_id/secret env vars don't
work in that model.

This migration adds `oauth_client_id_override` (plaintext) and
`oauth_client_secret_enc` (Fernet-encrypted) to api_connections. When
set, OAuthFlowManager uses them instead of the platform defaults.

Revision ID: 0041_connection_oauth_client_creds
Revises: 0040_connection_oauth_url_overrides
Create Date: 2026-04-14 05:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_connection_oauth_client_creds"
down_revision = "0040_connection_oauth_url_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_connections",
        sa.Column("oauth_client_id_override", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "api_connections",
        sa.Column("oauth_client_secret_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_connections", "oauth_client_secret_enc")
    op.drop_column("api_connections", "oauth_client_id_override")
