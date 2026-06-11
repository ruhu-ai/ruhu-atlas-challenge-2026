"""add ticketing activity log table

Revision ID: 0012_ticketing_activity
Revises: 0011_kpi_execution_constraints
Create Date: 2026-04-11 01:45:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_ticketing_activity"
down_revision = "0011_kpi_execution_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticketing_activity",
        sa.Column("activity_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column(
            "connection_id",
            sa.String(length=255),
            sa.ForeignKey("ticketing_connections.connection_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "link_id",
            sa.String(length=255),
            sa.ForeignKey("external_case_links.link_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_case_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("response_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ticketing_activity_organization_id", "ticketing_activity", ["organization_id"])
    op.create_index("ix_ticketing_activity_connection_id", "ticketing_activity", ["connection_id"])
    op.create_index("ix_ticketing_activity_link_id", "ticketing_activity", ["link_id"])
    op.create_index("ix_ticketing_activity_provider", "ticketing_activity", ["provider"])
    op.create_index("ix_ticketing_activity_direction", "ticketing_activity", ["direction"])
    op.create_index("ix_ticketing_activity_action", "ticketing_activity", ["action"])
    op.create_index("ix_ticketing_activity_status", "ticketing_activity", ["status"])
    op.create_index("ix_ticketing_activity_external_case_id", "ticketing_activity", ["external_case_id"])
    op.create_index("ix_ticketing_activity_created_at", "ticketing_activity", ["created_at"])

    policy_name = "tenant_scope_ticketing_activity"
    op.execute(sa.text('ALTER TABLE "ticketing_activity" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('ALTER TABLE "ticketing_activity" FORCE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "ticketing_activity"'))
    op.execute(
        sa.text(
            f'''
            CREATE POLICY "{policy_name}" ON "ticketing_activity"
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
    )


def downgrade() -> None:
    op.execute(sa.text('DROP POLICY IF EXISTS "tenant_scope_ticketing_activity" ON "ticketing_activity"'))
    op.drop_index("ix_ticketing_activity_created_at", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_external_case_id", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_status", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_action", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_direction", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_provider", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_link_id", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_connection_id", table_name="ticketing_activity")
    op.drop_index("ix_ticketing_activity_organization_id", table_name="ticketing_activity")
    op.drop_table("ticketing_activity")
