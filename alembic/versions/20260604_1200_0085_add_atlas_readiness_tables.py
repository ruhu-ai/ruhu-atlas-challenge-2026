"""add atlas readiness evaluation tables

Revision ID: 0085_atlas_readiness
Revises: 0084a_atlas_core
Create Date: 2026-06-04 12:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0085_atlas_readiness"
down_revision = "0084a_atlas_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "atlas_readiness_runs",
        sa.Column("run_id", sa.String(255), primary_key=True),
        sa.Column("agent_id", sa.String(255), sa.ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "agent_version_id",
            sa.String(255),
            sa.ForeignKey("agent_versions.version_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "atlas_session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("provider_policy", sa.String(32), nullable=False),
        sa.Column("case_set_id", sa.String(255), nullable=True),
        sa.Column("document_hash", sa.String(128), nullable=True),
        sa.Column("policy_hash", sa.String(128), nullable=True),
        sa.Column("provider_config_hash", sa.String(128), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("blocker_codes_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_readiness_runs_org_created", "atlas_readiness_runs", ["organization_id", "created_at"])
    op.create_index("ix_atlas_readiness_runs_agent_created", "atlas_readiness_runs", ["agent_id", "created_at"])
    op.create_index("ix_atlas_readiness_runs_agent_id", "atlas_readiness_runs", ["agent_id"])
    op.create_index("ix_atlas_readiness_runs_agent_version_id", "atlas_readiness_runs", ["agent_version_id"])
    op.create_index("ix_atlas_readiness_runs_atlas_session_id", "atlas_readiness_runs", ["atlas_session_id"])
    op.create_index("ix_atlas_readiness_runs_scope", "atlas_readiness_runs", ["scope"])
    op.create_index("ix_atlas_readiness_runs_state", "atlas_readiness_runs", ["state"])
    op.create_index("ix_atlas_readiness_runs_provider_policy", "atlas_readiness_runs", ["provider_policy"])
    op.create_index("ix_atlas_readiness_runs_case_set_id", "atlas_readiness_runs", ["case_set_id"])
    op.create_index("ix_atlas_readiness_runs_document_hash", "atlas_readiness_runs", ["document_hash"])
    op.create_index("ix_atlas_readiness_runs_created_by_user_id", "atlas_readiness_runs", ["created_by_user_id"])
    op.create_index("ix_atlas_readiness_runs_created_at", "atlas_readiness_runs", ["created_at"])
    op.create_index("ix_atlas_readiness_runs_updated_at", "atlas_readiness_runs", ["updated_at"])
    op.create_index("ix_atlas_readiness_runs_completed_at", "atlas_readiness_runs", ["completed_at"])
    op.create_index("ix_atlas_readiness_runs_organization_id", "atlas_readiness_runs", ["organization_id"])

    op.create_table(
        "atlas_readiness_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_atlas_readiness_events_run_sequence"),
    )
    op.create_index("ix_atlas_readiness_events_run_created", "atlas_readiness_events", ["run_id", "created_at"])
    op.create_index("ix_atlas_readiness_events_run_id", "atlas_readiness_events", ["run_id"])
    op.create_index("ix_atlas_readiness_events_event_type", "atlas_readiness_events", ["event_type"])
    op.create_index("ix_atlas_readiness_events_created_at", "atlas_readiness_events", ["created_at"])
    op.create_index("ix_atlas_readiness_events_organization_id", "atlas_readiness_events", ["organization_id"])

    op.create_table(
        "atlas_readiness_case_sets",
        sa.Column("case_set_id", sa.String(255), primary_key=True),
        sa.Column("agent_id", sa.String(255), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("provider_policy", sa.String(32), nullable=False),
        sa.Column("cases_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_readiness_case_sets_org_created", "atlas_readiness_case_sets", ["organization_id", "created_at"])
    op.create_index("ix_atlas_readiness_case_sets_agent_created", "atlas_readiness_case_sets", ["agent_id", "created_at"])
    op.create_index("ix_atlas_readiness_case_sets_agent_id", "atlas_readiness_case_sets", ["agent_id"])
    op.create_index("ix_atlas_readiness_case_sets_seed", "atlas_readiness_case_sets", ["seed"])
    op.create_index("ix_atlas_readiness_case_sets_provider_policy", "atlas_readiness_case_sets", ["provider_policy"])
    op.create_index("ix_atlas_readiness_case_sets_created_at", "atlas_readiness_case_sets", ["created_at"])
    op.create_index("ix_atlas_readiness_case_sets_organization_id", "atlas_readiness_case_sets", ["organization_id"])

    op.create_table(
        "atlas_readiness_cases",
        sa.Column("readiness_case_id", sa.String(255), primary_key=True),
        sa.Column("case_id", sa.String(255), nullable=False),
        sa.Column(
            "case_set_id",
            sa.String(255),
            sa.ForeignKey("atlas_readiness_case_sets.case_set_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=True),
        sa.Column("case_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_readiness_cases_case_id", "atlas_readiness_cases", ["case_id"])
    op.create_index("ix_atlas_readiness_cases_case_set", "atlas_readiness_cases", ["case_set_id"])
    op.create_index("ix_atlas_readiness_cases_case_set_id", "atlas_readiness_cases", ["case_set_id"])
    op.create_index("ix_atlas_readiness_cases_run_id", "atlas_readiness_cases", ["run_id"])
    op.create_index("ix_atlas_readiness_cases_created_at", "atlas_readiness_cases", ["created_at"])
    op.create_index("ix_atlas_readiness_cases_organization_id", "atlas_readiness_cases", ["organization_id"])

    op.create_table(
        "atlas_readiness_trace_snapshots",
        sa.Column("trace_snapshot_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_readiness_trace_snapshots_run_id", "atlas_readiness_trace_snapshots", ["run_id"])
    op.create_index("ix_atlas_readiness_trace_snapshots_case_id", "atlas_readiness_trace_snapshots", ["case_id"])
    op.create_index("ix_atlas_readiness_trace_snapshots_conversation_id", "atlas_readiness_trace_snapshots", ["conversation_id"])
    op.create_index("ix_atlas_readiness_trace_snapshots_created_at", "atlas_readiness_trace_snapshots", ["created_at"])
    op.create_index("ix_atlas_readiness_trace_snapshots_organization_id", "atlas_readiness_trace_snapshots", ["organization_id"])

    op.create_table(
        "atlas_readiness_scores",
        sa.Column("score_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.String(255), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("case_score", sa.Float(), nullable=False),
        sa.Column("score_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_readiness_scores_run_id", "atlas_readiness_scores", ["run_id"])
    op.create_index("ix_atlas_readiness_scores_case_id", "atlas_readiness_scores", ["case_id"])
    op.create_index("ix_atlas_readiness_scores_passed", "atlas_readiness_scores", ["passed"])
    op.create_index("ix_atlas_readiness_scores_created_at", "atlas_readiness_scores", ["created_at"])
    op.create_index("ix_atlas_readiness_scores_organization_id", "atlas_readiness_scores", ["organization_id"])

    op.create_table(
        "atlas_readiness_reports",
        sa.Column("report_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("publish_recommendation", sa.String(32), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("run_id", name="uq_atlas_readiness_reports_run_id"),
    )
    op.create_index("ix_atlas_readiness_reports_run_id", "atlas_readiness_reports", ["run_id"])
    op.create_index("ix_atlas_readiness_reports_publish_recommendation", "atlas_readiness_reports", ["publish_recommendation"])
    op.create_index("ix_atlas_readiness_reports_created_at", "atlas_readiness_reports", ["created_at"])
    op.create_index("ix_atlas_readiness_reports_organization_id", "atlas_readiness_reports", ["organization_id"])

    op.create_table(
        "atlas_model_invocations",
        sa.Column("invocation_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_model_invocations_run_created", "atlas_model_invocations", ["run_id", "created_at"])
    op.create_index("ix_atlas_model_invocations_run_id", "atlas_model_invocations", ["run_id"])
    op.create_index("ix_atlas_model_invocations_provider", "atlas_model_invocations", ["provider"])
    op.create_index("ix_atlas_model_invocations_model", "atlas_model_invocations", ["model"])
    op.create_index("ix_atlas_model_invocations_role", "atlas_model_invocations", ["role"])
    op.create_index("ix_atlas_model_invocations_created_at", "atlas_model_invocations", ["created_at"])
    op.create_index("ix_atlas_model_invocations_organization_id", "atlas_model_invocations", ["organization_id"])

    op.create_table(
        "atlas_voice_artifacts",
        sa.Column("artifact_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_voice_artifacts_run_id", "atlas_voice_artifacts", ["run_id"])
    op.create_index("ix_atlas_voice_artifacts_case_id", "atlas_voice_artifacts", ["case_id"])
    op.create_index("ix_atlas_voice_artifacts_provider", "atlas_voice_artifacts", ["provider"])
    op.create_index("ix_atlas_voice_artifacts_artifact_type", "atlas_voice_artifacts", ["artifact_type"])
    op.create_index("ix_atlas_voice_artifacts_created_at", "atlas_voice_artifacts", ["created_at"])
    op.create_index("ix_atlas_voice_artifacts_organization_id", "atlas_voice_artifacts", ["organization_id"])

    op.create_table(
        "atlas_readiness_apply_locks",
        sa.Column("lock_id", sa.String(255), primary_key=True),
        sa.Column("run_id", sa.String(255), sa.ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("draft_version_id", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("agent_id", "draft_version_id", name="uq_atlas_readiness_apply_locks_agent_draft"),
    )
    op.create_index("ix_atlas_readiness_apply_locks_run_id", "atlas_readiness_apply_locks", ["run_id"])
    op.create_index("ix_atlas_readiness_apply_locks_agent_id", "atlas_readiness_apply_locks", ["agent_id"])
    op.create_index("ix_atlas_readiness_apply_locks_draft_version_id", "atlas_readiness_apply_locks", ["draft_version_id"])
    op.create_index("ix_atlas_readiness_apply_locks_expires_at", "atlas_readiness_apply_locks", ["expires_at"])
    op.create_index("ix_atlas_readiness_apply_locks_created_at", "atlas_readiness_apply_locks", ["created_at"])
    op.create_index("ix_atlas_readiness_apply_locks_organization_id", "atlas_readiness_apply_locks", ["organization_id"])


def downgrade() -> None:
    for table_name in (
        "atlas_readiness_apply_locks",
        "atlas_voice_artifacts",
        "atlas_model_invocations",
        "atlas_readiness_reports",
        "atlas_readiness_scores",
        "atlas_readiness_trace_snapshots",
        "atlas_readiness_cases",
        "atlas_readiness_case_sets",
        "atlas_readiness_events",
        "atlas_readiness_runs",
    ):
        op.drop_table(table_name)
