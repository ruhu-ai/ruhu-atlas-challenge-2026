"""attachment_view_deliveries: dedup marker for view-ready worker dispatch

Part of the attachment-system first-principles rebuild (canonical spec
§"Storage Impact" — Option B).

The view-ready worker (see
docs/realtime-system/Attachment-Only-Turn-Kernel-Behavior.md §"View-ready
follow-up turns") synthesizes system_event turns when an attachment view
transitions to ``ready`` and the current state has an opted-in
``view_ready`` transition.

This table is the **dedup marker**: at most one follow-up turn per
``(conversation_id, attachment_id, view_kind)`` tuple, ever.  The worker
writes the delivery marker in the **same transaction** as the dispatch so
two concurrent workers cannot both pass revalidation and both fire.

``result`` captures why the worker reached its decision, for
observability and debugging:
  - ``dispatched`` — turn injected into the kernel
  - ``skipped_no_match`` — current state had no matching view_ready
    transition at subscription time
  - ``skipped_stale`` — current state moved on between subscription and
    dispatch (race with unrelated user turn, etc.)
  - ``skipped_attachment_gone`` — attachment was deleted/detached
  - ``skipped_graph_missing`` — couldn't load the graph version
  - ``failed`` — dispatch raised; ``error_detail`` holds the message

Revision ID: 0044_attachment_view_deliveries
Revises: 0043_realtime_idempotency_nullable_org
Create Date: 2026-04-15 02:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0044_attachment_view_deliveries"
down_revision = "0043_realtime_idempotency_nullable_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachment_view_deliveries",
        sa.Column("delivery_id", sa.String(255), primary_key=True),
        sa.Column("organization_id", sa.String(255), nullable=True, index=True),
        sa.Column("conversation_id", sa.String(255), nullable=False, index=True),
        sa.Column(
            "attachment_id",
            sa.String(255),
            sa.ForeignKey("attachments.attachment_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("view_kind", sa.String(32), nullable=False, index=True),
        sa.Column("result", sa.String(64), nullable=False),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("source_event_id", sa.String(255), nullable=True, index=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Enforces the "at most one follow-up turn per tuple, ever" rule.
    op.create_unique_constraint(
        "uq_attachment_view_deliveries_tuple",
        "attachment_view_deliveries",
        ["conversation_id", "attachment_id", "view_kind"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_attachment_view_deliveries_tuple",
        "attachment_view_deliveries",
        type_="unique",
    )
    op.drop_table("attachment_view_deliveries")
