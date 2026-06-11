"""add journey definition and projection foundation tables

Revision ID: 0013_journey_foundation
Revises: 0012_ticketing_activity
Create Date: 2026-04-11 02:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_journey_foundation"
down_revision = "0012_ticketing_activity"
branch_labels = None
depends_on = None

_JOURNEY_TENANT_TABLES = (
    "journey_definitions",
    "journey_definition_versions",
    "journey_instances",
    "journey_touchpoints",
    "journey_events",
    "journey_analytics_snapshots",
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
    op.create_table(
        "journey_definitions",
        sa.Column("definition_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("subject_strategy_json", sa.JSON(), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("current_draft_version_id", sa.String(length=255), nullable=True),
        sa.Column("current_published_version_id", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("definition_id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_journey_definitions_org_slug"),
    )
    op.create_index("ix_journey_definitions_organization_id", "journey_definitions", ["organization_id"], unique=False)
    op.create_index("ix_journey_definitions_slug", "journey_definitions", ["slug"], unique=False)
    op.create_index("ix_journey_definitions_status", "journey_definitions", ["status"], unique=False)
    op.create_index("ix_journey_definitions_current_draft_version_id", "journey_definitions", ["current_draft_version_id"], unique=False)
    op.create_index("ix_journey_definitions_current_published_version_id", "journey_definitions", ["current_published_version_id"], unique=False)
    op.create_index("ix_journey_definitions_created_by_user_id", "journey_definitions", ["created_by_user_id"], unique=False)

    op.create_table(
        "journey_definition_versions",
        sa.Column("definition_version_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("definition_id", sa.String(length=255), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("based_on_version_id", sa.String(length=255), nullable=True),
        sa.Column("rules_json", sa.JSON(), nullable=False),
        sa.Column("compiled_rules_json", sa.JSON(), nullable=False),
        sa.Column("review_summary_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["definition_id"], ["journey_definitions.definition_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("definition_version_id"),
        sa.UniqueConstraint("definition_id", "version_number", name="uq_journey_definition_versions_number"),
    )
    op.create_index("ix_journey_definition_versions_organization_id", "journey_definition_versions", ["organization_id"], unique=False)
    op.create_index("ix_journey_definition_versions_definition_id", "journey_definition_versions", ["definition_id"], unique=False)
    op.create_index("ix_journey_definition_versions_status", "journey_definition_versions", ["status"], unique=False)
    op.create_index("ix_journey_definition_versions_based_on_version_id", "journey_definition_versions", ["based_on_version_id"], unique=False)
    op.create_index("ix_journey_definition_versions_created_by_user_id", "journey_definition_versions", ["created_by_user_id"], unique=False)
    op.create_index("ix_journey_definition_versions_published_at", "journey_definition_versions", ["published_at"], unique=False)

    op.create_table(
        "journey_instances",
        sa.Column("journey_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("definition_id", sa.String(length=255), nullable=False),
        sa.Column("definition_version_id", sa.String(length=255), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("subject_summary_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("current_milestone_id", sa.String(length=255), nullable=True),
        sa.Column("current_milestone_order", sa.Integer(), nullable=True),
        sa.Column("milestone_path_json", sa.JSON(), nullable=False),
        sa.Column("first_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("latest_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("first_graph_id", sa.String(length=255), nullable=True),
        sa.Column("first_graph_version_id", sa.String(length=255), nullable=True),
        sa.Column("latest_graph_id", sa.String(length=255), nullable=True),
        sa.Column("latest_graph_version_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["definition_id"], ["journey_definitions.definition_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["definition_version_id"], ["journey_definition_versions.definition_version_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["first_conversation_id"], ["conversations.conversation_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["latest_conversation_id"], ["conversations.conversation_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("journey_id"),
    )
    op.create_index("ix_journey_instances_organization_id", "journey_instances", ["organization_id"], unique=False)
    op.create_index("ix_journey_instances_definition_id", "journey_instances", ["definition_id"], unique=False)
    op.create_index("ix_journey_instances_definition_version_id", "journey_instances", ["definition_version_id"], unique=False)
    op.create_index("ix_journey_instances_subject_key", "journey_instances", ["subject_key"], unique=False)
    op.create_index("ix_journey_instances_status", "journey_instances", ["status"], unique=False)
    op.create_index("ix_journey_instances_outcome", "journey_instances", ["outcome"], unique=False)
    op.create_index("ix_journey_instances_current_milestone_id", "journey_instances", ["current_milestone_id"], unique=False)
    op.create_index("ix_journey_instances_current_milestone_order", "journey_instances", ["current_milestone_order"], unique=False)
    op.create_index("ix_journey_instances_first_conversation_id", "journey_instances", ["first_conversation_id"], unique=False)
    op.create_index("ix_journey_instances_latest_conversation_id", "journey_instances", ["latest_conversation_id"], unique=False)
    op.create_index("ix_journey_instances_first_graph_id", "journey_instances", ["first_graph_id"], unique=False)
    op.create_index("ix_journey_instances_first_graph_version_id", "journey_instances", ["first_graph_version_id"], unique=False)
    op.create_index("ix_journey_instances_latest_graph_id", "journey_instances", ["latest_graph_id"], unique=False)
    op.create_index("ix_journey_instances_latest_graph_version_id", "journey_instances", ["latest_graph_version_id"], unique=False)
    op.create_index("ix_journey_instances_started_at", "journey_instances", ["started_at"], unique=False)
    op.create_index("ix_journey_instances_last_activity_at", "journey_instances", ["last_activity_at"], unique=False)
    op.create_index("ix_journey_instances_ended_at", "journey_instances", ["ended_at"], unique=False)
    op.create_index(
        "uq_journey_instances_open_subject",
        "journey_instances",
        ["organization_id", "definition_id", "subject_key"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "journey_touchpoints",
        sa.Column("touchpoint_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("journey_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=True),
        sa.Column("graph_version_id", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=True),
        sa.Column("entry_reason", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journey_id"], ["journey_instances.journey_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("touchpoint_id"),
        sa.UniqueConstraint("journey_id", "conversation_id", name="uq_journey_touchpoints_journey_conversation"),
    )
    op.create_index("ix_journey_touchpoints_organization_id", "journey_touchpoints", ["organization_id"], unique=False)
    op.create_index("ix_journey_touchpoints_journey_id", "journey_touchpoints", ["journey_id"], unique=False)
    op.create_index("ix_journey_touchpoints_conversation_id", "journey_touchpoints", ["conversation_id"], unique=False)
    op.create_index("ix_journey_touchpoints_graph_id", "journey_touchpoints", ["graph_id"], unique=False)
    op.create_index("ix_journey_touchpoints_graph_version_id", "journey_touchpoints", ["graph_version_id"], unique=False)
    op.create_index("ix_journey_touchpoints_channel", "journey_touchpoints", ["channel"], unique=False)
    op.create_index("ix_journey_touchpoints_mode", "journey_touchpoints", ["mode"], unique=False)
    op.create_index("ix_journey_touchpoints_entry_reason", "journey_touchpoints", ["entry_reason"], unique=False)
    op.create_index("ix_journey_touchpoints_started_at", "journey_touchpoints", ["started_at"], unique=False)
    op.create_index("ix_journey_touchpoints_ended_at", "journey_touchpoints", ["ended_at"], unique=False)

    op.create_table(
        "journey_events",
        sa.Column("journey_event_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("journey_id", sa.String(length=255), nullable=False),
        sa.Column("touchpoint_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("turn_trace_id", sa.String(length=255), nullable=True),
        sa.Column("realtime_event_id", sa.String(length=255), nullable=True),
        sa.Column("tool_invocation_id", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("milestone_id", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journey_id"], ["journey_instances.journey_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["touchpoint_id"], ["journey_touchpoints.touchpoint_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["turn_trace_id"], ["turn_traces.trace_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["realtime_event_id"], ["realtime_events.event_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_invocation_id"], ["tool_invocations.invocation_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("journey_event_id"),
        sa.UniqueConstraint("journey_id", "idempotency_key", name="uq_journey_events_journey_idempotency"),
    )
    op.create_index("ix_journey_events_organization_id", "journey_events", ["organization_id"], unique=False)
    op.create_index("ix_journey_events_journey_id", "journey_events", ["journey_id"], unique=False)
    op.create_index("ix_journey_events_touchpoint_id", "journey_events", ["touchpoint_id"], unique=False)
    op.create_index("ix_journey_events_conversation_id", "journey_events", ["conversation_id"], unique=False)
    op.create_index("ix_journey_events_turn_trace_id", "journey_events", ["turn_trace_id"], unique=False)
    op.create_index("ix_journey_events_realtime_event_id", "journey_events", ["realtime_event_id"], unique=False)
    op.create_index("ix_journey_events_tool_invocation_id", "journey_events", ["tool_invocation_id"], unique=False)
    op.create_index("ix_journey_events_event_type", "journey_events", ["event_type"], unique=False)
    op.create_index("ix_journey_events_milestone_id", "journey_events", ["milestone_id"], unique=False)
    op.create_index("ix_journey_events_source", "journey_events", ["source"], unique=False)
    op.create_index("ix_journey_events_idempotency_key", "journey_events", ["idempotency_key"], unique=False)
    op.create_index("ix_journey_events_occurred_at", "journey_events", ["occurred_at"], unique=False)

    op.create_table(
        "journey_analytics_snapshots",
        sa.Column("snapshot_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("view_kind", sa.String(length=64), nullable=False),
        sa.Column("definition_id", sa.String(length=255), nullable=True),
        sa.Column("definition_version_id", sa.String(length=255), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity", sa.String(length=32), nullable=False),
        sa.Column("filter_key", sa.String(length=255), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "organization_id",
            "view_kind",
            "definition_id",
            "definition_version_id",
            "period_start",
            "period_end",
            "granularity",
            "filter_key",
            name="uq_journey_analytics_snapshots_scope",
        ),
    )
    op.create_index("ix_journey_analytics_snapshots_organization_id", "journey_analytics_snapshots", ["organization_id"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_view_kind", "journey_analytics_snapshots", ["view_kind"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_definition_id", "journey_analytics_snapshots", ["definition_id"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_definition_version_id", "journey_analytics_snapshots", ["definition_version_id"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_period_start", "journey_analytics_snapshots", ["period_start"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_period_end", "journey_analytics_snapshots", ["period_end"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_granularity", "journey_analytics_snapshots", ["granularity"], unique=False)
    op.create_index("ix_journey_analytics_snapshots_filter_key", "journey_analytics_snapshots", ["filter_key"], unique=False)

    for table_name in _JOURNEY_TENANT_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(_policy_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(_JOURNEY_TENANT_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_journey_analytics_snapshots_filter_key", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_granularity", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_period_end", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_period_start", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_definition_version_id", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_definition_id", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_view_kind", table_name="journey_analytics_snapshots")
    op.drop_index("ix_journey_analytics_snapshots_organization_id", table_name="journey_analytics_snapshots")
    op.drop_table("journey_analytics_snapshots")

    op.drop_index("ix_journey_events_occurred_at", table_name="journey_events")
    op.drop_index("ix_journey_events_idempotency_key", table_name="journey_events")
    op.drop_index("ix_journey_events_source", table_name="journey_events")
    op.drop_index("ix_journey_events_milestone_id", table_name="journey_events")
    op.drop_index("ix_journey_events_event_type", table_name="journey_events")
    op.drop_index("ix_journey_events_tool_invocation_id", table_name="journey_events")
    op.drop_index("ix_journey_events_realtime_event_id", table_name="journey_events")
    op.drop_index("ix_journey_events_turn_trace_id", table_name="journey_events")
    op.drop_index("ix_journey_events_conversation_id", table_name="journey_events")
    op.drop_index("ix_journey_events_touchpoint_id", table_name="journey_events")
    op.drop_index("ix_journey_events_journey_id", table_name="journey_events")
    op.drop_index("ix_journey_events_organization_id", table_name="journey_events")
    op.drop_table("journey_events")

    op.drop_index("ix_journey_touchpoints_ended_at", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_started_at", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_entry_reason", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_mode", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_channel", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_graph_version_id", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_graph_id", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_conversation_id", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_journey_id", table_name="journey_touchpoints")
    op.drop_index("ix_journey_touchpoints_organization_id", table_name="journey_touchpoints")
    op.drop_table("journey_touchpoints")

    op.drop_index("uq_journey_instances_open_subject", table_name="journey_instances")
    op.drop_index("ix_journey_instances_ended_at", table_name="journey_instances")
    op.drop_index("ix_journey_instances_last_activity_at", table_name="journey_instances")
    op.drop_index("ix_journey_instances_started_at", table_name="journey_instances")
    op.drop_index("ix_journey_instances_latest_graph_version_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_latest_graph_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_first_graph_version_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_first_graph_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_latest_conversation_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_first_conversation_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_current_milestone_order", table_name="journey_instances")
    op.drop_index("ix_journey_instances_current_milestone_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_outcome", table_name="journey_instances")
    op.drop_index("ix_journey_instances_status", table_name="journey_instances")
    op.drop_index("ix_journey_instances_subject_key", table_name="journey_instances")
    op.drop_index("ix_journey_instances_definition_version_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_definition_id", table_name="journey_instances")
    op.drop_index("ix_journey_instances_organization_id", table_name="journey_instances")
    op.drop_table("journey_instances")

    op.drop_index("ix_journey_definition_versions_published_at", table_name="journey_definition_versions")
    op.drop_index("ix_journey_definition_versions_created_by_user_id", table_name="journey_definition_versions")
    op.drop_index("ix_journey_definition_versions_based_on_version_id", table_name="journey_definition_versions")
    op.drop_index("ix_journey_definition_versions_status", table_name="journey_definition_versions")
    op.drop_index("ix_journey_definition_versions_definition_id", table_name="journey_definition_versions")
    op.drop_index("ix_journey_definition_versions_organization_id", table_name="journey_definition_versions")
    op.drop_table("journey_definition_versions")

    op.drop_index("ix_journey_definitions_created_by_user_id", table_name="journey_definitions")
    op.drop_index("ix_journey_definitions_current_published_version_id", table_name="journey_definitions")
    op.drop_index("ix_journey_definitions_current_draft_version_id", table_name="journey_definitions")
    op.drop_index("ix_journey_definitions_status", table_name="journey_definitions")
    op.drop_index("ix_journey_definitions_slug", table_name="journey_definitions")
    op.drop_index("ix_journey_definitions_organization_id", table_name="journey_definitions")
    op.drop_table("journey_definitions")
