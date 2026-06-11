"""add live turn scores

Revision ID: 0079
Revises: 0078
Create Date: 2026-05-02 12:00:00.000000

Persistence for the continuous evaluation loop (see ``ruhu.live_eval``).
The table is auto-enrolled in the runtime tenant RLS policy set because
its model carries an ``organization_id`` column — see
``ruhu.db._compute_runtime_tenant_rls_tables``. The
``ensure_postgres_runtime_tenant_policies`` step that runs after every
schema build (see ``ruhu.db.build_session_factory``) installs the
``tenant_scope_live_turn_scores`` policy automatically.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_turn_scores",
        # Composite primary key — see model docstring for rationale.
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("scorer_name", sa.String(length=128), nullable=False),
        sa.Column("scorer_version", sa.String(length=64), nullable=False),
        # Tenant scoping (RLS).
        sa.Column("organization_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "trace_id", "scorer_name", "scorer_version",
            name="pk_live_turn_scores",
        ),
    )
    op.create_index(
        "ix_live_turn_scores_organization_id",
        "live_turn_scores",
        ["organization_id"],
    )
    op.create_index(
        "ix_live_turn_scores_agent_id",
        "live_turn_scores",
        ["agent_id"],
    )
    op.create_index(
        "ix_live_turn_scores_conversation",
        "live_turn_scores",
        ["conversation_id"],
    )
    op.create_index(
        "ix_live_turn_scores_org_scored_at",
        "live_turn_scores",
        ["organization_id", "scored_at"],
    )
    op.create_index(
        "ix_live_turn_scores_dimension_scored_at",
        "live_turn_scores",
        ["dimension", "scored_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_live_turn_scores_dimension_scored_at", table_name="live_turn_scores"
    )
    op.drop_index(
        "ix_live_turn_scores_org_scored_at", table_name="live_turn_scores"
    )
    op.drop_index(
        "ix_live_turn_scores_conversation", table_name="live_turn_scores"
    )
    op.drop_index(
        "ix_live_turn_scores_agent_id", table_name="live_turn_scores"
    )
    op.drop_index(
        "ix_live_turn_scores_organization_id", table_name="live_turn_scores"
    )
    op.drop_table("live_turn_scores")
