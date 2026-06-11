"""api_connections: add auth_url_override and token_url_override

Per-connection URL overrides for OAuth providers. Enables:
- Per-tenant endpoints (Zendesk subdomains: https://acme.zendesk.com/oauth/...)
- Fully custom OAuth providers (self-hosted GitLab, custom IdPs)

When set, OAuthFlowManager uses these instead of the static provider
defaults from OAUTH_PROVIDERS.

Revision ID: 0040_connection_oauth_url_overrides
Revises: 0039_control_state_json
Create Date: 2026-04-14 04:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_connection_oauth_url_overrides"
down_revision = "0039_control_state_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_connections",
        sa.Column("auth_url_override", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "api_connections",
        sa.Column("token_url_override", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_connections", "token_url_override")
    op.drop_column("api_connections", "auth_url_override")
