"""Rename remaining graph-era storage columns to agent/step/workflow names.

Revision ID: 0063
Revises: 0062
Create Date: 2026-04-22 15:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def _rename_column_if_exists(table_name: str, old_name: str, new_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                      AND column_name = '{old_name}'
                ) THEN
                    ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}";
                END IF;
            END $$;
            """
        )
    )


def _rename_index_if_exists(old_name: str, new_name: str) -> None:
    op.execute(sa.text(f'ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}"'))


def upgrade() -> None:
    column_renames = [
        ("agents", "graph_id", "agent_id"),
        ("agent_versions", "graph_id", "agent_id"),
        ("agent_versions", "state_graph_json", "workflow_view_json"),
        ("agent_templates", "state_graph_json", "workflow_view_json"),
        ("conversations", "graph_id", "agent_id"),
        ("conversations", "graph_version_id", "agent_version_id"),
        ("conversations", "state_id", "step_id"),
        ("turn_traces", "graph_id", "agent_id"),
        ("turn_traces", "graph_version_id", "agent_version_id"),
        ("turn_traces", "state_before", "step_before"),
        ("turn_traces", "state_after", "step_after"),
        ("phone_number_routes", "graph_id", "agent_id"),
        ("simulation_fixtures", "graph_id", "agent_id"),
        ("evaluation_runs", "graph_id", "agent_id"),
        ("evaluation_runs", "graph_version_id", "agent_version_id"),
        ("journey_instances", "first_graph_id", "first_agent_id"),
        ("journey_instances", "first_graph_version_id", "first_agent_version_id"),
        ("journey_instances", "latest_graph_id", "latest_agent_id"),
        ("journey_instances", "latest_graph_version_id", "latest_agent_version_id"),
        ("journey_touchpoints", "graph_id", "agent_id"),
        ("journey_touchpoints", "graph_version_id", "agent_version_id"),
        ("support_cases", "owning_graph_id", "owning_agent_id"),
        ("intent_definitions", "graph_id", "agent_id"),
        ("tag_definitions", "graph_id", "agent_id"),
        ("intent_tag_classifier_profiles", "graph_id", "agent_id"),
        ("intent_tag_classification_events", "graph_id", "agent_id"),
        ("intent_tag_classification_events", "graph_version_id", "agent_version_id"),
        ("intent_tag_conversation_summaries", "graph_id", "agent_id"),
        ("intent_tag_conversation_summaries", "graph_version_id", "agent_version_id"),
        ("intent_tag_semantic_webhook_targets", "graph_ids_json", "agent_ids_json"),
        ("rule_bindings", "graph_ids", "agent_ids"),
        ("rule_bindings", "state_ids", "step_ids"),
    ]
    for table_name, old_name, new_name in column_renames:
        _rename_column_if_exists(table_name, old_name, new_name)

    index_renames = [
        ("ix_agent_versions_graph_id", "ix_agent_versions_agent_id"),
        ("ix_conversations_graph_version_id", "ix_conversations_agent_version_id"),
        ("ix_turn_traces_graph_version_id", "ix_turn_traces_agent_version_id"),
        ("ix_turn_traces_org_graph_recorded", "ix_turn_traces_org_agent_recorded"),
        ("ix_turn_traces_org_graph_version_recorded", "ix_turn_traces_org_agent_version_recorded"),
        ("ix_phone_number_routes_graph_id", "ix_phone_number_routes_agent_id"),
        ("ix_simulation_fixtures_graph_id", "ix_simulation_fixtures_agent_id"),
        ("ix_evaluation_runs_graph_id", "ix_evaluation_runs_agent_id"),
        ("ix_evaluation_runs_graph_version_id", "ix_evaluation_runs_agent_version_id"),
        ("ix_journey_instances_first_graph_id", "ix_journey_instances_first_agent_id"),
        ("ix_journey_instances_first_graph_version_id", "ix_journey_instances_first_agent_version_id"),
        ("ix_journey_instances_latest_graph_id", "ix_journey_instances_latest_agent_id"),
        ("ix_journey_instances_latest_graph_version_id", "ix_journey_instances_latest_agent_version_id"),
        ("ix_journey_touchpoints_graph_id", "ix_journey_touchpoints_agent_id"),
        ("ix_journey_touchpoints_graph_version_id", "ix_journey_touchpoints_agent_version_id"),
        ("ix_support_cases_owning_graph_id", "ix_support_cases_owning_agent_id"),
        ("ix_intent_definitions_graph_id", "ix_intent_definitions_agent_id"),
        ("ix_tag_definitions_graph_id", "ix_tag_definitions_agent_id"),
        ("ix_intent_tag_classifier_profiles_graph_id", "ix_intent_tag_classifier_profiles_agent_id"),
        ("ix_intent_tag_classification_events_graph_id", "ix_intent_tag_classification_events_agent_id"),
        (
            "ix_intent_tag_classification_events_graph_version_id",
            "ix_intent_tag_classification_events_agent_version_id",
        ),
        (
            "ix_intent_tag_conversation_summaries_graph_id",
            "ix_intent_tag_conversation_summaries_agent_id",
        ),
        (
            "ix_intent_tag_conversation_summaries_graph_version_id",
            "ix_intent_tag_conversation_summaries_agent_version_id",
        ),
        ("ix_rule_bindings_graph_ids_gin", "ix_rule_bindings_agent_ids_gin"),
    ]
    for old_name, new_name in index_renames:
        _rename_index_if_exists(old_name, new_name)


