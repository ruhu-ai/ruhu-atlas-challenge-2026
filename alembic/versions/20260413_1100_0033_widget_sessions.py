"""widget_sessions table

First-class widget session persistence. Replaces the previous approach of
storing session-token hashes and anonymous IDs in conversation.metadata_json,
making widget session lifecycle queryable and enabling proper billing rollup,
heartbeat tracking, and analytics attribution.

Design notes:
  - session_id is a separate UUID from conversation_id. A widget session wraps
    exactly one conversation, but they are logically distinct objects: a
    conversation is durable audit history; a session is an ephemeral runtime
    context with a token that expires.
  - session_token_hash stores SHA-256 of the bearer token. The plain token
    is never persisted — it lives only in HTTPS response bodies and browser
    memory.
  - token_expires_at is nullable so that rows created during the transition
    period (before the new create-session path is deployed) can still be
    authenticated via the fallback conversation.metadata_json path.
  - voice_duration_seconds is additive: one widget session can contain
    multiple voice calls (user disconnects and reconnects).
  - publishable_key_id is SET NULL on key deletion — revoking a key should
    not destroy session history.
  - conversation_id is CASCADE — if a conversation is purged, the session
    record goes with it.

Revision ID: 0033_widget_sessions
Revises: 0032_publishable_api_keys
Create Date: 2026-04-13 11:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_widget_sessions"
down_revision = "0032_publishable_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "widget_sessions",
        # ── Primary key ───────────────────────────────────────────────────────
        sa.Column("session_id", sa.String(length=255), nullable=False),

        # ── Tenant scope (RequiredTenantScopeMixin) ───────────────────────────
        sa.Column("organization_id", sa.String(length=255), nullable=False),

        # ── Core references ───────────────────────────────────────────────────
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("publishable_key_id", sa.String(length=255), nullable=True),

        # ── Visitor identity ──────────────────────────────────────────────────
        sa.Column("anonymous_id", sa.String(length=255), nullable=True),

        # ── Request context (captured at session creation) ────────────────────
        sa.Column("origin", sa.String(length=2083), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),  # IPv6-safe
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),

        # ── Session metadata ──────────────────────────────────────────────────
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="chat"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),

        # ── Auth ──────────────────────────────────────────────────────────────
        # SHA-256 hex of the bearer token (64 chars). Never store plain token.
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),

        # ── Usage counters (updated in-place for fast billing reads) ──────────
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("voice_duration_seconds", sa.Integer(), nullable=False, server_default="0"),

        # ── Lifecycle timestamps ──────────────────────────────────────────────
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),

        # ── Constraints ───────────────────────────────────────────────────────
        sa.PrimaryKeyConstraint("session_id", name="pk_widget_sessions"),

        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            name="fk_widget_sessions_conversation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publishable_key_id"],
            ["identity_api_keys.key_id"],
            name="fk_widget_sessions_publishable_key_id",
            ondelete="SET NULL",
        ),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────

    # Required-tenant index (mixin convention)
    op.create_index(
        "ix_widget_sessions_organization_id",
        "widget_sessions",
        ["organization_id"],
    )
    # Join from conversation → session (load session given a conversation_id)
    op.create_index(
        "ix_widget_sessions_conversation_id",
        "widget_sessions",
        ["conversation_id"],
    )
    # Load sessions by publishable key (key usage analytics)
    op.create_index(
        "ix_widget_sessions_publishable_key_id",
        "widget_sessions",
        ["publishable_key_id"],
    )
    # Visitor lookup for returning-visitor detection
    op.create_index(
        "ix_widget_sessions_org_anonymous_id",
        "widget_sessions",
        ["organization_id", "anonymous_id"],
    )
    # Status + recency scan (heartbeat sweep, expiry sweep)
    op.create_index(
        "ix_widget_sessions_org_status_last_activity",
        "widget_sessions",
        ["organization_id", "status", "last_activity_at"],
    )
    # Token lookup (auth path — must be fast)
    op.create_index(
        "ix_widget_sessions_token_hash",
        "widget_sessions",
        ["session_token_hash"],
        unique=True,
    )
    # Token expiry sweep
    op.create_index(
        "ix_widget_sessions_token_expires_at",
        "widget_sessions",
        ["token_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_widget_sessions_token_expires_at",        table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_token_hash",              table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_org_status_last_activity",table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_org_anonymous_id",        table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_publishable_key_id",      table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_conversation_id",         table_name="widget_sessions")
    op.drop_index("ix_widget_sessions_organization_id",         table_name="widget_sessions")
    op.drop_table("widget_sessions")
