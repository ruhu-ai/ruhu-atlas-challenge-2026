"""tool_integration_jobs: durable external work plane

Adds a dedicated durable job table for long-running external tool work. Jobs
are linked 1:1 with tool invocations and carry worker lease, poll/webhook wait,
retry scheduling, and terminal result fields.

Revision ID: 0052
Revises: 0051
Create Date: 2026-04-17 14:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_integration_jobs",
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("job_id", sa.String(length=255), nullable=False),
        sa.Column("invocation_id", sa.String(length=255), nullable=False),
        sa.Column("tool_ref", sa.String(length=255), nullable=False),
        sa.Column("executor_kind", sa.String(length=64), nullable=False),
        sa.Column("resolution_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("external_job_id", sa.String(length=255), nullable=True),
        sa.Column("callback_correlation_id", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["invocation_id"], ["tool_invocations.invocation_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("invocation_id", name="uq_tool_integration_jobs_invocation_id"),
    )
    op.create_index("ix_tool_integration_jobs_organization_id", "tool_integration_jobs", ["organization_id"])
    op.create_index("ix_tool_integration_jobs_invocation_id", "tool_integration_jobs", ["invocation_id"])
    op.create_index("ix_tool_integration_jobs_tool_ref", "tool_integration_jobs", ["tool_ref"])
    op.create_index("ix_tool_integration_jobs_resolution_mode", "tool_integration_jobs", ["resolution_mode"])
    op.create_index("ix_tool_integration_jobs_status", "tool_integration_jobs", ["status"])
    op.create_index("ix_tool_integration_jobs_queue_name", "tool_integration_jobs", ["queue_name"])
    op.create_index("ix_tool_integration_jobs_worker_id", "tool_integration_jobs", ["worker_id"])
    op.create_index("ix_tool_integration_jobs_lease_expires_at", "tool_integration_jobs", ["lease_expires_at"])
    op.create_index("ix_tool_integration_jobs_dedupe_key", "tool_integration_jobs", ["dedupe_key"])
    op.create_index("ix_tool_integration_jobs_external_job_id", "tool_integration_jobs", ["external_job_id"])
    op.create_index(
        "ix_tool_integration_jobs_callback_correlation_id",
        "tool_integration_jobs",
        ["callback_correlation_id"],
    )
    op.create_index("ix_tool_integration_jobs_submitted_at", "tool_integration_jobs", ["submitted_at"])
    op.create_index("ix_tool_integration_jobs_last_progress_at", "tool_integration_jobs", ["last_progress_at"])
    op.create_index("ix_tool_integration_jobs_next_poll_at", "tool_integration_jobs", ["next_poll_at"])
    op.create_index("ix_tool_integration_jobs_next_retry_at", "tool_integration_jobs", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_integration_jobs_next_retry_at", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_next_poll_at", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_last_progress_at", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_submitted_at", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_callback_correlation_id", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_external_job_id", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_dedupe_key", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_lease_expires_at", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_worker_id", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_queue_name", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_status", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_resolution_mode", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_tool_ref", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_invocation_id", table_name="tool_integration_jobs")
    op.drop_index("ix_tool_integration_jobs_organization_id", table_name="tool_integration_jobs")
    op.drop_table("tool_integration_jobs")
