"""add intent tags summary core tables

Revision ID: 0016_intent_tags_summary_core
Revises: 0015_intent_tags_foundation
Create Date: 2026-04-11 06:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_intent_tags_summary_core"
down_revision = "0015_intent_tags_foundation"
branch_labels = None
depends_on = None


_RLS_TABLES = (
    "intent_tag_conversation_summaries",
    "intent_tag_assignments",
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
        "intent_tag_conversation_summaries",
        sa.Column("conversation_summary_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("graph_id", sa.String(length=255), nullable=True),
        sa.Column("graph_version_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("summary_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("primary_intent_name", sa.String(length=100), nullable=True),
        sa.Column("secondary_intents_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("resolution_status", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("final_language", sa.String(length=32), nullable=True),
        sa.Column("response_language", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("requires_human_followup", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("summary_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evidence_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("generated_from_event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_event_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["graphs.graph_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_organization_id",
        "intent_tag_conversation_summaries",
        ["organization_id"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_graph_id",
        "intent_tag_conversation_summaries",
        ["graph_id"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_graph_version_id",
        "intent_tag_conversation_summaries",
        ["graph_version_id"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_conversation_id",
        "intent_tag_conversation_summaries",
        ["conversation_id"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_status",
        "intent_tag_conversation_summaries",
        ["status"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_primary_intent_name",
        "intent_tag_conversation_summaries",
        ["primary_intent_name"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_resolution_status",
        "intent_tag_conversation_summaries",
        ["resolution_status"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_outcome",
        "intent_tag_conversation_summaries",
        ["outcome"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_channel",
        "intent_tag_conversation_summaries",
        ["channel"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_requires_human_followup",
        "intent_tag_conversation_summaries",
        ["requires_human_followup"],
    )
    op.create_index(
        "ix_intent_tag_conversation_summaries_requires_review",
        "intent_tag_conversation_summaries",
        ["requires_review"],
    )
    op.create_index(
        "uq_intent_tag_conversation_summaries_active_final",
        "intent_tag_conversation_summaries",
        ["conversation_id", "summary_version"],
        unique=True,
        postgresql_where=sa.text("status = 'final'"),
    )

    op.create_table(
        "intent_tag_assignments",
        sa.Column("tag_assignment_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("classification_event_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_summary_id", sa.String(length=255), nullable=True),
        sa.Column("tag_definition_id", sa.String(length=255), nullable=False),
        sa.Column("assignment_scope", sa.String(length=32), nullable=False),
        sa.Column("assignment_source", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("evidence_payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_validated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("validated_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["classification_event_id"],
            ["intent_tag_classification_events.classification_event_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_summary_id"],
            ["intent_tag_conversation_summaries.conversation_summary_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tag_definition_id"], ["tag_definitions.tag_definition_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_intent_tag_assignments_organization_id", "intent_tag_assignments", ["organization_id"])
    op.create_index("ix_intent_tag_assignments_conversation_id", "intent_tag_assignments", ["conversation_id"])
    op.create_index(
        "ix_intent_tag_assignments_classification_event_id",
        "intent_tag_assignments",
        ["classification_event_id"],
    )
    op.create_index(
        "ix_intent_tag_assignments_conversation_summary_id",
        "intent_tag_assignments",
        ["conversation_summary_id"],
    )
    op.create_index("ix_intent_tag_assignments_tag_definition_id", "intent_tag_assignments", ["tag_definition_id"])
    op.create_index("ix_intent_tag_assignments_assignment_scope", "intent_tag_assignments", ["assignment_scope"])
    op.create_index("ix_intent_tag_assignments_assignment_source", "intent_tag_assignments", ["assignment_source"])
    op.create_index("ix_intent_tag_assignments_is_validated", "intent_tag_assignments", ["is_validated"])
    op.create_index(
        "ix_intent_tag_assignments_validated_by_user_id",
        "intent_tag_assignments",
        ["validated_by_user_id"],
    )

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

    op.drop_index("ix_intent_tag_assignments_validated_by_user_id", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_is_validated", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_assignment_source", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_assignment_scope", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_tag_definition_id", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_conversation_summary_id", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_classification_event_id", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_conversation_id", table_name="intent_tag_assignments")
    op.drop_index("ix_intent_tag_assignments_organization_id", table_name="intent_tag_assignments")
    op.drop_table("intent_tag_assignments")

    op.drop_index(
        "uq_intent_tag_conversation_summaries_active_final",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index(
        "ix_intent_tag_conversation_summaries_requires_review",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index(
        "ix_intent_tag_conversation_summaries_requires_human_followup",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index("ix_intent_tag_conversation_summaries_channel", table_name="intent_tag_conversation_summaries")
    op.drop_index("ix_intent_tag_conversation_summaries_outcome", table_name="intent_tag_conversation_summaries")
    op.drop_index(
        "ix_intent_tag_conversation_summaries_resolution_status",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index(
        "ix_intent_tag_conversation_summaries_primary_intent_name",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index("ix_intent_tag_conversation_summaries_status", table_name="intent_tag_conversation_summaries")
    op.drop_index(
        "ix_intent_tag_conversation_summaries_conversation_id",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index(
        "ix_intent_tag_conversation_summaries_graph_version_id",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_index("ix_intent_tag_conversation_summaries_graph_id", table_name="intent_tag_conversation_summaries")
    op.drop_index(
        "ix_intent_tag_conversation_summaries_organization_id",
        table_name="intent_tag_conversation_summaries",
    )
    op.drop_table("intent_tag_conversation_summaries")
