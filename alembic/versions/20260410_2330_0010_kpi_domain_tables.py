"""add kpi domain tables

Revision ID: 0010_kpi_domain_tables
Revises: 0009_ticket_system
Create Date: 2026-04-10 23:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_kpi_domain_tables"
down_revision = "0009_ticket_system"
branch_labels = None
depends_on = None


_RLS_TABLES = (
    "kpi_metric_scopes",
    "kpi_metric_observations",
    "kpi_baseline_snapshots",
    "kpi_goals_v2",
    "kpi_goal_evaluations",
    "kpi_insights",
    "kpi_recommendations",
    "kpi_execution_intents",
    "kpi_execution_results",
    "kpi_impact_assessments",
    "kpi_experiments",
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
        "kpi_metric_scopes",
        sa.Column("scope_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("segment_key", sa.String(length=255), nullable=True),
        sa.Column("campaign_key", sa.String(length=255), nullable=True),
        sa.Column("custom_scope_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "fingerprint", name="uq_kpi_metric_scopes_org_fingerprint"),
    )
    op.create_index("ix_kpi_metric_scopes_organization_id", "kpi_metric_scopes", ["organization_id"])
    op.create_index("ix_kpi_metric_scopes_scope_kind", "kpi_metric_scopes", ["scope_kind"])
    op.create_index("ix_kpi_metric_scopes_agent_id", "kpi_metric_scopes", ["agent_id"])
    op.create_index("ix_kpi_metric_scopes_workflow_id", "kpi_metric_scopes", ["workflow_id"])
    op.create_index("ix_kpi_metric_scopes_channel", "kpi_metric_scopes", ["channel"])
    op.create_index("ix_kpi_metric_scopes_segment_key", "kpi_metric_scopes", ["segment_key"])
    op.create_index("ix_kpi_metric_scopes_campaign_key", "kpi_metric_scopes", ["campaign_key"])
    op.create_index("ix_kpi_metric_scopes_fingerprint", "kpi_metric_scopes", ["fingerprint"])

    op.create_table(
        "kpi_metric_observations",
        sa.Column("observation_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("metric_definition_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("observation_kind", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("eligibility_count", sa.Integer(), nullable=True),
        sa.Column("excluded_count", sa.Integer(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=True),
        sa.Column("quality_flags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_metric_observations_organization_id", "kpi_metric_observations", ["organization_id"])
    op.create_index("ix_kpi_metric_observations_metric_key", "kpi_metric_observations", ["metric_key"])
    op.create_index("ix_kpi_metric_observations_scope_id", "kpi_metric_observations", ["scope_id"])
    op.create_index("ix_kpi_metric_observations_observation_kind", "kpi_metric_observations", ["observation_kind"])
    op.create_index("ix_kpi_metric_observations_period_end", "kpi_metric_observations", ["period_end"])

    op.create_table(
        "kpi_baseline_snapshots",
        sa.Column("baseline_snapshot_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=True),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("source_observation_id", sa.String(length=255), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_source", sa.String(length=64), nullable=False),
        sa.Column("baseline_reason", sa.Text(), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_baseline_snapshots_organization_id", "kpi_baseline_snapshots", ["organization_id"])
    op.create_index("ix_kpi_baseline_snapshots_goal_id", "kpi_baseline_snapshots", ["goal_id"])
    op.create_index("ix_kpi_baseline_snapshots_metric_key", "kpi_baseline_snapshots", ["metric_key"])
    op.create_index("ix_kpi_baseline_snapshots_scope_id", "kpi_baseline_snapshots", ["scope_id"])
    op.create_index("ix_kpi_baseline_snapshots_source_observation_id", "kpi_baseline_snapshots", ["source_observation_id"])

    op.create_table(
        "kpi_goals_v2",
        sa.Column("goal_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("baseline_snapshot_id", sa.String(length=255), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("latest_evaluation_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_goals_v2_organization_id", "kpi_goals_v2", ["organization_id"])
    op.create_index("ix_kpi_goals_v2_metric_key", "kpi_goals_v2", ["metric_key"])
    op.create_index("ix_kpi_goals_v2_scope_id", "kpi_goals_v2", ["scope_id"])
    op.create_index("ix_kpi_goals_v2_status", "kpi_goals_v2", ["status"])
    op.create_index("ix_kpi_goals_v2_owner_user_id", "kpi_goals_v2", ["owner_user_id"])
    op.create_index("ix_kpi_goals_v2_baseline_snapshot_id", "kpi_goals_v2", ["baseline_snapshot_id"])
    op.create_index("ix_kpi_goals_v2_latest_evaluation_id", "kpi_goals_v2", ["latest_evaluation_id"])

    op.create_table(
        "kpi_goal_evaluations",
        sa.Column("evaluation_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=False),
        sa.Column("observation_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress_ratio", sa.Float(), nullable=False),
        sa.Column("distance_to_target", sa.Float(), nullable=False),
        sa.Column("delta_from_baseline", sa.Float(), nullable=False),
        sa.Column("sample_size_sufficient", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("freshness_seconds", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_goal_evaluations_organization_id", "kpi_goal_evaluations", ["organization_id"])
    op.create_index("ix_kpi_goal_evaluations_goal_id", "kpi_goal_evaluations", ["goal_id"])
    op.create_index("ix_kpi_goal_evaluations_observation_id", "kpi_goal_evaluations", ["observation_id"])
    op.create_index("ix_kpi_goal_evaluations_status", "kpi_goal_evaluations", ["status"])

    op.create_table(
        "kpi_insights",
        sa.Column("insight_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=True),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("blocker_kind", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("severity", sa.Float(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rank_score", sa.Float(), nullable=False),
        sa.Column("evidence_bundle_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stale_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_insights_organization_id", "kpi_insights", ["organization_id"])
    op.create_index("ix_kpi_insights_goal_id", "kpi_insights", ["goal_id"])
    op.create_index("ix_kpi_insights_scope_id", "kpi_insights", ["scope_id"])
    op.create_index("ix_kpi_insights_metric_key", "kpi_insights", ["metric_key"])
    op.create_index("ix_kpi_insights_blocker_kind", "kpi_insights", ["blocker_kind"])
    op.create_index("ix_kpi_insights_status", "kpi_insights", ["status"])
    op.create_index("ix_kpi_insights_rank_score", "kpi_insights", ["rank_score"])

    op.create_table(
        "kpi_recommendations",
        sa.Column("recommendation_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=True),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("insight_id", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("projected_impact_min", sa.Float(), nullable=False),
        sa.Column("projected_impact_max", sa.Float(), nullable=False),
        sa.Column("projected_confidence", sa.Float(), nullable=False),
        sa.Column("evidence_bundle_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dependency_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("execution_template_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_recommendations_organization_id", "kpi_recommendations", ["organization_id"])
    op.create_index("ix_kpi_recommendations_goal_id", "kpi_recommendations", ["goal_id"])
    op.create_index("ix_kpi_recommendations_scope_id", "kpi_recommendations", ["scope_id"])
    op.create_index("ix_kpi_recommendations_metric_key", "kpi_recommendations", ["metric_key"])
    op.create_index("ix_kpi_recommendations_insight_id", "kpi_recommendations", ["insight_id"])
    op.create_index("ix_kpi_recommendations_category", "kpi_recommendations", ["category"])
    op.create_index("ix_kpi_recommendations_status", "kpi_recommendations", ["status"])

    op.create_table(
        "kpi_execution_intents",
        sa.Column("execution_intent_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("recommendation_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=False),
        sa.Column("adapter_kind", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=128), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("requested_via", sa.String(length=64), nullable=False),
        sa.Column("approved_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("validation_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("safety_level", sa.String(length=32), nullable=False),
        sa.Column("reversibility", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_execution_intents_organization_id", "kpi_execution_intents", ["organization_id"])
    op.create_index("ix_kpi_execution_intents_recommendation_id", "kpi_execution_intents", ["recommendation_id"])
    op.create_index("ix_kpi_execution_intents_goal_id", "kpi_execution_intents", ["goal_id"])
    op.create_index("ix_kpi_execution_intents_adapter_kind", "kpi_execution_intents", ["adapter_kind"])
    op.create_index("ix_kpi_execution_intents_action_type", "kpi_execution_intents", ["action_type"])
    op.create_index("ix_kpi_execution_intents_execution_mode", "kpi_execution_intents", ["execution_mode"])
    op.create_index("ix_kpi_execution_intents_requested_by", "kpi_execution_intents", ["requested_by"])
    op.create_index("ix_kpi_execution_intents_safety_level", "kpi_execution_intents", ["safety_level"])
    op.create_index("ix_kpi_execution_intents_reversibility", "kpi_execution_intents", ["reversibility"])

    op.create_table(
        "kpi_execution_results",
        sa.Column("execution_result_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("execution_intent_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("changed_object_refs_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("before_state_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("after_state_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("diff_artifact_ref", sa.Text(), nullable=True),
        sa.Column("adapter_diagnostics_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rollback_handle_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_execution_results_organization_id", "kpi_execution_results", ["organization_id"])
    op.create_index("ix_kpi_execution_results_execution_intent_id", "kpi_execution_results", ["execution_intent_id"])
    op.create_index("ix_kpi_execution_results_status", "kpi_execution_results", ["status"])
    op.create_index("ix_kpi_execution_results_error_code", "kpi_execution_results", ["error_code"])

    op.create_table(
        "kpi_impact_assessments",
        sa.Column("assessment_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=True),
        sa.Column("recommendation_id", sa.String(length=255), nullable=True),
        sa.Column("execution_intent_id", sa.String(length=255), nullable=True),
        sa.Column("experiment_id", sa.String(length=255), nullable=True),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("baseline_observation_id", sa.String(length=255), nullable=False),
        sa.Column("comparison_observation_id", sa.String(length=255), nullable=False),
        sa.Column("attribution_mode", sa.String(length=64), nullable=False),
        sa.Column("attribution_confidence", sa.String(length=32), nullable=False),
        sa.Column("observed_change", sa.Float(), nullable=False),
        sa.Column("attributed_change", sa.Float(), nullable=True),
        sa.Column("projected_impact_min", sa.Float(), nullable=True),
        sa.Column("projected_impact_max", sa.Float(), nullable=True),
        sa.Column("attainment_fraction", sa.Float(), nullable=True),
        sa.Column("competing_changes_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_impact_assessments_organization_id", "kpi_impact_assessments", ["organization_id"])
    op.create_index("ix_kpi_impact_assessments_goal_id", "kpi_impact_assessments", ["goal_id"])
    op.create_index("ix_kpi_impact_assessments_recommendation_id", "kpi_impact_assessments", ["recommendation_id"])
    op.create_index("ix_kpi_impact_assessments_execution_intent_id", "kpi_impact_assessments", ["execution_intent_id"])
    op.create_index("ix_kpi_impact_assessments_experiment_id", "kpi_impact_assessments", ["experiment_id"])
    op.create_index("ix_kpi_impact_assessments_metric_key", "kpi_impact_assessments", ["metric_key"])
    op.create_index("ix_kpi_impact_assessments_scope_id", "kpi_impact_assessments", ["scope_id"])
    op.create_index("ix_kpi_impact_assessments_baseline_observation_id", "kpi_impact_assessments", ["baseline_observation_id"])
    op.create_index("ix_kpi_impact_assessments_comparison_observation_id", "kpi_impact_assessments", ["comparison_observation_id"])
    op.create_index("ix_kpi_impact_assessments_attribution_mode", "kpi_impact_assessments", ["attribution_mode"])
    op.create_index("ix_kpi_impact_assessments_attribution_confidence", "kpi_impact_assessments", ["attribution_confidence"])

    op.create_table(
        "kpi_experiments",
        sa.Column("experiment_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("goal_id", sa.String(length=255), nullable=True),
        sa.Column("recommendation_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("primary_metric_key", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kpi_experiments_organization_id", "kpi_experiments", ["organization_id"])
    op.create_index("ix_kpi_experiments_goal_id", "kpi_experiments", ["goal_id"])
    op.create_index("ix_kpi_experiments_recommendation_id", "kpi_experiments", ["recommendation_id"])
    op.create_index("ix_kpi_experiments_status", "kpi_experiments", ["status"])
    op.create_index("ix_kpi_experiments_primary_metric_key", "kpi_experiments", ["primary_metric_key"])
    op.create_index("ix_kpi_experiments_scope_id", "kpi_experiments", ["scope_id"])

    for table_name in _RLS_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(_policy_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))

    op.drop_index("ix_kpi_experiments_scope_id", table_name="kpi_experiments")
    op.drop_index("ix_kpi_experiments_primary_metric_key", table_name="kpi_experiments")
    op.drop_index("ix_kpi_experiments_status", table_name="kpi_experiments")
    op.drop_index("ix_kpi_experiments_recommendation_id", table_name="kpi_experiments")
    op.drop_index("ix_kpi_experiments_goal_id", table_name="kpi_experiments")
    op.drop_index("ix_kpi_experiments_organization_id", table_name="kpi_experiments")
    op.drop_table("kpi_experiments")

    op.drop_index("ix_kpi_impact_assessments_attribution_confidence", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_attribution_mode", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_comparison_observation_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_baseline_observation_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_scope_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_metric_key", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_experiment_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_execution_intent_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_recommendation_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_goal_id", table_name="kpi_impact_assessments")
    op.drop_index("ix_kpi_impact_assessments_organization_id", table_name="kpi_impact_assessments")
    op.drop_table("kpi_impact_assessments")

    op.drop_index("ix_kpi_execution_results_error_code", table_name="kpi_execution_results")
    op.drop_index("ix_kpi_execution_results_status", table_name="kpi_execution_results")
    op.drop_index("ix_kpi_execution_results_execution_intent_id", table_name="kpi_execution_results")
    op.drop_index("ix_kpi_execution_results_organization_id", table_name="kpi_execution_results")
    op.drop_table("kpi_execution_results")

    op.drop_index("ix_kpi_execution_intents_reversibility", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_safety_level", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_requested_by", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_execution_mode", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_action_type", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_adapter_kind", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_goal_id", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_recommendation_id", table_name="kpi_execution_intents")
    op.drop_index("ix_kpi_execution_intents_organization_id", table_name="kpi_execution_intents")
    op.drop_table("kpi_execution_intents")

    op.drop_index("ix_kpi_recommendations_status", table_name="kpi_recommendations")
    op.drop_index("ix_kpi_recommendations_category", table_name="kpi_recommendations")
    op.drop_index("ix_kpi_recommendations_insight_id", table_name="kpi_recommendations")
    op.drop_index("ix_kpi_recommendations_metric_key", table_name="kpi_recommendations")
    op.drop_index("ix_kpi_recommendations_scope_id", table_name="kpi_recommendations")
    op.drop_index("ix_kpi_recommendations_goal_id", table_name="kpi_recommendations")
    op.drop_index("ix_kpi_recommendations_organization_id", table_name="kpi_recommendations")
    op.drop_table("kpi_recommendations")

    op.drop_index("ix_kpi_insights_rank_score", table_name="kpi_insights")
    op.drop_index("ix_kpi_insights_status", table_name="kpi_insights")
    op.drop_index("ix_kpi_insights_blocker_kind", table_name="kpi_insights")
    op.drop_index("ix_kpi_insights_metric_key", table_name="kpi_insights")
    op.drop_index("ix_kpi_insights_scope_id", table_name="kpi_insights")
    op.drop_index("ix_kpi_insights_goal_id", table_name="kpi_insights")
    op.drop_index("ix_kpi_insights_organization_id", table_name="kpi_insights")
    op.drop_table("kpi_insights")

    op.drop_index("ix_kpi_goal_evaluations_status", table_name="kpi_goal_evaluations")
    op.drop_index("ix_kpi_goal_evaluations_observation_id", table_name="kpi_goal_evaluations")
    op.drop_index("ix_kpi_goal_evaluations_goal_id", table_name="kpi_goal_evaluations")
    op.drop_index("ix_kpi_goal_evaluations_organization_id", table_name="kpi_goal_evaluations")
    op.drop_table("kpi_goal_evaluations")

    op.drop_index("ix_kpi_goals_v2_latest_evaluation_id", table_name="kpi_goals_v2")
    op.drop_index("ix_kpi_goals_v2_baseline_snapshot_id", table_name="kpi_goals_v2")
    op.drop_index("ix_kpi_goals_v2_owner_user_id", table_name="kpi_goals_v2")
    op.drop_index("ix_kpi_goals_v2_status", table_name="kpi_goals_v2")
    op.drop_index("ix_kpi_goals_v2_scope_id", table_name="kpi_goals_v2")
    op.drop_index("ix_kpi_goals_v2_metric_key", table_name="kpi_goals_v2")
    op.drop_index("ix_kpi_goals_v2_organization_id", table_name="kpi_goals_v2")
    op.drop_table("kpi_goals_v2")

    op.drop_index("ix_kpi_baseline_snapshots_source_observation_id", table_name="kpi_baseline_snapshots")
    op.drop_index("ix_kpi_baseline_snapshots_scope_id", table_name="kpi_baseline_snapshots")
    op.drop_index("ix_kpi_baseline_snapshots_metric_key", table_name="kpi_baseline_snapshots")
    op.drop_index("ix_kpi_baseline_snapshots_goal_id", table_name="kpi_baseline_snapshots")
    op.drop_index("ix_kpi_baseline_snapshots_organization_id", table_name="kpi_baseline_snapshots")
    op.drop_table("kpi_baseline_snapshots")

    op.drop_index("ix_kpi_metric_observations_period_end", table_name="kpi_metric_observations")
    op.drop_index("ix_kpi_metric_observations_observation_kind", table_name="kpi_metric_observations")
    op.drop_index("ix_kpi_metric_observations_scope_id", table_name="kpi_metric_observations")
    op.drop_index("ix_kpi_metric_observations_metric_key", table_name="kpi_metric_observations")
    op.drop_index("ix_kpi_metric_observations_organization_id", table_name="kpi_metric_observations")
    op.drop_table("kpi_metric_observations")

    op.drop_index("ix_kpi_metric_scopes_fingerprint", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_campaign_key", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_segment_key", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_channel", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_workflow_id", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_agent_id", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_scope_kind", table_name="kpi_metric_scopes")
    op.drop_index("ix_kpi_metric_scopes_organization_id", table_name="kpi_metric_scopes")
    op.drop_table("kpi_metric_scopes")
