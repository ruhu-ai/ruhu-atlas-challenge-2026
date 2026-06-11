"""add persona_avatar_blobs table for Phase 2d

Revision ID: 0081
Revises: 0080
Create Date: 2026-05-09 13:00:00.000000

Persistence for tenant-scoped persona avatars uploaded via the new
POST /agents/{agent_id}/persona/avatar endpoint. See
``docs/persona/phase-2.md`` Track 2d.

The table is auto-enrolled in the runtime tenant RLS policy set
because its model carries an ``organization_id`` column — see
``ruhu.db._compute_runtime_tenant_rls_tables``.

Avatar bytes are EXIF-stripped + re-encoded before persistence (see
``persona_avatar.process_avatar_upload``) so the rows here are safe
to serve back to the customer widget without leaking GPS / camera
metadata from the upload source.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_avatar_blobs",
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("agent_id", name="pk_persona_avatar_blobs"),
    )
    op.create_index(
        "ix_persona_avatar_blobs_organization_id",
        "persona_avatar_blobs",
        ["organization_id"],
    )
    op.create_index(
        "ix_persona_avatar_blobs_org",
        "persona_avatar_blobs",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_persona_avatar_blobs_org", table_name="persona_avatar_blobs")
    op.drop_index(
        "ix_persona_avatar_blobs_organization_id",
        table_name="persona_avatar_blobs",
    )
    op.drop_table("persona_avatar_blobs")
