"""add conversation_turns event log + last_turn_seq counter

RP-1.1 (docs/remediation-program/plan.md): append-only per-turn event log with
DB-enforced duplicate-turn guard and total order. ``conversations.last_turn_seq``
is the per-conversation counter incremented under a row lock at commit time.

Revision ID: 0086_conversation_turns
Revises: 0085_atlas_readiness
Create Date: 2026-06-09 14:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0086_conversation_turns"
down_revision = "0085_atlas_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("last_turn_seq", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "conversation_turns",
        sa.Column("turn_pk", sa.String(255), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(255),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("dedupe_key", sa.String(512), nullable=False),
        sa.Column("trace_id", sa.String(255), nullable=True),
        sa.Column("step_before", sa.String(255), nullable=False),
        sa.Column("step_after", sa.String(255), nullable=False),
        sa.Column("state_after_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "dedupe_key", name="uq_conversation_turns_dedupe"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_conversation_turns_seq"),
    )
    op.create_index("ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"])
    op.create_index("ix_conversation_turns_organization_id", "conversation_turns", ["organization_id"])
    op.create_index(
        "ix_conversation_turns_org_created",
        "conversation_turns",
        ["organization_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_column("conversations", "last_turn_seq")