def downgrade() -> None:
    index_renames = [
        ("ix_rule_bindings_agent_ids_gin", "ix_rule_bindings_graph_ids_gin"),
        (
            "ix_intent_tag_conversation_summaries_agent_version_id",
            "ix_intent_tag_conversation_summaries_graph_version_id",
        ),
        ("ix_intent_tag_conversation_summaries_agent_id", "ix_intent_tag_conversation_summaries_graph_id"),
        (
            "ix_intent_tag_classification_events_agent_version_id",
            "ix_intent_tag_classification_events_graph_version_id",
        ),
        ("ix_intent_tag_classification_events_agent_id", "ix_intent_tag_classification_events_graph_id"),
        ("ix_intent_tag_classifier_profiles_agent_id", "ix_intent_tag_classifier_profiles_graph_id"),
        ("ix_tag_definitions_agent_id", "ix_tag_definitions_graph_id"),
        ("ix_intent_definitions_agent_id", "ix_intent_definitions_graph_id"),
        ("ix_support_cases_owning_agent_id", "ix_support_cases_owning_graph_id"),
        ("ix_journey_touchpoints_agent_version_id", "ix_journey_touchpoints_graph_version_id"),
        ("ix_journey_touchpoints_agent_id", "ix_journey_touchpoints_graph_id"),
        ("ix_journey_instances_latest_agent_version_id", "ix_journey_instances_latest_graph_version_id"),
        ("ix_journey_instances_latest_agent_id", "ix_journey_instances_latest_graph_id"),
        ("ix_journey_instances_first_agent_version_id", "ix_journey_instances_first_graph_version_id"),
        ("ix_journey_instances_first_agent_id", "ix_journey_instances_first_graph_id"),
        ("ix_evaluation_runs_agent_version_id", "ix_evaluation_runs_graph_version_id"),
        ("ix_evaluation_runs_agent_id", "ix_evaluation_runs_graph_id"),
        ("ix_simulation_fixtures_agent_id", "ix_simulation_fixtures_graph_id"),
        ("ix_phone_number_routes_agent_id", "ix_phone_number_routes_graph_id"),
        ("ix_turn_traces_org_agent_version_recorded", "ix_turn_traces_org_graph_version_recorded"),
        ("ix_turn_traces_org_agent_recorded", "ix_turn_traces_org_graph_recorded"),
        ("ix_turn_traces_agent_version_id", "ix_turn_traces_graph_version_id"),
        ("ix_conversations_agent_version_id", "ix_conversations_graph_version_id"),
        ("ix_agent_versions_agent_id", "ix_agent_versions_graph_id"),
    ]
    for old_name, new_name in index_renames:
        _rename_index_if_exists(old_name, new_name)

    column_renames = [
        ("rule_bindings", "step_ids", "state_ids"),
        ("rule_bindings", "agent_ids", "graph_ids"),
        ("intent_tag_semantic_webhook_targets", "agent_ids_json", "graph_ids_json"),
        ("intent_tag_conversation_summaries", "agent_version_id", "graph_version_id"),
        ("intent_tag_conversation_summaries", "agent_id", "graph_id"),
        ("intent_tag_classification_events", "agent_version_id", "graph_version_id"),
        ("intent_tag_classification_events", "agent_id", "graph_id"),
        ("intent_tag_classifier_profiles", "agent_id", "graph_id"),
        ("tag_definitions", "agent_id", "graph_id"),
        ("intent_definitions", "agent_id", "graph_id"),
        ("support_cases", "owning_agent_id", "owning_graph_id"),
        ("journey_touchpoints", "agent_version_id", "graph_version_id"),
        ("journey_touchpoints", "agent_id", "graph_id"),
        ("journey_instances", "latest_agent_version_id", "latest_graph_version_id"),
        ("journey_instances", "latest_agent_id", "latest_graph_id"),
        ("journey_instances", "first_agent_version_id", "first_graph_version_id"),
        ("journey_instances", "first_agent_id", "first_graph_id"),
        ("evaluation_runs", "agent_version_id", "graph_version_id"),
        ("evaluation_runs", "agent_id", "graph_id"),
        ("simulation_fixtures", "agent_id", "graph_id"),
        ("phone_number_routes", "agent_id", "graph_id"),
        ("turn_traces", "step_after", "state_after"),
        ("turn_traces", "step_before", "state_before"),
        ("turn_traces", "agent_version_id", "graph_version_id"),
        ("turn_traces", "agent_id", "graph_id"),
        ("conversations", "step_id", "state_id"),
        ("conversations", "agent_version_id", "graph_version_id"),
        ("conversations", "agent_id", "graph_id"),
        ("agent_templates", "workflow_view_json", "state_graph_json"),
        ("agent_versions", "workflow_view_json", "state_graph_json"),
        ("agent_versions", "agent_id", "graph_id"),
        ("agents", "agent_id", "graph_id"),
    ]
    for table_name, old_name, new_name in column_renames:
        _rename_column_if_exists(table_name, old_name, new_name)
