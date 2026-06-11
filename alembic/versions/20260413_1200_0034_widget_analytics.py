"""widget_events analytics table

Stores client-emitted widget analytics events (page_view, session_start,
message_sent, voice_started, etc.) for per-agent engagement reporting.

Design notes:
  - agent_id is denormalised from the widget session at insert time so that
    aggregation queries avoid a join through widget_sessions → conversations.
  - occurred_at is the client-supplied timestamp; created_at is the server
    receipt time. Both are stored so that clock-skew analysis is possible.
  - CASCADE on session_id: if a widget session is deleted, its analytics go
    with it. This is intentional — analytics without a session context are
    not useful for billing or attribution.
  - The table is append-only. No UPDATE path exists in normal operation.

Revision ID: 0034_widget_analytics
Revises: 0033_widget_sessions
Create Date: 2026-04-13 12:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_widget_analytics"
down_revision = "0033_widget_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "widget_events",
        # ── Primary key ───────────────────────────────────────────────────────
        sa.Column("event_id", sa.String(length=255), nullable=False),

        # ── Tenant scope ──────────────────────────────────────────────────────
        sa.Column("organization_id", sa.String(length=255), nullable=False),

        # ── References ────────────────────────────────────────────────────────
        sa.Column("session_id", sa.String(length=255), nullable=False),
        # Denormalised for aggregate queries without joins:
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("agent_id", sa.String(length=255), nullable=True),

        # ── Event payload ─────────────────────────────────────────────────────
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=False, server_default="{}"),

        # ── Timestamps ────────────────────────────────────────────────────────
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        # ── Constraints ───────────────────────────────────────────────────────
        sa.PrimaryKeyConstraint("event_id", name="pk_widget_events"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["widget_sessions.session_id"],
            name="fk_widget_events_session_id",
            ondelete="CASCADE",
        ),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────

    # Required-tenant index
    op.create_index(
        "ix_widget_events_organization_id",
        "widget_events",
        ["organization_id"],
    )
    # Load all events for a session (session detail view)
    op.create_index(
        "ix_widget_events_session_id",
        "widget_events",
        ["session_id"],
    )
    # Conversation-level event lookup
    op.create_index(
        "ix_widget_events_conversation_id",
        "widget_events",
        ["conversation_id"],
    )
    # Per-agent analytics aggregation by event type and time window
    op.create_index(
        "ix_widget_events_org_agent_type_occurred",
        "widget_events",
        ["organization_id", "agent_id", "event_type", "occurred_at"],
    )
    # Org-wide time-series queries (dashboard rollup)
    op.create_index(
        "ix_widget_events_org_occurred",
        "widget_events",
        ["organization_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_widget_events_org_occurred",           table_name="widget_events")
    op.drop_index("ix_widget_events_org_agent_type_occurred",table_name="widget_events")
    op.drop_index("ix_widget_events_conversation_id",        table_name="widget_events")
    op.drop_index("ix_widget_events_session_id",             table_name="widget_events")
    op.drop_index("ix_widget_events_organization_id",        table_name="widget_events")
    op.drop_table("widget_events")
