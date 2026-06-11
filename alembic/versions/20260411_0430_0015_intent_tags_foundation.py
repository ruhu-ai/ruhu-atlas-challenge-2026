"""add intent tags foundation tables

Revision ID: 0015_intent_tags_foundation
Revises: 0014_ticketing_retry_queue
Create Date: 2026-04-11 04:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_intent_tags_foundation"
down_revision = "0014_ticketing_retry_queue"
branch_labels = None
depends_on = None


_RLS_TABLES = (
    "intent_tag_taxonomy_versions",
    "intent_definitions",
    "tag_definitions",
    "intent_tag_classifier_profiles",
    "intent_tag_classification_events",
    "intent_tag_review_items",
)


def _policy_sql(table_name: str) -> sa.TextClause:
    policy_name = f"tenant_scope_{table_name}"
    return sa.text(
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


def upgrade() -> None:
    op.create_table(
        "intent_tag_taxonomy_versions",
        sa.Column("taxonomy_version_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_intent_tag_taxonomy_versions_org_name"),
    )
    op.create_index("ix_intent_tag_taxonomy_versions_organization_id", "intent_tag_taxonomy_versions", ["organization_id"])
    op.create_index("ix_intent_tag_taxonomy_versions_status", "intent_tag_taxonomy_versions", ["status"])

    op.create_table(
        "intent_definitions",
        sa.Column("intent_definition_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=True),
        sa.Column("taxonomy_version_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("example_phrases_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_deprecated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["taxonomy_version_id"],
            ["intent_tag_taxonomy_versions.taxonomy_version_id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "graph_id",
            "taxonomy_version_id",
            "name",
            name="uq_intent_definitions_scope_name",
        ),
    )
    op.create_index("ix_intent_definitions_organization_id", "intent_definitions", ["organization_id"])
    op.create_index("ix_intent_definitions_graph_id", "intent_definitions", ["graph_id"])
    op.create_index("ix_intent_definitions_taxonomy_version_id", "intent_definitions", ["taxonomy_version_id"])
    op.create_index("ix_intent_definitions_name", "intent_definitions", ["name"])
    op.create_index("ix_intent_definitions_category", "intent_definitions", ["category"])
    op.create_index("ix_intent_definitions_is_active", "intent_definitions", ["is_active"])
    op.create_index("ix_intent_definitions_is_deprecated", "intent_definitions", ["is_deprecated"])

    op.create_table(
        "tag_definitions",
        sa.Column("tag_definition_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=True),
        sa.Column("taxonomy_version_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tag_kind", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.6"),
        sa.Column("apply_scope", sa.String(length=32), nullable=False, server_default="conversation"),
        sa.Column("related_intent_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_deprecated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("rule_config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_intent_id"], ["intent_definitions.intent_definition_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["taxonomy_version_id"],
            ["intent_tag_taxonomy_versions.taxonomy_version_id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "graph_id",
            "taxonomy_version_id",
            "name",
            name="uq_tag_definitions_scope_name",
        ),
    )
    op.create_index("ix_tag_definitions_organization_id", "tag_definitions", ["organization_id"])
    op.create_index("ix_tag_definitions_graph_id", "tag_definitions", ["graph_id"])
    op.create_index("ix_tag_definitions_taxonomy_version_id", "tag_definitions", ["taxonomy_version_id"])
    op.create_index("ix_tag_definitions_name", "tag_definitions", ["name"])
    op.create_index("ix_tag_definitions_tag_kind", "tag_definitions", ["tag_kind"])
    op.create_index("ix_tag_definitions_category", "tag_definitions", ["category"])
    op.create_index("ix_tag_definitions_related_intent_id", "tag_definitions", ["related_intent_id"])
    op.create_index("ix_tag_definitions_is_active", "tag_definitions", ["is_active"])
    op.create_index("ix_tag_definitions_is_deprecated", "tag_definitions", ["is_deprecated"])

    op.create_table(
        "intent_tag_classifier_profiles",
        sa.Column("classifier_profile_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=True),
        sa.Column("adapter_name", sa.String(length=255), nullable=False),
        sa.Column("supported_languages_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("taxonomy_mode", sa.String(length=32), nullable=False, server_default="live"),
        sa.Column("taxonomy_version_id", sa.String(length=255), nullable=True),
        sa.Column("intent_catalog_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("tool_catalog_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("catalog_cache_built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_profile_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("profile_metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["taxonomy_version_id"],
            ["intent_tag_taxonomy_versions.taxonomy_version_id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_intent_tag_classifier_profiles_organization_id", "intent_tag_classifier_profiles", ["organization_id"])
    op.create_index("ix_intent_tag_classifier_profiles_graph_id", "intent_tag_classifier_profiles", ["graph_id"])
    op.create_index("ix_intent_tag_classifier_profiles_taxonomy_version_id", "intent_tag_classifier_profiles", ["taxonomy_version_id"])
    op.create_index("ix_intent_tag_classifier_profiles_is_active", "intent_tag_classifier_profiles", ["is_active"])

    op.create_table(
        "intent_tag_classification_events",
        sa.Column("classification_event_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=True),
        sa.Column("graph_version_id", sa.String(length=255), nullable=True),
        sa.Column("classifier_profile_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("turn_trace_id", sa.String(length=255), nullable=True),
        sa.Column("realtime_event_id", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("source_kind", sa.String(length=64), nullable=False, server_default="runtime"),
        sa.Column("adapter_name", sa.String(length=255), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("taxonomy_mode", sa.String(length=32), nullable=False),
        sa.Column("taxonomy_version_id", sa.String(length=255), nullable=True),
        sa.Column("request_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("context_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("decision_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("intent_name", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("response_language", sa.String(length=32), nullable=False),
        sa.Column("tool_route", sa.String(length=255), nullable=True),
        sa.Column("slots_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("signals_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["classifier_profile_id"],
            ["intent_tag_classifier_profiles.classifier_profile_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_trace_id"], ["turn_traces.trace_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["realtime_event_id"], ["realtime_events.event_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["taxonomy_version_id"],
            ["intent_tag_taxonomy_versions.taxonomy_version_id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_intent_tag_classification_events_organization_id", "intent_tag_classification_events", ["organization_id"])
    op.create_index("ix_intent_tag_classification_events_graph_id", "intent_tag_classification_events", ["graph_id"])
    op.create_index("ix_intent_tag_classification_events_graph_version_id", "intent_tag_classification_events", ["graph_version_id"])
    op.create_index("ix_intent_tag_classification_events_classifier_profile_id", "intent_tag_classification_events", ["classifier_profile_id"])
    op.create_index("ix_intent_tag_classification_events_conversation_id", "intent_tag_classification_events", ["conversation_id"])
    op.create_index("ix_intent_tag_classification_events_turn_trace_id", "intent_tag_classification_events", ["turn_trace_id"])
    op.create_index("ix_intent_tag_classification_events_realtime_event_id", "intent_tag_classification_events", ["realtime_event_id"])
    op.create_index("ix_intent_tag_classification_events_channel", "intent_tag_classification_events", ["channel"])
    op.create_index("ix_intent_tag_classification_events_provider", "intent_tag_classification_events", ["provider"])
    op.create_index("ix_intent_tag_classification_events_intent_name", "intent_tag_classification_events", ["intent_name"])
    op.create_index("ix_intent_tag_classification_events_tool_route", "intent_tag_classification_events", ["tool_route"])
    op.create_index("ix_intent_tag_classification_events_taxonomy_version_id", "intent_tag_classification_events", ["taxonomy_version_id"])

    op.create_table(
        "intent_tag_review_items",
        sa.Column("review_item_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("classification_event_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_summary_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("review_kind", sa.String(length=64), nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("corrected_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reviewed_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["classification_event_id"],
            ["intent_tag_classification_events.classification_event_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_intent_tag_review_items_organization_id", "intent_tag_review_items", ["organization_id"])
    op.create_index("ix_intent_tag_review_items_classification_event_id", "intent_tag_review_items", ["classification_event_id"])
    op.create_index("ix_intent_tag_review_items_conversation_summary_id", "intent_tag_review_items", ["conversation_summary_id"])
    op.create_index("ix_intent_tag_review_items_status", "intent_tag_review_items", ["status"])
    op.create_index("ix_intent_tag_review_items_review_kind", "intent_tag_review_items", ["review_kind"])
    op.create_index("ix_intent_tag_review_items_reviewed_by_user_id", "intent_tag_review_items", ["reviewed_by_user_id"])

    bind = op.get_bind()
    for table_name in _RLS_TABLES:
        bind.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        bind.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        bind.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_scope_{table_name}" ON "{table_name}"'))
        bind.execute(_policy_sql(table_name))


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(_RLS_TABLES):
        bind.execute(sa.text(f'DROP POLICY IF EXISTS "tenant_scope_{table_name}" ON "{table_name}"'))

    op.drop_index("ix_intent_tag_review_items_reviewed_by_user_id", table_name="intent_tag_review_items")
    op.drop_index("ix_intent_tag_review_items_review_kind", table_name="intent_tag_review_items")
    op.drop_index("ix_intent_tag_review_items_status", table_name="intent_tag_review_items")
    op.drop_index("ix_intent_tag_review_items_conversation_summary_id", table_name="intent_tag_review_items")
    op.drop_index("ix_intent_tag_review_items_classification_event_id", table_name="intent_tag_review_items")
    op.drop_index("ix_intent_tag_review_items_organization_id", table_name="intent_tag_review_items")
    op.drop_table("intent_tag_review_items")

    op.drop_index("ix_intent_tag_classification_events_taxonomy_version_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_tool_route", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_intent_name", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_provider", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_channel", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_realtime_event_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_turn_trace_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_conversation_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_classifier_profile_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_graph_version_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_graph_id", table_name="intent_tag_classification_events")
    op.drop_index("ix_intent_tag_classification_events_organization_id", table_name="intent_tag_classification_events")
    op.drop_table("intent_tag_classification_events")

    op.drop_index("ix_intent_tag_classifier_profiles_is_active", table_name="intent_tag_classifier_profiles")
    op.drop_index("ix_intent_tag_classifier_profiles_taxonomy_version_id", table_name="intent_tag_classifier_profiles")
    op.drop_index("ix_intent_tag_classifier_profiles_graph_id", table_name="intent_tag_classifier_profiles")
    op.drop_index("ix_intent_tag_classifier_profiles_organization_id", table_name="intent_tag_classifier_profiles")
    op.drop_table("intent_tag_classifier_profiles")

    op.drop_index("ix_tag_definitions_is_deprecated", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_is_active", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_related_intent_id", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_category", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_tag_kind", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_name", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_taxonomy_version_id", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_graph_id", table_name="tag_definitions")
    op.drop_index("ix_tag_definitions_organization_id", table_name="tag_definitions")
    op.drop_table("tag_definitions")

    op.drop_index("ix_intent_definitions_is_deprecated", table_name="intent_definitions")
    op.drop_index("ix_intent_definitions_is_active", table_name="intent_definitions")
    op.drop_index("ix_intent_definitions_category", table_name="intent_definitions")
    op.drop_index("ix_intent_definitions_name", table_name="intent_definitions")
    op.drop_index("ix_intent_definitions_taxonomy_version_id", table_name="intent_definitions")
    op.drop_index("ix_intent_definitions_graph_id", table_name="intent_definitions")
    op.drop_index("ix_intent_definitions_organization_id", table_name="intent_definitions")
    op.drop_table("intent_definitions")

    op.drop_index("ix_intent_tag_taxonomy_versions_status", table_name="intent_tag_taxonomy_versions")
    op.drop_index("ix_intent_tag_taxonomy_versions_organization_id", table_name="intent_tag_taxonomy_versions")
    op.drop_table("intent_tag_taxonomy_versions")
