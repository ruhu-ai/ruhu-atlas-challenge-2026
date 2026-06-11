"""realtime_idempotency_keys: make organization_id nullable

The previous schema had a composite PK of
``(organization_id, scope, idempotency_key)``, which forced every row
to carry a non-null ``organization_id`` — even for untenanted events.
The runtime worked around this by writing the sentinel string
``"public"``, which was confusable with a real tenant named "public".

This migration restructures the table to honour the enterprise posture
that an ``organization_id`` is genuinely optional for internal
partitioning tokens:

1. Add a synthetic ``key_id`` column populated from existing rows.
2. Drop the composite primary key and promote ``key_id`` to the PK.
3. Make ``organization_id`` nullable.
4. Backfill any ``organization_id = 'public'`` rows to NULL.
5. Add a functional unique index that treats NULL and the empty string
   as the same partition, preserving the original uniqueness contract.

Revision ID: 0043_realtime_idempotency_nullable_org
Revises: 0042_attachment_views_and_trust
Create Date: 2026-04-15 01:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_realtime_idempotency_nullable_org"
# Converges the attachment_views branch (0041_attachment_views_and_trust)
# with the oauth branch (0041_connection_oauth_client_creds) so downstream
# migrations that reference ``attachments`` (0044+) can rely on that
# table existing.  The duplicate ``0042_attachment_views_and_trust`` that
# previously tried to serve this purpose on one branch only has been
# removed — its destructive cleanup (drop ``extraction_status`` column,
# drop ``attachment_extractions`` table) is intentionally unshipped and
# can be authored as a follow-up migration when the legacy surface is
# removed.
down_revision = (
    "0041_connection_oauth_client_creds",
    "0041_attachment_views_and_trust",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add synthetic PK column.
    op.add_column(
        "realtime_idempotency_keys",
        sa.Column("key_id", sa.String(length=64), nullable=True),
    )

    # 2. Populate key_id for existing rows using PG's gen_random_uuid() if
    #    the pgcrypto extension is available; fall back to md5 otherwise.
    op.execute(
        """
        UPDATE realtime_idempotency_keys
        SET key_id = md5(
            coalesce(organization_id, '') || '|' || scope || '|' || idempotency_key
        )
        WHERE key_id IS NULL
        """
    )

    op.alter_column(
        "realtime_idempotency_keys",
        "key_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )

    # 3. Drop the composite primary key and promote key_id to the PK.
    op.drop_constraint(
        "realtime_idempotency_keys_pkey",
        "realtime_idempotency_keys",
        type_="primary",
    )
    op.create_primary_key(
        "realtime_idempotency_keys_pkey",
        "realtime_idempotency_keys",
        ["key_id"],
    )

    # 4. Drop the NOT NULL on organization_id.
    op.alter_column(
        "realtime_idempotency_keys",
        "organization_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # 5. Backfill legacy "public" sentinel rows to NULL.
    op.execute(
        "UPDATE realtime_idempotency_keys SET organization_id = NULL "
        "WHERE organization_id = 'public'"
    )

    # 6. Preserve uniqueness over (organization_id, scope, idempotency_key).
    #    Use a functional index over COALESCE so NULL-org rows partition
    #    identically to the prior "public" sentinel (one entry per
    #    scope/idempotency-key within the untenanted bucket).
    op.create_index(
        "uq_realtime_idempotency_keys_org_scope_key",
        "realtime_idempotency_keys",
        [
            sa.text("coalesce(organization_id, '')"),
            "scope",
            "idempotency_key",
        ],
        unique=True,
    )


def downgrade() -> None:
    # Remove the functional unique index.
    op.drop_index(
        "uq_realtime_idempotency_keys_org_scope_key",
        table_name="realtime_idempotency_keys",
    )

    # Restore the "public" sentinel on nullable rows before enforcing NOT NULL.
    op.execute(
        "UPDATE realtime_idempotency_keys SET organization_id = 'public' "
        "WHERE organization_id IS NULL"
    )
    op.alter_column(
        "realtime_idempotency_keys",
        "organization_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )

    # Restore the composite primary key and drop the synthetic one.
    op.drop_constraint(
        "realtime_idempotency_keys_pkey",
        "realtime_idempotency_keys",
        type_="primary",
    )
    op.create_primary_key(
        "realtime_idempotency_keys_pkey",
        "realtime_idempotency_keys",
        ["organization_id", "scope", "idempotency_key"],
    )

    op.drop_column("realtime_idempotency_keys", "key_id")
