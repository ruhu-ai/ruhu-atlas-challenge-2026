"""journeys: add worker lease fields and claim telemetry

Revision ID: 0026_journey_worker_leases
Revises: 0025_realtime_outbox_fix
Create Date: 2026-04-11 19:00:00+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_journey_worker_leases"
down_revision = "0025_realtime_outbox_fix"
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
    if not _has_column("journey_runtime_jobs", "worker_id"):
        op.add_column(
            "journey_runtime_jobs",
            sa.Column("worker_id", sa.String(length=255), nullable=True),
        )
    if not _has_column("journey_runtime_jobs", "lease_expires_at"):
        op.add_column(
            "journey_runtime_jobs",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("journey_runtime_jobs", "attempt_count"):
        op.add_column(
            "journey_runtime_jobs",
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        )
        op.execute("UPDATE journey_runtime_jobs SET attempt_count = 0 WHERE attempt_count IS NULL")
        op.alter_column("journey_runtime_jobs", "attempt_count", server_default=None)

    if not _has_index("journey_runtime_jobs", "ix_journey_runtime_jobs_worker_id"):
        op.create_index(
            "ix_journey_runtime_jobs_worker_id",
            "journey_runtime_jobs",
            ["worker_id"],
            unique=False,
        )
    if not _has_index("journey_runtime_jobs", "ix_journey_runtime_jobs_lease_expires_at"):
        op.create_index(
            "ix_journey_runtime_jobs_lease_expires_at",
            "journey_runtime_jobs",
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    if _has_index("journey_runtime_jobs", "ix_journey_runtime_jobs_lease_expires_at"):
        op.drop_index("ix_journey_runtime_jobs_lease_expires_at", table_name="journey_runtime_jobs")
    if _has_index("journey_runtime_jobs", "ix_journey_runtime_jobs_worker_id"):
        op.drop_index("ix_journey_runtime_jobs_worker_id", table_name="journey_runtime_jobs")
    if _has_column("journey_runtime_jobs", "attempt_count"):
        op.drop_column("journey_runtime_jobs", "attempt_count")
    if _has_column("journey_runtime_jobs", "lease_expires_at"):
        op.drop_column("journey_runtime_jobs", "lease_expires_at")
    if _has_column("journey_runtime_jobs", "worker_id"):
        op.drop_column("journey_runtime_jobs", "worker_id")
