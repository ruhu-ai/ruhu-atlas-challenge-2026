"""add realtime core tables and conversation event sequencing

Revision ID: 0005_realtime_core
Revises: 0004_runtime_tenant_rls
Create Date: 2026-04-10 16:15:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_realtime_core"
down_revision = "0004_runtime_tenant_rls"
branch_labels = None
depends_on = None

_REALTIME_TENANT_TABLES = (
    "realtime_sessions",
    "realtime_events",
    "realtime_idempotency_keys",
    "realtime_outbox",
)


def _policy_sql(table_name: str) -> str:
    policy_name = f"tenant_scope_{table_name}"
    return f'''
    CREATE POLICY "{policy_name}" ON "{table_name}"
    USING (
        current_setting('app.current_is_superuser', true) = 'true'
        OR organization_id IS NULL
        OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
    )
    WITH CHECK (
        current_setting('app.current_is_superuser', true) = 'true'
        OR organization_id IS NULL
        OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
    )
    '''


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.add_column(
        "conversations",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "conversations",
        sa.Column("last_event_sequence", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column("conversations", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "conversations",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.execute("UPDATE conversations SET started_at = COALESCE(started_at, updated_at)")
    op.execute("UPDATE conversations SET created_at = COALESCE(created_at, updated_at)")

    op.create_table(
        "realtime_sessions",
        sa.Column("realtime_session_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("parent_realtime_session_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("surface", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("modality", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("external_session_key", sa.String(length=255), nullable=True),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("participant_identity", sa.String(length=255), nullable=True),
        sa.Column("transport_metadata_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_realtime_session_id"], ["realtime_sessions.realtime_session_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("realtime_session_id"),
    )
    op.create_index("ix_realtime_sessions_organization_id", "realtime_sessions", ["organization_id"], unique=False)
    op.create_index("ix_realtime_sessions_conversation_id", "realtime_sessions", ["conversation_id"], unique=False)
    op.create_index("ix_realtime_sessions_parent_realtime_session_id", "realtime_sessions", ["parent_realtime_session_id"], unique=False)
    op.create_index("ix_realtime_sessions_surface", "realtime_sessions", ["surface"], unique=False)
    op.create_index("ix_realtime_sessions_channel", "realtime_sessions", ["channel"], unique=False)
    op.create_index("ix_realtime_sessions_status", "realtime_sessions", ["status"], unique=False)
    op.create_index("ix_realtime_sessions_provider", "realtime_sessions", ["provider"], unique=False)
    op.create_index("ix_realtime_sessions_external_session_key", "realtime_sessions", ["external_session_key"], unique=False)
    op.create_index("ix_realtime_sessions_provider_session_id", "realtime_sessions", ["provider_session_id"], unique=False)
    op.create_index("ix_realtime_sessions_participant_identity", "realtime_sessions", ["participant_identity"], unique=False)

    op.create_table(
        "realtime_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("parent_realtime_session_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("realtime_session_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_sequence", sa.BigInteger(), nullable=False),
        sa.Column("family", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("causation_id", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("actor_type", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("audiences_json", sa.JSON(), nullable=False),
        sa.Column("projection_policy_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["realtime_session_id"], ["realtime_sessions.realtime_session_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("conversation_id", "conversation_sequence", name="uq_realtime_events_conversation_sequence"),
    )
    op.create_index("ix_realtime_events_organization_id", "realtime_events", ["organization_id"], unique=False)
    op.create_index("ix_realtime_events_conversation_id", "realtime_events", ["conversation_id"], unique=False)
    op.create_index("ix_realtime_events_realtime_session_id", "realtime_events", ["realtime_session_id"], unique=False)
    op.create_index("ix_realtime_events_family", "realtime_events", ["family"], unique=False)
    op.create_index("ix_realtime_events_name", "realtime_events", ["name"], unique=False)
    op.create_index("ix_realtime_events_causation_id", "realtime_events", ["causation_id"], unique=False)
    op.create_index("ix_realtime_events_correlation_id", "realtime_events", ["correlation_id"], unique=False)
    op.create_index("ix_realtime_events_created_at", "realtime_events", ["created_at"], unique=False)

    op.create_table(
        "realtime_idempotency_keys",
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("result_event_id", sa.String(length=255), nullable=True),
        sa.Column("result_ref_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_event_id"], ["realtime_events.event_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("organization_id", "scope", "idempotency_key"),
    )
    op.create_index("ix_realtime_idempotency_keys_conversation_id", "realtime_idempotency_keys", ["conversation_id"], unique=False)
    op.create_index("ix_realtime_idempotency_keys_result_event_id", "realtime_idempotency_keys", ["result_event_id"], unique=False)
    op.create_index("ix_realtime_idempotency_keys_expires_at", "realtime_idempotency_keys", ["expires_at"], unique=False)

    op.create_table(
        "realtime_outbox",
        sa.Column("outbox_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["realtime_events.event_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("outbox_id"),
    )
    op.create_index("ix_realtime_outbox_organization_id", "realtime_outbox", ["organization_id"], unique=False)
    op.create_index("ix_realtime_outbox_conversation_id", "realtime_outbox", ["conversation_id"], unique=False)
    op.create_index("ix_realtime_outbox_event_id", "realtime_outbox", ["event_id"], unique=False)
    op.create_index("ix_realtime_outbox_topic", "realtime_outbox", ["topic"], unique=False)
    op.create_index("ix_realtime_outbox_status", "realtime_outbox", ["status"], unique=False)
    op.create_index("ix_realtime_outbox_available_at", "realtime_outbox", ["available_at"], unique=False)

    for table_name in _REALTIME_TENANT_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(_policy_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(_REALTIME_TENANT_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_realtime_outbox_available_at", table_name="realtime_outbox")
    op.drop_index("ix_realtime_outbox_status", table_name="realtime_outbox")
    op.drop_index("ix_realtime_outbox_topic", table_name="realtime_outbox")
    op.drop_index("ix_realtime_outbox_event_id", table_name="realtime_outbox")
    op.drop_index("ix_realtime_outbox_conversation_id", table_name="realtime_outbox")
    op.drop_index("ix_realtime_outbox_organization_id", table_name="realtime_outbox")
    op.drop_table("realtime_outbox")

    op.drop_index("ix_realtime_idempotency_keys_expires_at", table_name="realtime_idempotency_keys")
    op.drop_index("ix_realtime_idempotency_keys_result_event_id", table_name="realtime_idempotency_keys")
    op.drop_index("ix_realtime_idempotency_keys_conversation_id", table_name="realtime_idempotency_keys")
    op.drop_table("realtime_idempotency_keys")

    op.drop_index("ix_realtime_events_created_at", table_name="realtime_events")
    op.drop_index("ix_realtime_events_correlation_id", table_name="realtime_events")
    op.drop_index("ix_realtime_events_causation_id", table_name="realtime_events")
    op.drop_index("ix_realtime_events_name", table_name="realtime_events")
    op.drop_index("ix_realtime_events_family", table_name="realtime_events")
    op.drop_index("ix_realtime_events_realtime_session_id", table_name="realtime_events")
    op.drop_index("ix_realtime_events_conversation_id", table_name="realtime_events")
    op.drop_index("ix_realtime_events_organization_id", table_name="realtime_events")
    op.drop_table("realtime_events")

    op.drop_index("ix_realtime_sessions_participant_identity", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_provider_session_id", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_external_session_key", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_provider", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_status", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_channel", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_surface", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_parent_realtime_session_id", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_conversation_id", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_organization_id", table_name="realtime_sessions")
    op.drop_table("realtime_sessions")

    op.drop_column("conversations", "created_at")
    op.drop_column("conversations", "ended_at")
    op.drop_column("conversations", "started_at")
    op.drop_column("conversations", "last_event_sequence")
    op.drop_column("conversations", "metadata_json")
    op.drop_column("conversations", "status")
