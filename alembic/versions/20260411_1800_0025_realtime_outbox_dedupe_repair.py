"""merge alembic heads and repair realtime outbox dedupe key drift

Revision ID: 0025_realtime_outbox_fix
Revises: 0024_org_deletion_state, 0024_journey_runtime_jobs
Create Date: 2026-04-11 18:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0025_realtime_outbox_fix"
down_revision = ("0024_org_deletion_state", "0024_journey_runtime_jobs")
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("realtime_outbox", "dedupe_key"):
        op.add_column(
            "realtime_outbox",
            sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        )
    op.execute(
        """
        UPDATE realtime_outbox
        SET dedupe_key = event_id
        WHERE dedupe_key IS NULL AND event_id IS NOT NULL
        """
    )
    if not _has_index("realtime_outbox", "ix_realtime_outbox_dedupe_key"):
        op.create_index(
            "ix_realtime_outbox_dedupe_key",
            "realtime_outbox",
            ["dedupe_key"],
            unique=False,
        )


def downgrade() -> None:
    # Forward-only repair merge. The canonical 0005 migration already owns this column.
    pass
