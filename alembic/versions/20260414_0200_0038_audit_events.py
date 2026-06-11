"""audit: create audit_events table

Stores tamper-evident, tenant-scoped audit events with hash chain.
Covers both HTTP-level mutations (via middleware) and explicit
application-level events (via emit_audit_event).

Revision ID: 0038_audit_events
Revises: 0037_tooling_redesign_entity_model
Create Date: 2026-04-14 02:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_audit_events"
down_revision = "0037_tooling_redesign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("organization_id", sa.String(255), nullable=False, index=True),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("actor_ip", sa.String(45), nullable=True),
        sa.Column("actor_session_id", sa.String(255), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("detail", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("outcome", sa.String(20), nullable=False, server_default=sa.text("'success'")),
        sa.Column("http_method", sa.String(10), nullable=True),
        sa.Column("http_path", sa.String(500), nullable=True),
        sa.Column("http_status", sa.SmallInteger, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(30), nullable=False),
    )

    op.create_index("ix_audit_org_created", "audit_events", ["organization_id", "created_at"])
    op.create_index("ix_audit_org_resource", "audit_events", ["organization_id", "resource_type", "resource_id", "created_at"])
    op.create_index("ix_audit_org_actor", "audit_events", ["organization_id", "actor_id", "created_at"])
    op.create_index("ix_audit_org_event_type", "audit_events", ["organization_id", "event_type", "created_at"])
    op.create_index("ix_audit_request_id", "audit_events", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_request_id", table_name="audit_events")
    op.drop_index("ix_audit_org_event_type", table_name="audit_events")
    op.drop_index("ix_audit_org_actor", table_name="audit_events")
    op.drop_index("ix_audit_org_resource", table_name="audit_events")
    op.drop_index("ix_audit_org_created", table_name="audit_events")
    op.drop_table("audit_events")
