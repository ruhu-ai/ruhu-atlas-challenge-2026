"""attachments: add trust_tier, retention fields, and attachment_views table

Part of the attachment-system first-principles rebuild. See:
  docs/realtime-system/Attachment-System-First-Principles-And-Rebuild-Spec.md

This migration is additive and non-destructive:

  - adds ``trust_tier``, ``uploaded_by_actor``, ``retention_expires_at``,
    ``deleted_at`` to ``attachments``
  - creates the new ``attachment_views`` table for the multi-view model
  - backfills one ``text`` view row per existing ``attachment_extractions``
    row so the service refactor has day-one data parity

Intentionally NOT changed in this migration:

  - ``scan_status`` column stays. The service layer will write ``passed``
    (new canonical value) while readers accept both ``clean`` and ``passed``
    per spec §13. A later migration will drop the column once callers
    migrate.
  - ``extraction_status`` column stays for backward compatibility during the
    service refactor. It will be derived from ``attachment_views`` in the
    service layer and dropped in a later migration.
  - ``attachment_extractions`` table stays for backward compatibility. The
    service will read from ``attachment_views`` going forward; the old
    table will be dropped once readers stop referencing it.

Revision ID: 0041_attachment_views_and_trust
Revises: 0040_connection_oauth_url_overrides
Create Date: 2026-04-14 05:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0041_attachment_views_and_trust"
down_revision = "0040_connection_oauth_url_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── attachments: new governance/retention columns ────────────────────────
    op.add_column(
        "attachments",
        sa.Column(
            "trust_tier",
            sa.String(32),
            nullable=False,
            # Existing rows are most likely widget uploads (anonymous);
            # callers that know the actor can update later.
            server_default="anonymous",
        ),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "uploaded_by_actor",
            sa.String(255),
            nullable=True,
        ),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_attachments_trust_tier",
        "attachments",
        ["trust_tier"],
    )
    op.create_index(
        "ix_attachments_retention_expires_at",
        "attachments",
        ["retention_expires_at"],
    )
    op.create_index(
        "ix_attachments_deleted_at",
        "attachments",
        ["deleted_at"],
    )

    # ── attachment_views: multi-view readiness per attachment ────────────────
    op.create_table(
        "attachment_views",
        sa.Column("view_id", sa.String(255), primary_key=True),
        sa.Column(
            "attachment_id",
            sa.String(255),
            sa.ForeignKey("attachments.attachment_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("organization_id", sa.String(255), nullable=True, index=True),
        sa.Column("conversation_id", sa.String(255), nullable=False, index=True),
        # view kind: text | vision | transcript | summary | native_file_uri |
        # retrieval (future). Kept as string for forward-compat.
        sa.Column("kind", sa.String(32), nullable=False, index=True),
        # view status: pending | processing | ready | failed | skipped
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("content_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default="{}"),
        # which producer generated this view (e.g. "pypdf", "gemini-vision",
        # "whisper-cloud"). Useful for observability and native-URI provider
        # selection at materialization time.
        sa.Column("provider", sa.String(128), nullable=True, index=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # A given attachment should have at most one active view per kind.
    # Multiple providers for the same kind (e.g. two vision summaries) is a
    # later concern; enforce uniqueness now to keep materialization simple.
    op.create_unique_constraint(
        "uq_attachment_views_attachment_kind",
        "attachment_views",
        ["attachment_id", "kind"],
    )

    # ── Backfill: seed one ``text`` view per existing extraction row ─────────
    # Day-one parity: anything that had extracted text should show up as a
    # ready text view in the new model.  Status maps:
    #   - extraction with text_content present → "ready"
    #   - extraction with metadata placeholder_only=true → "skipped"
    #   - everything else → "ready" if text_content present, else "failed"
    op.execute(
        """
        INSERT INTO attachment_views (
            view_id,
            attachment_id,
            organization_id,
            conversation_id,
            kind,
            status,
            content_text,
            content_json,
            metadata_json,
            provider,
            error_code,
            error_detail,
            created_at,
            updated_at
        )
        SELECT
            ext.extraction_id,
            ext.attachment_id,
            ext.organization_id,
            ext.conversation_id,
            'text',
            CASE
                WHEN ext.text_content IS NOT NULL AND length(ext.text_content) > 0 THEN 'ready'
                WHEN (ext.metadata_json::jsonb ->> 'placeholder_only') = 'true' THEN 'skipped'
                ELSE 'failed'
            END,
            ext.text_content,
            COALESCE(ext.structured_data_json, '{}'::json),
            COALESCE(ext.metadata_json, '{}'::json),
            NULL,
            NULL,
            NULL,
            ext.created_at,
            ext.updated_at
        FROM attachment_extractions ext
        WHERE NOT EXISTS (
            SELECT 1 FROM attachment_views v
            WHERE v.attachment_id = ext.attachment_id AND v.kind = 'text'
        )
        """
    )

    # ── Backfill: normalize scan_status values ───────────────────────────────
    # Existing rows may have 'clean' (old canonical); the spec standardizes on
    # 'passed'.  Readers accept both during migration, but future writes use
    # 'passed' only, so normalize stored rows now.
    op.execute(
        "UPDATE attachments SET scan_status = 'passed' WHERE scan_status = 'clean'"
    )

    # ── Backfill: set trust_tier based on source ─────────────────────────────
    # Existing widget uploads are anonymous; agent/system outputs (if any)
    # haven't been created by the current code path, so leave the default.
    # No-op for now; intentional for clarity.


def downgrade() -> None:
    # Drop the backfilled text views only if they were created from
    # attachment_extractions (best-effort: match by view_id == extraction_id).
    op.execute(
        """
        DELETE FROM attachment_views
        WHERE view_id IN (SELECT extraction_id FROM attachment_extractions)
          AND kind = 'text'
        """
    )
    op.drop_constraint(
        "uq_attachment_views_attachment_kind",
        "attachment_views",
        type_="unique",
    )
    op.drop_table("attachment_views")

    op.drop_index("ix_attachments_deleted_at", table_name="attachments")
    op.drop_index("ix_attachments_retention_expires_at", table_name="attachments")
    op.drop_index("ix_attachments_trust_tier", table_name="attachments")
    op.drop_column("attachments", "deleted_at")
    op.drop_column("attachments", "retention_expires_at")
    op.drop_column("attachments", "uploaded_by_actor")
    op.drop_column("attachments", "trust_tier")

    # Revert scan_status rename is not strictly needed but keeps downgrade
    # symmetrical.
    op.execute(
        "UPDATE attachments SET scan_status = 'clean' WHERE scan_status = 'passed'"
    )
