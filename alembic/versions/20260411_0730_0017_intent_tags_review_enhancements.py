"""add intent tags review enhancement columns

Revision ID: 0017_intent_tags_review
Revises: 0016_intent_tags_summary_core
Create Date: 2026-04-11 07:30:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_intent_tags_review"
down_revision = "0016_intent_tags_summary_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "intent_tag_review_items",
        sa.Column("review_disposition", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "intent_tag_review_items",
        sa.Column("claimed_by_user_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "intent_tag_review_items",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "intent_tag_review_items",
        sa.Column("corrected_conversation_summary_id", sa.String(length=255), nullable=True),
    )
    op.create_foreign_key(
        "fk_intent_tag_review_items_corrected_summary",
        "intent_tag_review_items",
        "intent_tag_conversation_summaries",
        ["corrected_conversation_summary_id"],
        ["conversation_summary_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_intent_tag_review_items_review_disposition",
        "intent_tag_review_items",
        ["review_disposition"],
    )
    op.create_index(
        "ix_intent_tag_review_items_claimed_by_user_id",
        "intent_tag_review_items",
        ["claimed_by_user_id"],
    )
    op.create_index(
        "ix_intent_tag_review_items_corrected_conversation_summary_id",
        "intent_tag_review_items",
        ["corrected_conversation_summary_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_intent_tag_review_items_corrected_conversation_summary_id",
        table_name="intent_tag_review_items",
    )
    op.drop_index(
        "ix_intent_tag_review_items_claimed_by_user_id",
        table_name="intent_tag_review_items",
    )
    op.drop_index(
        "ix_intent_tag_review_items_review_disposition",
        table_name="intent_tag_review_items",
    )
    op.drop_constraint(
        "fk_intent_tag_review_items_corrected_summary",
        "intent_tag_review_items",
        type_="foreignkey",
    )
    op.drop_column("intent_tag_review_items", "corrected_conversation_summary_id")
    op.drop_column("intent_tag_review_items", "claimed_at")
    op.drop_column("intent_tag_review_items", "claimed_by_user_id")
    op.drop_column("intent_tag_review_items", "review_disposition")
