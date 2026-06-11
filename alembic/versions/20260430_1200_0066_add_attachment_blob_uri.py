"""Add attachments.blob_uri for BlobStore-backed storage.

Revision ID: 0066
Revises: 0065
Create Date: 2026-04-30 12:00:00.000000

Existing rows store bytes in ``attachment_blobs.content_bytes`` (Postgres
``LargeBinary``). New uploads route through the BlobStore (S3 / GCS /
local) and record the storage URI here. ``AttachmentService`` reads from
BlobStore when ``blob_uri`` is set, else falls back to the legacy
DB-bytes path — so this migration is forward-only and incremental.
Existing rows do NOT need backfill.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("blob_uri", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachments", "blob_uri")
