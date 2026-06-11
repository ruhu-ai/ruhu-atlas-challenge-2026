"""add atlas core tables

The eight core Atlas tables (sessions, messages, events, agent policies,
proposed deltas, apply requests, permission requests, review decisions) were
previously created only via ``Base.metadata.create_all`` — there was no
migration, while 0085 (atlas readiness) declares a foreign key to
``atlas_sessions.session_id``. This migration is chained BETWEEN 0084 and
0085 so a plain ``alembic upgrade head`` works on a fresh database.

Column definitions mirror ``src/ruhu/db_models.py`` exactly.

Revision ID: 0084a_atlas_core
Revises: 0084_capture_audit_outbox
Create Date: 2026-06-04 11:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0084a_atlas_core"
down_revision = "0084_capture_audit_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "atlas_agent_policies",
        sa.Column(
            "agent_id",
            sa.String(255),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("atlas_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_atlas_agent_policies_atlas_enabled", "atlas_agent_policies", ["atlas_enabled"])
    op.create_index("ix_atlas_agent_policies_updated_by_user_id", "atlas_agent_policies", ["updated_by_user_id"])
    op.create_index("ix_atlas_agent_policies_organization_id", "atlas_agent_policies", ["organization_id"])

    op.create_table(
        "atlas_sessions",
        sa.Column("session_id", sa.String(255), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(255),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_version_id",
            sa.String(255),
            sa.ForeignKey("agent_versions.version_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("scenario_id", sa.String(255), nullable=True),
        sa.Column("step_id", sa.String(255), nullable=True),
        sa.Column(
            "conversation_id",
            sa.String(255),
            sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "trace_id",
            sa.String(255),
            sa.ForeignKey("turn_traces.trace_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("atlas_enabled_snapshot", sa.Boolean(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_atlas_sessions_org_updated",
        "atlas_sessions",
        ["organization_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "ix_atlas_sessions_agent_updated",
        "atlas_sessions",
        ["agent_id", sa.text("updated_at DESC")],
    )
    op.create_index("ix_atlas_sessions_agent_id", "atlas_sessions", ["agent_id"])
    op.create_index("ix_atlas_sessions_agent_version_id", "atlas_sessions", ["agent_version_id"])
    op.create_index("ix_atlas_sessions_scope", "atlas_sessions", ["scope"])
    op.create_index("ix_atlas_sessions_status", "atlas_sessions", ["status"])
    op.create_index("ix_atlas_sessions_created_by", "atlas_sessions", ["created_by"])
    op.create_index("ix_atlas_sessions_scenario_id", "atlas_sessions", ["scenario_id"])
    op.create_index("ix_atlas_sessions_step_id", "atlas_sessions", ["step_id"])
    op.create_index("ix_atlas_sessions_conversation_id", "atlas_sessions", ["conversation_id"])
    op.create_index("ix_atlas_sessions_trace_id", "atlas_sessions", ["trace_id"])
    op.create_index("ix_atlas_sessions_archived_at", "atlas_sessions", ["archived_at"])
    op.create_index("ix_atlas_sessions_organization_id", "atlas_sessions", ["organization_id"])

    op.create_table(
        "atlas_messages",
        sa.Column("message_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("session_id", "sequence_number", name="uq_atlas_messages_session_sequence"),
    )
    op.create_index(
        "ix_atlas_messages_session_created",
        "atlas_messages",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_atlas_messages_session_id", "atlas_messages", ["session_id"])
    op.create_index("ix_atlas_messages_role", "atlas_messages", ["role"])
    op.create_index("ix_atlas_messages_created_at", "atlas_messages", ["created_at"])
    op.create_index("ix_atlas_messages_organization_id", "atlas_messages", ["organization_id"])

    op.create_table(
        "atlas_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("session_id", "sequence_number", name="uq_atlas_events_session_sequence"),
    )
    op.create_index(
        "ix_atlas_events_session_created",
        "atlas_events",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_atlas_events_session_id", "atlas_events", ["session_id"])
    op.create_index("ix_atlas_events_event_type", "atlas_events", ["event_type"])
    op.create_index("ix_atlas_events_created_at", "atlas_events", ["created_at"])
    op.create_index("ix_atlas_events_organization_id", "atlas_events", ["organization_id"])

    op.create_table(
        "atlas_review_decisions",
        sa.Column("review_decision_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta_id", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_atlas_review_decisions_session_created",
        "atlas_review_decisions",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_atlas_review_decisions_session_id", "atlas_review_decisions", ["session_id"])
    op.create_index("ix_atlas_review_decisions_delta_id", "atlas_review_decisions", ["delta_id"])
    op.create_index("ix_atlas_review_decisions_decision", "atlas_review_decisions", ["decision"])
    op.create_index(
        "ix_atlas_review_decisions_decided_by_user_id",
        "atlas_review_decisions",
        ["decided_by_user_id"],
    )
    op.create_index("ix_atlas_review_decisions_created_at", "atlas_review_decisions", ["created_at"])
    op.create_index("ix_atlas_review_decisions_organization_id", "atlas_review_decisions", ["organization_id"])

    op.create_table(
        "atlas_proposed_deltas",
        sa.Column("delta_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta_family", sa.String(64), nullable=False),
        sa.Column("delta_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_atlas_proposed_deltas_session_created",
        "atlas_proposed_deltas",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_atlas_proposed_deltas_session_family",
        "atlas_proposed_deltas",
        ["session_id", "delta_family"],
    )
    op.create_index("ix_atlas_proposed_deltas_session_id", "atlas_proposed_deltas", ["session_id"])
    op.create_index("ix_atlas_proposed_deltas_delta_family", "atlas_proposed_deltas", ["delta_family"])
    op.create_index("ix_atlas_proposed_deltas_created_at", "atlas_proposed_deltas", ["created_at"])
    op.create_index("ix_atlas_proposed_deltas_organization_id", "atlas_proposed_deltas", ["organization_id"])

    op.create_table(
        "atlas_apply_requests",
        sa.Column("apply_request_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("delta_ids_json", sa.JSON(), nullable=False),
        sa.Column("apply_note", sa.Text(), nullable=True),
        sa.Column("confirmed_by_user_id", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_atlas_apply_requests_session_created",
        "atlas_apply_requests",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_atlas_apply_requests_session_id", "atlas_apply_requests", ["session_id"])
    op.create_index("ix_atlas_apply_requests_status", "atlas_apply_requests", ["status"])
    op.create_index(
        "ix_atlas_apply_requests_confirmed_by_user_id",
        "atlas_apply_requests",
        ["confirmed_by_user_id"],
    )
    op.create_index("ix_atlas_apply_requests_created_at", "atlas_apply_requests", ["created_at"])
    op.create_index("ix_atlas_apply_requests_organization_id", "atlas_apply_requests", ["organization_id"])

    op.create_table(
        "atlas_permission_requests",
        sa.Column("request_id", sa.String(255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(255),
            sa.ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("risk_summary", sa.Text(), nullable=True),
        sa.Column("scope_ref_json", sa.JSON(), nullable=False),
        sa.Column("delta_ids_json", sa.JSON(), nullable=False),
        sa.Column("requested_actions_json", sa.JSON(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_atlas_permission_requests_session_created",
        "atlas_permission_requests",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_atlas_permission_requests_session_status",
        "atlas_permission_requests",
        ["session_id", "status"],
    )
    op.create_index("ix_atlas_permission_requests_session_id", "atlas_permission_requests", ["session_id"])
    op.create_index("ix_atlas_permission_requests_kind", "atlas_permission_requests", ["kind"])
    op.create_index("ix_atlas_permission_requests_status", "atlas_permission_requests", ["status"])
    op.create_index(
        "ix_atlas_permission_requests_decided_by_user_id",
        "atlas_permission_requests",
        ["decided_by_user_id"],
    )
    op.create_index("ix_atlas_permission_requests_created_at", "atlas_permission_requests", ["created_at"])
    op.create_index("ix_atlas_permission_requests_expires_at", "atlas_permission_requests", ["expires_at"])
    op.create_index("ix_atlas_permission_requests_decided_at", "atlas_permission_requests", ["decided_at"])
    op.create_index(
        "ix_atlas_permission_requests_organization_id",
        "atlas_permission_requests",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_table("atlas_permission_requests")
    op.drop_table("atlas_apply_requests")
    op.drop_table("atlas_proposed_deltas")
    op.drop_table("atlas_review_decisions")
    op.drop_table("atlas_events")
    op.drop_table("atlas_messages")
    op.drop_table("atlas_sessions")
    op.drop_table("atlas_agent_policies")
