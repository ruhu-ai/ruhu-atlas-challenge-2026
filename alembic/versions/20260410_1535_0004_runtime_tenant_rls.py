"""add runtime tenant RLS and tool invocation organization scope

Revision ID: 0004_runtime_tenant_rls
Revises: 0003_graph_versions
Create Date: 2026-04-10 15:35:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_runtime_tenant_rls"
down_revision = "0003_graph_versions"
branch_labels = None
depends_on = None

_RUNTIME_TENANT_TABLES = (
    "conversations",
    "turn_traces",
    "tool_invocations",
    "graphs",
    "graph_versions",
    "sales_demo_leads",
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
    op.add_column("tool_invocations", sa.Column("organization_id", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE tool_invocations
        SET organization_id = NULLIF(caller_json ->> 'tenant_id', '')
        WHERE organization_id IS NULL
        """
    )
    op.create_index(
        "ix_tool_invocations_organization_id",
        "tool_invocations",
        ["organization_id"],
        unique=False,
    )

    for table_name in _RUNTIME_TENANT_TABLES:
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(_policy_sql(table_name))


def downgrade() -> None:
    for table_name in reversed(_RUNTIME_TENANT_TABLES):
        policy_name = f"tenant_scope_{table_name}"
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_tool_invocations_organization_id", table_name="tool_invocations")
    op.drop_column("tool_invocations", "organization_id")
