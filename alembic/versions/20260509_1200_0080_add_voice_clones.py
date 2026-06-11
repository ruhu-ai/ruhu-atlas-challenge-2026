"""add voice_clones table for Phase 2a-cloning

Revision ID: 0080
Revises: 0079
Create Date: 2026-05-09 12:00:00.000000

Persistence for tenant-scoped cloned voices via Chirp 3 HD Instant
Custom Voice (see ``docs/persona/phase-2.md`` Track 2a-cloning).

The table is auto-enrolled in the runtime tenant RLS policy set
because its model carries an ``organization_id`` column — see
``ruhu.db._compute_runtime_tenant_rls_tables``. The
``ensure_postgres_runtime_tenant_policies`` step that runs after every
schema build (see ``ruhu.db.build_session_factory``) installs the
``tenant_scope_voice_clones`` policy automatically.

Cloning keys are stored encrypted in ``voice_cloning_key_enc`` (AES-GCM
with AAD ``b"voiceclone:" + organization_id + b"|" + clone_id``).
Consent audio is retained for seven years per the Track 2a-cloning
compliance section; hard-delete is handled by a future retention
sweep, not this migration.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_clones",
        # Tenant scoping (RLS) — required for the auto-enrolled policy.
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        # Primary key. UUIDv4 generated client-side so the multipart
        # upload handler can include it in the Location header before
        # commit.
        sa.Column("clone_id", sa.String(length=64), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=64),
            nullable=False,
            server_default="vertex_gemini",
        ),
        # Optional: NULL = org-wide clone; otherwise scoped to a specific
        # agent. Don't FK to agents.agent_id — clones survive agent
        # deletion (audit + cost retention reasons).
        sa.Column("agent_id", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("voice_cloning_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("consent_audio_blob", sa.LargeBinary(), nullable=False),
        sa.Column("consent_audio_mime", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("clone_id", name="pk_voice_clones"),
    )
    op.create_index(
        "ix_voice_clones_organization_id",
        "voice_clones",
        ["organization_id"],
    )
    op.create_index(
        "ix_voice_clones_agent_id",
        "voice_clones",
        ["agent_id"],
    )
    op.create_index(
        "ix_voice_clones_created_by",
        "voice_clones",
        ["created_by"],
    )
    op.create_index(
        "ix_voice_clones_deleted_at",
        "voice_clones",
        ["deleted_at"],
    )
    # Compound indexes for the catalog-merge hot path:
    # listing active clones for an org (most common query) and
    # filtering to a specific agent_id when the picker is in
    # agent-scoped mode.
    op.create_index(
        "ix_voice_clones_org_active",
        "voice_clones",
        ["organization_id", "deleted_at"],
    )
    op.create_index(
        "ix_voice_clones_org_agent",
        "voice_clones",
        ["organization_id", "agent_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_voice_clones_org_agent", table_name="voice_clones")
    op.drop_index("ix_voice_clones_org_active", table_name="voice_clones")
    op.drop_index("ix_voice_clones_deleted_at", table_name="voice_clones")
    op.drop_index("ix_voice_clones_created_by", table_name="voice_clones")
    op.drop_index("ix_voice_clones_agent_id", table_name="voice_clones")
    op.drop_index("ix_voice_clones_organization_id", table_name="voice_clones")
    op.drop_table("voice_clones")
