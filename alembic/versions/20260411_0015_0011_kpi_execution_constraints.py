"""tighten kpi execution integrity constraints

Revision ID: 0011_kpi_execution_constraints
Revises: 0010_kpi_domain_tables
Create Date: 2026-04-11 00:15:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_kpi_execution_constraints"
down_revision = "0010_kpi_domain_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE kpi_execution_intents SET goal_id = NULL WHERE goal_id = ''"))
    op.alter_column("kpi_execution_intents", "goal_id", existing_type=sa.String(length=255), nullable=True)

    op.create_foreign_key(
        "fk_kpi_metric_observations_scope_id",
        "kpi_metric_observations",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_baseline_snapshots_scope_id",
        "kpi_baseline_snapshots",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_baseline_snapshots_source_observation_id",
        "kpi_baseline_snapshots",
        "kpi_metric_observations",
        ["source_observation_id"],
        ["observation_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_goals_scope_id",
        "kpi_goals_v2",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_goals_baseline_snapshot_id",
        "kpi_goals_v2",
        "kpi_baseline_snapshots",
        ["baseline_snapshot_id"],
        ["baseline_snapshot_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_goal_evaluations_goal_id",
        "kpi_goal_evaluations",
        "kpi_goals_v2",
        ["goal_id"],
        ["goal_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_kpi_goal_evaluations_observation_id",
        "kpi_goal_evaluations",
        "kpi_metric_observations",
        ["observation_id"],
        ["observation_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_insights_goal_id",
        "kpi_insights",
        "kpi_goals_v2",
        ["goal_id"],
        ["goal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_insights_scope_id",
        "kpi_insights",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_recommendations_goal_id",
        "kpi_recommendations",
        "kpi_goals_v2",
        ["goal_id"],
        ["goal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_recommendations_scope_id",
        "kpi_recommendations",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_recommendations_insight_id",
        "kpi_recommendations",
        "kpi_insights",
        ["insight_id"],
        ["insight_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_execution_intents_recommendation_id",
        "kpi_execution_intents",
        "kpi_recommendations",
        ["recommendation_id"],
        ["recommendation_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_kpi_execution_intents_goal_id",
        "kpi_execution_intents",
        "kpi_goals_v2",
        ["goal_id"],
        ["goal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_execution_results_execution_intent_id",
        "kpi_execution_results",
        "kpi_execution_intents",
        ["execution_intent_id"],
        ["execution_intent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_goal_id",
        "kpi_impact_assessments",
        "kpi_goals_v2",
        ["goal_id"],
        ["goal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_recommendation_id",
        "kpi_impact_assessments",
        "kpi_recommendations",
        ["recommendation_id"],
        ["recommendation_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_execution_intent_id",
        "kpi_impact_assessments",
        "kpi_execution_intents",
        ["execution_intent_id"],
        ["execution_intent_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_experiment_id",
        "kpi_impact_assessments",
        "kpi_experiments",
        ["experiment_id"],
        ["experiment_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_scope_id",
        "kpi_impact_assessments",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_baseline_observation_id",
        "kpi_impact_assessments",
        "kpi_metric_observations",
        ["baseline_observation_id"],
        ["observation_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_impact_assessments_comparison_observation_id",
        "kpi_impact_assessments",
        "kpi_metric_observations",
        ["comparison_observation_id"],
        ["observation_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_kpi_experiments_goal_id",
        "kpi_experiments",
        "kpi_goals_v2",
        ["goal_id"],
        ["goal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_experiments_recommendation_id",
        "kpi_experiments",
        "kpi_recommendations",
        ["recommendation_id"],
        ["recommendation_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_kpi_experiments_scope_id",
        "kpi_experiments",
        "kpi_metric_scopes",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_kpi_experiments_scope_id", "kpi_experiments", type_="foreignkey")
    op.drop_constraint("fk_kpi_experiments_recommendation_id", "kpi_experiments", type_="foreignkey")
    op.drop_constraint("fk_kpi_experiments_goal_id", "kpi_experiments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_comparison_observation_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_baseline_observation_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_scope_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_experiment_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_execution_intent_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_recommendation_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_impact_assessments_goal_id", "kpi_impact_assessments", type_="foreignkey")
    op.drop_constraint("fk_kpi_execution_results_execution_intent_id", "kpi_execution_results", type_="foreignkey")
    op.drop_constraint("fk_kpi_execution_intents_goal_id", "kpi_execution_intents", type_="foreignkey")
    op.drop_constraint("fk_kpi_execution_intents_recommendation_id", "kpi_execution_intents", type_="foreignkey")
    op.drop_constraint("fk_kpi_recommendations_insight_id", "kpi_recommendations", type_="foreignkey")
    op.drop_constraint("fk_kpi_recommendations_scope_id", "kpi_recommendations", type_="foreignkey")
    op.drop_constraint("fk_kpi_recommendations_goal_id", "kpi_recommendations", type_="foreignkey")
    op.drop_constraint("fk_kpi_insights_scope_id", "kpi_insights", type_="foreignkey")
    op.drop_constraint("fk_kpi_insights_goal_id", "kpi_insights", type_="foreignkey")
    op.drop_constraint("fk_kpi_goal_evaluations_observation_id", "kpi_goal_evaluations", type_="foreignkey")
    op.drop_constraint("fk_kpi_goal_evaluations_goal_id", "kpi_goal_evaluations", type_="foreignkey")
    op.drop_constraint("fk_kpi_goals_baseline_snapshot_id", "kpi_goals_v2", type_="foreignkey")
    op.drop_constraint("fk_kpi_goals_scope_id", "kpi_goals_v2", type_="foreignkey")
    op.drop_constraint("fk_kpi_baseline_snapshots_source_observation_id", "kpi_baseline_snapshots", type_="foreignkey")
    op.drop_constraint("fk_kpi_baseline_snapshots_scope_id", "kpi_baseline_snapshots", type_="foreignkey")
    op.drop_constraint("fk_kpi_metric_observations_scope_id", "kpi_metric_observations", type_="foreignkey")

    op.alter_column("kpi_execution_intents", "goal_id", existing_type=sa.String(length=255), nullable=False)
