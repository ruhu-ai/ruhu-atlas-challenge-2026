"""knowledge_documents: add source_url column

Revision ID: 0020_knowledge_source_url
Revises: 0019_turn_trace_rules
Create Date: 2026-04-11 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_knowledge_source_url"
down_revision = "0019_turn_trace_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='knowledge_documents' AND column_name='source_url'"
        )
    ).fetchone()
    if result is None:
        op.add_column(
            "knowledge_documents",
            sa.Column("source_url", sa.String(2048), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "source_url")
