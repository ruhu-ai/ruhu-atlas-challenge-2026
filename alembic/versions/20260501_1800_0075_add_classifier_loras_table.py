"""Add ``classifier_loras`` table for the prefill-first LoRA registry (WI-6.5).

Revision ID: 0075
Revises: 0074
Create Date: 2026-05-01 18:00:00.000000

Stores one row per LoRA artifact produced by the Stage 6 training
pipeline. Schema per
``docs/pre-fill-intent-classifier-design/05-training-pipeline.md`` and
WI-6.5 in ``07-work-items.md``. Resolution order at runtime
(``ruhu.classifier.registry.resolve_lora``):

1. (organization, agent_id, step_id, status="production") — per-step
2. (organization, agent_id, step_id IS NULL, status="production") — per-agent
3. None — base model

The "at most one production row per (organization, agent, step)"
invariant is enforced at the application level in ``registry.py``
(SQLite tests don't reliably support partial unique indexes; the app
gates promotion through ``promote_to_production``).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classifier_loras",
        sa.Column("lora_id", sa.String(length=255), primary_key=True),
        sa.Column("organization_id", sa.String(length=255), nullable=True, index=True),
        sa.Column(
            "agent_id",
            sa.String(length=255),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step_id", sa.String(length=255), nullable=True, index=True),
        sa.Column("lora_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("model_uri", sa.String(length=2048), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="candidate",
            index=True,
        ),
        sa.Column("eval_score_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True, index=True),
    )
    op.create_index(
        "ix_classifier_loras_resolution",
        "classifier_loras",
        ["organization_id", "agent_id", "step_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_classifier_loras_resolution", table_name="classifier_loras")
    op.drop_table("classifier_loras")
