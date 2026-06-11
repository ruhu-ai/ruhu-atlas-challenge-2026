"""add unified jobs table (transactional outbox + worker queue)

RP-1.3 / RP-2.1 (docs/remediation-program/plan.md): one jobs table for all
background work. Producers insert in the same transaction as the state change
that caused the job; workers claim with FOR UPDATE SKIP LOCKED + leases.

Revision ID: 0087_jobs_table
Revises: 0086_conversation_turns
Create Date: 2026-06-09 15:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0087_jobs_table"
down_revision = "0086_conversation_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(255), primary_key=True),
        sa.Column("job_type", sa.String(255), nullable=False),
        sa.Column("organization_id", sa.String(255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("4")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_jobs_type_dedupe_active",
        "jobs",
        ["job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_organization_id", "jobs", ["organization_id"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_at", "priority"])


def downgrade() -> None:
    op.drop_table("jobs")
