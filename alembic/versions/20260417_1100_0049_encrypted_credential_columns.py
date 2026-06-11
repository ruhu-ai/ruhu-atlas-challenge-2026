"""Add BYTEA columns for encrypted OAuth tokens and connection credentials.

Phase 1 of the credential-encryption rollout.  We add the encrypted columns
alongside the existing plaintext columns; the store dual-writes for one
release cycle so rollback stays trivial (just flip reads back to the
plaintext columns).

``oauth_token_ct`` replaces ``oauth_token_json`` (the OAuth access/refresh
token payload).  ``credentials_ct`` replaces ``credentials_enc`` (HTTP basic,
API key, or other per-connection secrets).

Phase 2 (migration 0050) will drop the plaintext columns after a backfill.

Blob layout is self-describing (version byte + key_id + nonce + ciphertext)
so we never need to widen the schema again when the cipher evolves.  See
``src/ruhu/tools/cipher.py`` for the format.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_connections",
        sa.Column("oauth_token_ct", sa.LargeBinary, nullable=True),
    )
    op.add_column(
        "api_connections",
        sa.Column("credentials_ct", sa.LargeBinary, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_connections", "credentials_ct")
    op.drop_column("api_connections", "oauth_token_ct")
