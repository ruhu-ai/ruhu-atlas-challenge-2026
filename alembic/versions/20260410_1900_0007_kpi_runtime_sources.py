"""add kpi runtime source fields and provider cost records

Revision ID: 0007_kpi_runtime_sources
Revises: 0006_simulation_eval
Create Date: 2026-04-10 19:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_kpi_runtime_sources"
down_revision = "0006_simulation_eval"
branch_labels = None
depends_on = None


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
    op.add_column("conversations", sa.Column("channel", sa.String(length=64), nullable=True))
    op.add_column("conversations", sa.Column("outcome", sa.String(length=64), nullable=True))
    op.create_index("ix_conversations_channel", "conversations", ["channel"], unique=False)
    op.create_index("ix_conversations_outcome", "conversations", ["outcome"], unique=False)

    op.create_table(
        "provider_cost_records",
        sa.Column("cost_record_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("realtime_session_id", sa.String(length=255), nullable=True),
        sa.Column("turn_trace_id", sa.String(length=255), nullable=True),
        sa.Column("tool_invocation_id", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("cost_type", sa.String(length=64), nullable=False),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("reference_key", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.conversation_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["realtime_session_id"], ["realtime_sessions.realtime_session_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_invocation_id"], ["tool_invocations.invocation_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["turn_trace_id"], ["turn_traces.trace_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("cost_record_id"),
    )
    op.create_index("ix_provider_cost_records_organization_id", "provider_cost_records", ["organization_id"], unique=False)
    op.create_index("ix_provider_cost_records_conversation_id", "provider_cost_records", ["conversation_id"], unique=False)
    op.create_index("ix_provider_cost_records_realtime_session_id", "provider_cost_records", ["realtime_session_id"], unique=False)
    op.create_index("ix_provider_cost_records_turn_trace_id", "provider_cost_records", ["turn_trace_id"], unique=False)
    op.create_index("ix_provider_cost_records_tool_invocation_id", "provider_cost_records", ["tool_invocation_id"], unique=False)
    op.create_index("ix_provider_cost_records_provider", "provider_cost_records", ["provider"], unique=False)
    op.create_index("ix_provider_cost_records_cost_type", "provider_cost_records", ["cost_type"], unique=False)
    op.create_index("ix_provider_cost_records_reference_key", "provider_cost_records", ["reference_key"], unique=False)
    op.create_index("ix_provider_cost_records_occurred_at", "provider_cost_records", ["occurred_at"], unique=False)

    table_name = "provider_cost_records"
    policy_name = f"tenant_scope_{table_name}"
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
    op.execute(_policy_sql(table_name))


def downgrade() -> None:
    table_name = "provider_cost_records"
    policy_name = f"tenant_scope_{table_name}"
    op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
    op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_provider_cost_records_occurred_at", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_reference_key", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_cost_type", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_provider", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_tool_invocation_id", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_turn_trace_id", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_realtime_session_id", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_conversation_id", table_name="provider_cost_records")
    op.drop_index("ix_provider_cost_records_organization_id", table_name="provider_cost_records")
    op.drop_table("provider_cost_records")

    op.drop_index("ix_conversations_outcome", table_name="conversations")
    op.drop_index("ix_conversations_channel", table_name="conversations")
    op.drop_column("conversations", "outcome")
    op.drop_column("conversations", "channel")
