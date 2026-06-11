"""add ticket system domain tables

Revision ID: 0009_ticket_system
Revises: 0008_graph_settings_eval_policy
Create Date: 2026-04-10 22:15:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_ticket_system"
down_revision = "0008_graph_settings_eval_policy"
branch_labels = None
depends_on = None


_RLS_TABLES = (
    "support_cases",
    "support_case_notes",
    "support_case_events",
    "ticketing_connections",
    "external_case_links",
)


def upgrade() -> None:
    op.create_table(
        "support_cases",
        sa.Column("case_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("case_number", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "primary_conversation_id",
            sa.String(length=255),
            sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("related_conversation_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("assigned_to_user_id", sa.String(length=255), nullable=True),
        sa.Column("assigned_team", sa.String(length=255), nullable=True),
        sa.Column("owning_graph_id", sa.String(length=255), nullable=True),
        sa.Column("participant_ref", sa.String(length=255), nullable=True),
        sa.Column("participant_display", sa.String(length=255), nullable=True),
        sa.Column("participant_email", sa.String(length=320), nullable=True),
        sa.Column("participant_phone", sa.String(length=64), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("custom_fields_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("case_metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("resolution_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "case_number", name="uq_support_cases_org_case_number"),
    )
    op.create_index("ix_support_cases_organization_id", "support_cases", ["organization_id"])
    op.create_index("ix_support_cases_case_number", "support_cases", ["case_number"])
    op.create_index("ix_support_cases_status", "support_cases", ["status"])
    op.create_index("ix_support_cases_priority", "support_cases", ["priority"])
    op.create_index("ix_support_cases_category", "support_cases", ["category"])
    op.create_index("ix_support_cases_source", "support_cases", ["source"])
    op.create_index("ix_support_cases_primary_conversation_id", "support_cases", ["primary_conversation_id"])
    op.create_index("ix_support_cases_assigned_to_user_id", "support_cases", ["assigned_to_user_id"])
    op.create_index("ix_support_cases_assigned_team", "support_cases", ["assigned_team"])
    op.create_index("ix_support_cases_owning_graph_id", "support_cases", ["owning_graph_id"])
    op.create_index("ix_support_cases_participant_ref", "support_cases", ["participant_ref"])
    op.create_index("ix_support_cases_participant_email", "support_cases", ["participant_email"])
    op.create_index("ix_support_cases_participant_phone", "support_cases", ["participant_phone"])
    op.create_index("ix_support_cases_resolved_at", "support_cases", ["resolved_at"])
    op.create_index("ix_support_cases_closed_at", "support_cases", ["closed_at"])

    op.create_table(
        "support_case_notes",
        sa.Column("note_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column(
            "case_id",
            sa.String(length=255),
            sa.ForeignKey("support_cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_user_id", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_case_notes_organization_id", "support_case_notes", ["organization_id"])
    op.create_index("ix_support_case_notes_case_id", "support_case_notes", ["case_id"])
    op.create_index("ix_support_case_notes_author_user_id", "support_case_notes", ["author_user_id"])

    op.create_table(
        "support_case_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column(
            "case_id",
            sa.String(length=255),
            sa.ForeignKey("support_cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=255), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_case_events_organization_id", "support_case_events", ["organization_id"])
    op.create_index("ix_support_case_events_case_id", "support_case_events", ["case_id"])
    op.create_index("ix_support_case_events_event_type", "support_case_events", ["event_type"])
    op.create_index("ix_support_case_events_actor_user_id", "support_case_events", ["actor_user_id"])
    op.create_index("ix_support_case_events_created_at", "support_case_events", ["created_at"])

    op.create_table(
        "ticketing_connections",
        sa.Column("connection_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("auth_type", sa.String(length=64), nullable=False),
        sa.Column("credentials_ref", sa.String(length=255), nullable=True),
        sa.Column("provider_config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("field_mappings_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status_mappings_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("priority_mappings_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("default_queue", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ticketing_connections_organization_id", "ticketing_connections", ["organization_id"])
    op.create_index("ix_ticketing_connections_provider", "ticketing_connections", ["provider"])
    op.create_index("ix_ticketing_connections_status", "ticketing_connections", ["status"])

    op.create_table(
        "external_case_links",
        sa.Column("link_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "connection_id",
            sa.String(length=255),
            sa.ForeignKey("ticketing_connections.connection_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_case_id", sa.String(length=255), nullable=False),
        sa.Column("external_case_key", sa.String(length=255), nullable=True),
        sa.Column("external_case_url", sa.Text(), nullable=True),
        sa.Column("external_case_status", sa.String(length=128), nullable=True),
        sa.Column("external_case_priority", sa.String(length=64), nullable=True),
        sa.Column(
            "support_case_id",
            sa.String(length=255),
            sa.ForeignKey("support_cases.case_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=255),
            sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sync_status", sa.String(length=32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("provider_payload_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "connection_id",
            "external_case_id",
            name="uq_external_case_links_connection_case",
        ),
    )
    op.create_index("ix_external_case_links_organization_id", "external_case_links", ["organization_id"])
    op.create_index("ix_external_case_links_provider", "external_case_links", ["provider"])
    op.create_index("ix_external_case_links_connection_id", "external_case_links", ["connection_id"])
    op.create_index("ix_external_case_links_external_case_id", "external_case_links", ["external_case_id"])
    op.create_index("ix_external_case_links_external_case_key", "external_case_links", ["external_case_key"])
    op.create_index("ix_external_case_links_external_case_status", "external_case_links", ["external_case_status"])
    op.create_index("ix_external_case_links_support_case_id", "external_case_links", ["support_case_id"])
    op.create_index("ix_external_case_links_conversation_id", "external_case_links", ["conversation_id"])
    op.create_index("ix_external_case_links_sync_status", "external_case_links", ["sync_status"])

    for table_name in _RLS_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(
            sa.text(
                f'''
                CREATE POLICY "{policy_name}" ON "{table_name}"
                USING (
                    current_setting('app.current_is_superuser', true) = 'true'
                    OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
                )
                WITH CHECK (
                    current_setting('app.current_is_superuser', true) = 'true'
                    OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
                )
                '''
            )
        )


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))

    op.drop_index("ix_external_case_links_sync_status", table_name="external_case_links")
    op.drop_index("ix_external_case_links_conversation_id", table_name="external_case_links")
    op.drop_index("ix_external_case_links_support_case_id", table_name="external_case_links")
    op.drop_index("ix_external_case_links_external_case_status", table_name="external_case_links")
    op.drop_index("ix_external_case_links_external_case_key", table_name="external_case_links")
    op.drop_index("ix_external_case_links_external_case_id", table_name="external_case_links")
    op.drop_index("ix_external_case_links_connection_id", table_name="external_case_links")
    op.drop_index("ix_external_case_links_provider", table_name="external_case_links")
    op.drop_index("ix_external_case_links_organization_id", table_name="external_case_links")
    op.drop_table("external_case_links")

    op.drop_index("ix_ticketing_connections_status", table_name="ticketing_connections")
    op.drop_index("ix_ticketing_connections_provider", table_name="ticketing_connections")
    op.drop_index("ix_ticketing_connections_organization_id", table_name="ticketing_connections")
    op.drop_table("ticketing_connections")

    op.drop_index("ix_support_case_events_created_at", table_name="support_case_events")
    op.drop_index("ix_support_case_events_actor_user_id", table_name="support_case_events")
    op.drop_index("ix_support_case_events_event_type", table_name="support_case_events")
    op.drop_index("ix_support_case_events_case_id", table_name="support_case_events")
    op.drop_index("ix_support_case_events_organization_id", table_name="support_case_events")
    op.drop_table("support_case_events")

    op.drop_index("ix_support_case_notes_author_user_id", table_name="support_case_notes")
    op.drop_index("ix_support_case_notes_case_id", table_name="support_case_notes")
    op.drop_index("ix_support_case_notes_organization_id", table_name="support_case_notes")
    op.drop_table("support_case_notes")

    op.drop_index("ix_support_cases_closed_at", table_name="support_cases")
    op.drop_index("ix_support_cases_resolved_at", table_name="support_cases")
    op.drop_index("ix_support_cases_participant_phone", table_name="support_cases")
    op.drop_index("ix_support_cases_participant_email", table_name="support_cases")
    op.drop_index("ix_support_cases_participant_ref", table_name="support_cases")
    op.drop_index("ix_support_cases_owning_graph_id", table_name="support_cases")
    op.drop_index("ix_support_cases_assigned_team", table_name="support_cases")
    op.drop_index("ix_support_cases_assigned_to_user_id", table_name="support_cases")
    op.drop_index("ix_support_cases_primary_conversation_id", table_name="support_cases")
    op.drop_index("ix_support_cases_source", table_name="support_cases")
    op.drop_index("ix_support_cases_category", table_name="support_cases")
    op.drop_index("ix_support_cases_priority", table_name="support_cases")
    op.drop_index("ix_support_cases_status", table_name="support_cases")
    op.drop_index("ix_support_cases_case_number", table_name="support_cases")
    op.drop_index("ix_support_cases_organization_id", table_name="support_cases")
    op.drop_table("support_cases")
