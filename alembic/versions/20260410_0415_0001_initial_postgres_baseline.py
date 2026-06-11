"""initial postgres baseline

Revision ID: 0001_initial_postgres
Revises: None
Create Date: 2026-04-10 04:15:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_postgres"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("state_id", sa.String(length=255), nullable=False),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("processed_dedupe_keys_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_conversations_organization_id",
        "conversations",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "turn_traces",
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("turn_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=False),
        sa.Column("state_before", sa.String(length=255), nullable=False),
        sa.Column("state_after", sa.String(length=255), nullable=False),
        sa.Column("semantic_events_json", sa.JSON(), nullable=False),
        sa.Column("fact_updates_json", sa.JSON(), nullable=False),
        sa.Column("chosen_action_json", sa.JSON(), nullable=False),
        sa.Column("emitted_messages_json", sa.JSON(), nullable=False),
        sa.Column("tool_calls_json", sa.JSON(), nullable=False),
        sa.Column("latency_breakdown_ms_json", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_turn_traces_conversation_id", "turn_traces", ["conversation_id"], unique=False)
    op.create_index("ix_turn_traces_organization_id", "turn_traces", ["organization_id"], unique=False)
    op.create_index("ix_turn_traces_recorded_at", "turn_traces", ["recorded_at"], unique=False)

    op.create_table(
        "tool_invocations",
        sa.Column("invocation_id", sa.String(length=255), nullable=False),
        sa.Column("tool_ref", sa.String(length=255), nullable=False),
        sa.Column("executor_kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("caller_json", sa.JSON(), nullable=False),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("invocation_id"),
    )
    op.create_index("ix_tool_invocations_tool_ref", "tool_invocations", ["tool_ref"], unique=False)
    op.create_index("ix_tool_invocations_status", "tool_invocations", ["status"], unique=False)

    op.create_table(
        "identity_users",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("preferences_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_identity_users_email", "identity_users", ["email"], unique=True)

    op.create_table(
        "identity_organizations",
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("brand_color", sa.String(length=32), nullable=True),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_index("ix_identity_organizations_slug", "identity_organizations", ["slug"], unique=True)

    op.create_table(
        "identity_org_memberships",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("is_account_owner", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "organization_id"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
    op.create_index("ix_auth_sessions_organization_id", "auth_sessions", ["organization_id"], unique=False)

    op.create_table(
        "auth_refresh_families",
        sa.Column("family_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("current_token_id", sa.String(length=255), nullable=False),
        sa.Column("current_token_hash", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compromised_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("family_id"),
    )
    op.create_index("ix_auth_refresh_families_session_id", "auth_refresh_families", ["session_id"], unique=False)
    op.create_index("ix_auth_refresh_families_user_id", "auth_refresh_families", ["user_id"], unique=False)
    op.create_index(
        "ix_auth_refresh_families_organization_id",
        "auth_refresh_families",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "identity_external_identities",
        sa.Column("external_identity_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=128), nullable=False),
        sa.Column("provider_key", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("claims_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("external_identity_id"),
        sa.UniqueConstraint(
            "provider_type",
            "provider_key",
            "subject",
            name="uq_external_identity_provider_subject",
        ),
    )
    op.create_index("ix_identity_external_identities_user_id", "identity_external_identities", ["user_id"], unique=False)
    op.create_index(
        "ix_identity_external_identities_organization_id",
        "identity_external_identities",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_identity_external_identities_organization_id", table_name="identity_external_identities")
    op.drop_index("ix_identity_external_identities_user_id", table_name="identity_external_identities")
    op.drop_table("identity_external_identities")

    op.drop_index("ix_auth_refresh_families_organization_id", table_name="auth_refresh_families")
    op.drop_index("ix_auth_refresh_families_user_id", table_name="auth_refresh_families")
    op.drop_index("ix_auth_refresh_families_session_id", table_name="auth_refresh_families")
    op.drop_table("auth_refresh_families")

    op.drop_index("ix_auth_sessions_organization_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_table("identity_org_memberships")

    op.drop_index("ix_identity_organizations_slug", table_name="identity_organizations")
    op.drop_table("identity_organizations")

    op.drop_index("ix_identity_users_email", table_name="identity_users")
    op.drop_table("identity_users")

    op.drop_index("ix_tool_invocations_status", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_tool_ref", table_name="tool_invocations")
    op.drop_table("tool_invocations")

    op.drop_index("ix_turn_traces_recorded_at", table_name="turn_traces")
    op.drop_index("ix_turn_traces_organization_id", table_name="turn_traces")
    op.drop_index("ix_turn_traces_conversation_id", table_name="turn_traces")
    op.drop_table("turn_traces")

    op.drop_index("ix_conversations_organization_id", table_name="conversations")
    op.drop_table("conversations")
