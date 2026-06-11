"""add simulation fixture and evaluation persistence tables

Revision ID: 0006_simulation_eval
Revises: 0005_realtime_core
Create Date: 2026-04-10 17:45:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_simulation_eval"
down_revision = "0005_realtime_core"
branch_labels = None
depends_on = None

_SIMULATION_EVAL_TENANT_TABLES = (
    "simulation_fixtures",
    "simulation_fixture_turns",
    "simulation_fixture_assertions",
    "evaluation_runs",
    "evaluation_case_results",
    "evaluation_assertion_results",
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
        "simulation_fixtures",
        sa.Column("fixture_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("default_channel", sa.String(length=64), nullable=False),
        sa.Column("default_modality", sa.String(length=32), nullable=False),
        sa.Column("starting_state_id", sa.String(length=255), nullable=True),
        sa.Column("seed_facts_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("gate_required", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fixture_id"),
    )
    op.create_index("ix_simulation_fixtures_organization_id", "simulation_fixtures", ["organization_id"], unique=False)
    op.create_index("ix_simulation_fixtures_graph_id", "simulation_fixtures", ["graph_id"], unique=False)
    op.create_index("ix_simulation_fixtures_is_active", "simulation_fixtures", ["is_active"], unique=False)
    op.create_index("ix_simulation_fixtures_gate_required", "simulation_fixtures", ["gate_required"], unique=False)
    op.create_index("ix_simulation_fixtures_created_by_user_id", "simulation_fixtures", ["created_by_user_id"], unique=False)

    op.create_table(
        "simulation_fixture_turns",
        sa.Column("fixture_turn_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("fixture_id", sa.String(length=255), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("modality", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["simulation_fixtures.fixture_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fixture_turn_id"),
        sa.UniqueConstraint("fixture_id", "order_index", name="uq_simulation_fixture_turns_fixture_order"),
    )
    op.create_index("ix_simulation_fixture_turns_organization_id", "simulation_fixture_turns", ["organization_id"], unique=False)
    op.create_index("ix_simulation_fixture_turns_fixture_id", "simulation_fixture_turns", ["fixture_id"], unique=False)

    op.create_table(
        "simulation_fixture_assertions",
        sa.Column("fixture_assertion_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("fixture_id", sa.String(length=255), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("assertion_kind", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["simulation_fixtures.fixture_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fixture_assertion_id"),
        sa.UniqueConstraint("fixture_id", "order_index", name="uq_simulation_fixture_assertions_fixture_order"),
    )
    op.create_index("ix_simulation_fixture_assertions_organization_id", "simulation_fixture_assertions", ["organization_id"], unique=False)
    op.create_index("ix_simulation_fixture_assertions_fixture_id", "simulation_fixture_assertions", ["fixture_id"], unique=False)
    op.create_index("ix_simulation_fixture_assertions_assertion_kind", "simulation_fixture_assertions", ["assertion_kind"], unique=False)
    op.create_index("ix_simulation_fixture_assertions_severity", "simulation_fixture_assertions", ["severity"], unique=False)

    op.create_table(
        "evaluation_runs",
        sa.Column("evaluation_run_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("graph_version_id", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("gate_eligible", sa.Boolean(), nullable=False),
        sa.Column("fixture_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("pass_rate_ratio", sa.Float(), nullable=True),
        sa.Column("triggered_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["graph_version_id"], ["graph_versions.version_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evaluation_run_id"),
    )
    op.create_index("ix_evaluation_runs_organization_id", "evaluation_runs", ["organization_id"], unique=False)
    op.create_index("ix_evaluation_runs_graph_id", "evaluation_runs", ["graph_id"], unique=False)
    op.create_index("ix_evaluation_runs_graph_version_id", "evaluation_runs", ["graph_version_id"], unique=False)
    op.create_index("ix_evaluation_runs_mode", "evaluation_runs", ["mode"], unique=False)
    op.create_index("ix_evaluation_runs_source", "evaluation_runs", ["source"], unique=False)
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"], unique=False)
    op.create_index("ix_evaluation_runs_gate_eligible", "evaluation_runs", ["gate_eligible"], unique=False)
    op.create_index("ix_evaluation_runs_triggered_by_user_id", "evaluation_runs", ["triggered_by_user_id"], unique=False)
    op.create_index("ix_evaluation_runs_started_at", "evaluation_runs", ["started_at"], unique=False)
    op.create_index("ix_evaluation_runs_qualified_at", "evaluation_runs", ["qualified_at"], unique=False)

    op.create_table(
        "evaluation_case_results",
        sa.Column("case_result_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("evaluation_run_id", sa.String(length=255), nullable=False),
        sa.Column("fixture_id", sa.String(length=255), nullable=True),
        sa.Column("fixture_name", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("final_state", sa.String(length=255), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("assertions_passed", sa.Integer(), nullable=False),
        sa.Column("assertions_failed", sa.Integer(), nullable=False),
        sa.Column("blocker_failures", sa.Integer(), nullable=False),
        sa.Column("warning_failures", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("actual_facts_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.evaluation_run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fixture_id"], ["simulation_fixtures.fixture_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("case_result_id"),
    )
    op.create_index("ix_evaluation_case_results_organization_id", "evaluation_case_results", ["organization_id"], unique=False)
    op.create_index("ix_evaluation_case_results_evaluation_run_id", "evaluation_case_results", ["evaluation_run_id"], unique=False)
    op.create_index("ix_evaluation_case_results_fixture_id", "evaluation_case_results", ["fixture_id"], unique=False)
    op.create_index("ix_evaluation_case_results_conversation_id", "evaluation_case_results", ["conversation_id"], unique=False)
    op.create_index("ix_evaluation_case_results_status", "evaluation_case_results", ["status"], unique=False)

    op.create_table(
        "evaluation_assertion_results",
        sa.Column("assertion_result_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("case_result_id", sa.String(length=255), nullable=False),
        sa.Column("fixture_assertion_id", sa.String(length=255), nullable=True),
        sa.Column("assertion_kind", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("expected_json", sa.JSON(), nullable=False),
        sa.Column("actual_json", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_result_id"], ["evaluation_case_results.case_result_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fixture_assertion_id"], ["simulation_fixture_assertions.fixture_assertion_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("assertion_result_id"),
    )
    op.create_index("ix_evaluation_assertion_results_organization_id", "evaluation_assertion_results", ["organization_id"], unique=False)
    op.create_index("ix_evaluation_assertion_results_case_result_id", "evaluation_assertion_results", ["case_result_id"], unique=False)
    op.create_index("ix_evaluation_assertion_results_fixture_assertion_id", "evaluation_assertion_results", ["fixture_assertion_id"], unique=False)
    op.create_index("ix_evaluation_assertion_results_assertion_kind", "evaluation_assertion_results", ["assertion_kind"], unique=False)
    op.create_index("ix_evaluation_assertion_results_severity", "evaluation_assertion_results", ["severity"], unique=False)
    op.create_index("ix_evaluation_assertion_results_passed", "evaluation_assertion_results", ["passed"], unique=False)

    for table_name in _SIMULATION_EVAL_TENANT_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(_policy_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(_SIMULATION_EVAL_TENANT_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_evaluation_assertion_results_passed", table_name="evaluation_assertion_results")
    op.drop_index("ix_evaluation_assertion_results_severity", table_name="evaluation_assertion_results")
    op.drop_index("ix_evaluation_assertion_results_assertion_kind", table_name="evaluation_assertion_results")
    op.drop_index("ix_evaluation_assertion_results_fixture_assertion_id", table_name="evaluation_assertion_results")
    op.drop_index("ix_evaluation_assertion_results_case_result_id", table_name="evaluation_assertion_results")
    op.drop_index("ix_evaluation_assertion_results_organization_id", table_name="evaluation_assertion_results")
    op.drop_table("evaluation_assertion_results")

    op.drop_index("ix_evaluation_case_results_status", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_conversation_id", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_fixture_id", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_evaluation_run_id", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_organization_id", table_name="evaluation_case_results")
    op.drop_table("evaluation_case_results")

    op.drop_index("ix_evaluation_runs_qualified_at", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_started_at", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_triggered_by_user_id", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_gate_eligible", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_status", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_source", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_mode", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_graph_version_id", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_graph_id", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_organization_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")

    op.drop_index("ix_simulation_fixture_assertions_severity", table_name="simulation_fixture_assertions")
    op.drop_index("ix_simulation_fixture_assertions_assertion_kind", table_name="simulation_fixture_assertions")
    op.drop_index("ix_simulation_fixture_assertions_fixture_id", table_name="simulation_fixture_assertions")
    op.drop_index("ix_simulation_fixture_assertions_organization_id", table_name="simulation_fixture_assertions")
    op.drop_table("simulation_fixture_assertions")

    op.drop_index("ix_simulation_fixture_turns_fixture_id", table_name="simulation_fixture_turns")
    op.drop_index("ix_simulation_fixture_turns_organization_id", table_name="simulation_fixture_turns")
    op.drop_table("simulation_fixture_turns")

    op.drop_index("ix_simulation_fixtures_created_by_user_id", table_name="simulation_fixtures")
    op.drop_index("ix_simulation_fixtures_gate_required", table_name="simulation_fixtures")
    op.drop_index("ix_simulation_fixtures_is_active", table_name="simulation_fixtures")
    op.drop_index("ix_simulation_fixtures_graph_id", table_name="simulation_fixtures")
    op.drop_index("ix_simulation_fixtures_organization_id", table_name="simulation_fixtures")
    op.drop_table("simulation_fixtures")
