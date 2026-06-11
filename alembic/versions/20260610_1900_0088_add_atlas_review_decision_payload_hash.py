"""add delta_payload_hash to atlas_review_decisions

AR-1.1 (docs/atlas/Atlas-Review-Remediation-Plan.md): review approvals become
content-addressed. The apply gate only honors an approval whose recorded
payload hash matches the current delta content, so a re-proposed delta that
reuses an approved delta_id with different content cannot inherit the prior
approval. Existing rows keep NULL and therefore no longer gate applies —
fail-closed; affected deltas need re-approval.

Revision ID: 0088_atlas_review_hash
Revises: 0087_jobs_table
Create Date: 2026-06-10 19:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0088_atlas_review_hash"
down_revision = "0087_jobs_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "atlas_review_decisions",
        sa.Column("delta_payload_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("atlas_review_decisions", "delta_payload_hash")
