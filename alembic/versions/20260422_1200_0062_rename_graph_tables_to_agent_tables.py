"""Rename graph-era storage tables to agent-era names.

Revision ID: 0062
Revises: 0061
Create Date: 2026-04-22 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def _rename_index_if_exists(old_name: str, new_name: str) -> None:
    op.execute(sa.text(f'ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}"'))


def _rename_constraint_if_exists(table_name: str, old_name: str, new_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = '{old_name}'
                ) THEN
                    ALTER TABLE "{table_name}" RENAME CONSTRAINT "{old_name}" TO "{new_name}";
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    op.rename_table("graphs", "agents")
    op.rename_table("graph_versions", "agent_versions")
    op.rename_table("graph_templates", "agent_templates")

    _rename_constraint_if_exists("agents", "graphs_pkey", "agents_pkey")
    _rename_constraint_if_exists("agent_versions", "graph_versions_pkey", "agent_versions_pkey")
    _rename_constraint_if_exists(
        "agent_versions",
        "uq_graph_versions_graph_version_number",
        "uq_agent_versions_agent_version_number",
    )
    _rename_constraint_if_exists("agent_templates", "graph_templates_pkey", "agent_templates_pkey")
    _rename_constraint_if_exists("agent_templates", "uq_graph_templates_slug", "uq_agent_templates_slug")

    _rename_index_if_exists("ix_graphs_organization_id", "ix_agents_organization_id")
    _rename_index_if_exists("ix_graphs_current_draft_version_id", "ix_agents_current_draft_version_id")
    _rename_index_if_exists("ix_graphs_current_published_version_id", "ix_agents_current_published_version_id")
    _rename_index_if_exists("ix_graph_versions_graph_id", "ix_agent_versions_graph_id")
    _rename_index_if_exists("ix_graph_versions_organization_id", "ix_agent_versions_organization_id")
    _rename_index_if_exists("ix_graph_versions_status", "ix_agent_versions_status")
    _rename_index_if_exists("ix_graph_versions_based_on_version_id", "ix_agent_versions_based_on_version_id")
    _rename_index_if_exists("ix_graph_versions_published_at", "ix_agent_versions_published_at")
    _rename_index_if_exists("ix_graph_templates_organization_id", "ix_agent_templates_organization_id")
    _rename_index_if_exists("ix_graph_templates_category", "ix_agent_templates_category")
    _rename_index_if_exists("ix_graph_templates_is_published", "ix_agent_templates_is_published")
    _rename_index_if_exists("ix_graph_templates_is_featured", "ix_agent_templates_is_featured")


def downgrade() -> None:
    _rename_index_if_exists("ix_agent_templates_is_featured", "ix_graph_templates_is_featured")
    _rename_index_if_exists("ix_agent_templates_is_published", "ix_graph_templates_is_published")
    _rename_index_if_exists("ix_agent_templates_category", "ix_graph_templates_category")
    _rename_index_if_exists("ix_agent_templates_organization_id", "ix_graph_templates_organization_id")
    _rename_index_if_exists("ix_agent_versions_published_at", "ix_graph_versions_published_at")
    _rename_index_if_exists("ix_agent_versions_based_on_version_id", "ix_graph_versions_based_on_version_id")
    _rename_index_if_exists("ix_agent_versions_status", "ix_graph_versions_status")
    _rename_index_if_exists("ix_agent_versions_organization_id", "ix_graph_versions_organization_id")
    _rename_index_if_exists("ix_agent_versions_graph_id", "ix_graph_versions_graph_id")
    _rename_index_if_exists("ix_agents_current_published_version_id", "ix_graphs_current_published_version_id")
    _rename_index_if_exists("ix_agents_current_draft_version_id", "ix_graphs_current_draft_version_id")
    _rename_index_if_exists("ix_agents_organization_id", "ix_graphs_organization_id")

    _rename_constraint_if_exists("agent_templates", "uq_agent_templates_slug", "uq_graph_templates_slug")
    _rename_constraint_if_exists("agent_templates", "agent_templates_pkey", "graph_templates_pkey")
    _rename_constraint_if_exists(
        "agent_versions",
        "uq_agent_versions_agent_version_number",
        "uq_graph_versions_graph_version_number",
    )
    _rename_constraint_if_exists("agent_versions", "agent_versions_pkey", "graph_versions_pkey")
    _rename_constraint_if_exists("agents", "agents_pkey", "graphs_pkey")

    op.rename_table("agent_templates", "graph_templates")
    op.rename_table("agent_versions", "graph_versions")
    op.rename_table("agents", "graphs")
