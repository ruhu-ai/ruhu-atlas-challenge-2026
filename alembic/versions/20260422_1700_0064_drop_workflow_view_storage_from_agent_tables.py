"""Drop duplicated workflow-view storage from agent tables.

Revision ID: 0064
Revises: 0063
Create Date: 2026-04-22 17:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    op.execute(
        sa.text(
            f'''
            ALTER TABLE "{table_name}"
            DROP COLUMN IF EXISTS "{column_name}"
            '''
        )
    )


def _add_json_column_if_missing(table_name: str, column_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                      AND column_name = '{column_name}'
                ) THEN
                    ALTER TABLE "{table_name}"
                    ADD COLUMN "{column_name}" JSON NOT NULL DEFAULT '{{}}';
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    for table_name in ("agent_versions", "agent_templates"):
        _drop_column_if_exists(table_name, "workflow_view_json")
        _drop_column_if_exists(table_name, "state_graph_json")


def downgrade() -> None:
    for table_name in ("agent_versions", "agent_templates"):
        _add_json_column_if_missing(table_name, "workflow_view_json")
