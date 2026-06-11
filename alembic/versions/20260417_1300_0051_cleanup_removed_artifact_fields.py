"""cleanup: remove deprecated artifact fields from JSON blobs

Removes four artifact subsystem fields that were deprecated in the projection-first
redesign (docs 27-28):
  - allowed_followups (removed from ConversationArtifact)
  - focusable (removed from ConversationArtifact)
  - focus_priority (removed from ConversationArtifact)
  - refresh_before_execute (removed from ArtifactFollowupHandler)

These fields live in JSONB columns (control_state_json, graph_data) and are
silently dropped by Pydantic v2's default extra="ignore" on read. This migration
eagerly normalizes existing data for cleaner schemas and reduced blob sizes.

Revision ID: 0051
Revises: 0050
Create Date: 2026-04-17 13:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Strip removed artifact fields from conversations and graphs."""
    # Script 1: Remove deprecated artifact fields from conversations.control_state_json
    op.execute(
        sa.text("""
        UPDATE conversations
        SET control_state_json = jsonb_set(
          control_state_json::jsonb,
          '{active_artifacts}',
          COALESCE(
            (SELECT jsonb_agg(art - 'allowed_followups' - 'focusable' - 'focus_priority')
             FROM jsonb_array_elements(control_state_json::jsonb -> 'active_artifacts') AS art),
            '[]'::jsonb
          )
        )
        WHERE control_state_json::jsonb -> 'active_artifacts' IS NOT NULL
          AND control_state_json::jsonb -> 'active_artifacts' <> 'null'::jsonb
        """)
    )

    # Script 2: Remove refresh_before_execute from graph_versions.state_graph_json
    op.execute(
        sa.text("""
        UPDATE graph_versions
        SET state_graph_json = jsonb_set(
          state_graph_json::jsonb,
          '{followup_handlers}',
          COALESCE(
            (SELECT jsonb_agg(h - 'refresh_before_execute')
             FROM jsonb_array_elements(state_graph_json::jsonb -> 'followup_handlers') AS h),
            '[]'::jsonb
          )
        )
        WHERE state_graph_json::jsonb -> 'followup_handlers' IS NOT NULL
          AND state_graph_json::jsonb -> 'followup_handlers' <> 'null'::jsonb
        """)
    )


def downgrade() -> None:
    """Downgrade is not supported for this data-cleanup migration.

    The removed fields were deprecated and are not recoverable. Downgrade requires
    restoring from a backup taken before this migration ran.
    """
    raise NotImplementedError(
        "Cannot downgrade data cleanup migration 0051. "
        "Restore from a backup taken before the migration ran."
    )
