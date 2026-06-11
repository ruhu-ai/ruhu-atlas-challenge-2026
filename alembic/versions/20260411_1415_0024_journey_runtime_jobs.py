"""journeys: persistent runtime job tracking

Revision ID: 0024_journey_runtime_jobs
Revises: 0023_api_keys
Create Date: 2026-04-11 14:15:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_journey_runtime_jobs"
down_revision = "0023_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journey_runtime_jobs",
        sa.Column("job_id", sa.String(255), primary_key=True),
        sa.Column("organization_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("definition_id", sa.String(255), nullable=True),
        sa.Column("journey_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("live_key", sa.String(255), nullable=True),
        sa.Column("payload_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["definition_id"], ["journey_definitions.definition_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["journey_id"], ["journey_instances.journey_id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_journey_runtime_jobs_org_status_submitted",
        "journey_runtime_jobs",
        ["organization_id", "status", sa.text("submitted_at DESC")],
    )
    op.create_index(
        "ix_journey_runtime_jobs_kind_submitted",
        "journey_runtime_jobs",
        ["kind", sa.text("submitted_at DESC")],
    )
    op.create_index(
        "ix_journey_runtime_jobs_definition_id",
        "journey_runtime_jobs",
        ["definition_id"],
        unique=False,
    )
    op.create_index(
        "ix_journey_runtime_jobs_journey_id",
        "journey_runtime_jobs",
        ["journey_id"],
        unique=False,
    )
    op.create_index(
        "uq_journey_runtime_jobs_live_key",
        "journey_runtime_jobs",
        ["live_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_journey_runtime_jobs_live_key", table_name="journey_runtime_jobs")
    op.drop_index("ix_journey_runtime_jobs_journey_id", table_name="journey_runtime_jobs")
    op.drop_index("ix_journey_runtime_jobs_definition_id", table_name="journey_runtime_jobs")
    op.drop_index("ix_journey_runtime_jobs_kind_submitted", table_name="journey_runtime_jobs")
    op.drop_index("ix_journey_runtime_jobs_org_status_submitted", table_name="journey_runtime_jobs")
    op.drop_table("journey_runtime_jobs")
